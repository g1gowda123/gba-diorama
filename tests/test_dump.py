from pathlib import Path

import numpy as np

from gba_diorama.dump import DumpWorld, GbaDump, export_synthetic_dump, load_dump
from gba_diorama.inspect import main as inspect_main
from gba_diorama.mesh import scene_extent
from gba_diorama.profiles import LayerProfile, SceneProfile
from gba_diorama.synthetic import build_synthetic_world


def _layer(h: int, w: int, seed: int, coverage: float = 1.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    image = np.zeros((h, w, 4), dtype=np.uint8)
    image[..., :3] = rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)
    image[..., 3] = 255
    if coverage < 1.0:
        mask = rng.random((h, w)) >= coverage
        image[mask, 3] = 0
    return image


def _fake_dump(layers: dict[int, np.ndarray], obj=None, oam=None) -> GbaDump:
    return GbaDump(
        root=Path("."),
        meta={"scale": 1},
        vram=None,
        palette=None,
        oam=oam,
        layers=layers,
        obj=obj,
    )


def test_synthetic_dump_roundtrip_from_vram(tmp_path):
    world = build_synthetic_world(7)
    export_synthetic_dump(world, tmp_path, write_layers=False)
    dump = load_dump(tmp_path)
    for index in (1, 2, 3):
        assert np.array_equal(dump.layers[index], world.layers[index])
    assert 0 not in dump.layers  # BG0 is disabled in the synthetic fixture
    assert dump.vram is not None
    assert dump.vram.size == 0x18000


def test_synthetic_dump_roundtrip_from_raw_layers(tmp_path):
    world = build_synthetic_world(7)
    export_synthetic_dump(world, tmp_path, write_layers=True)
    dump = load_dump(tmp_path)
    for index in (1, 2, 3):
        assert np.array_equal(dump.layers[index], world.layers[index])


def test_synthetic_dump_flip_y(tmp_path):
    world = build_synthetic_world(7)
    export_synthetic_dump(world, tmp_path, write_layers=True, flip_y=True)
    dump = load_dump(tmp_path)
    for index in (1, 2, 3):
        assert np.array_equal(dump.layers[index], world.layers[index])


def test_dump_world_adapter(tmp_path):
    world = build_synthetic_world(7)
    export_synthetic_dump(world, tmp_path, write_layers=False)
    dump = load_dump(tmp_path)
    adapted = DumpWorld.from_dump(dump)
    assert adapted.layers[3].shape == (512, 512, 4)
    assert adapted.layer_present[2].shape == (64, 64)
    assert adapted.ground_height(0, 0) == 0.0
    assert adapted.ground_height(9999, 9999) == 0.0


def test_inspect_writes_layer_pngs(tmp_path):
    world = build_synthetic_world(7)
    dump_dir = tmp_path / "dump"
    export_synthetic_dump(world, dump_dir, write_layers=False)
    out = tmp_path / "pngs"
    assert inspect_main([str(dump_dir), "--out", str(out)]) == 0
    for index in (1, 2, 3):
        assert (out / f"bg{index}.png").exists()


def test_ground_selection_prefers_world_over_backdrop():
    # BG3 is a full-screen two-tile checkerboard backdrop; BG2 is the varied
    # playfield.  The real Route 101 frame has exactly this shape.
    h = w = 64
    checker = np.zeros((h, w, 4), dtype=np.uint8)
    for ty in range(h // 8):
        for tx in range(w // 8):
            checker[ty * 8 : (ty + 1) * 8, tx * 8 : (tx + 1) * 8] = (
                (20, 80, 20, 255) if (tx + ty) % 2 else (30, 100, 30, 255)
            )
    world = _layer(h, w, seed=1)
    sparse = _layer(h, w, seed=2, coverage=0.02)
    dump = _fake_dump({1: sparse, 2: world, 3: checker})
    adapted = DumpWorld.from_dump(dump)
    assert adapted.ground_layer == 2
    assert set(adapted.layer_present) == {1}
    assert scene_extent(adapted) == (512.0, 512.0)


def test_sparse_overlay_skip_is_opt_in():
    h, w = 64, 96
    dump = _fake_dump(
        {
            1: _layer(h, w, 2, coverage=0.02),
            2: _layer(h, w, 1),
            3: _layer(h, w, 3, coverage=0.01),
        }
    )
    assert 1 in DumpWorld.from_dump(dump).layer_present
    assert 1 not in DumpWorld.from_dump(dump, min_overlay_coverage=0.05).layer_present
    width, depth = scene_extent(DumpWorld.from_dump(dump))
    assert width == 512.0
    assert abs(depth - 512.0 * 64 / 96) < 1e-4


def test_ground_composites_layers_behind_it():
    h = w = 16
    background = np.full((h, w, 4), (90, 50, 30, 255), dtype=np.uint8)
    ground = np.full((h, w, 4), (40, 150, 70, 255), dtype=np.uint8)
    ground[:8, :8, 3] = 0  # transparent hole, as on Route 101's ledge
    profile = SceneProfile("test", (LayerProfile(2, role="ground"),))

    adapted = DumpWorld.from_dump(
        _fake_dump({2: ground, 3: background}), profile=profile
    )

    assert adapted.ground_layer == 2
    assert tuple(adapted.layers[2][0, 0]) == (90, 50, 30, 255)
    assert tuple(adapted.layers[2][12, 12]) == (40, 150, 70, 255)


def test_obj_sprite_is_cropped_from_oam():
    h, w = 64, 96
    obj = np.zeros((h, w, 4), dtype=np.uint8)
    obj[16:32, 32:48] = (255, 0, 0, 255)
    oam = bytearray(0x400)
    # OAM entry 0: x=32, y=16, square 16x16, tile 0.
    attr0 = 16 | (0 << 14)
    attr1 = 32 | (1 << 14)
    oam[0:2] = attr0.to_bytes(2, "little")
    oam[2:4] = attr1.to_bytes(2, "little")
    adapted = DumpWorld.from_dump(_fake_dump({2: _layer(h, w, 1)}, obj=obj, oam=np.frombuffer(oam, dtype=np.uint8)))
    assert len(adapted.sprites) == 1
    sprite = adapted.sprites[0]
    assert sprite.texture.shape == (16, 16, 4)
    assert sprite.x == (32 + 48) / 2 * (512 / 96)
    assert sprite.z == (16 + 32) / 2 * (512 / 96)


def test_obj_sprite_preserves_top_down_orientation():
    h, w = 32, 32
    obj = np.zeros((h, w, 4), dtype=np.uint8)
    obj[8:16, 8:24] = (255, 0, 0, 255)
    obj[16:24, 8:24] = (0, 0, 255, 255)
    oam = bytearray(0x400)
    # OAM entry 0: x=8, y=8, square 16x16.
    oam[0:2] = (8 | (0 << 14)).to_bytes(2, "little")
    oam[2:4] = (8 | (1 << 14)).to_bytes(2, "little")
    adapted = DumpWorld.from_dump(
        _fake_dump({2: _layer(h, w, 1)}, obj=obj, oam=np.frombuffer(oam, dtype=np.uint8))
    )
    texture = adapted.sprites[0].texture
    assert tuple(texture[0, 0]) == (255, 0, 0, 255)
    assert tuple(texture[-1, 0]) == (0, 0, 255, 255)
