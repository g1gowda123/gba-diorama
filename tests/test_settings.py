"""Guards for the viewer's settings file (Godot side is not under pytest)."""

from __future__ import annotations

import json
from pathlib import Path

SETTINGS = Path(__file__).resolve().parents[1] / "viewer3d" / "settings.json"


def _load() -> dict:
    return json.loads(SETTINGS.read_text())


def test_settings_file_parses() -> None:
    data = _load()

    assert isinstance(data, dict)


def test_settings_cover_every_feature_group() -> None:
    data = _load()

    for group in ("camera", "render", "shadows", "island", "lights", "live"):
        assert group in data, f"missing group {group}"
    assert isinstance(data["time"], str)
    assert isinstance(data["buildings"], str)


def test_switches_are_booleans() -> None:
    data = _load()

    for group in ("render", "shadows", "island", "lights"):
        for key, value in data[group].items():
            if group == "shadows" and key == "strength":
                assert isinstance(value, (int, float))
                continue
            assert isinstance(value, bool), f"{group}.{key} must be a bool"


def test_shadow_switches_exist() -> None:
    shadows = _load()["shadows"]

    # The three shadow paths are independent: the map, the soft contact quads
    # and the baked ground overlay.  A user can turn any one off.
    assert set(shadows) >= {"cast", "contact", "baked", "strength"}
