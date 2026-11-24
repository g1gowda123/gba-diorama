"""A point-in-time GBA machine snapshot."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class GbaSnapshot:
    vram: np.ndarray          # 0x18000 bytes
    palette: np.ndarray       # 0x400 bytes
    oam: np.ndarray           # 0x400 bytes
    io: np.ndarray            # u16 view of 0x400 bytes
    iwram: np.ndarray         # 0x8000 bytes
    ewram: np.ndarray         # 0x40000 bytes
    frame: int = 0
    title: str = ""
    game_code: str = ""

    def __post_init__(self) -> None:
        self.vram = np.asarray(self.vram, dtype=np.uint8)
        self.palette = np.asarray(self.palette, dtype=np.uint8)
        self.oam = np.asarray(self.oam, dtype=np.uint8)
        self.io = np.asarray(self.io, dtype=np.uint16)
        self.iwram = np.asarray(self.iwram, dtype=np.uint8)
        self.ewram = np.asarray(self.ewram, dtype=np.uint8)

    @property
    def dispcnt(self) -> int:
        return int(self.io[0])
