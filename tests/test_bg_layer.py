"""BG0 coverage: the "is this frame a screen" signal."""

from __future__ import annotations

import numpy as np

from gen3hd.gba import bg_layer_coverage, decode_palette

PALETTE_BYTES = 1024
VRAM_BYTES = 0x18000


def _palette() -> np.ndarray:
    raw = np.zeros(PALETTE_BYTES, dtype=np.uint8)
    # Bank 0, colour 1 = pure red (BGR555 0x001F).
    raw[2:4] = np.frombuffer(np.uint16(0x001F).tobytes(), dtype=np.uint8)
    return decode_palette(raw)


def _vram_with_tile() -> np.ndarray:
    vram = np.zeros(VRAM_BYTES, dtype=np.uint8)
    # char base 1 -> 0x4000; one tile of index-1 pixels.
    vram[0x4000 : 0x4020] = 0x11
    return vram


def _io(enabled: bool, char_base: int = 1, map_base: int = 0) -> np.ndarray:
    registers = np.zeros(0x100, dtype="<u2")
    registers[0] = 0x0100 if enabled else 0x0000
    registers[4] = (char_base << 2) | (map_base << 8)
    return registers


def test_enabled_bg0_with_a_full_tile_covers_the_screen() -> None:
    coverage = bg_layer_coverage(_vram_with_tile(), _palette(), _io(True), 0)

    assert coverage > 0.95


def test_disabled_or_empty_bg0_covers_nothing() -> None:
    vram = _vram_with_tile()

    assert bg_layer_coverage(vram, _palette(), _io(False), 0) == 0.0
    # Enabled, but the map points at a tile of index 0 (transparent).
    empty = np.zeros(VRAM_BYTES, dtype=np.uint8)
    assert bg_layer_coverage(empty, _palette(), _io(True), 0) < 0.01


def test_scroll_only_samples_the_visible_window() -> None:
    vram = _vram_with_tile()
    io = _io(True)
    io[8] = 128  # BG0HOFS
    io[9] = 64   # BG0VOFS

    assert bg_layer_coverage(vram, _palette(), io, 0) > 0.95
