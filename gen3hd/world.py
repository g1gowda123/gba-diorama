"""Whole-map Gen 3 world compiler.

Input: a live GBA snapshot + the matching ROM.
Output: a semantic `WorldModel` — full-map layers, per-cell collision /
elevation / layer type / behaviour, object cutouts, and OAM sprites.

Object discovery uses metatile patterns and collision, not colours.  A tree is
a repeated 2x2 metatile block on solid cells; a building is a large solid
rectangle.  Both rules survive a romhack that replaces the tileset.
"""

from __future__ import annotations

import json
import math
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .gba import decode_oam, decode_palette, decode_4bpp_tile, Sprite
from .gen3.locate import LocatedMap, locate_map
from .gen3.rom import (
    GameFamily,
    Gen3Rom,
    MAPGRID_COLLISION_SHIFT,
    MAPGRID_ELEVATION_SHIFT,
    MAPGRID_ID_MASK,
    MapLayout,
    Metatile,
)
from .state import GbaSnapshot

# field_camera.c DrawMetatile(): tile slot -> BG layer (1 top, 2 middle,
# 3 bottom).  Slots 0-3 are the bottom 2x2, slots 4-7 the top 2x2.
LAYER_FOR_TYPE = {
    0: (2, 2, 2, 2, 1, 1, 1, 1),  # NORMAL
    1: (3, 3, 3, 3, 2, 2, 2, 2),  # COVERED
    2: (3, 3, 3, 3, 1, 1, 1, 1),  # SPLIT
}
TILE_POS = ((0, 0), (1, 0), (0, 1), (1, 1), (0, 0), (1, 0), (0, 1), (1, 1))

# Behaviours that are terrain, not liftable objects.
LEDGE_BEHAVIORS = {59, 60, 61, 62, 63, 64, 65, 66}
WATER_BEHAVIORS = {16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27}
TERRAIN_BEHAVIORS = LEDGE_BEHAVIORS | WATER_BEHAVIORS
# Tall/long grass is a low overlay, not a liftable card.
GRASS_BEHAVIORS = {2, 3}
DECOR_EXCLUDED_BEHAVIORS = TERRAIN_BEHAVIORS | GRASS_BEHAVIORS


@dataclass(slots=True)
class WorldObject:
    asset_id: str
    kind: str
    bbox: tuple[int, int, int, int]       # pixels, x0,y0,x1,y1 (exclusive)
    footprint: tuple[int, int, int, int]  # pixels
    anchor: tuple[int, int]               # world px, bottom-centre
    elevation: int
    texture: np.ndarray
    source_cells: tuple[tuple[int, int], ...] = ()
    eave: int = 0
    roof_style: str = "flat"
    depth: float = 0.0

    @property
    def width(self) -> int:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> int:
        return self.bbox[3] - self.bbox[1]


@dataclass(slots=True)
class SpriteBillboard:
    asset_id: str
    texture: np.ndarray
    screen: tuple[int, int]
    world: tuple[int, int]
    priority: int


