from pathlib import Path

import numpy as np
import pytest

from gba_diorama.atlas import (
    discover_sequence,
    load_sequence,
    stitch_dumps,
    stitch_paths,
)
from gba_diorama.dump import GbaDump


def _dump(color, offset=0, size=4, vram=None, vram_gen=None):
    image = np.full((size, size, 4), color, dtype=np.uint8)
    meta = {
        "scale": 1,
        "layer_size": [size, size],
        "bg_hofs": [0, 0, offset, 0],
        "bg_vofs": [0, 0, 0, 0],
    }
    if vram_gen is not None:
        meta["vram_generation"] = vram_gen
    return GbaDump(
        root=Path("."),
        meta=meta,
        vram=vram,
        palette=None,
        oam=None,
        layers={2: image},
    )


def test_stitch_places_layers_at_scroll_offsets():
    red = _dump((255, 0, 0, 255), offset=0)
    green = _dump((0, 255, 0, 255), offset=2)

    result = stitch_dumps([red, green], layer_indices=(2,))

    atlas = result.dump.layers[2]
    assert atlas.shape == (4, 6, 4)
    assert result.frames == 2
    assert result.origin == (0, 0)
    assert result.layer_bounds[2] == (0, 0, 6, 4)
    assert result.coverage[2].all()
    assert tuple(atlas[0, 0]) == (255, 0, 0, 255)
    assert tuple(atlas[0, 2]) == (0, 255, 0, 255)
    assert tuple(atlas[0, 5]) == (0, 255, 0, 255)


def test_coverage_mask_records_observed_regions():
    red = _dump((255, 0, 0, 255), offset=0, size=2)
    green = _dump((0, 255, 0, 255), offset=4, size=2)

    result = stitch_dumps([red, green], layer_indices=(2,))

    coverage = result.coverage[2]
    assert coverage.shape == (2, 6)
    assert coverage[:, :2].all()
    assert not coverage[:, 2:4].any()
    assert coverage[:, 4:].all()
    assert result.coverage_ratio[2] == pytest.approx(8 / 12)
    assert result.dump.meta["atlas"]["coverage"]["2"] == pytest.approx(8 / 12)


def test_atlas_size_can_expand_canvas():
    red = _dump((255, 0, 0, 255))
    green = _dump((0, 255, 0, 255), offset=2)

    result = stitch_dumps([red, green], layer_indices=(2,), atlas_size=(8, 6))

    assert result.dump.layers[2].shape == (6, 8, 4)
    assert result.layer_bounds[2] == (0, 0, 6, 4)
    assert result.dump.meta["layer_size"] == [8, 6]
    assert result.dump.meta["atlas"]["frames"] == 2


def test_stitch_requires_a_requested_layer():
    red = _dump((255, 0, 0, 255))

    with pytest.raises(ValueError, match="none of the requested layers"):
        stitch_dumps([red], layer_indices=(1,))


def test_discover_sequence_finds_and_sorts_subdirectories(tmp_path):
    # Create unordered frame subdirectories with meta.json
    for name, frame in [("f2", 120), ("f0", 60), ("f1", 90)]:
        d = tmp_path / name
        d.mkdir()
        (d / "meta.json").write_text(f'{{"frame": {frame}}}')

    found = discover_sequence(tmp_path)
    assert len(found) == 3
    assert [p.name for p in found] == ["f0", "f1", "f2"]


def test_discover_sequence_single_dump(tmp_path):
    (tmp_path / "meta.json").write_text('{"frame": 10}')
    found = discover_sequence(tmp_path)
    assert found == [tmp_path]


def test_vram_invalidation_resets_stale_layer_frames():
    # Frame 0 and 1: red/green at offset 0 and 2 with vram_gen=1
    # Frame 2: blue at offset 4 with vram_gen=2 (VRAM changed!)
    d0 = _dump((255, 0, 0, 255), offset=0, vram_gen=1)
    d1 = _dump((0, 255, 0, 255), offset=2, vram_gen=1)
    d2 = _dump((0, 0, 255, 255), offset=4, vram_gen=2)

    # With invalidation enabled (default):
    result_inv = stitch_dumps([d0, d1, d2], layer_indices=(2,), invalidate_vram=True)
    assert result_inv.invalidations[2] == 1
    atlas = result_inv.dump.layers[2]
    # The canvas only contains d2 (blue), offset 4 (origin at 4 -> shape width 4)
    assert atlas.shape == (4, 4, 4)
    assert tuple(atlas[0, 0]) == (0, 0, 255, 255)

    # Without invalidation: all 3 dumps are stitched into a width-8 canvas
    result_no_inv = stitch_dumps([d0, d1, d2], layer_indices=(2,), invalidate_vram=False)
    assert result_no_inv.invalidations[2] == 0
    assert result_no_inv.dump.layers[2].shape == (4, 8, 4)
    assert tuple(result_no_inv.dump.layers[2][0, 0]) == (255, 0, 0, 255)
    assert tuple(result_no_inv.dump.layers[2][0, 4]) == (0, 0, 255, 255)


def test_stitch_paths_discovers_sequence_automatically(tmp_path):
    from PIL import Image

    for name, offset, color in [("d0", 0, (255, 0, 0, 255)), ("d1", 2, (0, 255, 0, 255))]:
        sub = tmp_path / name
        sub.mkdir()
        (sub / "meta.json").write_text(
            f'{{"scale": 1, "layer_size": [4, 4], "bg_hofs": [0, 0, {offset}, 0], "bg_vofs": [0, 0, 0, 0]}}'
        )
        img = Image.new("RGBA", (4, 4), color)
        img.save(sub / "bg2.rgba")

    result = stitch_paths([tmp_path], layer_indices=(2,))
    assert result.frames == 2
    assert result.dump.layers[2].shape == (4, 6, 4)
