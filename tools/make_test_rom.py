#!/usr/bin/env python3
"""Build a tiny, self-contained GBA test ROM for the layer dump hook.

It draws one red 8x8 tile at the top-left of BG0 and leaves the rest of the
screen transparent/backdrop. The ROM is generated from ARM7TDMI machine code
here, so the repository contains no copyrighted ROM or boot logo.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path


class Asm:
    def __init__(self) -> None:
        self.words: list[int] = []
        self.labels: dict[str, int] = {}

    def label(self, name: str) -> None:
        self.labels[name] = len(self.words) * 4

    def emit(self, word: int) -> None:
        self.words.append(word & 0xFFFFFFFF)

    def mov(self, rd: int, imm8: int, rot: int = 0) -> None:
        self.emit(0xE3A00000 | (rd << 12) | ((rot & 0xF) << 8) | (imm8 & 0xFF))

    def add(self, rd: int, rn: int, imm12: int) -> None:
        self.emit(0xE2800000 | (rn << 16) | (rd << 12) | (imm12 & 0xFFF))

    def orr_lsl(self, rd: int, rn: int, rm: int, shift: int) -> None:
        self.emit(0xE1800000 | (rn << 16) | (rd << 12) | ((shift & 0x1F) << 7) | rm)

    def strh(self, rd: int, rn: int, offset: int = 0) -> None:
        self.emit(0xE1C000B0 | (rn << 16) | (rd << 12) |
                  (((offset >> 4) & 0xF) << 8) | (offset & 0xF))

    def strh_post(self, rd: int, rn: int, offset: int) -> None:
        self.emit(0xE0C000B0 | (rn << 16) | (rd << 12) |
                  (((offset >> 4) & 0xF) << 8) | (offset & 0xF))

    def str_post(self, rd: int, rn: int, offset: int) -> None:
        self.emit(0xE4800000 | (rn << 16) | (rd << 12) | (offset & 0xFFF))

    def subs(self, rd: int, rn: int, imm12: int) -> None:
        self.emit(0xE2500000 | (rn << 16) | (rd << 12) | (imm12 & 0xFFF))

    def b(self, label: str, cond: int = 0xE) -> None:
        index = len(self.words)
        self.emit(0)
        target = self.labels[label]
        here = index * 4 + 8
        offset = (target - here) // 4
        self.words[index] = (cond << 28) | 0x0A000000 | (offset & 0xFFFFFF)

    def b_self(self) -> None:
        self.emit(0xEAFFFFFE)


def build_rom() -> bytes:
    a = Asm()

    # Display control: mode 0, BG0 on.
    a.mov(0, 0x04, 4)       # r0 = 0x04000000
    a.mov(1, 0x01, 12)      # r1 = 0x00000100
    a.strh(1, 0, 0x00)      # DISPCNT
    a.strh(1, 0, 0x08)      # BG0CNT: char base 0, screen base 1, 4bpp
    a.mov(1, 0)             # r1 = 0
    a.strh(1, 0, 0x10)      # BG0HOFS
    a.strh(1, 0, 0x12)      # BG0VOFS

    # Palette entry 1 = red (BGR555).
    a.mov(2, 0x05, 4)       # r2 = 0x05000000
    a.mov(3, 0x1F)
    a.strh(3, 2, 0x02)

    # Tile 0: 8x8 4bpp, all pixels index 1.
    a.mov(4, 0x06, 4)       # r4 = 0x06000000
    a.mov(5, 0x11)
    a.orr_lsl(5, 5, 5, 8)
    a.orr_lsl(5, 5, 5, 16)
    for _ in range(8):
        a.str_post(5, 4, 4)

    # Tile 1: transparent.
    a.mov(4, 0x06, 4)
    a.add(4, 4, 0x20)
    a.mov(5, 0)
    for _ in range(8):
        a.str_post(5, 4, 4)

    # Fill the 32x32 BG0 map with tile 1, then put tile 0 at the origin.
    a.mov(4, 0x06, 4)
    a.mov(7, 2)
    a.orr_lsl(4, 4, 7, 10)   # r4 += 0x800 (screen base 1)
    a.mov(5, 1)
    a.mov(6, 1, 11)         # r6 = 1024
    a.label("map")
    a.strh_post(5, 4, 2)
    a.subs(6, 6, 1)
    a.b("map", cond=0x1)    # BNE

    a.mov(4, 0x06, 4)
    a.mov(7, 2)
    a.orr_lsl(4, 4, 7, 10)   # r4 += 0x800 (screen base 1)
    a.mov(5, 0)
    a.strh(5, 4, 0)
    a.b_self()

    code = b"".join(struct.pack("<I", word) for word in a.words)
    header = bytearray(0xC0)
    struct.pack_into("<I", header, 0x00, 0xEA00002E)  # branch to 0x080000C0
    title = b"DIORAMA TEST"
    header[0xA0:0xA0 + len(title)] = title
    header[0xAC:0xB0] = b"DIO1"
    header[0xB0:0xB2] = b"00"
    header[0xB2] = 0x96
    header[0xB3] = 0
    # Complement check over 0xA0..0xBC; mGBA does not require it, but make it valid.
    checksum = (-(0x19 + sum(header[0xA0:0xBD]))) & 0xFF
    header[0xBD] = checksum
    return bytes(header) + code


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "diorama-test.gba")
    out.write_bytes(build_rom())
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
