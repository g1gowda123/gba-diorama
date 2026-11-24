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
