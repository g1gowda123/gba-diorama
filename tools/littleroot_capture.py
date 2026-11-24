"""Drive Emerald from the Route 101 save into Littleroot Town and capture it.

This is the reproducible path used for the town/building screenshots.  It
boots the ROM, imports the battery save, walks south through the Route 101
connection, saves an emulator state for later reuse, and exports the compiled
world for the Godot viewer.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gen3hd.gen3.rom import Gen3Rom
from gen3hd.host import RomdevHost
from gen3hd.world import compile_world

# Down two tiles, right seven, then hold down through the map connection.
WALK_TO_LITTLEROOT = {
    "op": "sequence",
    "steps": [
        {"input": {"ports": [{"down": True}]}, "frames": 34},
        {"input": {"ports": [{}]}, "frames": 3},
        {"input": {"ports": [{"right": True}]}, "frames": 116},
        {"input": {"ports": [{}]}, "frames": 3},
        {"input": {"ports": [{"down": True}]}, "frames": 120},
        {"input": {"ports": [{}]}, "frames": 20},
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", required=True)
    parser.add_argument("--save", required=True)
    parser.add_argument("--session", default="gen3hd-live")
    parser.add_argument("--out", default="out/gen3hd-littleroot")
    parser.add_argument("--state", default="out/littleroot.ss1")
    parser.add_argument(
        "--from-state",
        action="store_true",
        help="load --state instead of booting and walking",
    )
    args = parser.parse_args()

    host = RomdevHost(session=args.session)
    state_path = Path(args.state)
    if args.from_state and state_path.exists():
        host.load(args.rom)
        host.load_state(state_path)
    else:
        host.boot_to_overworld(args.save, args.rom)
        host.call("input", **WALK_TO_LITTLEROOT)
        host.step(40)
        host.save_state(state_path)

    world = compile_world(host.snapshot(), Gen3Rom.from_path(args.rom))
    counts = Counter(obj.kind for obj in world.objects)
    print(
        f"map={world.width}x{world.height} objects={len(world.objects)} "
        f"counts={dict(counts)}"
    )
    print(world.export(args.out))


if __name__ == "__main__":
    main()