@dataclass(slots=True)
class WorldModel:
    layout: MapLayout
    family: GameFamily
    width: int
    height: int
    layers: dict[int, np.ndarray]
    composite: np.ndarray
    ground: np.ndarray
    metatile_ids: np.ndarray
    collision: np.ndarray
    elevation: np.ndarray
    layer_types: np.ndarray
    behaviors: np.ndarray
    objects: list[WorldObject] = field(default_factory=list)
    sprites: list[SpriteBillboard] = field(default_factory=list)
    camera_focus: tuple[int, int] = (0, 0)
    player: SpriteBillboard | None = None
    header: str = ""

    @property
    def pixel_width(self) -> int:
        return self.width * 16

    @property
    def pixel_height(self) -> int:
        return self.height * 16

    def summary(self) -> dict:
        return {
            "format": "gen3hd-world-v1",
            "rom": {
                "title": self.header,
                "family": self.family.value,
            },
            "map": {
                "width": self.width,
                "height": self.height,
                "pixel_width": self.pixel_width,
                "pixel_height": self.pixel_height,
                "rom_offset": self.layout.rom_offset,
                "primary_tileset": f"0x{self.layout.primary_pointer:08X}",
                "secondary_tileset": f"0x{self.layout.secondary_pointer:08X}",
            },
            "camera_focus": list(self.camera_focus),
            "layers": {str(k): f"bg{k}.png" for k in sorted(self.layers)},
            "ground": "ground.png",
            "composite": "composite.png",
            "grids": {
                "metatile_ids": self.metatile_ids.tolist(),
                "collision": self.collision.tolist(),
                "elevation": self.elevation.tolist(),
                "layer_types": self.layer_types.tolist(),
                "behaviors": self.behaviors.tolist(),
            },
            "objects": [
                {
                    "id": obj.asset_id,
                    "kind": obj.kind,
                    "bbox": list(obj.bbox),
                    "footprint": list(obj.footprint),
                    "anchor": list(obj.anchor),
                    "elevation": obj.elevation,
                    "texture": f"objects/{obj.asset_id}.png",
                    "eave": obj.eave,
                    "roof_style": obj.roof_style,
                    "depth": obj.depth,
                }
                for obj in self.objects
            ],
            "sprites": [
                {
                    "id": spr.asset_id,
                    "screen": list(spr.screen),
                    "world": list(spr.world),
                    "priority": spr.priority,
                    "texture": f"sprites/{spr.asset_id}.png",
                }
                for spr in self.sprites
            ],
        }

    def export(self, out_dir: str | Path) -> Path:
        root = Path(out_dir)
        (root / "objects").mkdir(parents=True, exist_ok=True)
        (root / "sprites").mkdir(parents=True, exist_ok=True)
        for index, image in self.layers.items():
            Image.fromarray(image, "RGBA").save(root / f"bg{index}.png")
        Image.fromarray(self.composite, "RGBA").save(root / "composite.png")
        Image.fromarray(self.ground, "RGBA").save(root / "ground.png")
        # A soft radial contact shadow shared by every object/sprite card.
        size = 64
        yy, xx = np.mgrid[0:size, 0:size]
        radius = np.sqrt((xx - size / 2 + 0.5) ** 2 + (yy - size / 2 + 0.5) ** 2)
        alpha = np.clip(1.0 - radius / (size * 0.5), 0.0, 1.0) ** 2
        shadow = np.zeros((size, size, 4), dtype=np.uint8)
        shadow[..., 3] = (alpha * 220).astype(np.uint8)
        Image.fromarray(shadow, "RGBA").save(root / "shadow.png")
        # Baked contact occlusion around every card, sampled by the terrain
        # shader in map space.  Real-time shadows supply direction; this keeps
        # objects grounded from every angle without extra fill light.
        Image.fromarray(
            bake_ground_ao(self.objects, self.sprites, self.pixel_width, self.pixel_height),
            "RGBA",
        ).save(root / "ao.png")
        Image.fromarray(
            bake_ground_shadow(
                self.objects, self.sprites, self.pixel_width, self.pixel_height
            ),
            "RGBA",
        ).save(root / "shadow_ground.png")
        for obj in self.objects:
            Image.fromarray(obj.texture, "RGBA").save(root / "objects" / f"{obj.asset_id}.png")
        for spr in self.sprites:
            Image.fromarray(spr.texture, "RGBA").save(root / "sprites" / f"{spr.asset_id}.png")
        manifest = self.summary()
        (root / "world.json").write_text(json.dumps(manifest, indent=1) + "\n")
        (root / "summary.json").write_text(json.dumps({
            "map": manifest["map"],
            "counts": {
                "objects": len(self.objects),
                "trees": sum(1 for o in self.objects if o.kind == "tree"),
                "buildings": sum(1 for o in self.objects if o.kind == "building"),
                "props": sum(1 for o in self.objects if o.kind == "prop"),
                "decor": sum(1 for o in self.objects if o.kind == "decor"),
                "sprites": len(self.sprites),
            },
            "collision": {int(k): int(v) for k, v in zip(*np.unique(self.collision, return_counts=True))},
            "elevation": {int(k): int(v) for k, v in zip(*np.unique(self.elevation, return_counts=True))},
            "layer_types": {int(k): int(v) for k, v in zip(*np.unique(self.layer_types, return_counts=True))},
            "behaviors": {int(k): int(v) for k, v in zip(*np.unique(self.behaviors, return_counts=True))},
        }, indent=1) + "\n")
        return root


# ---------------------------------------------------------------------------
# Layer composition
# ---------------------------------------------------------------------------


