"""CLI: render the synthetic diorama demo.

Examples:
    python -m gba_diorama --out docs/images/demo.png
    python -m gba_diorama --yaw 18 --width 1920 --height 1080 --no-post
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from .pipeline import DEFAULT_HEIGHT, DEFAULT_SEED, DEFAULT_WIDTH, render_demo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the gba-diorama synthetic demo")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--yaw", type=float, default=0.0, help="camera yaw in degrees")
    parser.add_argument("--out", type=Path, default=Path("docs/images/demo.png"))
    parser.add_argument("--no-post", action="store_true", help="skip bloom/tilt-shift/grade")
    args = parser.parse_args(argv)

    if args.width < 64 or args.height < 64:
        parser.error("width and height must be at least 64")

    result = render_demo(
        width=args.width,
        height=args.height,
        seed=args.seed,
        yaw_deg=args.yaw,
        post=not args.no_post,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(result.image).save(args.out)

    print(f"wrote {args.out}")
    print(
        f"resolution={args.width}x{args.height} seed={args.seed} yaw={args.yaw:.1f} "
        f"triangles={len(result.scene.triangles)}"
    )
    print(f"layers={result.scene.layers}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
