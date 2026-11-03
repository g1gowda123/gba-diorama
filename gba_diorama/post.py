"""Small, dependency-light post stack for the diorama demo.

This is deliberately CPU-side so it can be iterated with plain Python before it
gets ported into mGBA's GL finalize pass.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter


def _as_float(img: np.ndarray) -> np.ndarray:
    return img.astype(np.float32) / 255.0


def _to_u8(img: np.ndarray) -> np.ndarray:
    return np.clip(img * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _blur(img: np.ndarray, radius: float) -> np.ndarray:
    pil = Image.fromarray(_to_u8(img))
    return _as_float(np.asarray(pil.filter(ImageFilter.GaussianBlur(radius))))


def _bloom(rgb: np.ndarray, strength: float) -> np.ndarray:
    if strength <= 0:
        return rgb
    bright = np.clip(rgb - 0.62, 0.0, 1.0)
    glow = _blur(bright, 10.0) * 1.7 + _blur(bright, 24.0) * 1.1
    return rgb + glow * strength


def _tilt_shift(rgb: np.ndarray, strength: float) -> np.ndarray:
    if strength <= 0:
        return rgb
    h = rgb.shape[0]
    blurred = _blur(rgb, 4.5)
    yy = np.linspace(-1.0, 1.0, h, dtype=np.float32)
    band = np.clip((np.abs(yy) - 0.30) / 0.70, 0.0, 1.0) ** 1.35
    mask = (band * strength)[:, None, None]
    return rgb * (1.0 - mask) + blurred * mask


def _vignette(rgb: np.ndarray, strength: float) -> np.ndarray:
    if strength <= 0:
        return rgb
    h, w = rgb.shape[:2]
    yy = np.linspace(-1.0, 1.0, h, dtype=np.float32)[:, None]
    xx = np.linspace(-1.0, 1.0, w, dtype=np.float32)[None, :]
    r2 = xx * xx * 0.85 + yy * yy
    mask = np.clip(1.0 - strength * np.clip(r2 - 0.25, 0.0, 2.0) ** 1.1, 0.0, 1.0)
    return rgb * mask[..., None]


def _grade(rgb: np.ndarray) -> np.ndarray:
    # Gentle warm highlights / cool shadows; keeps the pixel palette readable.
    lum = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
    shadow = np.clip(1.0 - lum * 1.6, 0.0, 1.0)[..., None]
    highlight = np.clip(lum * 1.25 - 0.25, 0.0, 1.0)[..., None]
    rgb = rgb + shadow * np.array([-0.012, 0.004, 0.030], dtype=np.float32)
    rgb = rgb + highlight * np.array([0.030, 0.014, -0.008], dtype=np.float32)
    rgb = (rgb - 0.5) * 1.035 + 0.5
    return np.clip(rgb, 0.0, 1.0)


def apply_post(
    image: np.ndarray,
    *,
    bloom: float = 0.18,
    tilt_shift: float = 0.22,
    vignette: float = 0.34,
    grade: bool = True,
) -> np.ndarray:
    rgb = _as_float(image[..., :3])
    rgb = _bloom(rgb, bloom)
    rgb = _tilt_shift(rgb, tilt_shift)
    rgb = _vignette(rgb, vignette)
    if grade:
        rgb = _grade(rgb)
    out = np.zeros_like(image)
    out[..., :3] = _to_u8(rgb)
    if image.shape[2] == 4:
        out[..., 3] = 255
    return out
