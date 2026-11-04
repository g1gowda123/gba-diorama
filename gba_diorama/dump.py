"""Load/save the debug dump described in docs/MGBA-HANDOFF.md.

The loader is intentionally independent of mGBA headers.  It accepts a dump
directory and returns decoded RGBA layers, raw PPU state, and a small
`DumpWorld` adapter that the mesher can consume once an atlas exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .gba import TextBg, decode_oam_entry, decode_text_bg
from .profiles import LayerProfile, SceneProfile
from .relief import (
    build_canopy_field,
    detect_alpha_objects,
    detect_canopies,
)

LAYER_NAMES = {3: "bg3", 2: "bg2", 1: "bg1", 0: "bg0"}


def _parse_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise TypeError(f"cannot parse integer: {value!r}")


def _load_rgba(path: Path, size: tuple[int, int] | None = None) -> np.ndarray:
    """Load a PNG or a raw RGBA8 file.

    ``size`` is (width, height) for raw files.  PNGs carry their own size.
    """

    try:
        return np.asarray(Image.open(path).convert("RGBA"), dtype=np.uint8)
    except Exception:
        if size is None:
            raise
        width, height = size
        raw = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        expected = width * height * 4
        if raw.size != expected:
            raise ValueError(
                f"{path}: expected {expected} RGBA bytes for {width}x{height}, got {raw.size}"
            )
        return raw.reshape(height, width, 4).copy()


@dataclass
class GbaDump:
    root: Path
    meta: dict
    vram: np.ndarray | None
    palette: np.ndarray | None
    oam: np.ndarray | None
    layers: dict[int, np.ndarray]
    obj: np.ndarray | None = None
    obj_flags: np.ndarray | None = None
    window: np.ndarray | None = None
    backdrop: np.ndarray | None = None

    @property
    def game_code(self) -> str:
        return str(self.meta.get("game", {}).get("code", ""))


def layer_scroll(dump: GbaDump, index: int) -> tuple[int, int]:
    """Return a BG's scroll offset in dump-image pixels."""

    scale = int(dump.meta.get("scale", 1))
    bg = dump.meta.get("bg")
    if isinstance(bg, list) and index < len(bg) and isinstance(bg[index], dict):
        scroll = bg[index].get("scroll", [0, 0])
        return int(scroll[0]) * scale, int(scroll[1]) * scale

    # The synthetic exporter uses the raw register arrays instead of a bg list.
    hofs = dump.meta.get("bg_hofs", [])
    vofs = dump.meta.get("bg_vofs", [])
    x = int(hofs[index]) * scale if index < len(hofs) else 0
    y = int(vofs[index]) * scale if index < len(vofs) else 0
    return x, y


def load_dump(root: str | Path) -> GbaDump:
    root = Path(root)
    meta_path = root / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"missing {meta_path}")
    meta = json.loads(meta_path.read_text())

    vram = None
    vram_path = root / "vram.bin"
    if vram_path.exists():
        vram = np.frombuffer(vram_path.read_bytes(), dtype=np.uint8)

    palette = None
    palette_path = root / "palette.bin"
    if palette_path.exists():
        palette = np.frombuffer(palette_path.read_bytes(), dtype=np.uint8)

    oam = None
    oam_path = root / "oam.bin"
    if oam_path.exists():
        oam = np.frombuffer(oam_path.read_bytes(), dtype=np.uint8)

    screen = meta.get("screen", [240, 160])
    scale = int(meta.get("scale", 1))
    screen_size = (int(screen[0]) * scale, int(screen[1]) * scale)
    layer_size = tuple(meta.get("layer_size", screen_size))
    flip_y = bool(meta.get("flip_y", False))

    layers: dict[int, np.ndarray] = {}
    bgcnt = meta.get("bgcnt", [])
    dispcnt = _parse_int(meta.get("dispcnt", 0))
    enabled_mask = (dispcnt >> 8) & 0x0F
    for index in (3, 2, 1, 0):
        path = root / f"bg{index}.rgba"
        if path.exists():
            layers[index] = _load_rgba(path, layer_size)
            continue
        if enabled_mask and not (enabled_mask & (1 << index)):
            continue
        if vram is None or palette is None or index >= len(bgcnt):
            continue
        raw = _parse_int(bgcnt[index])
        bg = TextBg(
            char_base=(raw >> 2) & 0x03,
            screen_base=(raw >> 8) & 0x1F,
            size=(raw >> 14) & 0x03,
            bpp=8 if raw & 0x80 else 4,
            priority=raw & 0x03,
        )
        layers[index] = decode_text_bg(vram, palette, bg)

    if flip_y:
        layers = {index: image[::-1] for index, image in layers.items()}

    obj = _optional_rgba(root / "obj.rgba", screen_size)
    obj_flags = _optional_bytes(root / "obj_flags.bin")
    window = _optional_rgba(root / "window.rgba", screen_size)
    backdrop = _optional_rgba(root / "backdrop.rgba", screen_size)
    if flip_y:
        obj = obj[::-1] if obj is not None else None
        window = window[::-1] if window is not None else None
        backdrop = backdrop[::-1] if backdrop is not None else None
    return GbaDump(
        root=root,
        meta=meta,
        vram=vram,
        palette=palette,
        oam=oam,
        layers=layers,
        obj=obj,
        obj_flags=obj_flags,
        window=window,
        backdrop=backdrop,
    )


