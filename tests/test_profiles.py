from pathlib import Path

import numpy as np

from gba_diorama.dump import DumpWorld, export_synthetic_dump, load_dump
from gba_diorama.pipeline import dump_camera
from gba_diorama.profiles import (
    CameraProfile,
    LayerProfile,
    ProfileBook,
    ProfileMatch,
    SceneProfile,
    SceneSignature,
)
from gba_diorama.synthetic import build_synthetic_world


def test_signature_from_real_meta():
    meta = {
        "dispcnt": 0x7F60,
        "mode": 0,
        "bg": [
            {"priority": 0, "size": 0, "affine": False, "enabled": True},
            {"priority": 1, "size": 0, "affine": False, "enabled": True},
            {"priority": 2, "size": 0, "affine": False, "enabled": True},
            {"priority": 3, "size": 0, "affine": False, "enabled": True},
        ],
    }
    signature = SceneSignature.from_meta(meta)
    assert signature == SceneSignature(
        bg_mode=0,
        enabled_mask=0x0F,
        priorities=(0, 1, 2, 3),
        affine_mask=0,
        screen_sizes=(0, 0, 0, 0),
    )
    assert "mode=0" in signature.label()


def test_signature_from_bgcnt_fallback():
    meta = {
        "dispcnt": "0x1E00",
        "bgcnt": ["0x0000", "0x4001", "0x8002", "0xC003"],
    }
    signature = SceneSignature.from_meta(meta)
    assert signature.enabled_mask == 0x0E
    assert signature.priorities == (0, 1, 2, 3)
    assert signature.affine_mask == 0
    assert signature.screen_sizes == (0, 1, 2, 3)


def test_toml_profile_matches_and_prefers_specific():
    text = """
[[profiles]]
name = "generic"
[profiles.match]
mode = 0

[[profiles.layers]]
bg = 3
role = "ground"

[[profiles]]
name = "specific"
game_code = "BPEE"
[profiles.match]
mode = 0
enabled = 15
priorities = [0, 1, 2, 3]

[[profiles.layers]]
bg = 2
role = "ground"
"""
    book = ProfileBook.from_text(text)
    signature = SceneSignature(0, 0x0F, (0, 1, 2, 3), 0, (0, 0, 0, 0))
    assert book.match(signature).name == "generic"
    assert book.match(signature, "BPEE").name == "specific"
    assert book.match(signature, "BPGE").name == "generic"


def test_nested_profile_format():
    text = """
[profile.demo.overworld]
match = { mode = 0, enabled = 15, priorities = [0, 0, 0, 0] }
layers = [
  { bg = 3, role = "ground" },
  { bg = 1, role = "world", height = 7.0 },
]
"""
    book = ProfileBook.from_text(text)
    profile = book.match(SceneSignature(0, 0x0F, (0, 0, 0, 0)))
    assert profile is not None
    assert profile.name == "demo.overworld"
    assert profile.ground_layer == 3
    assert profile.layers[1].height == 7.0


def test_shipped_emerald_profile_matches_route101():
    root = Path(__file__).resolve().parents[1]
    book = ProfileBook.from_path(root / "profiles" / "emerald-route101.toml")
    signature = SceneSignature.from_meta(
        {
            "dispcnt": 0x7F60,
            "mode": 0,
            "bg": [
                {"priority": 0, "size": 0, "affine": False},
                {"priority": 1, "size": 0, "affine": False},
                {"priority": 2, "size": 0, "affine": False},
                {"priority": 3, "size": 0, "affine": False},
            ],
        }
    )
    profile = book.match(signature)
    assert profile is not None
    assert profile.name == "emerald-route101"
    assert profile.ground_layer == 2


def test_profile_overrides_ground_and_heights(tmp_path):
    world = build_synthetic_world(7)
    export_synthetic_dump(world, tmp_path, write_layers=False)
    dump = load_dump(tmp_path)
    profile = SceneProfile(
        name="test-ground",
        layers=(
            LayerProfile(2, "ground", side_material="dirt"),
            LayerProfile(1, "world", height=9.0, side_material="grass"),
        ),
        match=ProfileMatch(mode=0),
    )
    dumped = DumpWorld.from_dump(dump, profile=profile)

    assert dumped.profile is profile
    assert dumped.ground_layer == 2
    assert set(dumped.layer_present) == {1}
    assert np.all(dumped.layer_height[1] == 9.0)
    assert dumped.layer_side_material[1][0, 0] == "grass"
    assert np.all(dumped.ground_side_material == "dirt")


def test_profile_with_missing_ground_is_rejected(tmp_path):
    world = build_synthetic_world(7)
    export_synthetic_dump(world, tmp_path, write_layers=False)
    dump = load_dump(tmp_path)
    profile = SceneProfile(
        name="missing-ground",
        layers=(LayerProfile(1, "world"),),
        match=ProfileMatch(),
    )
    try:
        DumpWorld.from_dump(dump, profile=profile)
    except ValueError as exc:
        assert "has no ground layer" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected a ValueError")


def test_profile_camera_overrides_distance_pitch_and_yaw():
    world = build_synthetic_world(7)
    profile = SceneProfile(
        name="camera",
        layers=(LayerProfile(3, "ground"),),
        camera=CameraProfile(pitch_deg=45.0, yaw_deg=90.0, distance=100.0),
    )
    camera = dump_camera(world, 640, 360, profile=profile)
    center = np.array([256.0, 0.0, 256.0], dtype=np.float32)
    offset = camera.eye - center
    assert abs(float(offset[1]) - 100.0) < 1e-3
    assert abs(float(np.hypot(offset[0], offset[2])) - 100.0) < 1e-3
    assert float(offset[0]) > 99.0
