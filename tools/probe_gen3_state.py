#!/usr/bin/env python3
"""Probe a Gen 3 Pokemon savestate + ROM without an emulator.

This is an investigation tool, not part of the renderer.  It proves the data
path the next architecture should be built on:

  mGBA .ss1 (PNG chunks) -> VRAM/PALETTE/OAM/IWRAM/EWRAM
  + ROM tilesets/metatiles -> the *entire* map grid with art, collision,
    elevation, layer type, and behavior.

No game assets are stored in this repo.  Point ``--rom`` at a ROM you own.

Usage:
  python tools/probe_gen3_state.py \
      --state roms/emerald-test/emerald.ss1 \
      --rom "Pokemon - Emerald Version (USA, Europe).gba" \
      --out /tmp/gen3-probe

Outputs:
  bg1.png/bg2.png/bg3.png  full-map layers at 16 px per metatile
  sprites.png              decoded OAM entries on a transparent canvas
  summary.json             map/layout/tileset/semantic statistics

The map/layout finder is deliberately structural:
  * locate ``gBackupMapLayout`` in IWRAM by validating the pointed-to map grid
  * find the ``MapHeader`` in EWRAM whose ROM ``MapLayout`` map data matches
    the backup grid interior exactly
No hardcoded EWRAM/IWRAM addresses, so it survives most engine hacks.
"""

from __future__ import annotations

import argparse
import json
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

GBA_ROM_BASE = 0x08000000
EWRAM_BASE = 0x02000000
MAP_OFFSET = 7  # fieldmap.h: map grid is padded by 7 metatiles
MAPGRID_ID_MASK = 0x03FF
MAPGRID_COLLISION_SHIFT = 10
MAPGRID_ELEVATION_SHIFT = 12
METATILE_ATTR_LAYER_SHIFT = 12


# ---------------------------------------------------------------------------
# mGBA savestate
# ---------------------------------------------------------------------------


def read_mgba_png_state(path: Path) -> bytes:
    """Return the zlib-decompressed ``gbAs`` chunk from an mGBA .ss1 PNG."""

    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: not a PNG savestate")
    offset = 8
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4 : offset + 8]
        body = data[offset + 8 : offset + 8 + length]
        if kind == b"gbAs":
            return zlib.decompress(body)
        offset += 12 + length
        if kind == b"IEND":
            break
    raise ValueError(f"{path}: no gbAs chunk")


@dataclass
class GbaState:
    vram: np.ndarray
    palette: np.ndarray
    oam: np.ndarray
    io: np.ndarray
    iwram: np.ndarray
    ewram: np.ndarray
    frame: int
    title: str
    game_code: str


def parse_state(path: Path) -> GbaState:
    state = read_mgba_png_state(path)
    if len(state) < 0x61000:
        raise ValueError(f"{path}: state is {len(state)} bytes, expected 0x61000")
    # Layout documented in mGBA include/mgba/internal/gba/serialize.h.
    return GbaState(
        vram=np.frombuffer(state[0x01000:0x19000], dtype=np.uint8),
        palette=np.frombuffer(state[0x00800:0x00C00], dtype=np.uint8),
        oam=np.frombuffer(state[0x00C00:0x01000], dtype=np.uint8),
        io=np.frombuffer(state[0x00400:0x00800], dtype="<u2"),
        iwram=np.frombuffer(state[0x19000:0x21000], dtype=np.uint8),
        ewram=np.frombuffer(state[0x21000:0x61000], dtype=np.uint8),
        frame=struct.unpack_from("<I", state, 0x1FC)[0],
        title=state[0x10:0x1C].decode("ascii", "replace").rstrip("\0 "),
        game_code=state[0x1C:0x20].decode("ascii", "replace"),
    )


# ---------------------------------------------------------------------------
# GBA palette / tiles / OAM
# ---------------------------------------------------------------------------


