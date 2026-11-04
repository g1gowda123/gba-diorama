"""Parallax depth cues from a sequence of dumps (experiment E2).

If layer ``i`` scrolls by ``dx_i`` while the ground layer scrolls by ``dx``,
the ratio ``dx_i / dx`` is a cheap relative-depth cue: 1 is the same plane,
less than 1 is farther away, greater than 1 is closer, and 0 is a static
backdrop.  The estimate is intentionally conservative: it only uses frame
pairs where the reference layer actually moved.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from .dump import GbaDump, layer_scroll, load_dump


@dataclass(frozen=True)
class ParallaxEstimate:
    layer: int
    factor: float | None
    confidence: float
    samples: int
    motion: float


def _sample_ratio(
    layer_pos: tuple[int, int],
    layer_next: tuple[int, int],
    ground_pos: tuple[int, int],
    ground_next: tuple[int, int],
    min_motion: float,
) -> tuple[float, float] | None:
    dx_ground = ground_next[0] - ground_pos[0]
    dy_ground = ground_next[1] - ground_pos[1]
    if abs(dx_ground) >= abs(dy_ground):
        denominator = dx_ground
        numerator = layer_next[0] - layer_pos[0]
    else:
        denominator = dy_ground
        numerator = layer_next[1] - layer_pos[1]
    if abs(denominator) < min_motion:
        return None
    return numerator / denominator, abs(denominator)


def estimate_parallax(
    dumps: Sequence[GbaDump],
    ground_layer: int,
    *,
    layer_indices: Sequence[int] = (0, 1, 2, 3),
    min_motion: float = 1.0,
) -> dict[int, ParallaxEstimate]:
    if len(dumps) < 2:
        raise ValueError("need at least two dumps to estimate parallax")

    positions = {
        index: [layer_scroll(dump, index) for dump in dumps]
        for index in set(layer_indices) | {ground_layer}
    }
    estimates: dict[int, ParallaxEstimate] = {}
    for index in layer_indices:
        ratios: list[float] = []
        weights: list[float] = []
        for frame in range(len(dumps) - 1):
            sample = _sample_ratio(
                positions[index][frame],
                positions[index][frame + 1],
                positions[ground_layer][frame],
                positions[ground_layer][frame + 1],
                min_motion,
            )
            if sample is None:
                continue
            ratio, weight = sample
            ratios.append(ratio)
            weights.append(weight)

        if not ratios:
            estimates[index] = ParallaxEstimate(index, None, 0.0, 0, 0.0)
            continue

        ratio_array = np.asarray(ratios, dtype=np.float64)
        weight_array = np.asarray(weights, dtype=np.float64)
        total_weight = float(weight_array.sum())
        factor = float((ratio_array * weight_array).sum() / total_weight)
        spread = float(
            np.sqrt((weight_array * (ratio_array - factor) ** 2).sum() / total_weight)
        )
        consistency = 1.0 / (1.0 + 2.0 * spread)
        motion_confidence = min(1.0, total_weight / (min_motion * 4.0))
        estimates[index] = ParallaxEstimate(
            layer=index,
            factor=factor,
            confidence=motion_confidence * consistency,
            samples=len(ratios),
            motion=total_weight,
        )
    return estimates


def estimate_parallax_paths(
    roots: Sequence[str | Path],
    ground_layer: int,
    *,
    layer_indices: Sequence[int] = (0, 1, 2, 3),
    min_motion: float = 1.0,
) -> dict[int, ParallaxEstimate]:
    return estimate_parallax(
        [load_dump(root) for root in roots],
        ground_layer,
        layer_indices=layer_indices,
        min_motion=min_motion,
    )


def infer_layer_height(
    estimate: ParallaxEstimate | None,
    default_height: float,
    *,
    min_confidence: float = 0.4,
    scale: float = 20.0,
    min_height: float = 2.0,
    max_height: float = 40.0,
) -> tuple[float, bool]:
    """Infer overlay height from parallax estimate with confidence fallback.

    Returns (height, used_parallax). If confidence is below min_confidence,
    factor is None, or layer has insufficient motion, falls back to default_height.
    """
    if estimate is None or estimate.factor is None or estimate.confidence < min_confidence:
        return default_height, False

    factor = estimate.factor
    # Scrolling faster than ground -> foreground / higher elevation
    if factor > 1.05:
        h = 6.0 + scale * (factor - 1.0)
        return float(np.clip(round(h, 1), min_height, max_height)), True

    # Scrolling slower than ground -> recessed / lower elevation
    if 0.05 < factor < 0.95:
        h = max(-20.0, -scale * (1.0 - factor))
        return float(round(h, 1)), True

    # Near 1.0 -> same plane as ground
    if 0.95 <= factor <= 1.05:
        return 0.0, True

    # Static backdrop (<= 0.05) or unsupported factor
    return default_height, False
