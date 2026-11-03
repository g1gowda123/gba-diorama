"""gba-diorama: a 3D2D lab for GBA-era layer data.

The public API is intentionally small.  The emulator integration will feed the
same decoded layer images into :func:`gba_diorama.mesh.build_scene`.
"""

from .gba import Obj, TextBg, decode_oam_entry, decode_palette, decode_text_bg
from .pipeline import DemoResult, demo_camera, render_demo
from .synthetic import SyntheticWorld, build_synthetic_world

__all__ = [
    "Obj",
    "TextBg",
    "decode_oam_entry",
    "decode_palette",
    "decode_text_bg",
    "SyntheticWorld",
    "build_synthetic_world",
    "DemoResult",
    "demo_camera",
    "render_demo",
]