def decode_palette(palette: np.ndarray) -> np.ndarray:
    raw = np.frombuffer(bytes(palette[:0x400]), dtype="<u2")
    colors = np.zeros((512, 4), dtype=np.uint8)
    for shift, channel in ((0, 0), (5, 1), (10, 2)):
        value = (raw >> shift) & 0x1F
        colors[:, channel] = (value << 3) | (value >> 2)
    colors[:, 3] = 255
    colors[0, 3] = 0
    return colors


def decode_4bpp_tile(vram: np.ndarray, tile: int, palette: np.ndarray, bank: int) -> np.ndarray:
    data = vram[tile * 32 : tile * 32 + 32]
    if data.size < 32:
        return np.zeros((8, 8, 4), dtype=np.uint8)
    packed = data.reshape(8, 4)
    idx = np.empty((8, 8), dtype=np.uint8)
    idx[:, 0::2] = packed & 0x0F
    idx[:, 1::2] = packed >> 4
    rgba = palette[bank * 16 : bank * 16 + 16][idx].copy()
    rgba[idx == 0] = 0
    return rgba


def decode_8bpp_tile(vram: np.ndarray, tile: int, palette: np.ndarray) -> np.ndarray:
    data = vram[tile * 64 : tile * 64 + 64]
    if data.size < 64:
        return np.zeros((8, 8, 4), dtype=np.uint8)
    idx = data.reshape(8, 8)
    rgba = palette[idx].copy()
    rgba[idx == 0] = 0
    return rgba


# ---------------------------------------------------------------------------
# Gen 3 ROM structures
# ---------------------------------------------------------------------------


def u16(buf: np.ndarray | bytes, offset: int) -> int:
    return struct.unpack_from("<H", buf, offset)[0]


def u32(buf: np.ndarray | bytes, offset: int) -> int:
    return struct.unpack_from("<I", buf, offset)[0]


def rom_offset(pointer: int) -> int | None:
    if GBA_ROM_BASE <= pointer < GBA_ROM_BASE + 0x02000000:
        return pointer - GBA_ROM_BASE
    return None


def valid_map_block(block: int) -> bool:
    return (block & MAPGRID_ID_MASK) < 1024


def find_backup_map_layout(state: GbaState) -> tuple[int, int, int, int, np.ndarray]:
    """Locate ``gBackupMapLayout`` structurally in IWRAM.

    Layout: {s32 width; s32 height; u16 *map;} where map points into EWRAM.
    """

    best = None
    for offset in range(0, len(state.iwram) - 12, 4):
        width = u32(state.iwram, offset)
        height = u32(state.iwram, offset + 4)
        pointer = u32(state.iwram, offset + 8)
        if not (8 <= width <= 120 and 8 <= height <= 120):
            continue
        if not (EWRAM_BASE <= pointer < EWRAM_BASE + len(state.ewram)):
            continue
        count = width * height
        if count < 64 or count > 10240:
            continue
        start = pointer - EWRAM_BASE
        if start + count * 2 > len(state.ewram):
            continue
        blocks = np.frombuffer(state.ewram[start : start + count * 2], dtype="<u2")
        valid = sum(1 for block in blocks if valid_map_block(int(block)))
        ratio = valid / count
        if ratio > 0.98 and (best is None or ratio > best[0]):
            best = (ratio, offset, width, height, blocks.copy())
    if best is None:
        raise ValueError("could not locate gBackupMapLayout in IWRAM")
    _, offset, width, height, blocks = best
    return offset, width, height, count, blocks


@dataclass
class Layout:
    pointer: int
    width: int
    height: int
    border_pointer: int
    map_pointer: int
    primary_pointer: int
    secondary_pointer: int
    map_blocks: np.ndarray


@dataclass
class Tileset:
    pointer: int
    is_compressed: bool
    is_secondary: bool
    tiles_pointer: int
    palettes_pointer: int
    metatiles_pointer: int
    attributes_pointer: int


