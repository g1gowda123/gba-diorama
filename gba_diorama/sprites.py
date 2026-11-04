"""Tiny procedural pixel-art sprites for the synthetic demo.

These are intentionally generic (no Pokémon assets).  A real integration will
decode OAM/OBJ tiles from the emulator; this module only supplies stand-ins for
the lab's billboard/shadows experiment.
"""

from __future__ import annotations

import numpy as np


def _canvas(w: int, h: int) -> np.ndarray:
    return np.zeros((h, w, 4), dtype=np.uint8)


def _rect(img: np.ndarray, x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int, int]) -> None:
    h, w = img.shape[:2]
    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(w, x1)
    y1 = min(h, y1)
    if x1 > x0 and y1 > y0:
        img[y0:y1, x0:x1] = color


def _outline(img: np.ndarray, color: tuple[int, int, int, int] = (24, 20, 28, 255)) -> np.ndarray:
    """Add a 1px outline around the opaque silhouette."""

    alpha = img[..., 3] > 0
    grown = np.zeros_like(alpha)
    grown[1:, :] |= alpha[:-1, :]
    grown[:-1, :] |= alpha[1:, :]
    grown[:, 1:] |= alpha[:, :-1]
    grown[:, :-1] |= alpha[:, 1:]
    edge = grown & ~alpha
    out = img.copy()
    out[edge] = color
    return out


def make_character_sprite(kind: str = "player") -> np.ndarray:
    if kind == "critter":
        return _critter()
    if kind == "npc":
        return _person(
            hat=(58, 122, 42, 255),
            hat_dark=(36, 80, 36, 255),
            shirt=(216, 176, 74, 255),
            shirt_dark=(168, 122, 74, 255),
            pants=(58, 58, 74, 255),
        )
    return _person(
        hat=(176, 47, 47, 255),
        hat_dark=(122, 31, 31, 255),
        shirt=(47, 127, 196, 255),
        shirt_dark=(31, 79, 143, 255),
        pants=(40, 40, 56, 255),
    )


def _person(
    hat: tuple[int, int, int, int],
    hat_dark: tuple[int, int, int, int],
    shirt: tuple[int, int, int, int],
    shirt_dark: tuple[int, int, int, int],
    pants: tuple[int, int, int, int],
) -> np.ndarray:
    img = _canvas(16, 24)
    skin = (232, 184, 140, 255)
    skin_dark = (196, 142, 104, 255)
    hair = (92, 58, 36, 255)
    boot = (72, 46, 28, 255)
    eye = (32, 28, 36, 255)

    # Hair / head.
    _rect(img, 5, 3, 11, 10, hair)
    _rect(img, 5, 5, 11, 10, skin)
    _rect(img, 5, 5, 6, 9, hair)
    _rect(img, 10, 5, 11, 9, hair)

    # Cap.
    _rect(img, 4, 1, 12, 5, hat)
    _rect(img, 3, 4, 13, 6, hat_dark)
    _rect(img, 4, 1, 12, 2, (255, 255, 255, 255))

    # Face.
    _rect(img, 6, 7, 7, 8, eye)
    _rect(img, 9, 7, 10, 8, eye)
    _rect(img, 7, 9, 9, 10, skin_dark)

    # Body and arms.
    _rect(img, 4, 10, 12, 16, shirt)
    _rect(img, 4, 15, 12, 16, shirt_dark)
    _rect(img, 2, 11, 4, 15, skin)
    _rect(img, 12, 11, 14, 15, skin)

    # Legs and boots.
    _rect(img, 4, 16, 8, 21, pants)
    _rect(img, 8, 16, 12, 21, pants)
    _rect(img, 3, 21, 8, 23, boot)
    _rect(img, 8, 21, 13, 23, boot)

    return _outline(img)


def _critter() -> np.ndarray:
    img = _canvas(16, 16)
    fur = (216, 122, 58, 255)
    fur_dark = (176, 82, 42, 255)
    belly = (240, 230, 192, 255)
    eye = (32, 28, 36, 255)
    _rect(img, 3, 7, 13, 14, fur)
    _rect(img, 5, 5, 11, 8, fur)
    _rect(img, 3, 3, 6, 7, fur_dark)
    _rect(img, 10, 3, 13, 7, fur_dark)
    _rect(img, 6, 10, 10, 14, belly)
    _rect(img, 5, 7, 6, 8, eye)
    _rect(img, 10, 7, 11, 8, eye)
    _rect(img, 7, 9, 9, 10, (80, 40, 36, 255))
    _rect(img, 1, 9, 3, 12, fur_dark)  # tail
    return _outline(img)


def make_trunk_texture(width: int = 10, height: int = 28) -> np.ndarray:
    """A simple bark card used under BG tree canopies."""

    img = _canvas(width, height)
    for y in range(height):
        t = y / max(1, height - 1)
        inset = int(round(1 + 2 * t))
        shade = int(78 - 28 * t)
        _rect(
            img,
            inset,
            y,
            width - inset,
            y + 1,
            (shade + 28, shade, max(18, shade - 18), 255),
        )
    # A couple of darker bark streaks.
    _rect(img, width // 2 - 1, 2, width // 2 + 1, height - 2, (58, 38, 24, 255))
    return _outline(img, (30, 22, 16, 255))


def make_shadow_texture(size: int = 32) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = cy = (size - 1) * 0.5
    r = np.sqrt(((xx - cx) / (size * 0.5)) ** 2 + ((yy - cy) / (size * 0.42)) ** 2)
    alpha = np.clip(1.0 - r, 0.0, 1.0) ** 1.7
    out = np.zeros((size, size, 4), dtype=np.uint8)
    out[..., 0:3] = 0
    out[..., 3] = (alpha * 185).astype(np.uint8)
    return out
