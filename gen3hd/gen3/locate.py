"""Structural location of the live Gen 3 map state.

No fixed EWRAM/IWRAM addresses and no save-block symbol lookup.  The finder
works from the shape of the data:

* IWRAM holds ``{s32 width; s32 height; u16 *map;}`` from ``gBackupMapLayout``.
* EWRAM holds a ROM pointer to the matching ``MapLayout`` (the active
  ``gMapHeader.mapLayout``), whose map data equals the interior of the backup
  grid at the 7-metatile border.

This survives romhacks that move maps, tilesets, and save blocks.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .rom import (
    EWRAM_BASE,
    MAPGRID_ID_MASK,
    MAP_OFFSET,
    GameFamily,
    Gen3Rom,
    MapLayout,
    RomError,
    rom_offset,
)

# Gen 3's map grid is padded by 7 metatiles on the top/left and 7 on the
# bottom/right, plus one extra column (fieldmap.h's MAP_OFFSET).
INTERIOR_X = MAP_OFFSET
INTERIOR_Y = MAP_OFFSET
INTERIOR_WIDTH_DELTA = MAP_OFFSET * 2 + 1
INTERIOR_HEIGHT_DELTA = MAP_OFFSET * 2


@dataclass(frozen=True, slots=True)
class BackupLayout:
    iwram_offset: int
    width: int
    height: int
    map_address: int
    blocks: tuple[int, ...]

    @property
    def interior_width(self) -> int:
        return self.width - INTERIOR_WIDTH_DELTA

    @property
    def interior_height(self) -> int:
        return self.height - INTERIOR_HEIGHT_DELTA

    def interior(self) -> np.ndarray:
        grid = np.asarray(self.blocks, dtype=np.uint16).reshape(self.height, self.width)
        return grid[
            INTERIOR_Y : INTERIOR_Y + self.interior_height,
            INTERIOR_X : INTERIOR_X + self.interior_width,
        ]


@dataclass(frozen=True, slots=True)
class LocatedMap:
    header_ewram_offset: int
    layout: MapLayout
    backup: BackupLayout
    equality: float


class LocateError(RuntimeError):
    """Raised when live RAM does not contain a recognisable Gen 3 map."""


def _u32(buf: np.ndarray, offset: int) -> int:
    return int(np.frombuffer(buf[offset : offset + 4].tobytes(), dtype="<u4")[0])


def _plausible_cell(block: int) -> bool:
    return (block & MAPGRID_ID_MASK) < 1024


def find_backup_layout(iwram: np.ndarray, ewram: np.ndarray) -> BackupLayout:
    """Find the active ``gBackupMapLayout`` in IWRAM."""

    best: tuple[float, int, int, int, int, tuple[int, ...]] | None = None
    limit = min(len(iwram) - 12, 0x8000)
    for offset in range(0, limit, 4):
        width = _u32(iwram, offset)
        height = _u32(iwram, offset + 4)
        pointer = _u32(iwram, offset + 8)
        if not (8 <= width <= 120 and 8 <= height <= 120):
            continue
        if not (EWRAM_BASE <= pointer < EWRAM_BASE + len(ewram)):
            continue
        count = width * height
        if count < 64 or count > 10240:
            continue
        start = pointer - EWRAM_BASE
        if start + count * 2 > len(ewram):
            continue
        blocks = tuple(
            int(np.frombuffer(ewram[start + 2 * i : start + 2 * i + 2].tobytes(), dtype="<u2")[0])
            for i in range(count)
        )
        valid = sum(1 for block in blocks if _plausible_cell(block))
        ratio = valid / count
        if ratio >= 0.98 and (best is None or ratio > best[0]):
            best = (ratio, offset, width, height, pointer, blocks)
    if best is None:
        raise LocateError("no gBackupMapLayout-shaped struct found in IWRAM")
    _, offset, width, height, pointer, blocks = best
    return BackupLayout(
        iwram_offset=offset,
        width=width,
        height=height,
        map_address=pointer,
        blocks=blocks,
    )


def find_map_layout(ewram: np.ndarray, rom: Gen3Rom, backup: BackupLayout) -> LocatedMap:
    """Find the active ``MapLayout`` by matching its ROM map data to live RAM."""

    if backup.interior_width <= 0 or backup.interior_height <= 0:
        raise LocateError("backup map is too small to contain an interior")
    interior = backup.interior().reshape(-1)
    candidates: list[tuple[float, int, MapLayout]] = []
    seen: set[int] = set()

    for offset in range(0, len(ewram) - 4, 4):
        pointer = _u32(ewram, offset)
        if pointer in seen:
            continue
        seen.add(pointer)
        if rom_offset(pointer) is None:
            continue
        family = rom.detect_family(pointer)
        try:
            layout = rom.layout(pointer, family)
        except RomError:
            continue
        if (layout.width, layout.height) != (backup.interior_width, backup.interior_height):
            continue
        candidate = np.asarray(layout.map_blocks, dtype=np.uint16)
        equality = float((candidate == interior).mean())
        if equality >= 0.98:
            candidates.append((equality, offset, layout))

    if not candidates:
        raise LocateError("no ROM MapLayout matches the live backup grid")
    candidates.sort(key=lambda item: item[0], reverse=True)
    equality, header_offset, layout = candidates[0]
    return LocatedMap(
        header_ewram_offset=header_offset,
        layout=layout,
        backup=backup,
        equality=equality,
    )


def locate_map(iwram: np.ndarray, ewram: np.ndarray, rom: Gen3Rom) -> LocatedMap:
    """Convenience wrapper: find the backup grid and then the ROM layout."""

    backup = find_backup_layout(iwram, ewram)
    return find_map_layout(ewram, rom, backup)
