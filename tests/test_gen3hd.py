from __future__ import annotations

import numpy as np
import pytest

from gen3hd.gba import decode_palette
from gen3hd.gen3.locate import find_backup_layout, find_map_layout
from gen3hd.gen3.rom import GBA_ROM_BASE, GameFamily, Gen3Rom
from gen3hd.world import (
    WorldObject,
    annotate_buildings,
    discover_objects,
    repair_ground,
    sanitize_elevation,
)


def _rom_with_header(code: str, size: int = 0x200) -> bytearray:
    rom = bytearray(size)
    rom[0xA0:0xAC] = b"POKEMON TEST"
    rom[0xAC:0xB0] = code.encode("ascii")
    rom[0xB0:0xB2] = b"01"
    return rom


def _layout_rom(code: str, family: GameFamily) -> tuple[bytearray, int]:
    rom = _rom_with_header(code, 0xA000)
    layout = 0x1000
    map_data = 0x2000
    tileset = 0x3000
    metatiles = 0x6000

    # 2x2 map, metatile 0 on every cell, collision 0, elevation 3.
    rom[layout : layout + 4] = (2).to_bytes(4, "little")
    rom[layout + 4 : layout + 8] = (2).to_bytes(4, "little")
    rom[layout + 0x0C : layout + 0x10] = (GBA_ROM_BASE + map_data).to_bytes(4, "little")
    rom[layout + 0x10 : layout + 0x14] = (GBA_ROM_BASE + tileset).to_bytes(4, "little")
    block = 3 << 12
    for index in range(4):
        rom[map_data + index * 2 : map_data + index * 2 + 2] = block.to_bytes(2, "little")

    rom[tileset + 4 : tileset + 8] = (GBA_ROM_BASE + 0x4000).to_bytes(4, "little")
    rom[tileset + 8 : tileset + 0x0C] = (GBA_ROM_BASE + 0x5000).to_bytes(4, "little")
    rom[tileset + 0x0C : tileset + 0x10] = (GBA_ROM_BASE + metatiles).to_bytes(4, "little")
    if family is GameFamily.FRLG:
        rom[tileset + 0x10 : tileset + 0x14] = (GBA_ROM_BASE + 0x7000).to_bytes(4, "little")
        rom[tileset + 0x14 : tileset + 0x18] = (GBA_ROM_BASE + 0x8000).to_bytes(4, "little")
        rom[0x8000:0x8004] = ((1 << 29) | 2).to_bytes(4, "little")
    else:
        rom[tileset + 0x10 : tileset + 0x14] = (GBA_ROM_BASE + 0x7000).to_bytes(4, "little")
        rom[0x7000:0x7002] = (0x1002).to_bytes(2, "little")
    return rom, layout


def test_palette_decode() -> None:
    raw = bytearray(1024)
    raw[0:2] = (0x001F).to_bytes(2, "little")      # index 0: red
    raw[2:4] = (0x03E0).to_bytes(2, "little")      # index 1: green
    palette = decode_palette(raw)
    assert palette.shape == (512, 4)
    assert tuple(palette[1, :3]) == (0, 255, 0)
    assert palette[0, 3] == 0
    assert palette[16, 3] == 0


def test_header_family_hints() -> None:
    assert Gen3Rom(bytes(_rom_with_header("BPEE"))).header.family_hint is GameFamily.RSE
    assert Gen3Rom(bytes(_rom_with_header("AXVE"))).header.family_hint is GameFamily.RSE
    assert Gen3Rom(bytes(_rom_with_header("BPRE"))).header.family_hint is GameFamily.FRLG
    assert Gen3Rom(bytes(_rom_with_header("XXXX"))).header.family_hint is GameFamily.UNKNOWN


