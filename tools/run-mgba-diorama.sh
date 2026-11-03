#!/usr/bin/env bash
# Launch mGBA's OpenGL renderer with the diorama dump hook enabled.
#
# The GLES3 build must run with a desktop OpenGL context on this setup:
# Qt otherwise selects a GLES context that fails framebuffer creation.
set -euo pipefail

ROM="${1:?usage: tools/run-mgba-diorama.sh /path/to/game.gba [dump-frame]}"
FRAME="${2:-${GBA_DIORAMA_DUMP_FRAME:-120}}"
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

echo "ROM:  $ROM"
echo "dump: $DUMP_DIR (frame $FRAME)"
echo "If mGBA asks about temporary storage, the ROM is under /tmp; use a permanent path."
exec "$MGBA_QT" -C hwaccelVideo=1 -C videoScale=2 -C mute=1 "$ROM"
