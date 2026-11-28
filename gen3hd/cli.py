"""Command line entry point: capture live Gen 3 world data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .gen3.rom import Gen3Rom
from .host import DEFAULT_BASE, DEFAULT_SESSION, RomdevHost
from .live import LiveLink
from .world import compile_world


def _capture(args: argparse.Namespace) -> int:
    rom = Gen3Rom.from_path(args.rom)
    host = RomdevHost(args.base, args.session)
    host.wait_ready()
    if args.boot:
        host.boot_to_overworld(args.save, args.rom)
    snapshot = host.snapshot()
    snapshot.title = rom.header.title
    snapshot.game_code = rom.header.game_code
    world = compile_world(
        snapshot, rom, flat=args.flat, auto_flat=not args.no_auto_flat
    )
    out = world.export(args.out)
    if world.mode == "flat":
        # Flat mode is a screen, not a map: keep the emulator's own frame so
        # BG0 (dialogue, menus) and OAM sprites are in the card too.
        host.screenshot(out / "screen.png")
    if args.screenshot:
        host.screenshot(args.screenshot)
    print(json.dumps(world.summary()["map"], indent=1))
    print(
        f"family={world.family.value} mode={world.mode} "
        f"ui={world.ui_coverage:.2f} cover={world.map_coverage:.2f} objects={len(world.objects)} "
        f"sprites={len(world.sprites)} -> {out}"
    )
    return 0


def _live(args: argparse.Namespace) -> int:
    rom = Gen3Rom.from_path(args.rom)
    world_dir = Path(args.world)
    manifest = json.loads((world_dir / "world.json").read_text())
    map_info = manifest["map"]
    # Only slots the compiled world knows: a stale OAM entry cannot be proven
    # empty without decoding its tiles, and the viewer has no billboard for it.
    allowed = {
        int(sprite["oam"])
        for sprite in manifest.get("sprites", [])
        if int(sprite.get("oam", -1)) >= 0
    }
    host = RomdevHost(args.base, args.session)
    host.wait_ready()
    if args.boot:
        host.boot_to_overworld(args.save, args.rom)
    link = LiveLink(
        host,
        world_dir,
        int(map_info["pixel_width"]),
        int(map_info["pixel_height"]),
        interval=args.interval,
        frames=args.frames,
        walk=args.walk,
        allowed=allowed,
    )
    target = (world_dir / "live.json").resolve()
    print(f"live: publishing {target} at {1.0 / max(args.interval, 1e-3):.0f} Hz")
    ticks = link.run(args.seconds)
    print(f"live: {ticks} ticks, frame {host.frame_count}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gen3hd")
    parser.add_argument("--base", default=DEFAULT_BASE)
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture", help="compile the live map into a world directory")
    capture.add_argument("--rom", required=True)
    capture.add_argument("--out", required=True)
    capture.add_argument("--save", default="")
    capture.add_argument("--session", default=DEFAULT_SESSION)
    capture.add_argument("--boot", action="store_true", help="load ROM+save and drive the title menu")
    capture.add_argument(
        "--flat", action="store_true",
        help="treat the frame as a screen (battle/menu): one card, no 3D lifting",
    )
    capture.add_argument(
        "--no-auto-flat", action="store_true",
        help="never switch to flat mode; always lift the frame",
    )
    capture.add_argument("--screenshot", default="")
    capture.set_defaults(func=_capture)

    live = sub.add_parser("live", help="stream sprite positions into a compiled world")
    live.add_argument("--rom", required=True)
    live.add_argument("--world", required=True, help="compiled world directory")
    live.add_argument("--save", default="")
    live.add_argument("--session", default=DEFAULT_SESSION)
    live.add_argument("--boot", action="store_true", help="load ROM+save and drive the title menu")
    live.add_argument("--interval", type=float, default=0.1, help="seconds between updates")
    live.add_argument("--frames", type=int, default=6, help="emulator frames per update")
    live.add_argument("--seconds", type=float, default=0.0, help="0 = run until interrupted")
    live.add_argument("--walk", default="", help="hold this direction (up/down/left/right)")
    live.set_defaults(func=_live)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