def parse_tileset(rom: bytes, pointer: int) -> Tileset:
    offset = rom_offset(pointer)
    if offset is None:
        raise ValueError(f"tileset pointer {pointer:#x} is not in ROM")
    return Tileset(
        pointer=pointer,
        is_compressed=bool(rom[offset]),
        is_secondary=bool(rom[offset + 1]),
        tiles_pointer=u32(rom, offset + 4),
        palettes_pointer=u32(rom, offset + 8),
        metatiles_pointer=u32(rom, offset + 12),
        attributes_pointer=u32(rom, offset + 16),
    )


def find_map_layout(state: GbaState, rom: bytes, backup: np.ndarray, width: int, height: int) -> tuple[int, Layout]:
    """Find the MapHeader whose ROM map data matches the backup grid interior."""

    interior_width = width - (MAP_OFFSET * 2 + 1)
    interior_height = height - (MAP_OFFSET * 2)
    if interior_width <= 0 or interior_height <= 0:
        raise ValueError("backup map is too small")
    interior = backup.reshape(height, width)[
        MAP_OFFSET : MAP_OFFSET + interior_height,
        MAP_OFFSET : MAP_OFFSET + interior_width,
    ]

    candidates: list[tuple[float, int, Layout]] = []
    for offset in range(0, len(state.ewram) - 4, 4):
        pointer = u32(state.ewram, offset)
        layout_offset = rom_offset(pointer)
        if layout_offset is None or layout_offset + 24 > len(rom):
            continue
        layout_width = u32(rom, layout_offset)
        layout_height = u32(rom, layout_offset + 4)
        if (layout_width, layout_height) != (interior_width, interior_height):
            continue
        pointers = [u32(rom, layout_offset + 8 + 4 * i) for i in range(4)]
        if not all(rom_offset(value) is not None for value in pointers):
            continue
        map_offset = rom_offset(pointers[1])
        if map_offset is None or map_offset + 2 * layout_width * layout_height > len(rom):
            continue
        blocks = np.frombuffer(
            rom[map_offset : map_offset + 2 * layout_width * layout_height],
            dtype="<u2",
        )
        equality = float((blocks == interior.reshape(-1)).mean())
        if equality >= 0.98:
            candidates.append(
                (
                    equality,
                    offset,
                    Layout(
                        pointer=pointer,
                        width=layout_width,
                        height=layout_height,
                        border_pointer=pointers[0],
                        map_pointer=pointers[1],
                        primary_pointer=pointers[2],
                        secondary_pointer=pointers[3],
                        map_blocks=blocks.copy(),
                    ),
                )
            )
    if not candidates:
        raise ValueError("could not match gMapHeader.mapLayout against the backup map")
    candidates.sort(key=lambda item: item[0], reverse=True)
    equality, header_offset, layout = candidates[0]
    return header_offset, layout


def metatile_entry(rom: bytes, layout: Layout, metatile_id: int) -> tuple[list[int], int]:
    if metatile_id < 512:
        tileset = parse_tileset(rom, layout.primary_pointer)
        index = metatile_id
        attr_stride = 2
    else:
        tileset = parse_tileset(rom, layout.secondary_pointer)
        index = metatile_id - 512
        attr_stride = 2  # Emerald; FRLG uses 4-byte attributes (see notes)
    metatile_offset = rom_offset(tileset.metatiles_pointer) + index * 16
    attribute_offset = rom_offset(tileset.attributes_pointer) + index * attr_stride
    if attr_stride == 2:
        attribute = u16(rom, attribute_offset)
    else:
        attribute = u32(rom, attribute_offset)
    return [u16(rom, metatile_offset + 2 * i) for i in range(8)], attribute


# ---------------------------------------------------------------------------
# Full-map composition
# ---------------------------------------------------------------------------

