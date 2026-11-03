"""Minimal, hardware-accurate GBA background/tilemap decoding.

This module deliberately knows nothing about Pokémon.  It consumes the same
VRAM, palette and register state that an emulator's PPU consumes, so a dump
from any GBA game (including romhacks) can be decoded with it.

Only text backgrounds are implemented for now.  Affine and bitmap modes are
kept behind explicit errors so callers can fall back to the emulator's 2D
composite instead of silently producing the wrong image.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


VRAM_SIZE = 0x18000
PALETTE_SIZE = 0x400
TILE_BYTES_4BPP = 32
TILE_BYTES_8BPP = 64

# GBA text background screen sizes, in tiles.
SCREEN_SIZES: dict[int, tuple[int, int]] = {
    0: (32, 32),
    1: (64, 32),
    2: (32, 64),
    3: (64, 64),
}

# GBA BGR555 -> RGB888.  Replicating the high bits into the low bits gives a
# slightly better ramp than a plain shift.
def _expand5(v: np.ndarray) -> np.ndarray:
    return ((v & 0x1F) << 3) | ((v & 0x1F) >> 2)


def decode_palette(palette: bytes | bytearray | np.ndarray) -> np.ndarray:
    """Decode the 512-entry GBA palette into an (512, 4) RGBA uint8 array."""

    raw = np.frombuffer(bytes(palette[:PALETTE_SIZE]).ljust(PALETTE_SIZE, b"\0"), dtype="<u2")
    r = _expand5(raw)
    g = _expand5(raw >> 5)
    b = _expand5(raw >> 10)
    out = np.zeros((512, 4), dtype=np.uint8)
    out[:, 0] = r
    out[:, 1] = g
    out[:, 2] = b
    out[:, 3] = 255
    out[0, 3] = 0
    return out


def _decode_tile(
    vram: np.ndarray,
    tile_addr: int,
    bpp: int,
    palette: np.ndarray,
    palette_bank: int,
    hflip: bool,
    vflip: bool,
) -> np.ndarray:
    """Decode one 8x8 tile into an RGBA uint8 array."""

    tile = np.zeros((8, 8, 4), dtype=np.uint8)
    if bpp == 4:
        raw = vram[tile_addr : tile_addr + TILE_BYTES_4BPP]
        if raw.size < TILE_BYTES_4BPP:
            return tile
        data = raw.reshape(8, 4)
        # Low nibble is the left pixel, high nibble the right pixel.
        idx = np.empty((8, 8), dtype=np.uint8)
        idx[:, 0::2] = data & 0x0F
        idx[:, 1::2] = data >> 4
        colors = palette[palette_bank * 16 : palette_bank * 16 + 16]
        tile[:] = colors[idx]
        tile[idx == 0] = 0
    elif bpp == 8:
        raw = vram[tile_addr : tile_addr + TILE_BYTES_8BPP]
        if raw.size < TILE_BYTES_8BPP:
            return tile
        idx = raw.reshape(8, 8)
        tile[:] = palette[idx]
        tile[idx == 0] = 0
    else:
        raise ValueError(f"unsupported bpp: {bpp}")

    if hflip:
        tile = tile[:, ::-1]
    if vflip:
        tile = tile[::-1, :]
    return tile


@dataclass(frozen=True)
class TextBg:
    """Descriptor for a GBA text background.

    ``char_base`` and ``screen_base`` are the values written to BGxCNT, i.e.
    in units of 0x4000 and 0x800 bytes respectively.
    """

    char_base: int = 0
    screen_base: int = 0
    size: int = 0
    bpp: int = 4
    priority: int = 0
    mosaic: bool = False


def decode_text_bg(
    vram: bytes | bytearray | np.ndarray,
    palette: bytes | bytearray | np.ndarray,
    bg: TextBg,
) -> np.ndarray:
    """Decode a text background to a (height, width, 4) RGBA image.

    The returned image includes transparent pixels (alpha=0) so it can be used
    directly as a layer texture.  The image is in BG pixel space (256x256,
    512x256, 256x512 or 512x512), not screen space.
    """

    if bg.size not in SCREEN_SIZES:
        raise ValueError(f"invalid BG size: {bg.size}")
    if bg.bpp not in (4, 8):
        raise ValueError(f"invalid BG bpp: {bg.bpp}")

    vram_arr = np.frombuffer(bytes(vram[:VRAM_SIZE]).ljust(VRAM_SIZE, b"\0"), dtype=np.uint8)
    pal = decode_palette(palette)

    tiles_w, tiles_h = SCREEN_SIZES[bg.size]
    out = np.zeros((tiles_h * 8, tiles_w * 8, 4), dtype=np.uint8)

    screen_base = bg.screen_base * 0x800
    char_base = bg.char_base * 0x4000

    # A text BG screen is a grid of 32x32-entry blocks.  For 512-wide maps the
    # blocks are laid out left-to-right; for 512-tall maps they stack.
    blocks_x = tiles_w // 32
    blocks_y = tiles_h // 32

    for by in range(blocks_y):
        for bx in range(blocks_x):
            block = by * blocks_x + bx
            block_addr = screen_base + block * 0x800
            for ty in range(32):
                row_addr = block_addr + ty * 64
                for tx in range(32):
                    entry_addr = row_addr + tx * 2
                    if entry_addr + 1 >= vram_arr.size:
                        continue
                    entry = int(vram_arr[entry_addr]) | (int(vram_arr[entry_addr + 1]) << 8)
                    tile_index = entry & 0x03FF
                    hflip = bool(entry & 0x0400)
                    vflip = bool(entry & 0x0800)
                    bank = (entry >> 12) & 0x0F
                    tile_addr = char_base + tile_index * (TILE_BYTES_4BPP if bg.bpp == 4 else TILE_BYTES_8BPP)
                    tile = _decode_tile(vram_arr, tile_addr, bg.bpp, pal, bank, hflip, vflip)
                    px = (bx * 32 + tx) * 8
                    py = (by * 32 + ty) * 8
                    out[py : py + 8, px : px + 8] = tile

    return out


# ---------------------------------------------------------------------------
# OAM / sprite decoding primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Obj:
    """A decoded GBA object (sprite) entry.

    ``tile_index`` is the 1D tile index when DISPCNT OBJ_1D is set, otherwise
    the caller must interpret the 2D base.  ``width``/``height`` are in pixels.
    """

    x: int
    y: int
    tile_index: int
    palette_bank: int
    width: int
    height: int
    hflip: bool
    vflip: bool
    affine: bool
    priority: int
    mosaic: bool
    bpp: int
    shape: int
    size: int


# GBA OBJ dimensions indexed by [shape][size].
OBJ_SIZES: dict[int, dict[int, tuple[int, int]]] = {
    0: {0: (8, 8), 1: (16, 16), 2: (32, 32), 3: (64, 64)},
    1: {0: (16, 8), 1: (32, 8), 2: (32, 16), 3: (64, 32)},
    2: {0: (8, 16), 1: (8, 32), 2: (16, 32), 3: (32, 64)},
}


def decode_oam_entry(entry: bytes | bytearray | np.ndarray, index: int = 0) -> Obj | None:
    """Decode one 8-byte OAM entry.

    Returns ``None`` for the hidden/disabled entry (affine flag with zero
    dimensions).  Coordinates are wrapped into the GBA's 9-bit signed space.
    """

    raw = bytes(entry[:8]).ljust(8, b"\0")
    attr0 = int.from_bytes(raw[0:2], "little")
    attr1 = int.from_bytes(raw[2:4], "little")
    attr2 = int.from_bytes(raw[4:6], "little")

    if attr0 & 0x0200:
        return None  # affine-object disable bit

    y = attr0 & 0x00FF
    if y >= 0xA0:
        y -= 0x100
    x = attr1 & 0x01FF
    if x >= 0x100:
        x -= 0x200

    shape = (attr0 >> 14) & 0x03
    size = (attr1 >> 14) & 0x03
    if shape == 3:
        return None
    width, height = OBJ_SIZES[shape][size]

    return Obj(
        x=x,
        y=y,
        tile_index=attr2 & 0x03FF,
        palette_bank=(attr2 >> 12) & 0x0F,
        width=width,
        height=height,
        hflip=bool(attr1 & 0x1000),
        vflip=bool(attr1 & 0x2000),
        affine=bool(attr0 & 0x0100),
        priority=(attr2 >> 10) & 0x03,
        mosaic=bool(attr0 & 0x1000),
        bpp=8 if attr0 & 0x2000 else 4,
        shape=shape,
        size=size,
    )
