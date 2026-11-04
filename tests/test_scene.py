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


def test_prebuilt_canopy_cards_have_depth_slices():
    from gba_diorama.mesh import _add_prebuilt_cards

    class _World:
        ground_layer = 2
        layers = {2: np.zeros((8, 8, 4), dtype=np.uint8)}

        @staticmethod
        def ground_height(x: float, z: float) -> float:
            return 0.0

    card = np.zeros((20, 12, 4), dtype=np.uint8)
    card[..., 1] = 180
    card[..., 3] = 255
    out = []
    _add_prebuilt_cards(out, _World(), demo_camera(320, 180), [(card, (0, 0, 12, 12))])
    assert len(out) == 6  # three quads, two triangles each
    assert {tri.name.rsplit("-", 1)[-1] for tri in out} == {"0.20", "0.10", "0.00"}


def test_structure_mesh_builds_walls_roof_and_gables():
    from types import SimpleNamespace

    from gba_diorama.mesh import _add_structures
    from gba_diorama.relief import StructureComponent

    roof = np.zeros((20, 40, 4), dtype=np.uint8)
    roof[..., :3] = (60, 90, 180)
    roof[..., 3] = 255
    wall = np.zeros((16, 40, 4), dtype=np.uint8)
    wall[..., :3] = (200, 205, 215)
    wall[..., 3] = 255
    mask = np.ones((52, 56), dtype=bool)
    structure = StructureComponent(
        mask=mask,
        bbox=(16, 16, 72, 68),
        area=int(mask.sum()),
        eave_y=36,
        roof_texture=roof,
        wall_texture=wall,
    )
    world = SimpleNamespace(
        layers={3: np.zeros((64, 64, 4), dtype=np.uint8)},
        ground_layer=3,
        terrain_height=np.zeros((8, 8), dtype=np.float32),
        structures=[structure],
        ground_height=lambda x, z: 0.0,
    )
    triangles = []
    _add_structures(triangles, world, demo_camera(320, 180))
    names = {triangle.name for triangle in triangles}
    assert "structure-0-front" in names
    assert "structure-0-roof-front" in names
    assert "structure-0-roof-back" in names
    assert "structure-0-gable-left" in names
    assert "structure-0-gable-right" in names
    assert len(triangles) >= 9
