import numpy as np

from gba_diorama.dump import DumpWorld, export_synthetic_dump, load_dump
from gba_diorama.inspect import main as inspect_main
from gba_diorama.synthetic import build_synthetic_world


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
