import numpy as np

from gba_diorama.gba import (
    OBJ_SIZES,
    TextBg,
    decode_oam_entry,
    decode_palette,
    decode_text_bg,
)


def _palette_with(colors: dict[int, tuple[int, int, int]]) -> bytearray:
    pal = bytearray(0x400)
    for index, (r, g, b) in colors.items():
        value = (r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10)
        pal[index * 2 : index * 2 + 2] = value.to_bytes(2, "little")
    return pal


def test_palette_index_zero_is_transparent():
    pal = decode_palette(_palette_with({0: (255, 0, 255), 1: (255, 0, 0)}))
    # Entry 0 is the backdrop color: RGB is preserved, but it is transparent
    # when sampled as a BG/OBJ pixel.
    assert pal[0, 3] == 0
    assert tuple(pal[1]) == (255, 0, 0, 255)


def test_decode_4bpp_tile_and_flips():
    vram = bytearray(0x18000)
    # Tile 1 (32 bytes in) row 0: low nibble = index 1, high nibble = index 2.
    vram[32:36] = bytes([0x21, 0x00, 0x00, 0x00])
    # Screen entry 0 points at tile 1.
    vram[0:2] = (1).to_bytes(2, "little")
    pal = _palette_with({1: (255, 0, 0), 2: (0, 255, 0)})

    img = decode_text_bg(vram, pal, TextBg(char_base=0, screen_base=0, size=0, bpp=4))
    assert img.shape == (256, 256, 4)
    assert tuple(img[0, 0]) == (255, 0, 0, 255)
    assert tuple(img[0, 1]) == (0, 255, 0, 255)
    assert tuple(img[0, 2]) == (0, 0, 0, 0)

    # Horizontal flip: the same entry with bit 10 set.
    vram[0:2] = (1 | 0x0400).to_bytes(2, "little")
    flipped = decode_text_bg(vram, pal, TextBg(char_base=0, screen_base=0, size=0, bpp=4))
    assert tuple(flipped[0, 6]) == (0, 255, 0, 255)
    assert tuple(flipped[0, 7]) == (255, 0, 0, 255)


def test_decode_8bpp_tile():
    vram = bytearray(0x18000)
    # 8bpp tile 1 starts at 64 bytes.  First row is 8 bytes.
    vram[64 : 64 + 8] = bytes([1, 2, 0, 0, 0, 0, 0, 0])
    vram[0:2] = (1).to_bytes(2, "little")
    pal = _palette_with({1: (255, 0, 0), 2: (0, 0, 255)})

    img = decode_text_bg(vram, pal, TextBg(char_base=0, screen_base=0, size=0, bpp=8))
    assert tuple(img[0, 0]) == (255, 0, 0, 255)
    assert tuple(img[0, 1]) == (0, 0, 255, 255)
    assert tuple(img[0, 2]) == (0, 0, 0, 0)


def test_oam_entry_fields_and_disable():
    attr0 = 0x0010 | (0 << 14)
    attr1 = 0x0020 | (1 << 14)
    attr2 = 5 | (3 << 12) | (2 << 10)
    entry = attr0.to_bytes(2, "little") + attr1.to_bytes(2, "little") + attr2.to_bytes(2, "little")
    obj = decode_oam_entry(entry)
    assert obj is not None
    assert (obj.x, obj.y) == (0x20, 0x10)
    assert (obj.width, obj.height) == OBJ_SIZES[0][1]
    assert obj.tile_index == 5
    assert obj.palette_bank == 3
    assert obj.priority == 2

    hidden = (0x0200).to_bytes(2, "little") + b"\0" * 6
    assert decode_oam_entry(hidden) is None


def test_oam_signed_coordinates():
    # x = 0x1FF -> -1, y = 0xFF -> -1.
    entry = (0x00FF).to_bytes(2, "little") + (0x01FF).to_bytes(2, "little") + b"\0" * 4
    obj = decode_oam_entry(entry)
    assert obj is not None
    assert (obj.x, obj.y) == (-1, -1)
