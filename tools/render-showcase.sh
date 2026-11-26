#!/usr/bin/env bash
# Re-render the README showcase images from already-captured worlds.
#
#   1. Capture one or both worlds:
#        .venv/bin/python -m gen3hd capture ... --out out/gen3hd-world
#        .venv/bin/python tools/littleroot_capture.py ... --out out/gen3hd-littleroot
#   2. Re-bake presentation overlays after tuning:
#        .venv/bin/python tools/rebake_presentation.py out/gen3hd-world out/gen3hd-littleroot
#   3. Render:
#        tools/render-showcase.sh
#
# Override the Godot binary with GODOT=/path/to/godot if needed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GODOT="${GODOT:-godot}"
ROUTE="$ROOT/out/gen3hd-world"
TOWN="$ROOT/out/gen3hd-littleroot"
OUT="$ROOT/docs/images"

if [[ ! -f "$ROUTE/world.json" || ! -f "$TOWN/world.json" ]]; then
  echo "missing compiled worlds; capture $ROUTE and $TOWN first" >&2
  exit 1
fi

render() {
  local world="$1" name="$2"
  shift 2
  "$GODOT" --path "$ROOT/viewer3d" --resolution 1280x720 -- \
    --world="$world" --shot="$OUT/$name" --size=1600x900 "$@"
}

render "$ROUTE" gen3hd-route101.png --camera=classic --no-dof
render "$ROUTE" gen3hd-route101-map.png --camera=map --no-dof
render "$ROUTE" gen3hd-route101-night.png --camera=classic --no-dof --time=night
render "$TOWN" gen3hd-littleroot.png --camera=classic --buildings=mesh --no-dof
render "$TOWN" gen3hd-littleroot-night.png --camera=classic --buildings=mesh --no-dof --time=night

echo "wrote showcase images to $OUT"
