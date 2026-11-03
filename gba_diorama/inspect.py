"""Inspect a gba-diorama dump directory.

Usage:
    python -m gba_diorama.inspect path/to/dump --out /tmp/dump-pngs
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from .dump import load_dump


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a gba-diorama layer dump")
    parser.add_argument("dump", type=Path)
    parser.add_argument("--out", type=Path, default=Path("dump-pngs"))
    args = parser.parse_args(argv)

    dump = load_dump(args.dump)
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"dump: {dump.root}")
    print(f"game: {dump.game_code or '(unknown)'}  scale={dump.meta.get('scale', 1)}")
    for index in (0, 1, 2, 3):
        image = dump.layers.get(index)
        if image is None:
            print(f"BG{index}: missing")
            continue
        path = args.out / f"bg{index}.png"
        Image.fromarray(image, "RGBA").save(path)
        print(f"BG{index}: {image.shape[1]}x{image.shape[0]} -> {path}")

    for name, image in (
        ("obj", dump.obj),
        ("window", dump.window),
        ("backdrop", dump.backdrop),
    ):
        if image is None:
            continue
        path = args.out / f"{name}.png"
        Image.fromarray(image, "RGBA").save(path)
        print(f"{name}: {image.shape[1]}x{image.shape[0]} -> {path}")

    if dump.vram is not None:
        print(f"vram: {dump.vram.size} bytes")
    if dump.oam is not None:
        print(f"oam: {dump.oam.size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