def test_find_backup_and_layout_rse_and_frlg() -> None:
    for code, family in (("BPEE", GameFamily.RSE), ("BPRE", GameFamily.FRLG)):
        rom_bytes, layout_ptr = _layout_rom(code, family)
        rom = Gen3Rom(bytes(rom_bytes))

        iwram = np.zeros(0x8000, dtype=np.uint8)
        ewram = np.zeros(0x40000, dtype=np.uint8)
        # Backup layout: 17x16 padded grid -> 2x2 interior.
        offset = 0x100
        iwram[offset : offset + 4] = np.frombuffer((17).to_bytes(4, "little"), dtype=np.uint8)
        iwram[offset + 4 : offset + 8] = np.frombuffer((16).to_bytes(4, "little"), dtype=np.uint8)
        iwram[offset + 8 : offset + 12] = np.frombuffer((0x02000000).to_bytes(4, "little"), dtype=np.uint8)
        blocks = np.full((16, 17), 3 << 12, dtype="<u2")
        ewram[0 : blocks.size * 2] = blocks.view(np.uint8).reshape(-1)
        # gMapHeader.mapLayout pointer, 4-byte aligned in EWRAM.
        ewram[0x80 : 0x84] = np.frombuffer((GBA_ROM_BASE + layout_ptr).to_bytes(4, "little"), dtype=np.uint8)

        backup = find_backup_layout(iwram, ewram)
        assert (backup.width, backup.height) == (17, 16)
        located = find_map_layout(ewram, rom, backup)
        assert (located.layout.width, located.layout.height) == (2, 2)
        assert located.layout.family is family
        assert located.equality == pytest.approx(1.0)


def _object_grid() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    w = h = 6
    composite = np.zeros((h * 16, w * 16, 4), dtype=np.uint8)
    composite[..., :3] = (20, 100, 60)
    composite[..., 3] = 255
    metatiles = np.full((h, w), 13, dtype=np.uint16)
    collision = np.zeros((h, w), dtype=np.uint8)
    # 2x2 tree at (2,2).
    metatiles[2:4, 2:4] = 100
    collision[2:4, 2:4] = 1
    composite[2 * 16 : 4 * 16, 2 * 16 : 4 * 16, :3] = (10, 80, 30)
    elevation = np.full((h, w), 3, dtype=np.uint8)
    behaviors = np.zeros((h, w), dtype=np.uint8)
    return composite, metatiles, collision, elevation, behaviors


def test_discover_tree_and_repair_ground() -> None:
    composite, metatiles, collision, elevation, behaviors = _object_grid()
    objects, clear = discover_objects(composite, metatiles, collision, elevation, behaviors)
    assert len(objects) == 1
    assert objects[0].kind == "tree"
    assert objects[0].bbox == (32, 32, 64, 64)
    assert objects[0].texture[..., 3].all()
    assert clear[2:4, 2:4].all()

    ground = repair_ground(composite, clear, collision, metatiles)
    # The tree footprint is now the same ground colour as the rest of the map.
    assert tuple(ground[2 * 16 + 4, 2 * 16 + 4, :3]) == (20, 100, 60)


def test_three_wide_tree_absorbs_fragment() -> None:
    composite, metatiles, collision, elevation, behaviors = _object_grid()
    # Widen the tree to 3x2; the greedy 2x2 pass covers two columns and the
    # remaining column must be absorbed rather than left as a floating prop.
    metatiles[2:4, 4] = 100
    collision[2:4, 4] = 1
    composite[2 * 16 : 4 * 16, 4 * 16 : 5 * 16, :3] = (10, 80, 30)
    objects, _ = discover_objects(composite, metatiles, collision, elevation, behaviors)
    trees = [obj for obj in objects if obj.kind == "tree"]
    assert len(trees) == 1
    assert trees[0].bbox == (32, 32, 80, 64)


def test_sanitize_elevation_flattens_isolated_spike() -> None:
    elevation = np.full((5, 5), 3, dtype=np.uint8)
    elevation[2, 2] = 15
    collision = np.zeros((5, 5), dtype=np.uint8)
    cleaned = sanitize_elevation(elevation, collision)
    assert int(cleaned[2, 2]) == 3
    assert int(cleaned[0, 0]) == 3


def _building_texture(w: int, h: int, eave: int) -> np.ndarray:
    texture = np.zeros((h, w, 4), dtype=np.uint8)
    texture[..., 3] = 255
    texture[:eave, :, :3] = (190, 100, 60)   # roof band
    texture[eave:, :, :3] = (220, 210, 180)  # wall band
    return texture


def test_annotate_buildings_finds_eave_and_roof_style() -> None:
    small = WorldObject(
        asset_id="house", kind="building", bbox=(0, 0, 96, 64),
        footprint=(0, 0, 96, 64), anchor=(48, 64),
        elevation=3, texture=_building_texture(96, 64, 39),
    )
    large = WorldObject(
        asset_id="lab", kind="building", bbox=(0, 0, 112, 80),
        footprint=(0, 0, 112, 80), anchor=(56, 80),
        elevation=3, texture=_building_texture(112, 80, 32),
    )
    annotate_buildings([small, large])
    assert 37 <= small.eave <= 40
    assert small.roof_style == "gable"
    assert small.depth > 2.0
    assert large.roof_style == "flat"
    assert large.depth > 2.0
