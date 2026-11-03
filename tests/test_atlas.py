from pathlib import Path

import numpy as np
import pytest

from gba_diorama.atlas import stitch_dumps
from gba_diorama.dump import GbaDump


def _dump(color, offset=0, size=4):
    image = np.full((size, size, 4), color, dtype=np.uint8)
    meta = {
        "scale": 1,
        "layer_size": [size, size],
        "bg_hofs": [0, 0, offset, 0],
        "bg_vofs": [0, 0, 0, 0],
    }
    return GbaDump(
        root=Path("."),
        meta=meta,
        vram=None,
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
