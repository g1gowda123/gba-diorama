"""Game Boy Advance PPU decoding: palette, tiles and OAM sprites.

Deliberately game-agnostic.  This module knows the GBA hardware format only;
Gen 3 map semantics live in :mod:`gen3hd.gen3`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

PALETTE_SIZE = 512
PALETTE_BYTES = PALETTE_SIZE * 2
OAM_ENTRIES = 128
OAM_BYTES = OAM_ENTRIES * 8

OBJ_SIZES = {
    0: {0: (8, 8), 1: (16, 16), 2: (32, 32), 3: (64, 64)},
    1: {0: (16, 8), 1: (32, 8), 2: (32, 16), 3: (64, 32)},
    2: {0: (8, 16), 1: (8, 32), 2: (16, 32), 3: (32, 64)},
}


def decode_palette(raw: bytes | np.ndarray) -> np.ndarray:
    """Return the GBA's 512 colours as (512, 4) RGBA uint8.

    Colour 0 of every bank is transparent for BG/OBJ use.  We keep all
    512 entries and let callers decide; index 0 alpha is set to 0.
    """

    data = np.frombuffer(bytes(raw[:PALETTE_BYTES]), dtype="<u2")
    if data.size < PALETTE_SIZE:
        data = np.pad(data, (0, PALETTE_SIZE - data.size))
    colors = np.zeros((PALETTE_SIZE, 4), dtype=np.uint8)
    for shift, channel in ((0, 0), (5, 1), (10, 2)):
        value = (data >> shift) & 0x1F
        colors[:, channel] = (value << 3) | (value >> 2)
    colors[:, 3] = 255
    colors[0::16, 3] = 0
    return colors


def decode_4bpp_tile(
    vram: np.ndarray | bytes,
    tile_index: int,
    palette: np.ndarray,
    bank: int,
) -> np.ndarray:
    """Decode one 8x8 4bpp tile.  ``vram`` is sliced at the char base."""

    view = np.frombuffer(vram, dtype=np.uint8) if not isinstance(vram, np.ndarray) else vram
    start = tile_index * 32
    if start + 32 > view.size:
        return np.zeros((8, 8, 4), dtype=np.uint8)
    packed = view[start : start + 32].reshape(8, 4)
    idx = np.empty((8, 8), dtype=np.uint8)
    idx[:, 0::2] = packed & 0x0F
    idx[:, 1::2] = packed >> 4
    rgba = palette[bank * 16 : bank * 16 + 16][idx].copy()
    rgba[idx == 0] = 0
    return rgba


def decode_8bpp_tile(
    vram: np.ndarray | bytes,
    tile_index: int,
    palette: np.ndarray,
) -> np.ndarray:
    """Decode one 8x8 8bpp tile."""

    view = np.frombuffer(vram, dtype=np.uint8) if not isinstance(vram, np.ndarray) else vram
    start = tile_index * 64
    if start + 64 > view.size:
        return np.zeros((8, 8, 4), dtype=np.uint8)
    idx = view[start : start + 64].reshape(8, 8).copy()
    rgba = palette[idx].copy()
    rgba[idx == 0] = 0
    return rgba


def bg_layer_coverage(
    vram: np.ndarray | bytes,
    palette: np.ndarray,
    io: np.ndarray,
    index: int,
) -> float:
    """Fraction of the visible screen covered by one text BG layer.

    Used to tell a field map from a screen: a battle, menu or dialogue frame
    draws its boxes on BG0, while an overworld map leaves BG0 empty.  Only the
    visible 240x160 window is sampled, so a large scrollable map costs no more
    than a small one.
    """

    if index < 0 or index > 3:
        return 0.0
    registers = np.frombuffer(bytes(io), dtype="<u2") if not isinstance(io, np.ndarray) else io
    if registers.size <= 4 + index:
        return 0.0
    dispcnt = int(registers[0])
    if not (dispcnt >> (8 + index)) & 0x01:
        return 0.0
    # ``io`` is a 16-bit view: BGxCNT is at byte 0x08 + 2x (index 4 + x), and
    # BGxHOFS/VOFS at byte 0x10 + 4x (indices 8 + 2x, 9 + 2x).
    control = int(registers[4 + index])
    # Background char/map bases are VRAM offsets from 0x06000000; only OBJ
    # VRAM lives at 0x10000 and above.
    char_base = ((control >> 2) & 0x03) * 0x4000
    map_base = ((control >> 8) & 0x1F) * 0x0800
    eight_bpp = bool(control & 0x80)
    size = (control >> 14) & 0x03
    width_tiles = 32 if size in (0, 1) else 64
    height_tiles = 32 if size in (0, 2) else 64
    scroll_x = int(registers[8 + index * 2]) & 0x1FF
    scroll_y = int(registers[9 + index * 2]) & 0x1FF

    view = np.frombuffer(vram, dtype=np.uint8) if not isinstance(vram, np.ndarray) else vram
    object_vram = view[char_base:]
    covered = 0
    total = 0
    cache: dict[tuple[int, int], np.ndarray] = {}
    for py in range(0, 160, 2):
        for px in range(0, 240, 2):
            tile_x = (scroll_x + px) // 8 % width_tiles
            tile_y = (scroll_y + py) // 8 % height_tiles
            entry_index = tile_y * width_tiles + tile_x
            offset = map_base + entry_index * 2
            if offset + 2 > view.size:
                continue
            entry = int(view[offset]) | (int(view[offset + 1]) << 8)
            tile = entry & 0x03FF
            bank = (entry >> 12) & 0x0F
            pixel_x = (scroll_x + px) % 8
            pixel_y = (scroll_y + py) % 8
            key = (tile, bank if not eight_bpp else -1)
            rgba = cache.get(key)
            if rgba is None:
                if eight_bpp:
                    rgba = decode_8bpp_tile(object_vram, tile, palette)
                else:
                    rgba = decode_4bpp_tile(object_vram, tile, palette, bank)
                cache[key] = rgba
            total += 1
            if rgba[pixel_y, pixel_x, 3] > 0:
                covered += 1
    return (covered / total) if total else 0.0


@dataclass(slots=True)
class Sprite:
    """One non-affine OAM entry, already decoded to RGBA."""

    index: int
    rgba: np.ndarray
    x: int
    y: int
    priority: int
    palette_bank: int
    tile_index: int

    @property
    def width(self) -> int:
        return int(self.rgba.shape[1])

    @property
    def height(self) -> int:
        return int(self.rgba.shape[0])


def decode_oam(
    vram: np.ndarray | bytes,
    oam: np.ndarray | bytes,
    palette: np.ndarray,
    io: np.ndarray,
) -> list[Sprite]:
    """Decode visible, non-affine OAM entries.

    Affine sprites are skipped for now: Gen 3 overwork sprites are almost all
    regular 4bpp rects, and a wrong affine decode is worse than a missing one.
    ``io`` is the 16-bit view of the I/O registers; DISPCNT is ``io[0]``.
    """

    dispcnt = int(io[0])
    one_dimensional = bool(dispcnt & 0x40)
    obj_char_base = 0x10000 + (dispcnt & 0x03) * 0x4000
    object_vram = np.frombuffer(vram, dtype=np.uint8)[obj_char_base:] if not isinstance(vram, np.ndarray) else vram[obj_char_base:]
    entries = np.frombuffer(bytes(oam[:OAM_BYTES]), dtype=np.uint8)
    sprites: list[Sprite] = []

    for index in range(OAM_ENTRIES):
        entry = entries[index * 8 : index * 8 + 8]
        if entry.size < 8:
            break
        attr0 = int(entry[0]) | (int(entry[1]) << 8)
        attr1 = int(entry[2]) | (int(entry[3]) << 8)
        attr2 = int(entry[4]) | (int(entry[5]) << 8)

        affine = bool(attr0 & 0x0100)
        if not affine and (attr0 & 0x0200):  # disabled
            continue
        shape = (attr0 >> 14) & 0x03
        if shape == 3:
            continue
        size = (attr1 >> 14) & 0x03
        width, height = OBJ_SIZES[shape][size]

        y = attr0 & 0xFF
        if y >= 0xA0:
            y -= 0x100
        x = attr1 & 0x1FF
        if x >= 0x100:
            x -= 0x200

        bpp8 = bool(attr0 & 0x2000)
        tile_index = attr2 & 0x03FF
        bank = (attr2 >> 12) & 0x0F
        hflip = bool(attr1 & 0x1000) and not affine
        vflip = bool(attr1 & 0x2000) and not affine
        tiles_w, tiles_h = width // 8, height // 8

        rgba = np.zeros((tiles_h * 8, tiles_w * 8, 4), dtype=np.uint8)
        for ty in range(tiles_h):
            for tx in range(tiles_w):
                if one_dimensional:
                    tile = tile_index + ty * tiles_w + tx
                else:
                    tile = tile_index + ty * 32 + tx
                if bpp8:
                    pixels = decode_8bpp_tile(object_vram, tile, palette)
                else:
                    pixels = decode_4bpp_tile(object_vram, tile, palette, bank)
                rgba[ty * 8 : ty * 8 + 8, tx * 8 : tx * 8 + 8] = pixels
        if hflip:
            rgba = rgba[:, ::-1]
        if vflip:
            rgba = rgba[::-1, :]
        if not rgba[..., 3].any():
            continue
        sprites.append(
            Sprite(
                index=index,
                rgba=rgba,
                x=x,
                y=y,
                priority=(attr2 >> 10) & 0x03,
                palette_bank=bank,
                tile_index=tile_index,
            )
        )
    return sprites
