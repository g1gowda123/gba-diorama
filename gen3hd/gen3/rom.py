"""Gen 3 Pokémon ROM structures.

The important design rule: a romhack may replace every map, tileset and
metatile, but it keeps the engine's structures.  Everything here is parsed
from the ROM the user supplied; there are no vanilla tile IDs or offsets.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

GBA_ROM_BASE = 0x08000000
EWRAM_BASE = 0x02000000
IWRAM_BASE = 0x03000000
MAP_OFFSET = 7
MAPGRID_ID_MASK = 0x03FF
MAPGRID_COLLISION_SHIFT = 10
MAPGRID_ELEVATION_SHIFT = 12


class GameFamily(str, Enum):
    RSE = "rse"      # Ruby / Sapphire / Emerald, and hacks based on them
    FRLG = "frlg"    # FireRed / LeafGreen, and hacks based on them
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RomHeader:
    title: str
    game_code: str
    maker_code: str

    @property
    def family_hint(self) -> GameFamily:
        code = self.game_code.upper()
        if code.startswith(("AXV", "BPE")):
            return GameFamily.RSE
        if code.startswith(("BPR", "BPG")):
            return GameFamily.FRLG
        return GameFamily.UNKNOWN


@dataclass(frozen=True, slots=True)
class Tileset:
    pointer: int
    is_compressed: bool
    is_secondary: bool
    tiles_pointer: int
    palettes_pointer: int
    metatiles_pointer: int
    attributes_pointer: int
    family: GameFamily


@dataclass(frozen=True, slots=True)
class MapLayout:
    pointer: int
    rom_offset: int
    width: int
    height: int
    border_pointer: int
    map_pointer: int
    primary_pointer: int
    secondary_pointer: int
    map_blocks: tuple[int, ...]
    family: GameFamily
    border_width: int | None = None
    border_height: int | None = None


@dataclass(frozen=True, slots=True)
class Metatile:
    metatile_id: int
    tiles: tuple[int, ...]
    attribute: int
    family: GameFamily

    @property
    def layer_type(self) -> int:
        if self.family is GameFamily.FRLG:
            return (self.attribute >> 29) & 0x03
        return (self.attribute >> 12) & 0x0F

    @property
    def behavior(self) -> int:
        if self.family is GameFamily.FRLG:
            return self.attribute & 0x01FF
        return self.attribute & 0x00FF

    @property
    def terrain(self) -> int | None:
        if self.family is GameFamily.FRLG:
            return (self.attribute >> 9) & 0x1F
        return None


class RomError(ValueError):
    """Raised when a pointer or structure does not fit the supplied ROM."""


def _u16(buf: bytes, offset: int) -> int:
    if offset < 0 or offset + 2 > len(buf):
        raise RomError(f"u16 read out of range at {offset:#x}")
    return int.from_bytes(buf[offset : offset + 2], "little")


def _u32(buf: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(buf):
        raise RomError(f"u32 read out of range at {offset:#x}")
    return int.from_bytes(buf[offset : offset + 4], "little")


def rom_offset(pointer: int) -> int | None:
    if GBA_ROM_BASE <= pointer < GBA_ROM_BASE + 0x02000000:
        return pointer - GBA_ROM_BASE
    return None


class Gen3Rom:
    """A read-only view over a Gen 3 ROM image."""

    def __init__(self, data: bytes):
        self.data = data
        self.header = RomHeader(
            title=data[0xA0:0xAC].decode("ascii", "replace").rstrip("\0 "),
            game_code=data[0xAC:0xB0].decode("ascii", "replace"),
            maker_code=data[0xB0:0xB2].decode("ascii", "replace"),
        )

    @classmethod
    def from_path(cls, path: str | Path) -> "Gen3Rom":
        return cls(Path(path).read_bytes())

    # -- structure parsing -------------------------------------------------

    def layout(self, pointer: int, family: GameFamily) -> MapLayout:
        offset = rom_offset(pointer)
        if offset is None or offset + 0x18 > len(self.data):
            raise RomError(f"map layout pointer {pointer:#010x} is not in the ROM")
        width = _u32(self.data, offset)
        height = _u32(self.data, offset + 4)
        if not (1 <= width <= 4096 and 1 <= height <= 4096):
            raise RomError(f"implausible map layout {width}x{height}")
        border = _u32(self.data, offset + 8)
        map_ptr = _u32(self.data, offset + 0x0C)
        primary = _u32(self.data, offset + 0x10)
        secondary = _u32(self.data, offset + 0x14)
        map_offset = rom_offset(map_ptr)
        if map_offset is None or map_offset + width * height * 2 > len(self.data):
            raise RomError(f"map data pointer {map_ptr:#010x} is not in the ROM")
        blocks = tuple(
            _u16(self.data, map_offset + 2 * i) for i in range(width * height)
        )
        border_width = self.data[offset + 0x18] if family is GameFamily.FRLG and offset + 0x1A <= len(self.data) else None
        border_height = self.data[offset + 0x19] if family is GameFamily.FRLG and offset + 0x1A <= len(self.data) else None
        return MapLayout(
            pointer=pointer,
            rom_offset=offset,
            width=width,
            height=height,
            border_pointer=border,
            map_pointer=map_ptr,
            primary_pointer=primary,
            secondary_pointer=secondary,
            map_blocks=blocks,
            family=family,
            border_width=border_width,
            border_height=border_height,
        )

    def tileset(self, pointer: int, family: GameFamily) -> Tileset:
        offset = rom_offset(pointer)
        if offset is None or offset + 0x18 > len(self.data):
            raise RomError(f"tileset pointer {pointer:#010x} is not in the ROM")
        if family is GameFamily.FRLG:
            metatiles = _u32(self.data, offset + 0x0C)
            attributes = _u32(self.data, offset + 0x14)
        else:
            metatiles = _u32(self.data, offset + 0x0C)
            attributes = _u32(self.data, offset + 0x10)
        return Tileset(
            pointer=pointer,
            is_compressed=bool(self.data[offset]),
            is_secondary=bool(self.data[offset + 1]),
            tiles_pointer=_u32(self.data, offset + 4),
            palettes_pointer=_u32(self.data, offset + 8),
            metatiles_pointer=metatiles,
            attributes_pointer=attributes,
            family=family,
        )

    def metatile(self, layout: MapLayout, metatile_id: int, family: GameFamily | None = None) -> Metatile:
        family = family or layout.family
        if metatile_id < 512:
            tileset = self.tileset(layout.primary_pointer, family)
            index = metatile_id
        else:
            tileset = self.tileset(layout.secondary_pointer, family)
            index = metatile_id - 512
        metatiles_offset = rom_offset(tileset.metatiles_pointer)
        attributes_offset = rom_offset(tileset.attributes_pointer)
        if metatiles_offset is None or attributes_offset is None:
            raise RomError(f"metatile {metatile_id} points outside the ROM")
        definition = metatiles_offset + index * 16
        if definition + 16 > len(self.data):
            raise RomError(f"metatile {metatile_id} definition is outside the ROM")
        tiles = tuple(_u16(self.data, definition + 2 * i) for i in range(8))
        if family is GameFamily.FRLG:
            attribute = _u32(self.data, attributes_offset + index * 4)
        else:
            attribute = _u16(self.data, attributes_offset + index * 2)
        return Metatile(metatile_id=metatile_id, tiles=tiles, attribute=attribute, family=family)

    # -- family detection -------------------------------------------------

    def score_family(self, layout_pointer: int, family: GameFamily) -> float:
        """Score how plausible a family interpretation is for this layout.

        A wrong attribute stride almost always produces layer types outside
        0..2 or behaviours outside 0..255, so a sample of the map's unique
        metatiles separates RSE from FRLG cleanly.
        """

        try:
            layout = self.layout(layout_pointer, family)
        except RomError:
            return float("-inf")
        ids = {block & MAPGRID_ID_MASK for block in layout.map_blocks}
        if not ids:
            return float("-inf")
        score = 0.0
        checked = 0
        for metatile_id in list(ids)[:128]:
            try:
                metatile = self.metatile(layout, metatile_id, family)
            except RomError:
                return float("-inf")
            checked += 1
            layer = metatile.layer_type
            if 0 <= layer <= 2:
                score += 2.0
            else:
                score -= 6.0
            behavior = metatile.behavior
            if 0 <= behavior <= 0xFF:
                score += 1.0
            else:
                score -= 3.0
            if family is GameFamily.FRLG and metatile.terrain is not None:
                if 0 <= metatile.terrain <= 0x1F:
                    score += 0.25
        return score / max(checked, 1)

    def detect_family(self, layout_pointer: int) -> GameFamily:
        hint = self.header.family_hint
        scores = {
            GameFamily.RSE: self.score_family(layout_pointer, GameFamily.RSE),
            GameFamily.FRLG: self.score_family(layout_pointer, GameFamily.FRLG),
        }
        best = (
            GameFamily.RSE
            if scores[GameFamily.RSE] >= scores[GameFamily.FRLG]
            else GameFamily.FRLG
        )
        # A structural tie is common on tiny/simple maps; trust the header hint.
        if abs(scores[GameFamily.RSE] - scores[GameFamily.FRLG]) < 0.5 and hint is not GameFamily.UNKNOWN:
            return hint
        return best
