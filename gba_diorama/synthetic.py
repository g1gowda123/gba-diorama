"""A synthetic, GBA-format test world.

The point of this module is to exercise the *real* decoder in ``gba.py``: the
demo does not hand the renderer a pretty PNG.  It builds VRAM, a palette and
text-mode screen maps exactly like a GBA game would, then decodes them back.
That keeps the lab honest and makes it straightforward to swap in a real mGBA
dump later.

The semantic arrays (height / layer / material) are training wheels for the
synthetic scene.  Real games will need the heuristics described in
``docs/EXPERIMENTS.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .gba import PALETTE_SIZE, SCREEN_SIZES, VRAM_SIZE, TextBg

TILE = 8
WORLD_TILES = 64
WORLD_PX = WORLD_TILES * TILE

# Palette banks used by the synthetic scene.
BANK_GROUND = 0
BANK_OBJECT = 1
BANK_TOP = 2

PALETTE_BANKS: dict[int, list[tuple[int, int, int]]] = {
    BANK_GROUND: [
        (0, 0, 0),
        (47, 107, 47),
        (79, 154, 63),
        (127, 196, 90),
        (138, 90, 43),
        (201, 161, 90),
        (31, 79, 143),
        (47, 127, 196),
        (143, 211, 232),
        (107, 74, 42),
        (168, 122, 74),
        (216, 194, 122),
        (58, 58, 74),
        (122, 122, 138),
        (240, 230, 192),
        (255, 255, 255),
    ],
    BANK_OBJECT: [
        (0, 0, 0),
        (30, 59, 30),
        (47, 107, 47),
        (79, 154, 63),
        (107, 63, 31),
        (138, 90, 43),
        (176, 122, 58),
        (122, 31, 31),
        (176, 47, 47),
        (216, 74, 58),
        (232, 216, 168),
        (200, 184, 136),
        (58, 58, 74),
        (143, 211, 232),
        (240, 230, 192),
        (255, 216, 74),
    ],
    BANK_TOP: [
        (0, 0, 0),
        (20, 48, 20),
        (36, 80, 36),
        (58, 122, 42),
        (89, 163, 58),
        (216, 176, 74),
        (232, 90, 138),
        (255, 255, 255),
        (107, 63, 31),
        (138, 90, 43),
        (240, 230, 192),
        (143, 211, 232),
        (201, 161, 90),
        (58, 58, 74),
        (122, 122, 138),
        (255, 216, 74),
    ],
}


def _bgr555(rgb: tuple[int, int, int]) -> int:
    r, g, b = rgb
    return (r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10)


class BgTiles:
    """Writes 4bpp tiles + a text-BG screen map into a VRAM bytearray."""

    def __init__(
        self,
        vram: bytearray,
        layer: int,
        char_base: int,
        screen_base: int,
        size: int,
        bank: int,
    ) -> None:
        self.vram = vram
        self.layer = layer
        self.char_base = char_base
        self.screen_base = screen_base
        self.size = size
        self.bank = bank
        self.tiles_w, self.tiles_h = SCREEN_SIZES[size]
        self.next_slot = 1
        self.materials: dict[str, list[int]] = {}
        self._write_tile(0, np.zeros((8, 8), dtype=np.uint8))  # transparent tile

    def _write_tile(self, slot: int, indices: np.ndarray) -> None:
        addr = self.char_base * 0x4000 + slot * 32
        raw = np.zeros(32, dtype=np.uint8)
        for y in range(8):
            for x in range(8):
                value = int(indices[y, x]) & 0x0F
                raw[y * 4 + x // 2] |= value << (4 * (x % 2))
        self.vram[addr : addr + 32] = raw.tobytes()

    def add_material(self, name: str, variants: list[np.ndarray]) -> None:
        slots: list[int] = []
        for indices in variants:
            slot = self.next_slot
            self._write_tile(slot, indices)
            slots.append(slot)
            self.next_slot += 1
        self.materials[name] = slots

    def _screen_addr(self, tx: int, ty: int) -> int:
        blocks_x = self.tiles_w // 32
        block = (ty // 32) * blocks_x + (tx // 32)
        return (
            self.screen_base * 0x800
            + block * 0x800
            + ((ty % 32) * 32 + (tx % 32)) * 2
        )

    def set_cell(
        self,
        tx: int,
        ty: int,
        material: str,
        variant: int = 0,
        hflip: bool = False,
        vflip: bool = False,
    ) -> None:
        slots = self.materials[material]
        tile = slots[variant % len(slots)]
        entry = tile & 0x03FF
        if hflip:
            entry |= 0x0400
        if vflip:
            entry |= 0x0800
        entry |= (self.bank & 0x0F) << 12
        addr = self._screen_addr(tx, ty)
        self.vram[addr] = entry & 0xFF
        self.vram[addr + 1] = (entry >> 8) & 0xFF


# ---------------------------------------------------------------------------
# Tile pixel patterns (palette indices, 0 = transparent)
# ---------------------------------------------------------------------------


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def _grass(rng: np.random.Generator) -> np.ndarray:
    tile = np.full((8, 8), 2, dtype=np.uint8)
    for _ in range(10):
        x, y = rng.integers(0, 8, 2)
        tile[y, x] = int(rng.choice([1, 3, 3, 4]))
    for _ in range(2):
        x, y = rng.integers(0, 8, 2)
        tile[y, x] = 14
    return tile


def _path(rng: np.random.Generator) -> np.ndarray:
    tile = np.full((8, 8), 5, dtype=np.uint8)
    for _ in range(9):
        x, y = rng.integers(0, 8, 2)
        tile[y, x] = int(rng.choice([4, 11, 14]))
    return tile


def _water(rng: np.random.Generator) -> np.ndarray:
    tile = np.full((8, 8), 7, dtype=np.uint8)
    phase = int(rng.integers(0, 4))
    for y in range(8):
        if (y + phase) % 3 == 0:
            tile[y, :] = 6
        if (y + phase) % 3 == 1:
            tile[y, rng.integers(0, 8)] = 8
    tile[0, 0:3] = 8
    return tile


def _sand(rng: np.random.Generator) -> np.ndarray:
    tile = np.full((8, 8), 11, dtype=np.uint8)
    for _ in range(10):
        x, y = rng.integers(0, 8, 2)
        tile[y, x] = int(rng.choice([10, 14, 5]))
    return tile


def _flowers(rng: np.random.Generator) -> np.ndarray:
    tile = _grass(rng)
    for _ in range(5):
        x, y = rng.integers(0, 8, 2)
        tile[y, x] = int(rng.choice([14, 15]))
    return tile


def _canopy(rng: np.random.Generator) -> np.ndarray:
    tile = np.full((8, 8), 3, dtype=np.uint8)
    for _ in range(18):
        x, y = rng.integers(0, 8, 2)
        tile[y, x] = int(rng.choice([1, 2, 2, 4]))
    # A darker rim so adjacent canopy tiles still read as separate clumps.
    tile[0, :] = np.minimum(tile[0, :], 2)
    tile[:, 0] = np.minimum(tile[:, 0], 2)
    return tile


def _tall_grass(rng: np.random.Generator) -> np.ndarray:
    tile = np.full((8, 8), 2, dtype=np.uint8)
    for x in range(0, 8, 2):
        height = int(rng.integers(3, 8))
        tile[8 - height :, x] = 3
        tile[8 - height + 1 :, min(x + 1, 7)] = 4
        tile[8 - height, x] = 1
    return tile


def _roof(rng: np.random.Generator) -> np.ndarray:
    tile = np.full((8, 8), 8, dtype=np.uint8)
    for y in (2, 5):
        tile[y, :] = 7
        tile[y + 1, :] = 9
    for _ in range(6):
        x, y = rng.integers(0, 8, 2)
        tile[y, x] = int(rng.choice([7, 9]))
    return tile


def _wall(rng: np.random.Generator) -> np.ndarray:
    tile = np.full((8, 8), 10, dtype=np.uint8)
    for y in (0, 4):
        tile[y, :] = 11
    for x in (0, 4):
        tile[:, x] = 11
    return tile


def _rock(rng: np.random.Generator) -> np.ndarray:
    tile = np.full((8, 8), 13, dtype=np.uint8)
    for _ in range(14):
        x, y = rng.integers(0, 8, 2)
        tile[y, x] = int(rng.choice([12, 14, 13]))
    tile[0, :] = 12
    tile[:, 0] = 12
    return tile


def _bush(rng: np.random.Generator) -> np.ndarray:
    tile = np.full((8, 8), 2, dtype=np.uint8)
    for _ in range(16):
        x, y = rng.integers(0, 8, 2)
        tile[y, x] = int(rng.choice([1, 3, 4]))
    return tile


def _fence(rng: np.random.Generator) -> np.ndarray:
    tile = np.full((8, 8), 10, dtype=np.uint8)
    tile[0:2, :] = 5
    tile[6:8, :] = 5
    tile[:, 1:3] = 4
    tile[:, 5:7] = 4
    return tile


def _tile_set(fn, count: int = 4) -> list[np.ndarray]:
    return [fn(_rng(1000 + i)) for i in range(count)]


# ---------------------------------------------------------------------------
# Side-wall textures.  These are not GBA tiles; they are invented side
# surfaces for the extruded columns.  Keeping them separate makes it clear
# what is reconstructed vs. what came from the ROM.
# ---------------------------------------------------------------------------


def _side_texture(
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
    detail: str,
    seed: int,
) -> np.ndarray:
    rng = _rng(seed)
    out = np.zeros((8, 8, 4), dtype=np.uint8)
    for y in range(8):
        t = y / 7.0
        color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        out[y, :, :3] = color
        out[y, :, 3] = 255
    if detail == "brick":
        out[3, :, :3] = np.clip(out[3, :, :3].astype(int) - 24, 0, 255)
        out[:, 3, :3] = np.clip(out[:, 3, :3].astype(int) - 18, 0, 255)
    elif detail == "wood":
        for x in (1, 4, 7):
            out[:, x, :3] = np.clip(out[:, x, :3].astype(int) - 35, 0, 255)
    elif detail == "rock":
        for _ in range(14):
            x, y = rng.integers(0, 8, 2)
            out[y, x, :3] = np.clip(out[y, x, :3].astype(int) + rng.integers(-22, 22), 0, 255)
    elif detail == "water":
        for y in range(8):
            if y % 3 == 0:
                out[y, :, :3] = np.clip(out[y, :, :3].astype(int) + 28, 0, 255)
    elif detail == "grass":
        for _ in range(12):
            x, y = rng.integers(0, 8, 2)
            out[y, x, :3] = np.clip(out[y, x, :3].astype(int) + rng.integers(-18, 26), 0, 255)
    return out


SIDE_TEXTURES: dict[str, np.ndarray] = {
    "dirt": _side_texture((118, 82, 44), (74, 50, 28), "rock", 11),
    "cliff": _side_texture((122, 112, 98), (74, 68, 60), "rock", 12),
    "trunk": _side_texture((126, 82, 42), (72, 44, 24), "wood", 13),
    "wall": _side_texture((224, 208, 164), (166, 148, 112), "brick", 14),
    "rock": _side_texture((136, 136, 150), (78, 78, 92), "rock", 15),
    "wood": _side_texture((168, 122, 74), (92, 62, 36), "wood", 16),
    "water": _side_texture((47, 127, 196), (24, 68, 126), "water", 17),
    "grass": _side_texture((76, 142, 58), (40, 86, 38), "grass", 18),
    "canopy": _side_texture((44, 96, 40), (18, 48, 20), "grass", 21),
    "stone": _side_texture((118, 118, 130), (64, 64, 76), "rock", 19),
    "stone_dark": _side_texture((78, 78, 92), (38, 38, 48), "rock", 20),
}


# ---------------------------------------------------------------------------
# Scene description
# ---------------------------------------------------------------------------


@dataclass
class SpriteDef:
    name: str
    x: float
    z: float
    kind: str = "player"
    width: float = 13.0
    height: float = 22.0
    lean: float = 0.08


@dataclass
class SyntheticWorld:
    vram: bytearray
    palette: bytes
    bgs: dict[int, TextBg]
    layers: dict[int, np.ndarray]
    terrain_height: np.ndarray
    ground_side_material: np.ndarray
    layer_present: dict[int, np.ndarray]
    layer_height: dict[int, np.ndarray]
    layer_material: dict[int, np.ndarray]
    layer_side_material: dict[int, np.ndarray]
    sprites: list[SpriteDef] = field(default_factory=list)

    def ground_height(self, x: float, z: float) -> float:
        tx = int(np.clip(x // TILE, 0, WORLD_TILES - 1))
        ty = int(np.clip(z // TILE, 0, WORLD_TILES - 1))
        return float(self.terrain_height[ty, tx])


def _make_terrain() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    yy, xx = np.mgrid[0:WORLD_TILES, 0:WORLD_TILES]
    height = np.zeros((WORLD_TILES, WORLD_TILES), dtype=np.float32)
    material = np.full((WORLD_TILES, WORLD_TILES), "grass", dtype=object)
    side = np.full((WORLD_TILES, WORLD_TILES), "dirt", dtype=object)
    layer = np.zeros((WORLD_TILES, WORLD_TILES), dtype=np.int8)

    # A plateau in the north-east.
    plateau = ((xx - 46.0) / 15.0) ** 2 + ((yy - 18.0) / 12.0) ** 2 < 1.0
    height[plateau] = 6.0
    side[plateau] = "cliff"

    # A pond in the south-west.
    pond = ((xx - 14.0) / 9.0) ** 2 + ((yy - 48.0) / 7.0) ** 2 < 1.0
    height[pond] = -2.0
    material[pond] = "water"
    side[pond] = "water"

    # Sand ring around the pond.
    pond_d = np.sqrt(((xx - 14.0) / 10.5) ** 2 + ((yy - 48.0) / 8.5) ** 2)
    sand = (pond_d >= 0.86) & (pond_d < 1.0) & ~pond
    material[sand] = "sand"
    height[sand] = 0.0

    # Winding path from the south edge toward the plateau.
    path_x = 31.0 + 8.0 * np.sin(yy / 7.0) + 3.5 * np.sin(yy / 3.1)
    path = (np.abs(xx - path_x) < 2.2) & ~pond
    path &= ~((xx > 38) & (yy < 27))
    material[path & (height >= 0)] = "path"

    # A branch up to the house.
    branch = (np.abs(xx - 44.0) < 1.8) & (yy >= 26) & (yy <= 34)
    material[branch & (height >= 0)] = "path"

    # House footprint on the plateau.
    house = (xx >= 40) & (xx < 48) & (yy >= 20) & (yy < 26)
    height[house] = 6.0

    # Flower garden, a little west of the house.
    garden = (xx >= 34) & (xx < 39) & (yy >= 24) & (yy < 29)
    material[garden & (height >= 0)] = "flowers"

    return height, material, side, layer


def build_synthetic_world(seed: int = 7) -> SyntheticWorld:
    rng = np.random.default_rng(seed)
    height, ground_material, ground_side, _ = _make_terrain()
    yy, xx = np.mgrid[0:WORLD_TILES, 0:WORLD_TILES]

    vram = bytearray(VRAM_SIZE)
    palette = bytearray(PALETTE_SIZE)
    pal = np.frombuffer(palette, dtype="<u2")
    for bank, colors in PALETTE_BANKS.items():
        for i, rgb in enumerate(colors):
            pal[bank * 16 + i] = _bgr555(rgb)

    # Each 64x64 text BG needs four 0x800-byte screen blocks (0x2000 total),
    # so the bases must be spaced by four map slots, not one.
    bg3 = BgTiles(vram, 0, char_base=0, screen_base=24, size=3, bank=BANK_GROUND)
    bg2 = BgTiles(vram, 1, char_base=1, screen_base=28, size=3, bank=BANK_OBJECT)
    # The 5-bit screen-base field cannot address 32, so BG1's map lives at 20
    # (0xA000).  Its char data only uses the first few hundred bytes at 0x8000,
    # so the two regions do not collide in practice.
    bg1 = BgTiles(vram, 2, char_base=2, screen_base=20, size=3, bank=BANK_TOP)

    bg3.add_material("grass", _tile_set(_grass))
    bg3.add_material("path", _tile_set(_path))
    bg3.add_material("water", _tile_set(_water))
    bg3.add_material("sand", _tile_set(_sand))
    bg3.add_material("flowers", _tile_set(_flowers))

    bg2.add_material("roof", _tile_set(_roof))
    bg2.add_material("wall", _tile_set(_wall))
    bg2.add_material("rock", _tile_set(_rock))
    bg2.add_material("bush", _tile_set(_bush))
    bg2.add_material("fence", _tile_set(_fence))

    bg1.add_material("canopy", _tile_set(_canopy))
    bg1.add_material("tall_grass", _tile_set(_tall_grass))
    bg1.add_material("flowers", _tile_set(_flowers))

    # Ground layer: every cell is present.
    for ty in range(WORLD_TILES):
        for tx in range(WORLD_TILES):
            mat = str(ground_material[ty, tx])
            bg3.set_cell(tx, ty, mat, variant=(tx * 3 + ty * 5) % 4, hflip=bool((tx ^ ty) & 1))

    # Object layers start empty.
    present2 = np.zeros((WORLD_TILES, WORLD_TILES), dtype=bool)
    present1 = np.zeros((WORLD_TILES, WORLD_TILES), dtype=bool)
    height2 = height.copy()
    height1 = height.copy()
    material2 = np.full((WORLD_TILES, WORLD_TILES), "", dtype=object)
    material1 = np.full((WORLD_TILES, WORLD_TILES), "", dtype=object)
    side2 = np.full((WORLD_TILES, WORLD_TILES), "", dtype=object)
    side1 = np.full((WORLD_TILES, WORLD_TILES), "", dtype=object)

    def put2(tx: int, ty: int, mat: str, h: float, side_mat: str, variant: int = 0) -> None:
        if not (0 <= tx < WORLD_TILES and 0 <= ty < WORLD_TILES):
            return
        present2[ty, tx] = True
        height2[ty, tx] = h
        material2[ty, tx] = mat
        side2[ty, tx] = side_mat
        bg2.set_cell(tx, ty, mat, variant=variant, hflip=bool((tx + ty) & 1))

    def put1(tx: int, ty: int, mat: str, h: float, side_mat: str, variant: int = 0) -> None:
        if not (0 <= tx < WORLD_TILES and 0 <= ty < WORLD_TILES):
            return
        present1[ty, tx] = True
        height1[ty, tx] = h
        material1[ty, tx] = mat
        side1[ty, tx] = side_mat
        bg1.set_cell(tx, ty, mat, variant=variant, hflip=bool((tx + ty) & 1))

    # House: roof on BG2 over a wall facade.  The roof/wall colour break is
    # what the generic structure detector uses to find the eave.
    for ty in range(20, 24):
        for tx in range(40, 48):
            put2(tx, ty, "roof", 16.0, "wall", variant=(tx + ty) % 4)
    for ty in range(24, 27):
        for tx in range(40, 48):
            put2(tx, ty, "wall", 8.0, "wall", variant=(tx + ty) % 4)
    # Door.
    put2(43, 25, "rock", 9.0, "wood", variant=0)
    put2(43, 26, "rock", 9.0, "wood", variant=0)
    # Chimney.
    put2(46, 21, "rock", 20.0, "rock")

    # Fence around the flower garden.
    for tx in range(33, 39):
        put2(tx, 23, "fence", float(height[23, tx]) + 3.5, "wood", variant=tx % 4)
        put2(tx, 29, "fence", float(height[29, tx]) + 3.5, "wood", variant=tx % 4)
    for ty in range(23, 30):
        put2(33, ty, "fence", float(height[ty, 33]) + 3.5, "wood", variant=ty % 4)
        put2(38, ty, "fence", float(height[ty, 38]) + 3.5, "wood", variant=ty % 4)

    # Flower beds on the top layer, low to the ground.
    for ty in range(24, 29):
        for tx in range(34, 38):
            if 0 < tx < WORLD_TILES - 1 and 0 < ty < WORLD_TILES - 1:
                put1(tx, ty, "flowers", float(height[ty, tx]) + 2.0, "grass", variant=(tx * 7 + ty) % 4)

    # Scattered rocks and bushes.
    candidates = [
        (28, 12), (29, 12), (28, 13), (52, 34), (53, 34), (51, 35),
        (22, 20), (23, 20), (22, 21), (36, 44), (37, 44), (55, 45),
    ]
    for i, (tx, ty) in enumerate(candidates):
        if ground_material[ty, tx] in ("water",):
            continue
        if i % 2 == 0:
            put2(tx, ty, "rock", float(height[ty, tx]) + 3.0, "rock", variant=i % 4)
        else:
            put2(tx, ty, "bush", float(height[ty, tx]) + 4.0, "grass", variant=i % 4)

    # Trees.  Keep them off the path, pond, house, and garden.
    protected = (
        (ground_material == "path")
        | (ground_material == "water")
        | (ground_material == "sand")
        | ((xx >= 39) & (xx < 49) & (yy >= 19) & (yy < 27))
        | ((xx >= 32) & (xx < 40) & (yy >= 22) & (yy < 31))
    )
    tree_sites: list[tuple[int, int]] = []
    for _ in range(90):
        tx = int(rng.integers(2, WORLD_TILES - 3))
        ty = int(rng.integers(2, WORLD_TILES - 3))
        if protected[ty, tx]:
            continue
        # Clump trees together.
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                if rng.random() < 0.55:
                    tree_sites.append((tx + dx, ty + dy))
    for tx, ty in tree_sites:
        if not (0 <= tx < WORLD_TILES and 0 <= ty < WORLD_TILES):
            continue
        if protected[ty, tx]:
            continue
        put1(tx, ty, "canopy", float(height[ty, tx]) + 16.0, "trunk", variant=(tx * 5 + ty * 3) % 4)

    # Tall grass patches.
    for _ in range(50):
        tx = int(rng.integers(2, WORLD_TILES - 2))
        ty = int(rng.integers(2, WORLD_TILES - 2))
        if protected[ty, tx] or present1[ty, tx] or present2[ty, tx]:
            continue
        if rng.random() < 0.6:
            put1(tx, ty, "tall_grass", float(height[ty, tx]) + 2.0, "grass", variant=(tx + ty) % 4)

    # Small dock into the pond.
    for ty in range(40, 46):
        if 0 <= ty < WORLD_TILES:
            put2(14, ty, "fence", float(height[ty, 14]) + 3.0, "wood", variant=ty % 4)

    # Decode the layers back out of VRAM.  This is the exact path a real dump
    # will take.
    bgs = {
        3: TextBg(char_base=0, screen_base=24, size=3, bpp=4, priority=3),
        2: TextBg(char_base=1, screen_base=28, size=3, bpp=4, priority=2),
        1: TextBg(char_base=2, screen_base=20, size=3, bpp=4, priority=1),
    }
    from .gba import decode_text_bg

    layers = {n: decode_text_bg(vram, palette, bg) for n, bg in bgs.items()}

    sprites = [
        SpriteDef("player", 34 * TILE + 4, 44 * TILE + 4, "player", 13, 23, 0.08),
        SpriteDef("npc", 22 * TILE + 4, 37 * TILE + 4, "npc", 12, 21, 0.10),
        SpriteDef("critter", 37 * TILE + 4, 49 * TILE + 4, "critter", 10, 10, 0.0),
    ]

    return SyntheticWorld(
        vram=vram,
        palette=bytes(palette),
        bgs=bgs,
        layers=layers,
        terrain_height=height,
        ground_side_material=ground_side,
        layer_present={2: present2, 1: present1},
        layer_height={2: height2, 1: height1},
        layer_material={2: material2, 1: material1},
        layer_side_material={2: side2, 1: side1},
        sprites=sprites,
    )
