"""Temporal world atlas: stitch screen-space dumps into larger BG canvases.

A single mGBA dump only contains the visible 240x160 screen.  Tilting the
camera exposes pixels that were off-screen in that frame.  If the same scene is
captured at several scroll positions, each BG layer can be placed at its scroll
offset in a larger canvas.  This is the offline foundation for experiment E1;
the mGBA hook can later emit a short sequence of frames.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np

from .dump import GbaDump, layer_scroll, load_dump
from .parallax import ParallaxEstimate


@dataclass
class AtlasResult:
    dump: GbaDump
    frames: int
    origin: tuple[int, int]
    layer_bounds: dict[int, tuple[int, int, int, int]]
    coverage: dict[int, np.ndarray]
    parallax: dict[int, ParallaxEstimate] | None = None
    invalidations: dict[int, int] = field(default_factory=dict)

    @property
    def coverage_ratio(self) -> dict[int, float]:
        return {
            index: float(mask.mean()) if mask.size else 0.0
            for index, mask in self.coverage.items()
        }


def _parse_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    if value is None:
        return default
    return default


def layer_vram_hash(dump: GbaDump, index: int) -> bytes:
    """Compute a signature representing the tilemap, tiles, and palette of a layer."""
    h = hashlib.sha256()

    bg_entries = dump.meta.get("bg", [])
    if isinstance(bg_entries, list) and index < len(bg_entries) and isinstance(bg_entries[index], dict):
        entry = bg_entries[index]
        char_base = _parse_int(entry.get("char_base", 0))
        screen_base = _parse_int(entry.get("screen_base", 0))
        bpp = _parse_int(entry.get("bpp", 4), default=4)
        size = _parse_int(entry.get("size", 0))
    else:
        bgcnt = dump.meta.get("bgcnt", [])
        if isinstance(bgcnt, (list, tuple)) and index < len(bgcnt):
            raw = _parse_int(bgcnt[index])
            char_base = (raw >> 2) & 0x03
            screen_base = (raw >> 8) & 0x1F
            size = (raw >> 14) & 0x03
            bpp = 8 if raw & 0x80 else 4
        else:
            char_base, screen_base, bpp, size = 0, 0, 4, 0

    h.update(f"{char_base}:{screen_base}:{bpp}:{size}".encode("ascii"))

    if dump.vram is not None and dump.vram.size >= 0x18000:
        sb_offset = screen_base * 0x800
        sb_size = 0x800 * (1 << min(size, 2))
        if sb_offset < dump.vram.size:
            h.update(dump.vram[sb_offset : min(dump.vram.size, sb_offset + sb_size)].tobytes())

        cb_offset = char_base * 0x4000
        cb_size = 0x4000 if bpp == 4 else 0x8000
        if cb_offset < dump.vram.size:
            h.update(dump.vram[cb_offset : min(dump.vram.size, cb_offset + cb_size)].tobytes())

    if dump.palette is not None:
        h.update(dump.palette.tobytes())

    if dump.vram is None:
        if "vram_hash" in dump.meta:
            h.update(str(dump.meta["vram_hash"]).encode("utf-8"))
        elif "vram_generation" in dump.meta:
            h.update(str(dump.meta["vram_generation"]).encode("utf-8"))

    return h.digest()


def discover_sequence(root: str | Path) -> list[Path]:
    """Find dumps in a directory or its subdirectories in chronological order."""
    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(f"sequence path does not exist: {root_path}")

    # Single dump directory containing meta.json
    if (root_path / "meta.json").exists():
        return [root_path]

    # Search immediate subdirectories
    subdirs = [p for p in root_path.iterdir() if p.is_dir() and (p / "meta.json").exists()]
    if not subdirs:
        raise ValueError(f"no dump directories (with meta.json) found in {root_path}")

    def _sort_key(p: Path) -> tuple[int, int, list]:
        meta_path = p / "meta.json"
        frame_num = -1
        try:
            data = json.loads(meta_path.read_text())
            if "frame" in data and isinstance(data["frame"], int):
                frame_num = data["frame"]
        except Exception:
            pass
        parts = [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", p.name)]
        return (0 if frame_num >= 0 else 1, frame_num, parts)

    return sorted(subdirs, key=_sort_key)


def load_sequence(
    root: str | Path,
    *,
    flip_y: bool | None = None,
) -> list[GbaDump]:
    """Load an ordered sequence of dumps from a directory."""
    paths = discover_sequence(root)
    return [load_dump(p, flip_y=flip_y) for p in paths]


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
    invalidate_vram: bool = True,
) -> AtlasResult:
    """Place each layer at its scroll offset and composite overlapping frames."""

    if not dumps:
        raise ValueError("need at least one dump")

    placements: dict[int, list[tuple[int, int, np.ndarray]]] = {}
    invalidations: dict[int, int] = {index: 0 for index in layer_indices}

    for index in layer_indices:
        last_hash: bytes | None = None
        items: list[tuple[int, int, np.ndarray]] = []
        for dump in dumps:
            image = dump.layers.get(index)
            if image is None:
                continue
            if invalidate_vram:
                cur_hash = layer_vram_hash(dump, index)
                if last_hash is not None and cur_hash != last_hash:
                    items.clear()
                    invalidations[index] += 1
                last_hash = cur_hash
            x, y = layer_scroll(dump, index)
            items.append((x, y, image))
        if items:
            placements[index] = items

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
        "invalidations": {
            str(index): count
            for index, count in invalidations.items()
            if count > 0
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
        invalidations=invalidations,
    )


def stitch_paths(
    roots: Sequence[str | Path],
    *,
    layer_indices: Sequence[int] = (1, 2, 3),
    atlas_size: tuple[int, int] | None = None,
    invalidate_vram: bool = True,
    flip_y: bool | None = None,
) -> AtlasResult:
    all_paths: list[Path] = []
    for root in roots:
        p = Path(root)
        if p.is_dir() and not (p / "meta.json").exists():
            all_paths.extend(discover_sequence(p))
        else:
            all_paths.append(p)
    if not all_paths:
        raise ValueError("no dump paths provided")
    return stitch_dumps(
        [load_dump(p, flip_y=flip_y) for p in all_paths],
        layer_indices=layer_indices,
        atlas_size=atlas_size,
        invalidate_vram=invalidate_vram,
    )
