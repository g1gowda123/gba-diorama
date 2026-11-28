"""Audit a compiled world for the faults that show up as broken geometry.

The checks are structural, not aesthetic: they read ``world.json`` and the
exported textures and report things that are wrong *by construction* — a card
with no art, art that fills almost none of its card, two objects claiming the
same tile, a sprite with no pixels, a flat world carrying lifted objects, a
tree drawn at half the height of its neighbours.

A render of a broken world looks broken in a different way every time; these
findings are the same every run, so a regression is caught before it is looked
at.  ``contact_sheet`` lays the suspect textures out with their ids, so the
picture can be reviewed in one glance instead of hunting through a 1600-pixel
render.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SPARSE_SHARE = 0.18
MIN_TEXTURE_PX = 8
HALF_TREE_RATIO = 0.62  # a tree shorter than this share of its peers is cut


@dataclass(frozen=True)
class Finding:
    severity: str  # "error" stops a ship; "warn" is worth a look
    code: str
    target: str
    detail: str

    def line(self) -> str:
        return f"{self.severity:5s} {self.code:16s} {self.target:10s} {self.detail}"


@dataclass
class Audit:
    findings: list[Finding] = field(default_factory=list)
    checked: int = 0

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warn"]

    def add(self, severity: str, code: str, target: str, detail: str) -> None:
        self.findings.append(Finding(severity, code, target, detail))

    def summary(self) -> str:
        return (
            f"audit: {self.checked} assets, {len(self.errors)} errors, "
            f"{len(self.warnings)} warnings"
        )


def _rgba(path: Path) -> np.ndarray | None:
    try:
        with Image.open(path) as image:
            return np.array(image.convert("RGBA"))
    except OSError:
        return None


def _art_bounds(alpha: np.ndarray) -> tuple[int, int, int, int]:
    rows = np.where(alpha.any(axis=1))[0]
    columns = np.where(alpha.any(axis=0))[0]
    return int(rows[0]), int(rows[-1]) + 1, int(columns[0]), int(columns[-1]) + 1


def _tone_spread(rgba: np.ndarray, alpha: np.ndarray) -> tuple[float, float]:
    """Mean brightness and spread of the lit pixels, 0..1."""

    lum = (0.299 * rgba[..., 0] + 0.587 * rgba[..., 1] + 0.114 * rgba[..., 2]) / 255.0
    values = lum[alpha]
    if values.size == 0:
        return 0.0, 0.0
    return float(values.mean()), float(values.std())


def audit_world(world_dir: str | Path) -> Audit:
    world_dir = Path(world_dir)
    manifest = json.loads((world_dir / "world.json").read_text())
    audit = Audit()
    mode = manifest.get("mode", "terrain")

    if mode == "flat":
        if manifest.get("objects"):
            audit.add("error", "FLAT_WITH_OBJECTS", "world", "flat mode exported lifted objects")
        if not (world_dir / "screen.png").is_file():
            audit.add("error", "FLAT_NO_SCREEN", "world", "flat mode without screen.png")

    cells: dict[tuple[int, int], list[str]] = {}
    trees: list[tuple[str, int, int]] = []  # id, card width, art height

    for obj in manifest.get("objects", []):
        name = str(obj.get("id", "?"))
        kind = str(obj.get("kind", ""))
        rgba = _rgba(world_dir / str(obj.get("texture", "")))
        audit.checked += 1
        if rgba is None:
            audit.add("error", "MISSING_TEXTURE", name, str(obj.get("texture")))
            continue
        alpha = rgba[..., 3] > 128
        if not alpha.any():
            audit.add("error", "EMPTY_CARD", name, "texture has no opaque pixels")
            continue

        height, width = alpha.shape
        if width < MIN_TEXTURE_PX or height < MIN_TEXTURE_PX:
            audit.add("warn", "TINY_TEXTURE", name, f"{width}x{height} px")

        share = float(alpha.mean())
        if share < SPARSE_SHARE:
            audit.add("warn", "SPARSE_CARD", name, f"art fills {share:.0%} of the card")

        # Sparse *and* touching an edge means the sprite was cut by the card
        # boundary — a canopy sliced in half.  Sparse in the middle is just a
        # small prop sitting in a big card, which is fine.
        if kind not in {"building", "wall"} and share < 0.45:
            edges = []
            if alpha[0, :].sum() >= 3:
                edges.append("top")
            if alpha[-1, :].sum() >= 3:
                edges.append("bottom")
            if alpha[:, 0].sum() >= 3:
                edges.append("left")
            if alpha[:, -1].sum() >= 3:
                edges.append("right")
            if edges:
                audit.add(
                    "warn", "CLIPPED_ART", name,
                    f"{kind} art cut at the {'/'.join(edges)} edge ({share:.0%} fill)",
                )

        mean, spread = _tone_spread(rgba, alpha)
        if spread < 0.02 and kind != "building":
            audit.add(
                "warn", "FLAT_FILL_CARD", name,
                f"single tone ({mean:.2f}) lifted as a {kind}",
            )

        bbox = obj.get("bbox", [0, 0, 0, 0])
        card_h = max(1, (int(bbox[3]) - int(bbox[1])) // 16)
        if kind == "tree":
            top, bottom, _, _ = _art_bounds(alpha)
            trees.append((name, width, bottom - top))
            if card_h <= 1:
                audit.add("warn", "SHORT_TREE", name, "one tile tall: no room for a trunk")

        for cy in range(int(bbox[1]) // 16, int(bbox[3]) // 16):
            for cx in range(int(bbox[0]) // 16, int(bbox[2]) // 16):
                cells.setdefault((cx, cy), []).append(name)

    # A tree whose art is much shorter than its peers was cut through.
    if len(trees) >= 4:
        by_width: dict[int, list[float]] = {}
        for name, width, art_h in trees:
            by_width.setdefault(width, []).append(float(art_h))
        medians = {w: statistics.median(v) for w, v in by_width.items() if len(v) >= 3}
        for name, width, art_h in trees:
            median = medians.get(width)
            if median and art_h < median * HALF_TREE_RATIO:
                audit.add(
                    "warn", "HALF_TREE", name,
                    f"art {art_h}px vs {median:.0f}px for its width class",
                )

    for cell, owners in cells.items():
        if len(owners) > 1:
            audit.add(
                "error", "SHARED_CELL", f"({cell[0]},{cell[1]})",
                "tile claimed by " + ", ".join(owners),
            )

    for sprite in manifest.get("sprites", []):
        name = f"spr:{sprite.get('oam', sprite.get('id', '?'))}"
        rgba = _rgba(world_dir / str(sprite.get("texture", "")))
        audit.checked += 1
        if rgba is None:
            audit.add("error", "MISSING_TEXTURE", name, str(sprite.get("texture")))
        elif not (rgba[..., 3] > 128).any():
            audit.add("error", "EMPTY_SPRITE", name, "sprite texture is fully transparent")

    return audit


def contact_sheet(world_dir: str | Path, out_path: str | Path, limit: int = 60) -> Path:
    """One labelled strip of the textures the audit wants a person to see."""

    world_dir = Path(world_dir)
    manifest = json.loads((world_dir / "world.json").read_text())
    audit = audit_world(world_dir)
    flagged = {f.target for f in audit.findings if f.target.startswith("obj-")}
    objects = [o for o in manifest.get("objects", []) if str(o.get("id")) in flagged]
    if not objects:
        objects = manifest.get("objects", [])

    tiles = []
    for obj in objects[:limit]:
        path = world_dir / str(obj.get("texture", ""))
        try:
            with Image.open(path) as image:
                tile = image.convert("RGBA")
        except OSError:
            continue
        scale = max(1, 128 // max(tile.width, tile.height))
        tiles.append((str(obj.get("id")), str(obj.get("kind", "")), tile, scale))

    if not tiles:
        raise ValueError("nothing to draw")

    cell_w = max(t[2].width * t[3] for t in tiles) + 20
    cell_h = max(t[2].height * t[3] for t in tiles) + 34
    columns = 6
    rows = (len(tiles) + columns - 1) // columns
    sheet = Image.new("RGBA", (cell_w * columns, cell_h * rows), (18, 20, 26, 255))
    draw = ImageDraw.Draw(sheet)
    for index, (name, kind, tile, scale) in enumerate(tiles):
        column, row = index % columns, index // columns
        x = column * cell_w + 10
        y = row * cell_h + 6
        big = tile.resize((tile.width * scale, tile.height * scale), Image.Resampling.NEAREST)
        sheet.alpha_composite(big, (x, y + 14))
        draw.text((x, y), f"{name} {kind}", fill=(235, 226, 206, 255))
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(target)
    return target


def format_audit(audit: Audit, examples: int = 6) -> str:
    """Group by code: a wall of identical warnings hides the interesting one."""

    lines = [audit.summary()]
    grouped: dict[tuple[str, str], list[Finding]] = {}
    for finding in audit.findings:
        grouped.setdefault((finding.severity, finding.code), []).append(finding)
    for (severity, code), group in sorted(grouped.items(), key=lambda kv: (kv[0][0] != "error", kv[0][1])):
        if len(group) == 1:
            lines.append("  " + group[0].line())
            continue
        lines.append(f"  {severity:5s} {code:16s} x{len(group)}")
        for finding in group[:examples]:
            lines.append(f"        {finding.target:10s} {finding.detail}")
        if len(group) > examples:
            lines.append(f"        ... {len(group) - examples} more")
    if not audit.findings:
        lines.append("  clean")
    return "\n".join(lines)
