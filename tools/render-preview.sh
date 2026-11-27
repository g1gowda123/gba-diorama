#!/usr/bin/env bash
# Render the looping camera-sway preview used in the README.
#
# Usage: tools/render-preview.sh [world-dir]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORLD="${1:-$ROOT/out/gen3hd-world}"
FRAMES="$ROOT/out/preview-frames"
OUT="$ROOT/docs/images"

rm -rf "$FRAMES"
mkdir -p "$OUT"
godot --path "$ROOT/viewer3d" --resolution 800x450 -- \
    --world="$WORLD" --size=800x450 --camera=classic --no-dof \
    --turntable=90 --turntable-dir="$FRAMES" --turntable-arc=14

ffmpeg -y -loglevel error -framerate 30 -i "$FRAMES/frame_%04d.png" \
    -c:v libx264 -pix_fmt yuv420p -crf 18 -movflags +faststart \
    "$OUT/gen3hd-preview.mp4"
ffmpeg -y -loglevel error -framerate 30 -i "$FRAMES/frame_%04d.png" \
    -vf "fps=12,scale=560:-1:flags=neighbor,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3" \
    -loop 0 "$OUT/gen3hd-preview.gif"
ls -lh "$OUT/gen3hd-preview.mp4" "$OUT/gen3hd-preview.gif"
