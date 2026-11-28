"""Tests for the structural audit (no emulator, no renderer)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from gen3hd.audit import audit_world, contact_sheet, format_audit


def _write_world(root: Path, objects: list[dict], sprites: list[dict] | None = None, mode: str = "terrain") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "objects").mkdir(exist_ok=True)
    manifest = {
        "mode": mode,
        "map": {"pixel_width": 32, "pixel_height": 32},
        "objects": objects,
        "sprites": sprites or [],
    }
    (root / "world.json").write_text(json.dumps(manifest))
    return root


def _texture(root: Path, name: str, fill: int, size: tuple[int, int] = (32, 32), art: str = "full") -> str:
    rgba = np.zeros((size[1], size[0], 4), dtype=np.uint8)
    if art == "full":
        # Two tones so the card is not mistaken for a flat fill.
        rgba[..., 0] = fill
        rgba[..., 1] = fill // 2
        rgba[::4, :, 0] = 255 - fill
        rgba[..., 3] = 255
    elif art == "empty":
        pass
    elif art == "top_edge":
        rgba[0:2, :, 0] = fill
        rgba[0:2, :, 3] = 255
    elif art == "centre":
        rgba[14:18, 14:18, 0] = fill
        rgba[14:18, 14:18, 3] = 255
    with Image.fromarray(rgba, "RGBA") as image:
        image.save(root / "objects" / name)
    return f"objects/{name}"


def _obj(name: str, kind: str, texture: str, bbox: list[int]) -> dict:
    return {"id": name, "kind": kind, "texture": texture, "bbox": bbox}


def test_a_clean_world_reports_nothing(tmp_path) -> None:
    root = _write_world(tmp_path / "w", [])
    texture = _texture(root, "obj-000.png", 90)
    manifest = json.loads((root / "world.json").read_text())
    manifest["objects"] = [_obj("obj-000", "tree", texture, [0, 0, 32, 32])]
    (root / "world.json").write_text(json.dumps(manifest))

    audit = audit_world(root)

    assert not audit.errors
    assert [f.code for f in audit.findings] == []


def test_empty_and_missing_cards_are_errors(tmp_path) -> None:
    root = _write_world(
        tmp_path / "w",
        [],
    )
    empty = _texture(root, "obj-000.png", 0, art="empty")
    manifest = json.loads((root / "world.json").read_text())
    manifest["objects"] = [
        _obj("obj-000", "tree", empty, [0, 0, 32, 32]),
        _obj("obj-001", "prop", "objects/nope.png", [32, 0, 64, 32]),
    ]
    (root / "world.json").write_text(json.dumps(manifest))

    codes = {f.code for f in audit_world(root).errors}

    assert codes == {"EMPTY_CARD", "MISSING_TEXTURE"}


def test_two_objects_on_one_tile_is_an_error(tmp_path) -> None:
    root = _write_world(tmp_path / "w", [])
    a = _texture(root, "obj-000.png", 90)
    b = _texture(root, "obj-001.png", 60)
    manifest = json.loads((root / "world.json").read_text())
    manifest["objects"] = [
        _obj("obj-000", "tree", a, [0, 0, 32, 32]),
        _obj("obj-001", "decor", b, [16, 0, 48, 32]),
    ]
    (root / "world.json").write_text(json.dumps(manifest))

    findings = audit_world(root).errors

    assert {f.code for f in findings} == {"SHARED_CELL"}
    # The two cards overlap on two tiles, and both are named.
    assert len(findings) == 2
    assert "obj-000" in findings[0].detail and "obj-001" in findings[0].detail


def test_a_sparse_card_cut_at_the_edge_is_flagged(tmp_path) -> None:
    root = _write_world(tmp_path / "w", [])
    clipped = _texture(root, "obj-000.png", 200, art="top_edge")
    centre = _texture(root, "obj-001.png", 200, art="centre")
    manifest = json.loads((root / "world.json").read_text())
    manifest["objects"] = [
        _obj("obj-000", "tree", clipped, [0, 0, 32, 32]),
        _obj("obj-001", "decor", centre, [32, 0, 64, 32]),
    ]
    (root / "world.json").write_text(json.dumps(manifest))

    clipped_targets = [
        f.target for f in audit_world(root).findings if f.code == "CLIPPED_ART"
    ]

    assert clipped_targets == ["obj-000"]


def test_a_flat_world_with_objects_is_an_error(tmp_path) -> None:
    root = _write_world(tmp_path / "w", [], mode="flat")
    texture = _texture(root, "obj-000.png", 90)
    manifest = json.loads((root / "world.json").read_text())
    manifest["objects"] = [_obj("obj-000", "tree", texture, [0, 0, 32, 32])]
    (root / "world.json").write_text(json.dumps(manifest))

    codes = [f.code for f in audit_world(root).errors]

    assert "FLAT_WITH_OBJECTS" in codes
    assert "FLAT_NO_SCREEN" in codes


def test_format_groups_repeated_codes(tmp_path) -> None:
    root = _write_world(tmp_path / "w", [])
    manifest = json.loads((root / "world.json").read_text())
    objects = []
    for index in range(8):
        name = f"obj-{index:03d}.png"
        texture = _texture(root, name, 200, art="centre")
        objects.append(_obj(f"obj-{index:03d}", "decor", texture, [index * 32, 0, index * 32 + 32, 32]))
    manifest["objects"] = objects
    (root / "world.json").write_text(json.dumps(manifest))

    text = format_audit(audit_world(root))

    assert "SPARSE_CARD" in text
    assert "x8" in text
    assert "... 2 more" in text


def test_contact_sheet_is_written(tmp_path) -> None:
    root = _write_world(tmp_path / "w", [])
    texture = _texture(root, "obj-000.png", 120)
    manifest = json.loads((root / "world.json").read_text())
    manifest["objects"] = [_obj("obj-000", "tree", texture, [0, 0, 32, 32])]
    (root / "world.json").write_text(json.dumps(manifest))

    target = contact_sheet(root, tmp_path / "sheet.png")

    assert target.is_file() and target.stat().st_size > 0
