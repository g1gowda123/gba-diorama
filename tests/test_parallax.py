from pathlib import Path

from gba_diorama.dump import GbaDump
from gba_diorama.parallax import (
    ParallaxEstimate,
    estimate_parallax,
    infer_layer_height,
)
from gba_diorama.profiles import (
    LayerProfile,
    SceneProfile,
    SceneSignature,
    apply_parallax_to_profile,
)


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


def test_infer_layer_height_with_confidence_and_fallback():
    # High confidence foreground layer: factor=1.5
    est_fg = ParallaxEstimate(layer=1, factor=1.5, confidence=0.8, samples=5, motion=20.0)
    h_fg, used = infer_layer_height(est_fg, default_height=18.0)
    assert used is True
    assert h_fg == 6.0 + 20.0 * 0.5  # 16.0

    # Low confidence layer: fallback to default
    est_low = ParallaxEstimate(layer=1, factor=1.5, confidence=0.2, samples=1, motion=1.0)
    h_low, used = infer_layer_height(est_low, default_height=18.0)
    assert used is False
    assert h_low == 18.0

    # Near 1.0 -> ground plane
    est_ground = ParallaxEstimate(layer=2, factor=1.0, confidence=0.9, samples=5, motion=20.0)
    h_g, used = infer_layer_height(est_ground, default_height=0.0)
    assert used is True
    assert h_g == 0.0


def test_apply_parallax_to_profile_infers_heights_and_backdrops():
    estimates = {
        0: ParallaxEstimate(layer=0, factor=None, confidence=0.0, samples=0, motion=0.0),
        1: ParallaxEstimate(layer=1, factor=1.5, confidence=0.8, samples=4, motion=16.0),
        2: ParallaxEstimate(layer=2, factor=1.0, confidence=0.9, samples=4, motion=16.0),
        3: ParallaxEstimate(layer=3, factor=0.0, confidence=0.9, samples=4, motion=16.0),
    }

    # 1. Synthesize from scratch (profile=None)
    sig = SceneSignature(bg_mode=0, enabled_mask=0x0F)
    inferred = apply_parallax_to_profile(None, estimates, ground_layer=2, signature=sig)
    assert inferred.name == "parallax-inferred"
    layer_map = {l.bg: l for l in inferred.layers}
    assert layer_map[2].role == "ground"
    assert layer_map[2].height == 0.0
    assert layer_map[1].height == 16.0  # 6.0 + 20.0 * 0.5
    assert layer_map[3].role == "ignore"  # static backdrop behind ground

    # 2. Update existing profile: high confidence updates, low confidence keeps default
    orig_profile = SceneProfile(
        name="test",
        layers=(
            LayerProfile(bg=2, role="ground", height=0.0),
            LayerProfile(bg=1, role="world", height=24.0),
            LayerProfile(bg=0, role="world", height=30.0),
        ),
    )
    updated = apply_parallax_to_profile(orig_profile, estimates, ground_layer=2)
    updated_map = {l.bg: l for l in updated.layers}
    assert updated_map[1].height == 16.0  # updated by parallax
    assert updated_map[0].height == 30.0  # preserved default because confidence was 0.0
