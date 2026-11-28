"""Command line entry point: capture live Gen 3 world data."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np

from .gen3.locate import locate_map
from .gen3.rom import Gen3Rom
from .host import DEFAULT_BASE, DEFAULT_SESSION, RomdevHost
from .live import LiveLink
from .world import compile_world


def capture_world(rom: Gen3Rom, host: RomdevHost, out: Path, *, flat: bool = False):
    """Compile the frame the host is showing and export it to ``out``."""

    snapshot = host.snapshot()
    snapshot.title = rom.header.title
    snapshot.game_code = rom.header.game_code
    world = compile_world(snapshot, rom, flat=flat, auto_flat=True)
    exported = world.export(out)
    if world.mode == "flat":
        # Flat mode is a screen, not a map: keep the emulator's own frame so
        # BG0 (dialogue, menus) and OAM sprites are in the card too.
        host.screenshot(exported / "screen.png")
    return world, exported


def viewer_command(
    world_dir: Path,
    live_path: Path,
    *,
    godot: str = "",
    input_url: str = "",
    session: str = "",
    extra: tuple[str, ...] = (),
) -> list[str]:
    """The command that opens the diorama on a compiled world."""

    binary = godot or shutil.which("godot") or shutil.which("godot4") or "godot"
    project = Path(__file__).resolve().parent.parent / "viewer3d"
    command = [
        binary,
        "--path",
        str(project),
        "--",
        f"--world={Path(world_dir).resolve()}",
        f"--live={Path(live_path).resolve()}",
    ]
    if input_url:
        command.append(f"--input={input_url}")
    if session:
        command.append(f"--session={session}")
    command.extend(extra)
    return command


def _capture(args: argparse.Namespace) -> int:
    rom = Gen3Rom.from_path(args.rom)
    host = RomdevHost(args.base, args.session)
    host.wait_ready()
    if args.boot:
        host.boot_to_overworld(args.save, args.rom)
    world, out = capture_world(
        rom, host, Path(args.out), flat=args.flat
    )
    if args.screenshot:
        host.screenshot(args.screenshot)
    print(json.dumps(world.summary()["map"], indent=1))
    print(
        f"family={world.family.value} mode={world.mode} "
        f"ui={world.ui_coverage:.2f} cover={world.map_coverage:.2f} objects={len(world.objects)} "
        f"sprites={len(world.sprites)} -> {out}"
    )
    return 0


def make_link(
    host: RomdevHost,
    rom: Gen3Rom,
    world_dir: Path,
    *,
    interval: float,
    frames: int,
    walk: str,
    check_every: int,
    recompile: bool = True,
) -> LiveLink:
    """Build a live link for an already-exported world."""

    manifest = json.loads((world_dir / "world.json").read_text())
    map_info = manifest["map"]
    # Only slots the compiled world knows: a stale OAM entry cannot be proven
    # empty without decoding its tiles, and the viewer has no billboard for it.
    allowed = {
        int(sprite["oam"])
        for sprite in manifest.get("sprites", [])
        if int(sprite.get("oam", -1)) >= 0
    }

    def map_key() -> str:
        """Identity of the map on screen: layout pointer plus its size."""

        located = locate_map(
            np.frombuffer(host.read_region("gba_iwram"), dtype=np.uint8),
            np.frombuffer(host.read_region("system_ram"), dtype=np.uint8),
            rom,
        )
        return f"{located.layout.pointer:08X}:{located.layout.width}x{located.layout.height}"

    def on_change(key: str) -> dict | None:
        """Rebuild the world in place after a map change."""

        if not recompile:
            print(f"live: map changed to {key} (not recompiling)")
            return None
        world, _ = capture_world(rom, host, world_dir)
        fresh = json.loads((world_dir / "world.json").read_text())
        print(
            f"live: map changed to {key} -> {world.mode}, "
            f"{len(world.objects)} objects, {fresh['map']['pixel_width']}x"
            f"{fresh['map']['pixel_height']} px"
        )
        return {
            "allowed": {
                int(sprite["oam"])
                for sprite in fresh.get("sprites", [])
                if int(sprite.get("oam", -1)) >= 0
            },
            "width": int(fresh["map"]["pixel_width"]),
            "height": int(fresh["map"]["pixel_height"]),
        }

    return LiveLink(
        host,
        world_dir,
        int(map_info["pixel_width"]),
        int(map_info["pixel_height"]),
        interval=interval,
        frames=frames,
        walk=walk,
        allowed=allowed,
        map_key=map_key,
        on_map_change=on_change,
        check_every=check_every,
    )


def _live(args: argparse.Namespace) -> int:
    rom = Gen3Rom.from_path(args.rom)
    world_dir = Path(args.world)
    host = RomdevHost(args.base, args.session)
    host.wait_ready()
    if args.boot:
        host.boot_to_overworld(args.save, args.rom)
    link = make_link(
        host,
        rom,
        world_dir,
        interval=args.interval,
        frames=args.frames,
        walk=args.walk,
        check_every=args.check_every,
        recompile=args.recompile,
    )
    target = (world_dir / "live.json").resolve()
    print(f"live: publishing {target} at {1.0 / max(args.interval, 1e-3):.0f} Hz")
    ticks = link.run(args.seconds)
    print(f"live: {ticks} ticks, frame {host.frame_count}")
    return 0


def _play(args: argparse.Namespace) -> int:
    """Capture the current frame, open the diorama, and keep it live."""

    rom = Gen3Rom.from_path(args.rom)
    out = Path(args.out)
    host = RomdevHost(args.base, args.session)
    host.wait_ready()
    if args.boot:
        host.boot_to_overworld(args.save, args.rom)
    world, exported = capture_world(rom, host, out)
    print(f"play: {world.mode}, {len(world.objects)} objects -> {exported}")
    command = viewer_command(
        exported,
        exported / "live.json",
        godot=args.godot,
        input_url=f"{args.base.rstrip('/')}/tool/input",
        session=args.session,
        extra=tuple(
            part
            for part in (
                "--follow",
                "--camera=play" if args.follow else "",
                f"--time={args.time}" if args.time else "",
            )
            if part
        ),
    )
    print("play: " + " ".join(command))
    viewer = subprocess.Popen(command)
    link = make_link(
        host,
        rom,
        exported,
        interval=args.interval,
        frames=args.frames,
        walk=args.walk,
        check_every=args.check_every,
    )
    try:
        while viewer.poll() is None:
            started = time.perf_counter()
            link.tick()
            spent = time.perf_counter() - started
            if args.interval > spent:
                time.sleep(args.interval - spent)
    except KeyboardInterrupt:
        pass
    finally:
        if viewer.poll() is None:
            viewer.terminate()
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
    live.add_argument(
        "--walk", default="", choices=["", "up", "down", "left", "right"],
        help="hold this direction (up/down/left/right)",
    )
    live.add_argument(
        "--check-every", type=int, default=10,
        help="ticks between map-identity reads (default 10)",
    )
    live.add_argument(
        "--no-recompile", dest="recompile", action="store_false",
        help="detect map changes but leave the world as captured",
    )
    live.set_defaults(func=_live)

    play = sub.add_parser(
        "play", help="capture the current frame, open the diorama and keep it live"
    )
    play.add_argument("--rom", required=True)
    play.add_argument("--out", default="out/play", help="world directory to grow in place")
    play.add_argument("--save", default="")
    play.add_argument("--session", default=DEFAULT_SESSION)
    play.add_argument("--boot", action="store_true", help="load ROM+save and drive the title menu")
    play.add_argument("--interval", type=float, default=0.1)
    play.add_argument("--frames", type=int, default=6)
    play.add_argument("--check-every", type=int, default=10)
    play.add_argument("--godot", default="", help="godot binary (default: found on PATH)")
    play.add_argument(
        "--no-follow", dest="follow", action="store_false",
        help="keep the whole map framed instead of following the player",
    )
    play.add_argument(
        "--time", default="day",
        help="grade for play: day (faithful, default), golden, dusk or night",
    )
    play.add_argument(
        "--walk", default="", choices=["", "up", "down", "left", "right"],
        help="hold a direction (testing/demos)",
    )
    play.set_defaults(func=_play)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
