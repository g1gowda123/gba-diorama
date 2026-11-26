#!/usr/bin/env python3
"""Rebake the presentation overlays for an already-exported gen3hd world.

`gen3hd capture` writes `ao.png` and `shadow_ground.png` alongside `world.json`.
This tool regenerates those overlays from an existing export, which is useful
after tuning the presentation without booting the emulator again.

Usage:
    .venv/bin/python tools/rebake_presentation.py out/gen3hd-world
    .venv/bin/python tools/rebake_presentation.py out/gen3hd-littleroot --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gen3hd.world import (  # noqa: E402
    SpriteBillboard,
    WorldObject,
    bake_ground_ao,
    bake_ground_shadow,
)


def _objects(root: Path, manifest: dict) -> list[WorldObject]:
    objects = []
    for entry in manifest.get("objects", []):
        texture = np.array(Image.open(root / entry["texture"]).convert("RGBA"))
        objects.append(
            WorldObject(
                asset_id=entry["id"],
                kind=entry["kind"],
                bbox=tuple(entry["bbox"]),
                footprint=tuple(entry["footprint"]),
                anchor=tuple(entry["anchor"]),
                elevation=int(entry.get("elevation", 3)),
                texture=texture,
            )
        )
    return objects


def _sprites(root: Path, manifest: dict) -> list[SpriteBillboard]:
    sprites = []
    for entry in manifest.get("sprites", []):
        texture = np.array(Image.open(root / entry["texture"]).convert("RGBA"))
        sprites.append(
            SpriteBillboard(
                asset_id=entry["id"],
                texture=texture,
                screen=tuple(entry.get("screen", (0, 0))),
                world=tuple(entry["world"]),
                priority=int(entry.get("priority", 0)),
            )
        )
    return sprites


def rebake(world_dir: Path, check: bool) -> int:
    manifest_path = world_dir / "world.json"
    if not manifest_path.exists():
        print(f"missing {manifest_path}", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text())
    width = int(manifest["map"]["pixel_width"])
    height = int(manifest["map"]["pixel_height"])
    objects = _objects(world_dir, manifest)
    sprites = _sprites(world_dir, manifest)

    ao = bake_ground_ao(objects, sprites, width, height)
    shadow = bake_ground_shadow(objects, sprites, width, height)
    print(
        f"{world_dir}: objects={len(objects)} sprites={len(sprites)} "
        f"ao={int(ao[..., 3].max())} shadow={int(shadow[..., 3].max())}"
    )
    if not check:
        Image.fromarray(ao, "RGBA").save(world_dir / "ao.png")
        Image.fromarray(shadow, "RGBA").save(world_dir / "shadow_ground.png")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("world", nargs="+", help="exported world directory")
    parser.add_argument("--check", action="store_true", help="report only")
    args = parser.parse_args()
    return max(rebake(Path(path), args.check) for path in args.world)


if __name__ == "__main__":
    raise SystemExit(main())
