"""Command line entry point: capture live Gen 3 world data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .gen3.rom import Gen3Rom
from .host import DEFAULT_BASE, DEFAULT_SESSION, RomdevHost
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
    world = compile_world(snapshot, rom)
    out = world.export(args.out)
    if args.screenshot:
        host.screenshot(args.screenshot)
    print(json.dumps(world.summary()["map"], indent=1))
    print(
        f"family={world.family.value} objects={len(world.objects)} "
        f"sprites={len(world.sprites)} -> {out}"
    )
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
    capture.add_argument("--screenshot", default="")
    capture.set_defaults(func=_capture)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
