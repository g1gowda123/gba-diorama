"""Temporal world atlas: stitch screen-space dumps into larger BG canvases.

A single mGBA dump only contains the visible 240x160 screen.  Tilting the
camera exposes pixels that were off-screen in that frame.  If the same scene is
captured at several scroll positions, each BG layer can be placed at its scroll
offset in a larger canvas.  This is the offline foundation for experiment E1;
the mGBA hook can later emit a short sequence of frames.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from .dump import GbaDump, layer_scroll, load_dump


@dataclass
class AtlasResult:
    dump: GbaDump
    frames: int
    origin: tuple[int, int]
    layer_bounds: dict[int, tuple[int, int, int, int]]
    coverage: dict[int, np.ndarray]

    @property
    def coverage_ratio(self) -> dict[int, float]:
        return {
            index: float(mask.mean()) if mask.size else 0.0
            for index, mask in self.coverage.items()
        }


def _blend_into(canvas: np.ndarray, image: np.ndarray, x: int, y: int) -> None:
    height, width = canvas.shape[:2]
    image_h, image_w = image.shape[:2]
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(width, x + image_w)
    y1 = min(height, y + image_h)
    if x1 <= x0 or y1 <= y0:
        return

    region = canvas[y0:y1, x0:x1]
    source = image[y0 - y : y1 - y, x0 - x : x1 - x]
    alpha = source[..., 3:4].astype(np.float32) / 255.0
    rgb = source[..., :3].astype(np.float32) * alpha + region[..., :3] * (1.0 - alpha)
    region[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    region[..., 3] = np.maximum(region[..., 3], source[..., 3])


def stitch_dumps(
    dumps: Sequence[GbaDump],
    *,
    layer_indices: Sequence[int] = (1, 2, 3),
    atlas_size: tuple[int, int] | None = None,
) -> AtlasResult:
    """Place each layer at its scroll offset and composite overlapping frames."""

    if not dumps:
        raise ValueError("need at least one dump")

    placements: dict[int, list[tuple[int, int, np.ndarray]]] = {}
    for dump in dumps:
        for index in layer_indices:
            image = dump.layers.get(index)
            if image is None:
                continue
            x, y = layer_scroll(dump, index)
            placements.setdefault(index, []).append((x, y, image))

    if not placements:
        raise ValueError("none of the requested layers are present in the dumps")

    min_x = min(x for items in placements.values() for x, _, _ in items)
    min_y = min(y for items in placements.values() for _, y, _ in items)
    max_x = max(x + image.shape[1] for items in placements.values() for x, _, image in items)
    max_y = max(y + image.shape[0] for items in placements.values() for _, y, image in items)

    width = max_x - min_x
    height = max_y - min_y
    if atlas_size is not None:
        width = max(width, int(atlas_size[0]))
        height = max(height, int(atlas_size[1]))

    canvases: dict[int, np.ndarray] = {}
    coverages: dict[int, np.ndarray] = {}
    bounds: dict[int, tuple[int, int, int, int]] = {}
    for index, items in placements.items():
        canvas = np.zeros((height, width, 4), dtype=np.uint8)
        coverage = np.zeros((height, width), dtype=bool)
        for x, y, image in items:
            ox = x - min_x
            oy = y - min_y
            _blend_into(canvas, image, ox, oy)
            image_h, image_w = image.shape[:2]
            x0 = max(0, ox)
            y0 = max(0, oy)
            x1 = min(width, ox + image_w)
            y1 = min(height, oy + image_h)
            if x1 > x0 and y1 > y0:
                coverage[y0:y1, x0:x1] = True
        canvases[index] = canvas
        coverages[index] = coverage
        bounds[index] = (
            min(x for x, _, _ in items) - min_x,
            min(y for _, y, _ in items) - min_y,
            max(x + image.shape[1] for x, _, image in items) - min_x,
            max(y + image.shape[0] for _, y, image in items) - min_y,
        )

    meta = dict(dumps[-1].meta)
    meta["layer_size"] = [width, height]
    meta["atlas"] = {
        "frames": len(dumps),
        "origin": [min_x, min_y],
        "layers": sorted(canvases),
        "coverage": {
            str(index): round(float(mask.mean()), 6)
            for index, mask in coverages.items()
        },
    }
    # A stitched atlas is already in world coordinates; keep the profile
    # signature stable but clear per-frame scroll values.
    if isinstance(meta.get("bg"), list):
        meta["bg"] = [dict(entry, scroll=[0, 0]) for entry in meta["bg"]]
    if "bg_hofs" in meta:
        meta["bg_hofs"] = [0, 0, 0, 0]
    if "bg_vofs" in meta:
        meta["bg_vofs"] = [0, 0, 0, 0]

    atlas_dump = GbaDump(
        root=dumps[-1].root,
        meta=meta,
        vram=None,
        palette=None,
        oam=None,
        layers=canvases,
        obj=None,
        obj_flags=None,
        window=None,
        backdrop=None,
    )
    return AtlasResult(
        dump=atlas_dump,
        frames=len(dumps),
        origin=(min_x, min_y),
        layer_bounds=bounds,
        coverage=coverages,
    )


def stitch_paths(
    roots: Sequence[str | Path],
    *,
    layer_indices: Sequence[int] = (1, 2, 3),
    atlas_size: tuple[int, int] | None = None,
) -> AtlasResult:
    return stitch_dumps(
        [load_dump(root) for root in roots],
        layer_indices=layer_indices,
        atlas_size=atlas_size,
    )
