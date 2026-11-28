"""Tests for the live sprite link (no emulator required)."""

from __future__ import annotations

import json

import numpy as np

from gen3hd.live import LiveLink, sprite_positions, write_live


def _entry(y: int, x: int, *, disabled: bool = False, priority: int = 1) -> bytes:
    attr0 = (y & 0xFF) | (0x0200 if disabled else 0)
    attr1 = x & 0x1FF
    attr2 = (priority & 0x03) << 10
    return bytes(
        [
            attr0 & 0xFF,
            (attr0 >> 8) & 0xFF,
            attr1 & 0xFF,
            (attr1 >> 8) & 0xFF,
            attr2 & 0xFF,
            (attr2 >> 8) & 0xFF,
            0,
            0,
        ]
    )


def _oam(entries: list[bytes]) -> bytes:
    data = b"".join(entries)
    return data + bytes(128 * 8 - len(data))


def _io(scroll_x: int, scroll_y: int) -> np.ndarray:
    registers = np.zeros(0x100, dtype="<u2")
    registers[0x14] = scroll_x
    registers[0x15] = scroll_y
    return registers


def test_positions_match_screen_and_world() -> None:
    oam = _oam([_entry(16, 32, priority=2), _entry(120, 120)])
    frame = sprite_positions(oam, _io(64, 8).tobytes(), 320, 320)

    assert frame["scroll"] == [64, 8]
    assert [s["oam"] for s in frame["sprites"]] == [0, 1]
    assert frame["sprites"][0]["screen"] == [32, 16]
    assert frame["sprites"][0]["world"] == [96, 24]
    assert frame["sprites"][0]["priority"] == 2
    assert frame["sprites"][0]["id"] == "oam-000"


def test_disabled_entries_are_skipped_but_slots_are_stable() -> None:
    oam = _oam([_entry(16, 16, disabled=True), _entry(32, 48)])
    frame = sprite_positions(oam, _io(0, 0).tobytes(), 320, 320)

    assert [s["oam"] for s in frame["sprites"]] == [1]
    assert frame["player"] == 1


def test_world_position_wraps_like_the_compiler() -> None:
    oam = _oam([_entry(150, 200)])
    frame = sprite_positions(oam, _io(200, 40).tobytes(), 320, 320)

    # (200 + 200) % 320 = 80, (150 + 40) % 320 = 190.
    assert frame["sprites"][0]["world"] == [80, 190]


def test_allowed_slots_drop_stale_oam_entries() -> None:
    oam = _oam([_entry(16, 16), _entry(32, 48), _entry(40, 60)])
    frame = sprite_positions(oam, _io(0, 0).tobytes(), 320, 320, allowed={0, 2})

    assert [s["oam"] for s in frame["sprites"]] == [0, 2]
    # Slot 2 at (40, 60) is nearer the 120x80 screen centre than slot 0.
    assert frame["player"] == 2


def test_player_is_nearest_the_screen_centre() -> None:
    oam = _oam([_entry(10, 10), _entry(80, 120), _entry(150, 230)])
    frame = sprite_positions(oam, _io(0, 0).tobytes(), 320, 320)

    assert frame["player"] == 1


def test_write_live_is_atomic_and_readable(tmp_path) -> None:
    target = write_live(tmp_path / "live.json", {"frame": 3, "sprites": []})

    assert json.loads(target.read_text())["frame"] == 3
    assert not (tmp_path / "live.tmp").exists()


def test_live_link_publishes_through_a_stub_host(tmp_path) -> None:
    class StubHost:
        frame_count = 0

        def __init__(self) -> None:
            self.steps: list[int] = []
            self.oam = _oam([_entry(40, 60)])
            self.io = _io(16, 24)

        def step(self, frames: int = 1) -> dict:
            self.steps.append(frames)
            self.frame_count += frames
            return {"frameCount": self.frame_count}

        def read_region(self, region: str) -> bytes:
            return self.oam if region == "gba_oam" else self.io.tobytes()

    host = StubHost()
    link = LiveLink(host, tmp_path, 320, 320, interval=0.0, frames=3)
    payload = link.tick()

    assert host.steps == [3]
    assert payload["frame"] == 3
    written = json.loads((tmp_path / "live.json").read_text())
    assert written["sprites"][0]["world"] == [76, 64]


def test_map_change_is_published_once_and_updates_the_link(tmp_path) -> None:
    class StubHost:
        frame_count = 0

        def step(self, frames: int = 1) -> dict:
            self.frame_count += frames
            return {"frameCount": self.frame_count}

        def press(self, button: str, frames: int = 6) -> dict:
            return self.step(frames)

        def read_region(self, region: str) -> bytes:
            return _oam([_entry(40, 60)]) if region == "gba_oam" else _io(0, 0).tobytes()

    keys = iter(["A", "A", "B", "B"])
    rebuilt: list[str] = []

    def recompile(key: str) -> dict:
        rebuilt.append(key)
        return {"allowed": {7}, "width": 480, "height": 320}

    link = LiveLink(
        StubHost(),
        tmp_path,
        320,
        320,
        interval=0.0,
        frames=0,
        map_key=lambda: next(keys),
        on_map_change=recompile,
        check_every=1,
    )

    first = link.tick()
    assert "map_changed" not in first  # the first read only records the key
    assert first["map"] == "A"  # published so a viewer can compare
    second = link.tick()
    assert "map_changed" not in second
    third = link.tick()
    assert third["map_changed"] == "B"
    assert third["map"] == "B"
    assert rebuilt == ["B"]
    assert link.map_px_w == 480 and link.map_px_h == 320
    assert link.allowed == {7}
    # A repeated key must not rebuild again.
    link.tick()
    assert rebuilt == ["B"]


def test_a_failing_map_read_does_not_stop_the_link(tmp_path) -> None:
    class StubHost:
        frame_count = 0

        def step(self, frames: int = 1) -> dict:
            return {"frameCount": 0}

        def press(self, button: str, frames: int = 6) -> dict:
            return self.step(frames)

        def read_region(self, region: str) -> bytes:
            return _oam([_entry(16, 16)]) if region == "gba_oam" else _io(0, 0).tobytes()

    def explode() -> str:
        raise RuntimeError("ewram read failed")

    link = LiveLink(
        StubHost(), tmp_path, 320, 320, interval=0.0, frames=0,
        map_key=explode, check_every=1,
    )
    payload = link.tick()

    assert payload["sprites"][0]["oam"] == 0