def compose_layers(state: GbaSnapshot, rom: Gen3Rom, layout: MapLayout) -> dict[str, np.ndarray]:
    palette = decode_palette(state.palette)
    height, width = layout.height, layout.width
    layers = {
        index: np.zeros((height * 16, width * 16, 4), dtype=np.uint8)
        for index in (1, 2, 3)
    }
    metatiles = np.empty((height, width), dtype=np.uint16)
    collision = np.empty((height, width), dtype=np.uint8)
    elevation = np.empty((height, width), dtype=np.uint8)
    layer_types = np.empty((height, width), dtype=np.uint8)
    behaviors = np.empty((height, width), dtype=np.uint8)
    vram = state.vram

    metatile_cache: dict[int, Metatile] = {}
    for cy in range(height):
        for cx in range(width):
            block = int(layout.map_blocks[cy * width + cx])
            metatile_id = block & MAPGRID_ID_MASK
            metatiles[cy, cx] = metatile_id
            collision[cy, cx] = (block >> MAPGRID_COLLISION_SHIFT) & 0x03
            elevation[cy, cx] = (block >> MAPGRID_ELEVATION_SHIFT) & 0x0F
            metatile = metatile_cache.get(metatile_id)
            if metatile is None:
                metatile = rom.metatile(layout, metatile_id)
                metatile_cache[metatile_id] = metatile
            layer_type = metatile.layer_type
            layer_types[cy, cx] = layer_type
            behaviors[cy, cx] = metatile.behavior
            destination = LAYER_FOR_TYPE.get(layer_type, LAYER_FOR_TYPE[0])
            for slot, entry in enumerate(metatile.tiles):
                if entry == 0:
                    continue
                tile_index = entry & 0x03FF
                bank = (entry >> 12) & 0x0F
                tile = decode_4bpp_tile(vram, tile_index, palette, bank)
                if entry & 0x0400:
                    tile = tile[:, ::-1]
                if entry & 0x0800:
                    tile = tile[::-1, :]
                px, py = TILE_POS[slot]
                x = cx * 16 + px * 8
                y = cy * 16 + py * 8
                layers[destination[slot]][y : y + 8, x : x + 8] = tile
    return {
        "layers": layers,
        "metatile_ids": metatiles,
        "collision": collision,
        "elevation": elevation,
        "layer_types": layer_types,
        "behaviors": behaviors,
    }


def composite_layers(layers: dict[int, np.ndarray]) -> np.ndarray:
    out = np.zeros_like(layers[1])
    for index in (3, 2, 1):
        source = layers[index]
        alpha = source[..., 3:4].astype(np.float32) / 255.0
        out = (source * alpha + out * (1.0 - alpha)).astype(np.uint8)
    return out


# ---------------------------------------------------------------------------
# Object discovery
# ---------------------------------------------------------------------------


def _components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    result: list[list[tuple[int, int]]] = []
    for y in range(height):
        for x in range(width):
            if not mask[y, x] or seen[y, x]:
                continue
            queue = deque([(x, y)])
            seen[y, x] = True
            cells: list[tuple[int, int]] = []
            while queue:
                cx, cy = queue.popleft()
                cells.append((cx, cy))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < width and 0 <= ny < height and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        queue.append((nx, ny))
            result.append(cells)
    return result


