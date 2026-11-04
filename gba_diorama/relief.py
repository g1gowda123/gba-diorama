"""Cheap semantic relief for real BG layers.

The GBA does not give us a depth buffer, but it does give us layers with
distinct art.  For a top-down scene, tree canopies are usually the dark,
saturated green blobs in the world layer.  Detecting those blobs lets the
mesher lift them above the ground without hardcoding Pokémon tile coordinates.

This is deliberately a prototype: the thresholds are data, the connected
components are generic, and a profile can replace the detector later.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CanopyComponent:
    mask: np.ndarray
    bbox: tuple[int, int, int, int]
    area: int


@dataclass
class CanopyField:
    mask: np.ndarray
    present: np.ndarray
    heights: np.ndarray
    texture: np.ndarray
    ground: np.ndarray
    components: list[CanopyComponent]


def _rgb_to_hsv(image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rgb = image[..., :3].astype(np.float32) / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    maximum = rgb.max(axis=2)
    minimum = rgb.min(axis=2)
    delta = maximum - minimum

    hue = np.zeros_like(maximum)
    nonzero = delta > 1e-6
    red = nonzero & (maximum == r)
    green = nonzero & (maximum == g)
    blue = nonzero & (maximum == b)
    hue[red] = ((g[red] - b[red]) / delta[red]) % 6.0
    hue[green] = (b[green] - r[green]) / delta[green] + 2.0
    hue[blue] = (r[blue] - g[blue]) / delta[blue] + 4.0
    hue *= 60.0

    saturation = np.zeros_like(maximum)
    np.divide(delta, maximum, out=saturation, where=maximum > 1e-6)
    return hue, saturation, maximum


def _morph(mask: np.ndarray, *, erode: bool, iterations: int) -> np.ndarray:
    """3x3 morphology without SciPy, used to split touching tree canopies."""

    out = mask
    for _ in range(iterations):
        padded = np.pad(out, 1, mode="constant", constant_values=False)
        neighbours = [
            padded[1:-1, 1:-1],
            padded[:-2, 1:-1],
            padded[2:, 1:-1],
            padded[1:-1, :-2],
            padded[1:-1, 2:],
            padded[:-2, :-2],
            padded[:-2, 2:],
            padded[2:, :-2],
            padded[2:, 2:],
        ]
        out = np.logical_and.reduce(neighbours) if erode else np.logical_or.reduce(neighbours)
    return out


def detect_canopies(
    image: np.ndarray,
    *,
    min_area: int = 48,
    hue_min: float = 65.0,
    hue_max: float = 135.0,
    saturation_min: float = 0.30,
    value_max: float = 0.92,
    open_iterations: int = 2,
) -> list[CanopyComponent]:
    """Find saturated green blobs that look like tree/hedge canopies."""

    if image.ndim != 3 or image.shape[2] < 3:
        return []
    hue, saturation, value = _rgb_to_hsv(image)
    mask = (
        (hue >= hue_min)
        & (hue <= hue_max)
        & (saturation >= saturation_min)
        & (value <= value_max)
    )
    if not mask.any():
        return []
    if open_iterations:
        # Tree canopies often touch through one-pixel dark outlines.  Opening
        # breaks those bridges so each tree becomes its own volume.
        mask = _morph(mask, erode=True, iterations=open_iterations)
        mask = _morph(mask, erode=False, iterations=open_iterations)
    if not mask.any():
        return []

    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    components: list[CanopyComponent] = []
    for y in range(height):
        for x in range(width):
            if not mask[y, x] or seen[y, x]:
                continue
            queue: deque[tuple[int, int]] = deque([(y, x)])
            seen[y, x] = True
            pixels: list[tuple[int, int]] = []
            min_x = max_x = x
            min_y = max_y = y
            while queue:
                cy, cx = queue.popleft()
                pixels.append((cy, cx))
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = cy + dy, cx + dx
                    if (
                        0 <= ny < height
                        and 0 <= nx < width
                        and mask[ny, nx]
                        and not seen[ny, nx]
                    ):
                        seen[ny, nx] = True
                        queue.append((ny, nx))

            if len(pixels) < min_area:
                continue
            bbox_w = max_x - min_x + 1
            bbox_h = max_y - min_y + 1
            if bbox_w < 10 or bbox_h < 10:
                continue
            component_mask = np.zeros((bbox_h, bbox_w), dtype=bool)
            for py, px in pixels:
                component_mask[py - min_y, px - min_x] = True
            components.append(
                CanopyComponent(
                    mask=component_mask,
                    bbox=(min_x, min_y, max_x + 1, max_y + 1),
                    area=len(pixels),
                )
            )
    return components


def build_canopy_field(
    image: np.ndarray,
    components: list[CanopyComponent],
    *,
    tile: int = 4,
    min_height: float = 4.0,
    max_height: float = 15.0,
) -> CanopyField | None:
    """Turn canopy components into cell-aligned height and cutout data."""

    height, width = image.shape[:2]
    if height % tile or width % tile:
        return None
    cells = (height // tile, width // tile)
    mask = np.zeros((height, width), dtype=bool)
    for component in components:
        x0, y0, x1, y1 = component.bbox
        mask[y0:y1, x0:x1] |= component.mask

    if not mask.any():
        return None

    texture = image.copy()
    texture[..., 3] = (mask * 255).astype(np.uint8)

    # The ground under a lifted canopy should not show the same tree twice.
    # Replace only the canopy pixels with the dominant surrounding ground
    # colour; the raised cutout then owns the tree's silhouette.
    ground_pixels = image[..., :3][~mask]
    fill = (
        np.median(ground_pixels, axis=0).astype(np.uint8)
        if ground_pixels.size
        else np.array([110, 180, 120], dtype=np.uint8)
    )
    ground = image.copy()
    ground[mask, :3] = fill

    coverage = mask.reshape(cells[0], tile, cells[1], tile).mean(axis=(1, 3))
    present = coverage >= 0.25

    # Rounded volume: interior cells rise higher than silhouette edges.
    padded = np.pad(present.astype(np.int16), 1, mode="constant")
    neighbours = np.zeros(cells, dtype=np.float32)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            neighbours += padded[
                1 + dy : 1 + dy + cells[0], 1 + dx : 1 + dx + cells[1]
            ]
    heights = min_height + (max_height - min_height) * (neighbours / 8.0)
    heights = np.where(present, heights, 0.0).astype(np.float32)

    return CanopyField(
        mask=mask,
        present=present,
        heights=heights,
        texture=texture,
        ground=ground,
        components=components,
    )