def _optional_rgba(path: Path, size: tuple[int, int] | None = None) -> np.ndarray | None:
    return _load_rgba(path, size) if path.exists() else None


def _optional_bytes(path: Path) -> np.ndarray | None:
    return np.frombuffer(path.read_bytes(), dtype=np.uint8) if path.exists() else None


def _bgcnt(bg: TextBg) -> int:
    return (
        (bg.priority & 0x03)
        | ((bg.char_base & 0x03) << 2)
        | (0x80 if bg.bpp == 8 else 0)
        | ((bg.screen_base & 0x1F) << 8)
        | ((bg.size & 0x03) << 14)
    )


def export_synthetic_dump(
    world,
    root: str | Path,
    *,
    write_layers: bool = False,
    flip_y: bool = False,
) -> Path:
    """Write the synthetic fixture in the real dump format.

    This is the round-trip test for the loader: with ``write_layers=False`` the
    loader must decode the tilemaps from VRAM exactly as it would for a game.
    """

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "vram.bin").write_bytes(bytes(world.vram))
    (root / "palette.bin").write_bytes(world.palette)
    (root / "oam.bin").write_bytes(b"\0" * 0x400)

    bgcnt = [0, 0, 0, 0]
    for index, bg in world.bgs.items():
        bgcnt[index] = _bgcnt(bg)
    layer_h, layer_w = world.layers[3].shape[:2]
    meta = {
        "frame": 0,
        "game": {"title": "SYNTHETIC", "code": "SYNTH", "maker": "00", "version": 0},
        "scale": 1,
        "screen": [240, 160],
        "layer_size": [layer_w, layer_h],
        "flip_y": flip_y,
        "bgcnt": [f"0x{value:04X}" for value in bgcnt],
        "bg_hofs": [0, 0, 0, 0],
        "bg_vofs": [0, 0, 0, 0],
        "dispcnt": "0x1E00",
    }
    (root / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    if write_layers:
        for index, image in world.layers.items():
            if flip_y:
                image = image[::-1]
            (root / f"bg{index}.rgba").write_bytes(image.tobytes())
    return root


@dataclass
class DumpSprite:
    texture: np.ndarray
    x: float
    z: float
    width: float
    height: float
    kind: str = "player"
    lean: float = 0.0


def _extract_obj_sprites(dump: GbaDump, width: int, height: int, scale: float) -> list[DumpSprite]:
    """Crop visible OAM entries out of the already-composited OBJ layer."""

    if dump.obj is None or dump.oam is None:
        return []
    obj = dump.obj
    if obj.shape[:2] != (height, width):
        return []

    sprites: list[DumpSprite] = []
    for index in range(min(128, len(dump.oam) // 8)):
        entry = decode_oam_entry(dump.oam[index * 8 : (index + 1) * 8], index)
        if entry is None:
            continue
        x0 = max(0, entry.x * dump.meta.get("scale", 1))
        y0 = max(0, entry.y * dump.meta.get("scale", 1))
        x1 = min(width, (entry.x + entry.width) * dump.meta.get("scale", 1))
        y1 = min(height, (entry.y + entry.height) * dump.meta.get("scale", 1))
        if x1 <= x0 or y1 <= y0:
            continue
        crop = obj[y0:y1, x0:x1]
        if not np.any(crop[..., 3]):
            continue
        sprites.append(
            DumpSprite(
                texture=crop.copy(),
                x=(x0 + x1) * 0.5 * scale,
                z=(y0 + y1) * 0.5 * scale,
                width=(x1 - x0) * scale,
                height=(y1 - y0) * scale,
                kind="player",
            )
        )
    return sprites


def _composite_behind(
    dump: GbaDump, ground_index: int, ground: np.ndarray
) -> np.ndarray:
    """Fill transparent holes in the ground with layers behind it.

    Route 101's BG2 ground has a transparent rectangle where a ledge lives on
    BG3.  Extruding BG2 alone leaves a black hole; compositing the backdrop
    layers into the ground keeps the ledge and closes the mesh.
    """

    height, width = ground.shape[:2]
    background = np.zeros((height, width, 4), dtype=np.float32)
    background_alpha = np.zeros((height, width, 1), dtype=np.float32)
    # Composite farthest-to-nearest so the ground can sit on top of the result.
    for index in range(3, ground_index, -1):
        image = dump.layers.get(index)
        if image is None or image.shape != ground.shape:
            continue
        alpha = image[..., 3:4].astype(np.float32) / 255.0
        background[..., :3] = (
            image[..., :3] * alpha + background[..., :3] * (1.0 - alpha)
        )
        background_alpha = alpha + background_alpha * (1.0 - alpha)
        background[..., 3:4] = background_alpha * 255.0

    ground_alpha = ground[..., 3:4].astype(np.float32) / 255.0
    out = np.empty((height, width, 4), dtype=np.float32)
    out[..., :3] = (
        ground[..., :3] * ground_alpha
        + background[..., :3] * (1.0 - ground_alpha)
    )
    out[..., 3:4] = (
        ground_alpha + background_alpha * (1.0 - ground_alpha)
    ) * 255.0
    return np.clip(out, 0, 255).astype(np.uint8)


def _detect_objects(image: np.ndarray, layer: LayerProfile):
    """Run the layer's configured object detector.

    Green is the default because most top-down overworld foliage is saturated
    green, but profiles can switch to alpha for foreground layers or tune the
    HSV range for a romhack with autumn/snow/alien trees.
    """

    if layer.detector == "alpha":
        return detect_alpha_objects(
            image,
            min_alpha=layer.min_alpha,
            min_area=layer.min_area,
        )
    return detect_canopies(
        image,
        min_area=layer.min_area,
        hue_min=layer.hue_min,
        hue_max=layer.hue_max,
        saturation_min=layer.saturation_min,
        value_max=layer.value_max,
    )


@dataclass
class DumpWorld:
    """Minimal SyntheticWorld-compatible adapter for a decoded dump.

    A screen-sized dump becomes a small diorama: the selected ground layer is
    flat, and layers in front of it are extruded.  A real world atlas
    (experiment E1) is still needed to recover off-screen terrain.
    """

    layers: dict[int, np.ndarray]
    terrain_height: np.ndarray
    ground_side_material: np.ndarray
    layer_present: dict[int, np.ndarray]
    layer_height: dict[int, np.ndarray]
    layer_material: dict[int, np.ndarray]
    layer_side_material: dict[int, np.ndarray]
    sprites: list = field(default_factory=list)
    ground_layer: int = 3
    profile: SceneProfile | None = None
    canopy_present: np.ndarray | None = None
    canopy_height: np.ndarray | None = None
    canopy_texture: np.ndarray | None = None
    canopy_shadows: list[tuple[int, int, int, int]] = field(default_factory=list)
    billboard_layers: dict[
        int, tuple[np.ndarray, list[tuple[int, int, int, int]]]
    ] = field(default_factory=dict)

    @classmethod
    def from_dump(
        cls,
        dump: GbaDump,
        *,
        min_overlay_coverage: float = 0.0,
        profile: SceneProfile | None = None,
    ) -> "DumpWorld":
        if not any(index in dump.layers for index in (1, 2, 3)):
            raise ValueError("dump has no BG1/BG2/BG3 layer")

        # Pick the world layer by content, not by a fixed BG number.  A
        # backdrop/checkerboard can cover the whole screen while carrying very
        # few distinct tiles; the playfield is the high-coverage layer with
        # many distinct tiles.  Profiles will eventually override this.
        def score(index: int) -> float:
            image = dump.layers[index]
            h, w = image.shape[:2]
            if h % 8 or w % 8:
                return 0.0
            coverage = float((image[..., 3] > 0).mean())
            tiles = (
                image.reshape(h // 8, 8, w // 8, 8, 4)
                .transpose(0, 2, 1, 3, 4)
                .reshape(-1, 8, 8, 4)
            )
            unique = len({tile.tobytes() for tile in tiles}) / max(1, len(tiles))
            return coverage * (0.5 + 0.5 * min(1.0, unique * 4.0))

        if profile is not None:
            ground_index = profile.ground_layer
            if ground_index is None:
                raise ValueError(f"profile {profile.name!r} has no ground layer")
            if ground_index not in dump.layers:
                raise ValueError(
                    f"profile {profile.name!r} wants BG{ground_index}, "
                    "but that layer is not present in the dump"
                )
        else:
            ground_index = max(
                (index for index in (1, 2, 3) if index in dump.layers),
                key=score,
            )

        ground = _composite_behind(dump, ground_index, dump.layers[ground_index])
        h, w = ground.shape[:2]
        canopy_present = None
        canopy_height = None
        canopy_texture = None
        canopy_shadows: list[tuple[int, int, int, int]] = []
        billboard_layers: dict[
            int, tuple[np.ndarray, list[tuple[int, int, int, int]]]
        ] = {}
        if h % 8 == 0 and w % 8 == 0:
            ground_profile = (
                profile.ground_profile
                if profile is not None
                else LayerProfile(ground_index, role="ground")
            )
            components = _detect_objects(ground, ground_profile)
            # A uniform green field is not a forest.  Only lift components
            # when they cover a plausible fraction of the world layer.
            if components and sum(c.area for c in components) < 0.40 * h * w:
                canopy = build_canopy_field(ground, components)
                if canopy is not None:
                    canopy_present = canopy.present
                    canopy_height = canopy.heights
                    canopy_texture = canopy.texture
                    canopy_shadows = [component.bbox for component in canopy.components]
                    ground = canopy.ground
        if h % 8 or w % 8:
            raise ValueError(f"BG{ground_index} size {w}x{h} is not tile-aligned")
        cells = (h // 8, w // 8)
        terrain = np.zeros(cells, dtype=np.float32)
        ground_side = np.full(
            cells,
            profile.ground_side_material if profile is not None else "dirt",
            dtype=object,
        )

        present: dict[int, np.ndarray] = {}
        heights: dict[int, np.ndarray] = {}
        materials: dict[int, np.ndarray] = {}
        sides: dict[int, np.ndarray] = {}

        def add_overlay(
            index: int,
            image: np.ndarray,
            height: float,
            material: str,
            side_material: str,
            min_coverage: float,
        ) -> None:
            if image.shape[:2] != (h, w):
                raise ValueError(
                    f"BG{index} size {image.shape[1]}x{image.shape[0]} does not match "
                    f"BG{ground_index} {w}x{h}"
                )
            if float((image[..., 3] > 0).mean()) < min_coverage:
                # Sparse layers are usually foreground detail (tree tops,
                # weather) that needs height inference, not a full extrusion.
                return
            alpha = image[..., 3].reshape(cells[0], 8, cells[1], 8).max(axis=(1, 3))
            present[index] = alpha > 0
            heights[index] = np.full(cells, height, dtype=np.float32)
            materials[index] = np.full(cells, material, dtype=object)
            sides[index] = np.full(cells, side_material, dtype=object)

        if profile is not None:
            # Explicit profile layers are trusted even when sparse: a profile
            # may deliberately mark a canopy or a fence as a thin overlay.
            for layer in profile.layers:
                if layer.bg == ground_index or layer.is_ui:
                    continue
                image = dump.layers.get(layer.bg)
                if image is None:
                    continue
                if layer.billboard:
                    components = _detect_objects(image, layer)
                    area = image.shape[0] * image.shape[1]
                    if components and sum(c.area for c in components) < 0.40 * area:
                        field = build_canopy_field(image, components)
                        if field is not None:
                            billboard_layers[layer.bg] = (
                                field.texture,
                                [component.bbox for component in components],
                            )
                    continue
                default_height = (
                    layer.height
                    if layer.height > 0
                    else (6.0 if layer.bg == 2 else 18.0)
                )
                add_overlay(
                    layer.bg,
                    image,
                    default_height,
                    layer.material,
                    layer.side_material or "stone",
                    layer.min_coverage,
                )
        else:
            # Only layers in front of the ground are extruded.  Layers behind
            # it (higher BG index) are backdrop and are left for the atlas pass.
            for index in range(1, ground_index):
                image = dump.layers.get(index)
                if image is None:
                    continue
                add_overlay(
                    index,
                    image,
                    6.0 if index == 2 else 18.0,
                    "",
                    "stone",
                    min_overlay_coverage,
                )

        sprites = _extract_obj_sprites(dump, w, h, 512.0 / max(w, h))
        layers = dict(dump.layers)
        layers[ground_index] = ground
        return cls(
            layers=layers,
            terrain_height=terrain,
            ground_side_material=ground_side,
            layer_present=present,
            layer_height=heights,
            layer_material=materials,
            layer_side_material=sides,
            sprites=sprites,
            ground_layer=ground_index,
            profile=profile,
            canopy_present=canopy_present,
            canopy_height=canopy_height,
            canopy_texture=canopy_texture,
            canopy_shadows=canopy_shadows,
            billboard_layers=billboard_layers,
        )

    def ground_height(self, x: float, z: float) -> float:
        h, w = self.terrain_height.shape
        tx = int(np.clip(x // 8, 0, w - 1))
        ty = int(np.clip(z // 8, 0, h - 1))
        return float(self.terrain_height[ty, tx])