# field_camera.c DrawMetatile(): tile slot -> BG layer index (1=top, 2=middle,
# 3=bottom).  Slots 0-3 are the bottom 2x2; slots 4-7 are the top 2x2.
LAYER_FOR_TYPE = {
    0: {0: 2, 1: 2, 2: 2, 3: 2, 4: 1, 5: 1, 6: 1, 7: 1},  # NORMAL
    1: {0: 3, 1: 3, 2: 3, 3: 3, 4: 2, 5: 2, 6: 2, 7: 2},  # COVERED
    2: {0: 3, 1: 3, 2: 3, 3: 3, 4: 1, 5: 1, 6: 1, 7: 1},  # SPLIT
}
TILE_POS = ((0, 0), (1, 0), (0, 1), (1, 1), (0, 0), (1, 0), (0, 1), (1, 1))


def compose_map(state: GbaState, rom: bytes, layout: Layout):
    palette = decode_palette(state.palette)
    height, width = layout.height, layout.width
    image_h, image_w = height * 16, width * 16
    layers = {1: np.zeros((image_h, image_w, 4), np.uint8),
              2: np.zeros((image_h, image_w, 4), np.uint8),
              3: np.zeros((image_h, image_w, 4), np.uint8)}
    metatiles = (layout.map_blocks & MAPGRID_ID_MASK).reshape(height, width)
    collision = ((layout.map_blocks >> MAPGRID_COLLISION_SHIFT) & 0x03).reshape(height, width)
    elevation = ((layout.map_blocks >> MAPGRID_ELEVATION_SHIFT) & 0x0F).reshape(height, width)
    layer_types = np.zeros((height, width), np.uint8)
    behaviors = np.zeros((height, width), np.uint8)

    for cy in range(height):
        for cx in range(width):
            block = int(layout.map_blocks[cy * width + cx])
            metatile_id = block & MAPGRID_ID_MASK
            tiles, attribute = metatile_entry(rom, layout, metatile_id)
            layer_type = (attribute >> METATILE_ATTR_LAYER_SHIFT) & 0x0F
            layer_types[cy, cx] = layer_type
            behaviors[cy, cx] = attribute & 0xFF
            destination = LAYER_FOR_TYPE.get(layer_type, LAYER_FOR_TYPE[0])
            for slot, entry in enumerate(tiles):
                if entry == 0:
                    continue
                tile_index = entry & 0x03FF
                hflip = bool(entry & 0x0400)
                vflip = bool(entry & 0x0800)
                bank = (entry >> 12) & 0x0F
                tile = decode_4bpp_tile(state.vram, tile_index, palette, bank)
                if hflip:
                    tile = tile[:, ::-1]
                if vflip:
                    tile = tile[::-1, :]
                px, py = TILE_POS[slot]
                x = cx * 16 + px * 8
                y = cy * 16 + py * 8
                layers[destination[slot]][y : y + 8, x : x + 8] = tile

    return layers, metatiles, collision, elevation, layer_types, behaviors


# ---------------------------------------------------------------------------
# OAM sprites (4bpp / 8bpp, 1D mapping)
# ---------------------------------------------------------------------------

OBJ_SIZES = {
    0: {0: (8, 8), 1: (16, 16), 2: (32, 32), 3: (64, 64)},
    1: {0: (16, 8), 1: (32, 8), 2: (32, 16), 3: (64, 32)},
    2: {0: (8, 16), 1: (8, 32), 2: (16, 32), 3: (32, 64)},
}


