"""gba-diorama: a 3D2D lab for GBA-era layer data.

The public API is intentionally small.  The emulator integration will feed the
same decoded layer images into :func:`gba_diorama.mesh.build_scene`.
"""

from .atlas import AtlasResult, stitch_dumps, stitch_paths
from .dump import DumpWorld, GbaDump, export_synthetic_dump, load_dump
from .gba import Obj, TextBg, decode_oam_entry, decode_palette, decode_text_bg
from .parallax import ParallaxEstimate, estimate_parallax, estimate_parallax_paths
from .pipeline import DemoResult, DumpResult, demo_camera, render_atlas, render_demo, render_dump
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
    "DumpResult",
    "demo_camera",
    "render_atlas",
    "render_demo",
    "render_dump",
    "AtlasResult",
    "stitch_dumps",
    "stitch_paths",
    "ParallaxEstimate",
    "estimate_parallax",
    "estimate_parallax_paths",
    "DumpWorld",
    "GbaDump",
    "export_synthetic_dump",
    "load_dump",
]
