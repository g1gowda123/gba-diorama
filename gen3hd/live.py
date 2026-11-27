"""Live link: stream the emulator's sprite table into a compiled world.

A compiled world (terrain, objects, house meshes) is static for one map, so the
only state that changes every frame is the camera scroll and the OAM sprite
table.  This module rewrites a small ``live.json`` beside ``world.json`` at a
fixed rate; the Godot viewer polls it and moves the sprite billboards it
already has.  Keeping the link to two OAM/IO reads avoids re-reading VRAM and
re-cropping every sprite, which is what makes a usable update rate.

The live link never invents state: if a sprite leaves OAM it disappears from
``live.json`` and the viewer stops moving it, and sprite textures stay those of
the captured frame until the renderer can take a tile stream.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Protocol

import numpy as np

OAM_ENTRIES = 128
SCREEN_W = 240
SCREEN_H = 160


class SpriteSource(Protocol):
    """The two host calls the link needs; a fake is enough to test it."""

    frame_count: int

    def step(self, frames: int = 1) -> dict: ...

    def press(self, button: str, frames: int = 6) -> dict: ...

    def read_region(self, region: str) -> bytes: ...


def sprite_positions(
    oam: bytes | np.ndarray,
    io: bytes | np.ndarray,
    map_px_w: int,
    map_px_h: int,
    allowed: set[int] | None = None,
) -> dict:
    """Return OAM sprite positions in screen and world pixels.

    Mirrors ``gba.decode_oam`` for the fields that decide placement, so the
    live positions line up with the sprites in ``world.json`` without decoding
    tiles.  ``io`` is the 16-bit register view (DISPCNT is ``io[0]``; the BG2
    scroll registers are ``io[0x14]`` / ``io[0x15]``).

    ``allowed`` is the set of OAM slots the compiled world actually contains.
    Stale OAM entries left over from a previous frame are common and only a
    tile decode can prove them empty, so the link publishes the slots it can
    place instead of guessing.
    """

    registers = np.frombuffer(bytes(io), dtype="<u2")
    scroll_x = int(registers[0x14]) & 0x1FF if registers.size > 0x14 else 0
    scroll_y = int(registers[0x15]) & 0x1FF if registers.size > 0x15 else 0
    entries = np.frombuffer(bytes(oam[: OAM_ENTRIES * 8]), dtype=np.uint8)

    sprites: list[dict] = []
    for slot in range(OAM_ENTRIES):
        entry = entries[slot * 8 : slot * 8 + 8]
        if entry.size < 8:
            break
        attr0 = int(entry[0]) | (int(entry[1]) << 8)
        attr1 = int(entry[2]) | (int(entry[3]) << 8)
        attr2 = int(entry[4]) | (int(entry[5]) << 8)

        affine = bool(attr0 & 0x0100)
        if not affine and (attr0 & 0x0200):  # disabled
            continue
        if ((attr0 >> 14) & 0x03) == 3:  # prohibited shape
            continue
        # Object VRAM slot 0 is the transparent tile, and an all-zero entry is
        # the usual empty slot.  Checking tiles would mean decoding them every
        # tick; this rule keeps the payload to real candidates and the viewer
        # ignores any slot the compiled world does not know.
        if attr0 == 0 and attr1 == 0 and attr2 == 0:
            continue
        if allowed is not None and slot not in allowed:
            continue

        y = attr0 & 0xFF
        if y >= 0xA0:
            y -= 0x100
        x = attr1 & 0x1FF
        if x >= 0x100:
            x -= 0x200

        sprites.append(
            {
                "id": f"oam-{slot:03d}",
                "oam": slot,
                "screen": [x, y],
                "world": [
                    int((x + scroll_x) % max(map_px_w, 1)),
                    int((y + scroll_y) % max(map_px_h, 1)),
                ],
                "priority": (attr2 >> 10) & 0x03,
            }
        )

    # The player is the sprite nearest the screen centre, as in build_sprites.
    player = None
    if sprites:
        player = min(
            sprites,
            key=lambda s: (s["screen"][0] - SCREEN_W / 2) ** 2
            + (s["screen"][1] - SCREEN_H / 2) ** 2,
        )["oam"]

    return {"scroll": [scroll_x, scroll_y], "player": player, "sprites": sprites}


def write_live(path: str | Path, payload: dict) -> Path:
    """Write the payload atomically so the viewer never reads a partial file."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    os.replace(temporary, target)
    return target


class LiveLink:
    """Poll a running host and publish ``live.json`` into a world directory."""

    def __init__(
        self,
        host: SpriteSource,
        world_dir: str | Path,
        map_px_w: int,
        map_px_h: int,
        interval: float = 0.1,
        frames: int = 6,
        walk: str = "",
        allowed: set[int] | None = None,
    ) -> None:
        self.host: SpriteSource = host
        self.world_dir = Path(world_dir)
        self.map_px_w = int(map_px_w)
        self.map_px_h = int(map_px_h)
        self.interval = max(float(interval), 0.0)
        self.frames = max(int(frames), 0)
        self.walk = walk
        self.allowed = allowed
        self.ticks = 0

    def tick(self, advance: bool = True) -> dict:
        if advance and self.frames:
            if self.walk:
                # Holding a direction is what produces motion to look at; the
                # link still owns the frame budget, so the emulator advances.
                self.host.press(self.walk, self.frames)
            else:
                self.host.step(self.frames)
        oam = self.host.read_region("gba_oam")
        io = self.host.read_region("gba_io_regs")
        payload = sprite_positions(
            oam, io, self.map_px_w, self.map_px_h, self.allowed
        )
        payload["frame"] = self.host.frame_count
        payload["tick"] = self.ticks
        self.ticks += 1
        write_live(self.world_dir / "live.json", payload)
        return payload

    def run(self, seconds: float = 0.0) -> int:
        """Publish until ``seconds`` elapse (0 = until interrupted)."""

        deadline = time.time() + seconds if seconds > 0 else None
        while deadline is None or time.time() < deadline:
            started = time.perf_counter()
            self.tick()
            spent = time.perf_counter() - started
            if self.interval > spent:
                time.sleep(self.interval - spent)
        return self.ticks