def _bbox(cells: list[tuple[int, int]]) -> tuple[int, int, int, int]:
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Binary dilation with a Manhattan radius (no scipy dependency)."""

    out = mask.copy()
    for _ in range(radius):
        grown = out.copy()
        grown[1:, :] |= out[:-1, :]
        grown[:-1, :] |= out[1:, :]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def _is_building(
    cells: list[tuple[int, int]],
    bbox: tuple[int, int, int, int],
    width: int,
    height: int,
) -> bool:
    x0, y0, x1, y1 = bbox
    if x0 == 0 or y0 == 0 or x1 == width or y1 == height:
        return False  # border treelines are not buildings
    box_area = (x1 - x0) * (y1 - y0)
    if box_area < 20:
        return False
    return len(cells) / box_area >= 0.75


def discover_objects(
    composite: np.ndarray,
    metatiles: np.ndarray,
    collision: np.ndarray,
    elevation: np.ndarray,
    behaviors: np.ndarray,
) -> tuple[list[WorldObject], np.ndarray]:
    """Find trees, props and buildings from solid cells and metatile patterns.

    Returns the object list plus the full visual mask (including canopy cells
    that sit on non-solid cells above a trunk).  The mask is what ground repair
    must clear, otherwise painted canopies survive under the 3D cards.
    """

    solid = collision == 1
    height, width = solid.shape
    free = ~solid

    # Ground metatiles are the common ones seen on walkable cells.  A 5%
    # frequency floor rejects tree-canopy variants that also sit on
    # non-solid cells in dense border treelines.
    free_total = int(free.sum())
    ground_counts = Counter(int(v) for v in metatiles[free])
    ground_ids = {
        metatile
        for metatile, count in ground_counts.most_common(8)
        if count >= max(4, free_total * 0.05)
    } or {metatile for metatile, _ in ground_counts.most_common(2)}

    object_ids: set[int] = set()
    for y, x in zip(*np.where(solid)):
        if int(behaviors[y, x]) not in TERRAIN_BEHAVIORS:
            object_ids.add(int(metatiles[y, x]))
    near_solid = _dilate(solid, 4)
    for y, x in zip(*np.where(near_solid & free)):
        metatile = int(metatiles[y, x])
        if metatile not in ground_ids:
            object_ids.add(metatile)
    # Decorative bushes, flowers, rocks and signs sit on walkable cells with a
    # metatile that is not one of the common ground tiles.  Lift them too;
    # otherwise they stay painted flat while the trees become cards.
    decor_mask = (
        free
        & ~np.isin(metatiles, list(ground_ids))
        & ~np.isin(behaviors, list(DECOR_EXCLUDED_BEHAVIORS))
    )
    object_cells = (np.isin(metatiles, list(object_ids)) & near_solid) | decor_mask

    objects: list[WorldObject] = []
    covered = np.zeros_like(solid, dtype=bool)
    tree_ids: set[int] = set()

    def add(kind: str, x0: int, y0: int, x1: int, y1: int, cells: list[tuple[int, int]]) -> None:
        px0, py0, px1, py1 = x0 * 16, y0 * 16, x1 * 16, y1 * 16
        texture = composite[py0:py1, px0:px1].copy()
        # The composite is opaque ground + object art.  Keep only the cells
        # that belong to the object so the card is a cutout, not a rectangle
        # of ground with a tree painted on it.
        cell_mask = object_cells[y0:y1, x0:x1]
        pixel_mask = np.repeat(np.repeat(cell_mask, 16, axis=0), 16, axis=1)
        texture[..., 3] = np.where(pixel_mask, texture[..., 3], 0)
        elevation_value = int(np.median(elevation[y0:y1, x0:x1])) if cells else 0
        objects.append(
            WorldObject(
                asset_id=f"obj-{len(objects):03d}",
                kind=kind,
                bbox=(px0, py0, px1, py1),
                footprint=(px0, py0, px1, py1),
                anchor=((px0 + px1) // 2, py1),
                elevation=elevation_value,
                texture=texture,
                source_cells=tuple(cells),
            )
        )
        for cx, cy in cells:
            covered[cy, cx] = True
        covered[y0:y1, x0:x1] = True

    # Buildings: large contiguous solid rectangles away from the map border.
    for cells in _components(solid):
        bbox = _bbox(cells)
        if _is_building(cells, bbox, width, height):
            add("building", *bbox, cells)

    # Trees / props: repeated 2x2 metatile blocks, then 1x1 fallback.
    def signature_ok(x: int, y: int) -> bool:
        if x + 1 >= width or y + 1 >= height:
            return False
        block = object_cells[y : y + 2, x : x + 2]
        if not block.all():
            return False
        if not solid[y : y + 2, x : x + 2].any():
            return False
        return int(behaviors[y : y + 2, x : x + 2].max()) not in TERRAIN_BEHAVIORS

    # Count recurring 2x2 signatures.  Every all-object 2x2 block becomes a
    # card; border variants can be unique, so a frequency gate would leave
    # holes in the treeline.
    for y in range(height - 1):
        for x in range(width - 1):
            if covered[y : y + 2, x : x + 2].any():
                continue
            if not signature_ok(x, y):
                continue
            add("tree", x, y, x + 2, y + 2, [(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)])
            tree_ids.update(int(v) for v in metatiles[y : y + 2, x : x + 2].reshape(-1))

    for y, x in zip(*np.where(solid)):
        if covered[y, x]:
            continue
        if int(behaviors[y, x]) in TERRAIN_BEHAVIORS:
            continue
        kind = "tree_fragment" if int(metatiles[y, x]) in tree_ids else "prop"
        add(kind, int(x), int(y), int(x) + 1, int(y) + 1, [(int(x), int(y))])

    # Walkable decorations (bushes, flowers, small rocks) — one cell each.
    for y, x in zip(*np.where(decor_mask & ~covered)):
        add("decor", int(x), int(y), int(x) + 1, int(y) + 1, [(int(x), int(y))])

    # Absorb uncovered canopy/trunk cells into an adjacent tree card so a
    # 3-wide tree does not become a clean tree plus a floating leaf fragment.
    trees = [obj for obj in objects if obj.kind == "tree"]
    fragments = [obj for obj in objects if obj.kind == "tree_fragment"]
    absorbed: set[str] = set()
    for fragment in fragments:
        fx0, fy0, fx1, fy1 = fragment.bbox
        best = None
        best_distance = 1 << 30
        for tree in trees:
            tx0, ty0, tx1, ty1 = tree.bbox
            if fx1 < tx0 - 16 or fx0 > tx1 + 16 or fy1 < ty0 - 16 or fy0 > ty1 + 16:
                continue
            distance = abs((fx0 + fx1) - (tx0 + tx1)) + abs((fy0 + fy1) - (ty0 + ty1))
            if best is None or distance < best_distance:
                best = tree
                best_distance = distance
        if best is None:
            fragment.kind = "prop"
            continue
        x0 = min(best.bbox[0], fx0)
        y0 = min(best.bbox[1], fy0)
        x1 = max(best.bbox[2], fx1)
        y1 = max(best.bbox[3], fy1)
        best.bbox = (x0, y0, x1, y1)
        best.footprint = (x0, y0, x1, y1)
        best.anchor = ((x0 + x1) // 2, y1)
        best.texture = composite[y0:y1, x0:x1].copy()
        absorbed.add(fragment.asset_id)

    if absorbed:
        objects = [obj for obj in objects if obj.asset_id not in absorbed]
        for index, obj in enumerate(objects):
            obj.asset_id = f"obj-{index:03d}"

    return objects, object_cells | covered


def sanitize_elevation(elevation: np.ndarray, collision: np.ndarray) -> np.ndarray:
    """Flatten isolated elevation spikes on walkable cells.

    Some map cells carry an elevation sentinel (e.g. 15) for a door or an
    object, which would otherwise extrude a one-cell pillar in the middle of
    a town.  Use the median of the free neighbours when a cell is far above
    its surroundings.
    """

    result = elevation.copy()
    for _ in range(2):
        source = result.copy()
        for z in range(result.shape[0]):
            for x in range(result.shape[1]):
                if collision[z, x] != 0:
                    continue
                neighbours = []
                for nx, nz in ((x - 1, z), (x + 1, z), (x, z - 1), (x, z + 1)):
                    if 0 <= nx < result.shape[1] and 0 <= nz < result.shape[0]:
                        if collision[nz, nx] == 0:
                            neighbours.append(int(source[nz, nx]))
                if not neighbours:
                    continue
                median = int(np.median(neighbours))
                if abs(int(source[z, x]) - median) >= 4:
                    result[z, x] = median
    return result


def repair_ground(
    composite: np.ndarray,
    clear_mask: np.ndarray,
    collision: np.ndarray,
    metatiles: np.ndarray,
) -> np.ndarray:
    """Fill object footprints with the nearest collision-free ground tile."""

    ground = composite.copy()
    height, width = collision.shape
    free = collision == 0
    free_total = int(free.sum())
    counts = Counter(int(v) for v in metatiles[free])
    ground_ids = {
        metatile
        for metatile, count in counts.most_common(8)
        if count >= max(4, free_total * 0.05)
    } or {metatile for metatile, _ in counts.most_common(2)}
    donor_mask = free & ~clear_mask & np.isin(metatiles, list(ground_ids))
    if not donor_mask.any():
        donor_mask = free & ~clear_mask
    if not donor_mask.any():
        return ground

    # Multi-source BFS from ground cells; each object cell copies its nearest
    # ground cell's 16x16 art.
    queue: deque[tuple[int, int, int, int]] = deque()
    seen = np.zeros_like(donor_mask, dtype=bool)
    for y in range(height):
        for x in range(width):
            if donor_mask[y, x]:
                seen[y, x] = True
                queue.append((x, y, x, y))
    donor = np.full((height, width, 2), -1, dtype=np.int16)
    while queue:
        x, y, sx, sy = queue.popleft()
        donor[y, x] = (sx, sy)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height and not seen[ny, nx]:
                seen[ny, nx] = True
                queue.append((nx, ny, sx, sy))

    for y in range(height):
        for x in range(width):
            if not clear_mask[y, x]:
                continue
            sx, sy = donor[y, x]
            if sx < 0:
                continue
            ground[y * 16 : y * 16 + 16, x * 16 : x * 16 + 16] = composite[
                sy * 16 : sy * 16 + 16, sx * 16 : sx * 16 + 16
            ]
    return ground


def refine_object_alpha(objects: list[WorldObject], ground: np.ndarray) -> None:
    """Remove the ground-coloured background from each object card.

    A cell mask alone is not enough: a tree metatile can contain grass pixels
    around the canopy, and a 2x3 card can include a whole ground cell.  We
    learn the map's ground colours from the repaired ground image (no fixed
    green range, so custom tilesets work), then flood-fill the background from
    the card's transparent border.  Ground-coloured pixels *inside* the tree
    stay because they are not connected to the border.  Finally the card is
    cropped to its visible alpha bounds.
    """

    if not objects:
        return
    rgb = ground[..., :3].astype(np.uint32)
    keys = (rgb[..., 0] << 16) | (rgb[..., 1] << 8) | rgb[..., 2]
    global_ground_keys = np.unique(keys[ground[..., 3] > 0])
    if global_ground_keys.size == 0:
        return

    for obj in objects:
        texture = obj.texture
        height, width = texture.shape[:2]
        # Use the ground directly under this object, not every ground type on
        # the map: a brown trunk on grass would otherwise be keyed out by a
        # dirt path's palette elsewhere.
        fx0, fy0, fx1, fy1 = obj.footprint
        reference = ground[fy0:fy1, fx0:fx1]
        if reference.size:
            ref_rgb = reference[..., :3].astype(np.uint32)
            ref_keys = (ref_rgb[..., 0] << 16) | (ref_rgb[..., 1] << 8) | ref_rgb[..., 2]
            ground_keys = np.unique(ref_keys[reference[..., 3] > 0])
        else:
            ground_keys = global_ground_keys
        if ground_keys.size == 0:
            continue
        trgb = texture[..., :3].astype(np.uint32)
        tkeys = (trgb[..., 0] << 16) | (trgb[..., 1] << 8) | trgb[..., 2]
        background = np.isin(tkeys, ground_keys) & (texture[..., 3] > 0)
        # The dark strip at the bottom of a GBA tree is its contact shadow /
        # trunk base, not background.  Keep pixels darker than the ground in
        # the lower half so region-growing cannot eat the tree's feet.
        luma = (
            texture[..., 0].astype(np.float32) * 0.299
            + texture[..., 1].astype(np.float32) * 0.587
            + texture[..., 2].astype(np.float32) * 0.114
        )
        ground_luma = float(
            np.mean(
                reference[..., 0].astype(np.float32) * 0.299
                + reference[..., 1].astype(np.float32) * 0.587
                + reference[..., 2].astype(np.float32) * 0.114
            )
        )
        dark_base = luma < (ground_luma - 12.0)
        dark_base[: height // 2, :] = False
        background &= ~dark_base
        passable = background | (texture[..., 3] == 0)
        visited = np.zeros((height, width), dtype=bool)
        queue: deque[tuple[int, int]] = deque()
        # Seed from the top and sides, never the bottom edge: the bottom is
        # where the trunk/shadow meets the ground.
        for x in range(width):
            if passable[0, x]:
                queue.append((x, 0))
        for y in range(height):
            for x in (0, width - 1):
                if passable[y, x]:
                    queue.append((x, y))
        while queue:
            x, y = queue.popleft()
            if visited[y, x]:
                continue
            visited[y, x] = True
            if background[y, x]:
                texture[y, x, 3] = 0
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height and not visited[ny, nx] and passable[ny, nx]:
                    queue.append((nx, ny))

        visible = np.argwhere(texture[..., 3] > 0)
        if visible.size == 0:
            continue
        y0, x0 = visible.min(axis=0)
        _, x1 = visible.max(axis=0) + 1
        # Keep the full bottom edge: the card must stay anchored at the
        # footprint's front edge, even if the lowest pixels are shadow.
        if (x0, y0, x1, height) != (0, 0, width, height):
            obj.texture = texture[y0:height, x0:x1].copy()
            bx0, by0, _, by1 = obj.bbox
            obj.bbox = (bx0 + int(x0), by0 + int(y0), bx0 + int(x1), by1)
            obj.anchor = ((obj.bbox[0] + obj.bbox[2]) // 2, obj.bbox[3])


def _blur_mask(mask: np.ndarray, radius: int = 2) -> np.ndarray:
    """Separable box blur for the baked occlusion map (numpy only)."""

    kernel = np.ones(2 * radius + 1, dtype=np.float32) / float(2 * radius + 1)
    out = mask
    for axis in (0, 1):
        pad = [(radius, radius) if index == axis else (0, 0) for index in range(2)]
        padded = np.pad(out, pad, mode="edge")
        out = np.apply_along_axis(
            lambda line: np.convolve(line, kernel, mode="valid"), axis, padded
        )
    return out


def bake_ground_ao(
    objects: list[WorldObject],
    sprites: list[SpriteBillboard],
    pixel_width: int,
    pixel_height: int,
) -> np.ndarray:
    """Bake soft contact occlusion under every card into an RGBA overlay.

    The viewer multiplies the ground by this map.  It is deliberately subtle
    and only covers the footprint base: directional shadows come from the
    engine, while this keeps the grass under trunks, flowers and walls from
    looking like the card is pasted on.
    """

    ao = np.zeros((pixel_height, pixel_width), dtype=np.float32)

    def splat(cx: float, cy: float, rx: float, ry: float, strength: float) -> None:
        rx = max(rx, 2.0)
        ry = max(ry, 2.0)
        x0 = max(0, int(cx - rx * 2.2))
        x1 = min(pixel_width, int(cx + rx * 2.2) + 1)
        y0 = max(0, int(cy - ry * 2.2))
        y1 = min(pixel_height, int(cy + ry * 2.2) + 1)
        if x0 >= x1 or y0 >= y1:
            return
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        distance = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
        falloff = np.clip(1.0 - distance, 0.0, 1.0) ** 1.6
        ao[y0:y1, x0:x1] = np.maximum(ao[y0:y1, x0:x1], falloff * strength)

    for obj in objects:
        x0, y0, x1, y1 = obj.footprint
        width = float(x1 - x0)
        cx = (x0 + x1) * 0.5
        base = float(y1)
        if obj.kind == "building":
            splat(cx, base - width * 0.04, width * 0.56, width * 0.13, 0.50)
            splat(cx, base - float(y1 - y0) * 0.2, width * 0.46, width * 0.16, 0.22)
        elif obj.kind == "tree":
            splat(cx, base - width * 0.10, width * 0.34, width * 0.16, 0.40)
        else:
            splat(cx, base - width * 0.06, width * 0.36, width * 0.22, 0.32)

    for sprite in sprites:
        cx = float(sprite.world[0])
        base = float(sprite.world[1])
        splat(cx, base - 2.0, 12.0, 6.0, 0.38)

    ao = _blur_mask(ao, 2)
    out = np.zeros((pixel_height, pixel_width, 4), dtype=np.uint8)
    out[..., 3] = np.clip(ao, 0.0, 1.0) * 255.0
    return out


def bake_ground_shadow(
    objects: list[WorldObject],
    sprites: list[SpriteBillboard],
    pixel_width: int,
    pixel_height: int,
    shadow_dir: tuple[float, float] = (0.82, -0.57),
    elevation_deg: float = 38.0,
) -> np.ndarray:
    """Bake the cutout cards' silhouette shadows into a ground overlay.

    Engine shadow maps do not reliably include alpha-cutout billboards, and a
    radial blob under a tree cannot read as sunlight.  For each card row we
    take the authored alpha silhouette, project it along the sun direction by
    its height, and accumulate a soft band.  The result is a tree-shaped
    shadow in the map's own pixel grid, sampled by the terrain shader.
    """

    shadow = np.zeros((pixel_height, pixel_width), dtype=np.float32)
    projection = 1.0 / max(0.05, math.tan(math.radians(elevation_deg)))
    dir_x, dir_z = shadow_dir

    def splat(cx: float, cy: float, rx: float, ry: float, value: float) -> None:
        rx = max(rx, 0.6)
        ry = max(ry, 0.6)
        x0 = max(0, int(cx - rx * 2.0))
        x1 = min(pixel_width, int(cx + rx * 2.0) + 1)
        y0 = max(0, int(cy - ry * 2.0))
        y1 = min(pixel_height, int(cy + ry * 2.0) + 1)
        if x0 >= x1 or y0 >= y1:
            return
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        distance = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
        falloff = np.clip(1.0 - distance, 0.0, 1.0) ** 1.2
        shadow[y0:y1, x0:x1] = np.maximum(
            shadow[y0:y1, x0:x1], falloff * value
        )

    def project_texture(
        texture: np.ndarray,
        bbox_x0: int,
        bbox_y1: int,
        strength: float,
    ) -> None:
        height, width = texture.shape[:2]
        if height == 0 or width == 0:
            return
        solid = texture[..., 3] > 40
        if not solid.any():
            return
        height_world = height / 16.0
        for row in range(height):
            columns = np.where(solid[row])[0]
            if columns.size == 0:
                continue
            x0 = int(columns.min())
            x1 = int(columns.max()) + 1
            width_world = (x1 - x0) / 16.0
            if width_world <= 0.0:
                continue
            height_above_base = (1.0 - (row + 0.5) / height) * height_world
            offset = height_above_base * projection
            centre_x = (bbox_x0 + (x0 + x1) * 0.5) / 16.0 + dir_x * offset
            centre_y = bbox_y1 / 16.0 + dir_z * offset
            coverage = columns.size / float(max(1, x1 - x0))
            value = strength * (0.45 + coverage * 0.55)
            splat(
                centre_x * 16.0,
                centre_y * 16.0,
                width_world * 8.0 * 1.06,
                2.4,
                value,
            )

    for obj in objects:
        project_texture(
            obj.texture,
            obj.bbox[0],
            obj.bbox[3],
            0.82 if obj.kind == "building" else 0.84,
        )

    for sprite in sprites:
        project_texture(
            sprite.texture,
            sprite.world[0],
            sprite.world[1] + sprite.texture.shape[0],
            0.70,
        )
    shadow = _blur_mask(shadow, 2)
    out = np.zeros((pixel_height, pixel_width, 4), dtype=np.uint8)
    out[..., 3] = np.clip(shadow, 0.0, 1.0) * 255.0
    return out


def annotate_buildings(objects: list[WorldObject]) -> None:
    """Split each building card into wall/roof bands and give it depth."""

    for obj in objects:
        if obj.kind != "building":
            continue
        height, width = obj.texture.shape[:2]
        if height < 8 or width < 8:
            continue
        rgb = obj.texture[..., :3].astype(np.float32)
        rows = rgb.mean(axis=1)
        differences = np.abs(np.diff(rows, axis=0)).sum(axis=1)
        low = int(height * 0.30)
        high = max(low + 1, int(height * 0.65))
        if differences.size:
            eave = low + int(np.argmax(differences[low:high]))
        else:
            eave = height // 2
        obj.eave = int(np.clip(eave, 1, height - 1))
        # A front-on gable roof tapers toward its ridge, so its top rows are
        # narrower than the eaves.  A flat roof keeps the full width.
        alpha = obj.texture[..., 3] > 0
        row_widths = alpha.sum(axis=1)
        full_width = max(1, int(row_widths.max()))
        top_rows = row_widths[: max(1, obj.eave // 2)]
        top_ratio = float(top_rows[: min(4, top_rows.size)].mean()) / full_width
        obj.roof_style = "gable" if top_ratio < 0.92 else "flat"
        obj.depth = max(2.0, min(width / 16.0 * 0.75, 4.5))


# ---------------------------------------------------------------------------
# Sprites
# ---------------------------------------------------------------------------


def _scroll(state: GbaSnapshot) -> tuple[int, int]:
    # BG2 is the ground layer in Gen 3's field renderer.  The scroll registers
    # are the camera offset, so screen + scroll is a world pixel.
    return int(state.io[0x14]) & 0x1FF, int(state.io[0x15]) & 0x1FF


def build_sprites(
    state: GbaSnapshot,
    sprites: list[Sprite],
    map_width: int,
    map_height: int,
) -> tuple[list[SpriteBillboard], SpriteBillboard | None]:
    scroll_x, scroll_y = _scroll(state)
    result: list[SpriteBillboard] = []
    for index, sprite in enumerate(sprites):
        world_x = (sprite.x + scroll_x) % max(map_width, 1)
        world_y = (sprite.y + scroll_y) % max(map_height, 1)
        result.append(
            SpriteBillboard(
                asset_id=f"spr-{index:03d}",
                texture=sprite.rgba,
                screen=(sprite.x, sprite.y),
                world=(int(world_x), int(world_y)),
                priority=sprite.priority,
            )
        )
    # The player is the visible sprite nearest the screen centre.
    player = None
    if result:
        player = min(
            result,
            key=lambda s: (s.screen[0] - 120) ** 2 + (s.screen[1] - 80) ** 2,
        )
    return result, player


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def compile_world(state: GbaSnapshot, rom: Gen3Rom) -> WorldModel:
    located: LocatedMap = locate_map(state.iwram, state.ewram, rom)
    layout = located.layout
    composed = compose_layers(state, rom, layout)
    layers = composed["layers"]
    composite = composite_layers(layers)
    composed["elevation"] = sanitize_elevation(
        composed["elevation"], composed["collision"]
    )
    objects, clear_mask = discover_objects(
        composite,
        composed["metatile_ids"],
        composed["collision"],
        composed["elevation"],
        composed["behaviors"],
    )
    ground = repair_ground(
        composite,
        clear_mask,
        composed["collision"],
        composed["metatile_ids"],
    )
    refine_object_alpha(objects, ground)
    annotate_buildings(objects)
    palette = decode_palette(state.palette)
    oam = decode_oam(state.vram, state.oam, palette, state.io)
    sprites, player = build_sprites(state, oam, layout.width * 16, layout.height * 16)
    focus = player.world if player else (layout.width * 8, layout.height * 8)
    return WorldModel(
        layout=layout,
        family=layout.family,
        width=layout.width,
        height=layout.height,
        layers=layers,
        composite=composite,
        ground=ground,
        metatile_ids=composed["metatile_ids"],
        collision=composed["collision"],
        elevation=composed["elevation"],
        layer_types=composed["layer_types"],
        behaviors=composed["behaviors"],
        objects=objects,
        sprites=sprites,
        camera_focus=focus,
        player=player,
        header=rom.header.title,
    )
