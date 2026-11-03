"""CLI: render the synthetic demo or a real mGBA layer dump.

Examples:
    python -m gba_diorama --out docs/images/demo.png
    python -m gba_diorama --dump /tmp/opencode/emerald-route101c \\
        --out docs/images/emerald-route101.png
    python -m gba_diorama --yaw 18 --width 1920 --height 1080 --no-post
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from .pipeline import (
    DEFAULT_HEIGHT,
    DEFAULT_SEED,
    DEFAULT_WIDTH,
    render_atlas,
    render_demo,
    render_dump,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a GBA layer dump as a 3D diorama")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--yaw", type=float, default=0.0, help="camera yaw in degrees")
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--dump", type=Path, help="mGBA dump directory (meta.json etc.)")
    source_group.add_argument(
        "--atlas",
        type=Path,
        nargs="+",
        metavar="DUMP",
        help="two or more dumps to stitch into a world atlas",
    )
    parser.add_argument("--out", type=Path, help="output PNG (default: docs/images/demo.png)")
    parser.add_argument("--all-layers", action="store_true", help="extrude even sparse overlay BGs")
    parser.add_argument("--force-3d", action="store_true", help="mesh even UI scenes instead of 2D fallback")
    parser.add_argument("--profile", type=Path, help="TOML scene profile (file or directory)")
    parser.add_argument("--no-post", action="store_true", help="skip bloom/tilt-shift/grade")
    args = parser.parse_args(argv)

    if args.width < 64 or args.height < 64:
        parser.error("width and height must be at least 64")

    if args.dump:
        result = render_dump(
            args.dump,
            width=args.width,
            height=args.height,
            yaw_deg=args.yaw,
            post=not args.no_post,
            all_layers=args.all_layers,
            force_3d=args.force_3d,
            profile_path=args.profile,
        )
        source = f"dump={args.dump}"
    elif args.atlas:
        result = render_atlas(
            args.atlas,
            width=args.width,
            height=args.height,
            yaw_deg=args.yaw,
            post=not args.no_post,
            all_layers=args.all_layers,
            force_3d=args.force_3d,
            profile_path=args.profile,
        )
        source = f"atlas={len(args.atlas)} dumps"
    else:
        result = render_demo(
            width=args.width,
            height=args.height,
            seed=args.seed,
            yaw_deg=args.yaw,
            post=not args.no_post,
        )
        source = f"seed={args.seed}"

    out = args.out or Path("docs/images/demo.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(result.image).save(out)

    print(f"wrote {out}")
    profile_name = result.profile.name if result.profile is not None else "generic"
    signature = result.signature.label() if result.signature is not None else "unknown"
    if result.fallback:
        print(
            f"resolution={args.width}x{args.height} {source} fallback=2D "
            f"({result.reason}) profile={profile_name}"
        )
        print(f"signature={signature}")
    else:
        assert result.scene is not None
        atlas_info = f" atlas_frames={result.atlas.frames}" if result.atlas else ""
        print(
            f"resolution={args.width}x{args.height} {source} yaw={args.yaw:.1f} "
            f"triangles={len(result.scene.triangles)} profile={profile_name}"
            f"{atlas_info}"
        )
        print(f"signature={signature}")
        print(f"layers={result.scene.layers}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
