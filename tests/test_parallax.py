from pathlib import Path

from gba_diorama.dump import GbaDump
from gba_diorama.parallax import estimate_parallax


def _dump(scrolls):
    meta = {
        "scale": 1,
        "bg": [{"scroll": list(scroll)} for scroll in scrolls],
    }
    return GbaDump(
        root=Path("."),
        meta=meta,
        vram=None,
        palette=None,
        oam=None,
        layers={},
    )


def test_parallax_ratios_follow_scroll_speed():
    dumps = [
        _dump([(0, 0), (0, 0), (0, 0), (0, 0)]),
        _dump([(2, 0), (5, 0), (10, 0), (0, 0)]),
        _dump([(4, 0), (10, 0), (20, 0), (0, 0)]),
    ]

    estimates = estimate_parallax(dumps, ground_layer=2)

    assert estimates[0].factor == 0.2
    assert estimates[1].factor == 0.5
    assert estimates[2].factor == 1.0
    assert estimates[3].factor == 0.0
    assert estimates[1].samples == 2
    assert estimates[1].confidence > 0.5
    assert estimates[3].confidence > 0.5


def test_parallax_needs_ground_motion():
    dumps = [
        _dump([(0, 0), (0, 0), (0, 0), (0, 0)]),
        _dump([(0, 0), (0, 0), (0, 0), (0, 0)]),
    ]

    estimates = estimate_parallax(dumps, ground_layer=2)

    assert estimates[1].factor is None
    assert estimates[1].confidence == 0.0
    assert estimates[1].samples == 0


def test_parallax_uses_vertical_motion_when_larger():
    dumps = [
        _dump([(0, 0), (0, 0), (0, 0), (0, 0)]),
        _dump([(0, 3), (0, 6), (0, 12), (0, 0)]),
    ]

    estimates = estimate_parallax(dumps, ground_layer=2)

    assert estimates[1].factor == 0.5
