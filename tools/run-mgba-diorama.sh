#!/usr/bin/env bash
# Launch mGBA's OpenGL renderer with the diorama dump hook enabled.
#
# The GLES3 build must run with a desktop OpenGL context on this setup:
# Qt otherwise selects a GLES context that fails framebuffer creation.
set -euo pipefail

ROM="${1:?usage: tools/run-mgba-diorama.sh /path/to/game.gba [dump-frame] [seq-count] [seq-step]}"
FRAME="${2:-${GBA_DIORAMA_DUMP_FRAME:-120}}"
SEQ_COUNT="${3:-${GBA_DIORAMA_SEQ_COUNT:-0}}"
SEQ_STEP="${4:-${GBA_DIORAMA_SEQ_STEP:-30}}"
MGBA_QT="${MGBA_QT:-/tmp/opencode/mgba-qt/qt/mgba-qt}"
DUMP_DIR="${GBA_DIORAMA_DUMP_DIR:-$PWD/dumps/$(basename "${ROM%.*}")}"

if [[ ! -x "$MGBA_QT" ]]; then
	echo "mGBA Qt binary not found: $MGBA_QT" >&2
	echo "Set MGBA_QT to the path of a Qt build with patches/mgba-diorama-dump.patch applied." >&2
	exit 1
fi

mkdir -p "$DUMP_DIR"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
export QT_OPENGL=desktop
export GBA_DIORAMA_DUMP_DIR="$DUMP_DIR"
export GBA_DIORAMA_DUMP_FRAME="$FRAME"
export GBA_DIORAMA_SEQ_COUNT="$SEQ_COUNT"
export GBA_DIORAMA_SEQ_STEP="$SEQ_STEP"

echo "ROM:  $ROM"
echo "dump: $DUMP_DIR (frame $FRAME)"
if [[ "$SEQ_COUNT" != "0" ]]; then
	echo "seq:  $DUMP_DIR/seq (count $SEQ_COUNT, step $SEQ_STEP frames)"
	echo "      capture starts at frame $FRAME; pick a frame after the scene settles"
fi
echo "If mGBA asks about temporary storage, the ROM is under /tmp; use a permanent path."
exec "$MGBA_QT" -C hwaccelVideo=1 -C videoScale=2 -C mute=1 "$ROM"
