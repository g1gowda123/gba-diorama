import json

import numpy as np

from gba_diorama.dump import export_synthetic_dump
from gba_diorama.pipeline import render_demo, render_dump
from gba_diorama.synthetic import build_synthetic_world


def test_render_demo_without_post():
    result = render_demo(320, 180, seed=7, post=False)
    assert result.image.shape == (180, 320, 3)
    assert result.image.dtype == np.uint8
    assert result.image.std() > 4.0
    assert len(result.scene.triangles) > 500


def test_render_demo_with_post_is_deterministic():
    a = render_demo(256, 144, seed=7, post=True)
    b = render_demo(256, 144, seed=7, post=True)
    assert np.array_equal(a.image, b.image)


def test_render_dump_from_vram(tmp_path):
    world = build_synthetic_world(7)
    export_synthetic_dump(world, tmp_path, write_layers=False)
    result = render_dump(tmp_path, 256, 144, post=False, all_layers=True)
    assert result.image.shape == (144, 256, 3)
    assert result.image.dtype == np.uint8
    assert result.image.std() > 4.0
    assert result.world.ground_layer == 3
    assert len(result.scene.triangles) > 500


def test_ui_layer_falls_back_to_2d(tmp_path):
    world = build_synthetic_world(7)
    export_synthetic_dump(world, tmp_path, write_layers=False)
    meta = json.loads((tmp_path / "meta.json").read_text())
    width, height = meta["layer_size"]
    (tmp_path / "bg0.rgba").write_bytes(
        np.full((height, width, 4), 255, dtype=np.uint8).tobytes()
    )

    result = render_dump(tmp_path, 256, 144, post=False)
    assert result.fallback
    assert "BG0 UI" in result.reason
    assert result.image.shape == (144, 256, 3)
    assert result.scene is None

    forced = render_dump(tmp_path, 256, 144, post=False, force_3d=True)
    assert not forced.fallback
    assert forced.scene is not None