def decode_oam_sprites(state: GbaState) -> list[tuple[np.ndarray, int, int, int]]:
    palette = decode_palette(state.palette)
    dispcnt = int(state.io[0])
    one_dimensional = bool(dispcnt & 0x40)
    object_base = 0x10000 + (dispcnt & 0x03) * 0x4000
    result = []
    for index in range(128):
        entry = state.oam[index * 8 : index * 8 + 8]
        if entry.size < 8:
            break
        attr0 = u16(entry, 0)
        attr1 = u16(entry, 2)
        attr2 = u16(entry, 4)
        if attr0 & 0x0200:  # hidden affine object
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
        bpp = 8 if attr0 & 0x2000 else 4
        tile = attr2 & 0x3FF
        bank = (attr2 >> 12) & 0x0F
        hflip = bool(attr1 & 0x1000) and not (attr0 & 0x0100)
        vflip = bool(attr1 & 0x2000) and not (attr0 & 0x0100)
        tiles_w, tiles_h = width // 8, height // 8
        sprite = np.zeros((tiles_h * 8, tiles_w * 8, 4), np.uint8)
        for ty in range(tiles_h):
            for tx in range(tiles_w):
                if one_dimensional:
                    tile_index = tile + ty * tiles_w + tx
                else:
                    tile_index = tile + ty * 32 + tx
                if bpp == 4:
                    pixels = decode_4bpp_tile(state.vram[object_base:], tile_index, palette, bank)
                else:
                    pixels = decode_8bpp_tile(state.vram[object_base:], tile_index, palette)
                sprite[ty * 8 : ty * 8 + 8, tx * 8 : tx * 8 + 8] = pixels
        if hflip:
            sprite = sprite[:, ::-1]
        if vflip:
            sprite = sprite[::-1, :]
        if sprite[..., 3].any():
            result.append((sprite, x, y, attr2 >> 10 & 0x03))
    return result


# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    state = parse_state(args.state)
    rom = args.rom.read_bytes()
    backup_offset, backup_w, backup_h, _, backup = find_backup_map_layout(state)
    header_offset, layout = find_map_layout(state, rom, backup, backup_w, backup_h)
    layers, metatiles, collision, elevation, layer_types, behaviors = compose_map(
        state, rom, layout
    )
    for index, image in layers.items():
        Image.fromarray(image, "RGBA").save(args.out / f"bg{index}.png")

    sprites = decode_oam_sprites(state)
    if sprites:
        max_x = max(x + sprite.shape[1] for sprite, x, y, _ in sprites)
        max_y = max(y + sprite.shape[0] for sprite, x, y, _ in sprites)
        canvas = np.zeros((max(160, max_y), max(240, max_x), 4), np.uint8)
        for sprite, x, y, _ in sprites:
            mask = sprite[..., 3] > 0
            canvas[y : y + sprite.shape[0], x : x + sprite.shape[1]][mask] = sprite[mask]
        Image.fromarray(canvas, "RGBA").save(args.out / "sprites.png")

    uniq_mt, counts = np.unique(metatiles, return_counts=True)
    summary = {
        "state": {
            "frame": state.frame,
            "title": state.title,
            "game_code": state.game_code,
        },
        "backup_layout": {
            "iwram_offset": backup_offset,
            "width": backup_w,
            "height": backup_h,
        },
        "map_header": {"ewram_offset": header_offset},
        "layout": {
            "rom_offset": rom_offset(layout.pointer),
            "width": layout.width,
            "height": layout.height,
            "map_pointer": f"0x{layout.map_pointer:08X}",
            "primary_tileset": f"0x{layout.primary_pointer:08X}",
            "secondary_tileset": f"0x{layout.secondary_pointer:08X}",
        },
        "metatiles": {
            "unique": int(len(uniq_mt)),
            "histogram": {int(mt): int(c) for mt, c in zip(uniq_mt, counts)},
        },
        "collision": {int(k): int(v) for k, v in zip(*np.unique(collision, return_counts=True))},
        "elevation": {int(k): int(v) for k, v in zip(*np.unique(elevation, return_counts=True))},
        "layer_types": {int(k): int(v) for k, v in zip(*np.unique(layer_types, return_counts=True))},
        "behaviors": {int(k): int(v) for k, v in zip(*np.unique(behaviors, return_counts=True))},
        "sprites": len(sprites),
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
