import numpy as np

from gba_diorama.mesh import build_scene
from gba_diorama.pipeline import demo_camera
from gba_diorama.raster import render
from gba_diorama.synthetic import build_synthetic_world


def test_synthetic_world_decodes_three_layers():
    world = build_synthetic_world(seed=7)
    assert set(world.layers) == {1, 2, 3}
    for image in world.layers.values():
        assert image.shape == (512, 512, 4)
        assert image.dtype == np.uint8
        assert image[..., 3].max() == 255
    # Ground is a full opaque sheet; object layers are cutouts.
    assert world.layers[3][..., 3].min() == 255
    assert world.layers[2][..., 3].min() == 0
    assert world.layers[1][..., 3].min() == 0
    assert world.ground_height(34 * 8 + 4, 44 * 8 + 4) >= 0


def test_mesh_has_ground_objects_sprites_and_base():
    world = build_synthetic_world(seed=7)
    camera = demo_camera(320, 180)
    scene = build_scene(world, camera)
    assert len(scene.triangles) > 500
    names = {tri.name for tri in scene.triangles}
    assert any("BG3-ground" in name for name in names)
    assert any("BG2-mid" in name for name in names)
    assert any("BG1-top" in name for name in names)
    assert any(name.startswith("sprite-") for name in names)
    assert "shadow" in names
    assert "base-rim" in names


def test_render_is_deterministic_and_does_not_mutate_scene():
    world = build_synthetic_world(seed=7)
    camera = demo_camera(240, 135)
    scene = build_scene(world, camera)
    before = scene.triangles[0].v0.pos.copy()
    bg = np.zeros((135, 240, 3), dtype=np.uint8)
    first = render(scene.triangles, camera, bg)
    second = render(scene.triangles, camera, bg)
    assert first.shape == (135, 240, 3)
    assert np.array_equal(first, second)
    assert np.array_equal(scene.triangles[0].v0.pos, before)
    assert first.std() > 1.0
