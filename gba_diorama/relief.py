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
from dataclasses import dataclass, field
import math

import numpy as np


@dataclass(frozen=True)
class CanopyComponent:
    mask: np.ndarray
    bbox: tuple[int, int, int, int]
    area: int
    texture: np.ndarray | None = None
    canopy_height: int = 0


@dataclass
class CanopyField:
    mask: np.ndarray
    present: np.ndarray
    heights: np.ndarray
    texture: np.ndarray
    ground: np.ndarray
    components: list[CanopyComponent]
    cards: list[tuple[np.ndarray, tuple[int, int, int, int]]] = field(
        default_factory=list
    )


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


def _inpaint_nearest(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Fill masked pixels from the nearest unmasked pixel.

    A median fill turns every tree footprint into a visible flat rectangle.
    Nearest-neighbour propagation keeps the surrounding grass/path texture and
    is generic: it does not need to know what the ground looks like.
    """

    out = image.copy()
    unknown = mask.copy()
    if not unknown.any():
        return out
    height, width = unknown.shape
    queue: deque[tuple[int, int]] = deque()

    # Seed with known pixels that touch the hole.  This makes the fill grow
    # inward from all sides instead of smearing one edge across the hole.
    known = ~unknown
    for y, x in np.argwhere(known):
        if (
            (y > 0 and unknown[y - 1, x])
            or (y + 1 < height and unknown[y + 1, x])
            or (x > 0 and unknown[y, x - 1])
            or (x + 1 < width and unknown[y, x + 1])
        ):
            queue.append((int(y), int(x)))

    while queue:
        y, x = queue.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < height and 0 <= nx < width and unknown[ny, nx]:
                out[ny, nx, :3] = out[y, x, :3]
                out[ny, nx, 3] = 255
                unknown[ny, nx] = False
                queue.append((ny, nx))

    if unknown.any():
        # Degenerate case: the mask covers the whole layer.  Use the global
        # median so the result is at least stable.
        fill = np.median(image[..., :3][known], axis=0).astype(np.uint8)
        out[unknown, :3] = fill
        out[unknown, 3] = 255
    return out


def _inpaint_ring(
    image: np.ndarray,
    mask: np.ndarray,
    components: list[CanopyComponent],
) -> np.ndarray:
    """Fill each footprint from a ring of nearby ground pixels.

    Sampling the immediate boundary picks up the tree's own shadow and turns
    the hole into a flat teal rectangle.  A wider ring reaches actual grass and
    keeps its speckled texture.
    """

    out = image.copy()
    for component in components:
        x0, y0, x1, y1 = component.bbox
        local = np.zeros(mask.shape, dtype=bool)
        local[y0:y1, x0:x1] = component.mask
        fill = _morph(local, erode=False, iterations=2)
        ring = _morph(fill, erode=False, iterations=10) & ~_morph(
            fill, erode=False, iterations=2
        )
        ring &= ~mask
        pixels = image[ring, :3]
        if not len(pixels):
            continue
        rng = np.random.default_rng((x0 * 73856093) ^ (y0 * 19349663))
        picks = rng.integers(0, len(pixels), size=int(fill.sum()))
        # Blend the local median with ring samples: a pure random fill turns
        # into bright confetti, while a pure median becomes a flat teal block.
        base = np.median(pixels, axis=0)
        out[fill, :3] = (0.55 * base + 0.45 * pixels[picks]).astype(np.uint8)
        out[fill, 3] = 255
    return out


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


def _canopy_texture(
    image: np.ndarray,
    bbox: tuple[int, int, int, int],
    mask: np.ndarray,
    canopy_h: int,
) -> np.ndarray:
    x0, y0, x1, y1 = bbox
    texture = np.zeros((y1 - y0, x1 - x0, 4), dtype=np.uint8)
    texture[..., :3] = image[y0:y1, x0:x1, :3]
    texture[..., 3] = (mask * 255).astype(np.uint8)
    return texture


def _attach_trunk(
    image: np.ndarray,
    hue: np.ndarray,
    saturation: np.ndarray,
    value: np.ndarray,
    bbox: tuple[int, int, int, int],
    mask: np.ndarray,
) -> tuple[
    tuple[int, int, int, int], np.ndarray, np.ndarray | None, int
]:
    """Extend a canopy cutout through a compact non-green trunk below it.

    The GBA tree tile often has a small brown trunk a few pixels below the
    canopy.  Keeping those original pixels (instead of inventing a brown
    rectangle) is what makes the card read as a tree rather than a lollipop.
    """

    x0, y0, x1, y1 = bbox
    height, width = image.shape[:2]

    # Some trees (especially multi-tile ones) have no grass gap: the dark-pixel
    # growth already swallowed the trunk.  Split the existing bbox instead of
    # searching below it, otherwise the component loses its texture.
    local_brown = (
        (hue[y0:y1, x0:x1] < 70.0)
        & (saturation[y0:y1, x0:x1] > 0.08)
        & (value[y0:y1, x0:x1] < 0.62)
    )
    brown_rows = local_brown.any(axis=1)
    if len(brown_rows) and brown_rows[-1]:
        canopy_h = len(brown_rows) - 1
        while canopy_h > 0 and brown_rows[canopy_h - 1]:
            canopy_h -= 1
        if canopy_h >= 4:
            texture = np.zeros((y1 - y0, x1 - x0, 4), dtype=np.uint8)
            texture[..., :3] = image[y0:y1, x0:x1, :3]
            texture[..., 3] = (mask * 255).astype(np.uint8)
            return bbox, mask, texture, canopy_h

    search_y1 = min(height, y1 + max(12, (y1 - y0)))
    cx = (x0 + x1) // 2
    half = max(3, (x1 - x0) // 3)
    rx0 = max(0, cx - half)
    rx1 = min(width, cx + half + 1)
    if search_y1 <= y1 or rx1 <= rx0:
        return bbox, mask, _canopy_texture(image, bbox, mask, y1 - y0), y1 - y0

    region = (slice(y1, search_y1), slice(rx0, rx1))
    # Brown/grey trunk: low hue, low-ish value, not the bright green canopy.
    trunk = (
        (hue[region] < 70.0)
        & (saturation[region] > 0.08)
        & (value[region] < 0.62)
    )
    if not trunk.any():
        return bbox, mask, _canopy_texture(image, bbox, mask, y1 - y0), y1 - y0

    seen = np.zeros_like(trunk, dtype=bool)
    best: tuple[int, int, int, int] | None = None
    best_area = 0
    for ty in range(trunk.shape[0]):
        for tx in range(trunk.shape[1]):
            if not trunk[ty, tx] or seen[ty, tx]:
                continue
            queue: deque[tuple[int, int]] = deque([(ty, tx)])
            seen[ty, tx] = True
            pixels: list[tuple[int, int]] = []
            min_x = max_x = tx
            min_y = max_y = ty
            while queue:
                cy, cx2 = queue.popleft()
                pixels.append((cy, cx2))
                min_x = min(min_x, cx2)
                max_x = max(max_x, cx2)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = cy + dy, cx2 + dx
                    if (
                        0 <= ny < trunk.shape[0]
                        and 0 <= nx < trunk.shape[1]
                        and trunk[ny, nx]
                        and not seen[ny, nx]
                    ):
                        seen[ny, nx] = True
                        queue.append((ny, nx))
            bw = max_x - min_x + 1
            bh = max_y - min_y + 1
            if len(pixels) < 3 or bw > max(6, (x1 - x0) // 2) or bh > max(10, y1 - y0):
                continue
            if len(pixels) > best_area:
                best_area = len(pixels)
                best = (rx0 + min_x, y1 + min_y, rx0 + max_x + 1, y1 + max_y + 1)

    if best is None:
        return bbox, mask, _canopy_texture(image, bbox, mask, y1 - y0), y1 - y0

    tx0, ty0, tx1, ty1 = best
    trunk_h = ty1 - ty0
    trunk_w = tx1 - tx0
    canopy_h = y1 - y0
    new_h = canopy_h + trunk_h
    new_mask = np.zeros((new_h, x1 - x0), dtype=bool)
    new_mask[:canopy_h, :] = mask
    offset_x = max(0, ((x1 - x0) - trunk_w) // 2)
    new_mask[canopy_h:new_h, offset_x : offset_x + trunk_w] |= trunk[
        ty0 - y1 : ty1 - y1, tx0 - rx0 : tx1 - rx0
    ]

    # Compose the original canopy and trunk pixels without the grass gap, so
    # the card has the tree's real proportions instead of a long lollipop stem.
    texture = np.zeros((new_h, x1 - x0, 4), dtype=np.uint8)
    texture[:canopy_h, :, :3] = image[y0:y1, x0:x1, :3]
    texture[canopy_h:new_h, offset_x : offset_x + trunk_w, :3] = image[
        ty0:ty1, tx0:tx1, :3
    ]
    texture[..., 3] = (new_mask * 255).astype(np.uint8)
    return (x0, y0, x1, y0 + new_h), new_mask, texture, canopy_h


def detect_canopies(
    image: np.ndarray,
    *,
    min_area: int = 48,
    hue_min: float = 55.0,
    hue_max: float = 148.0,
    saturation_min: float = 0.20,
    value_max: float = 0.95,
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

            # The canopy is usually drawn above a small dark trunk.  Extend
            # only downward through a narrow center band, otherwise dark
            # outlines/shadows connect adjacent trees into tall columns.
            pad_y = max(4, bbox_h // 3)
            ey1 = min(height, max_y + 1 + pad_y)
            local = np.zeros((ey1 - min_y, bbox_w), dtype=bool)
            local[:bbox_h, :] = component_mask
            allowed = local | (value[min_y:ey1, min_x : max_x + 1] < 0.62)
            grown = local.copy()
            queue = deque(zip(*np.nonzero(local)))
            while queue:
                cy, cx = queue.popleft()
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        ny, nx = cy + dy, cx + dx
                        if (
                            0 <= ny < grown.shape[0]
                            and 0 <= nx < grown.shape[1]
                            and allowed[ny, nx]
                            and not grown[ny, nx]
                        ):
                            grown[ny, nx] = True
                            queue.append((ny, nx))

            ys, xs = np.nonzero(grown)
            if not len(ys):
                continue
            gy0 = min_y + int(ys.min())
            gy1 = min_y + int(ys.max()) + 1
            bbox = (min_x, gy0, max_x + 1, gy1)
            component_mask = grown[
                ys.min() : ys.max() + 1,
                xs.min() : xs.max() + 1,
            ]
            bbox, component_mask, texture, canopy_height = _attach_trunk(
                image,
                hue,
                saturation,
                value,
                bbox,
                component_mask,
            )
            components.append(
                CanopyComponent(
                    mask=component_mask,
                    bbox=bbox,
                    area=int(component_mask.sum()),
                    texture=texture,
                    canopy_height=canopy_height,
                )
            )
    return components


def detect_alpha_objects(
    image: np.ndarray,
    *,
    min_alpha: int = 8,
    min_area: int = 48,
    open_iterations: int = 1,
) -> list[CanopyComponent]:
    """Find cutout objects on a transparent foreground BG.

    Romhacks often put trees and props on a sparse BG1 instead of painting them
    into the opaque ground.  In that case alpha is a much better signal than a
    game-specific colour range.
    """

    if image.ndim != 3 or image.shape[2] < 4:
        return []
    mask = image[..., 3] > min_alpha
    if not mask.any():
        return []
    if open_iterations:
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
            if bbox_w < 4 or bbox_h < 4:
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


def _draw_canopy_lobe(
    out: np.ndarray,
    texture: np.ndarray,
    canopy_h: int,
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    *,
    mirror: bool = False,
) -> None:
    """Draw one oval of real canopy pixels into a side-view card."""

    height, width = texture.shape[:2]
    x0 = max(0, int(cx - rx))
    x1 = min(out.shape[1], int(cx + rx) + 1)
    y0 = max(0, int(cy - ry))
    y1 = min(out.shape[0], int(cy + ry) + 1)
    for y in range(y0, y1):
        ny = (y + 0.5 - cy) / max(ry, 1.0)
        for x in range(x0, x1):
            nx = (x + 0.5 - cx) / max(rx, 1.0)
            if nx * nx + ny * ny > 1.0:
                continue
            sx = int((nx * 0.5 + 0.5) * (width - 1))
            if mirror:
                sx = width - 1 - sx
            sy = min(canopy_h - 1, max(0, int((ny * 0.5 + 0.5) * (canopy_h - 1))))
            src = texture[sy, sx]
            if src[3] > 0:
                out[y, x] = src


def make_side_view_card(component: CanopyComponent) -> np.ndarray | None:
    """Build a side-view tree procedurally from the game's own canopy pixels.

    There is no per-tree sprite table and no hand-placed trunk.  The component
    bbox seeds a deterministic RNG; that RNG varies the crown proportions and
    the number/placement of overlapping lobes.  Every opaque pixel still comes
    from the source canopy/trunk art.
    """

    if component.texture is None or component.canopy_height <= 0:
        return None
    texture = component.texture
    height, width = texture.shape[:2]
    canopy_h = min(component.canopy_height, height)
    trunk = texture[canopy_h:height]
    if canopy_h < 4 or width < 4:
        return None

    x0, y0, x1, y1 = component.bbox
    seed = (
        (x0 * 73856093)
        ^ (y0 * 19349663)
        ^ (x1 * 83492791)
        ^ (y1 * 2654435761)
    ) & 0xFFFFFFFF
    rng = np.random.default_rng(seed)

    out_w = max(10, min(96, int(round(width * rng.uniform(1.05, 1.25)))))
    crown_h = max(14, min(128, int(round(canopy_h * rng.uniform(1.15, 1.35)))))
    trunk_h = max(3, int(round(max(1, height - canopy_h) * rng.uniform(0.7, 1.0))))
    out = np.zeros((crown_h + trunk_h, out_w, 4), dtype=np.uint8)

    # Central mass plus 3-5 satellite lobes.  Jitter and mirroring are seeded
    # by the bbox, so neighbouring trees do not share an identical silhouette.
    lobes: list[tuple[float, float, float, float, bool]] = [
        (0.50, 0.46, 0.32, 0.44, False)
    ]
    count = int(rng.integers(3, 6))
    for index in range(count):
        angle = 2.0 * np.pi * index / count + rng.uniform(-0.35, 0.35)
        cx = 0.50 + 0.19 * np.cos(angle) + rng.uniform(-0.03, 0.03)
        cy = 0.50 + 0.18 * np.sin(angle) + rng.uniform(-0.03, 0.03)
        rx = 0.19 + rng.uniform(0.0, 0.08)
        ry = 0.20 + rng.uniform(0.0, 0.08)
        lobes.append((cx, cy, rx, ry, bool(rng.integers(0, 2))))

    for cx, cy, rx, ry, mirror in lobes:
        _draw_canopy_lobe(
            out,
            texture,
            canopy_h,
            out_w * cx,
            crown_h * cy,
            out_w * rx,
            crown_h * ry,
            mirror=mirror,
        )

    # Actual trunk pixels, cropped to their real horizontal extent and scaled.
    if trunk_h > 0 and trunk.size:
        trunk_pixels = trunk[..., 3] > 0
        if trunk_pixels.any():
            _, xs = np.nonzero(trunk_pixels)
            trunk = trunk[:, xs.min() : xs.max() + 1]
        tw = max(6, int(round(out_w * rng.uniform(0.22, 0.30))))
        tx0 = (out_w - tw) // 2
        for y in range(trunk_h):
            sy = min(trunk.shape[0] - 1, int(y / trunk_h * trunk.shape[0]))
            for x in range(tw):
                sx = min(trunk.shape[1] - 1, int(x / tw * trunk.shape[1]))
                src = trunk[sy, sx]
                if src[3] > 0:
                    out[crown_h + y, tx0 + x] = src

    return out


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
    for component in components:
        if component.texture is None:
            continue
        x0, y0, x1, y1 = component.bbox
        texture[y0:y1, x0:x1] = component.texture

    # The ground under a lifted canopy should not show the same tree twice.
    # Inpaint from the nearest surrounding ground pixels so the result keeps
    # the map's grass/path texture instead of becoming a flat coloured block.
    fill_mask = _morph(mask, erode=False, iterations=2)
    ground = _inpaint_ring(image, fill_mask, components)

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

    cards: list[tuple[np.ndarray, tuple[int, int, int, int]]] = []
    for component in components:
        card = make_side_view_card(component)
        if card is not None:
            cards.append((card, component.bbox))

    return CanopyField(
        mask=mask,
        present=present,
        heights=heights,
        texture=texture,
        ground=ground,
        components=components,
        cards=cards,
    )
