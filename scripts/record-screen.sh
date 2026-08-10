#!/bin/bash
# Record the touch UI (pygame kmsdrm) by piping RGB frames to ffmpeg.
#
# Do NOT use fbdev (/dev/fb0) — that captures the Linux text console, not the GUI.
#
# Usage (on the Pi):
#   ./scripts/record-screen.sh [output.mkv] [fps]
#
# Operate the touch screen normally. Ctrl+C stops recording and restarts the browser
# without the record hook. Pull the file with scp (quote the remote glob for zsh):
#   scp 'mitch@raspberrypi.local:~/mpe-demo-*.mkv' .

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

OUT="${1:-$HOME/mpe-demo-$(date +%Y%m%d-%H%M%S).mkv}"
FPS="${2:-30}"
WIDTH="${MPE_SCREEN_RECORD_WIDTH:-800}"
HEIGHT="${MPE_SCREEN_RECORD_HEIGHT:-480}"
PIPE="${MPE_SCREEN_RECORD_PIPE:-/tmp/mpe-screen-record.pipe}"
ENV_FILE="/tmp/mpe-screen-record.env"
FFPID=""

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "record-screen: install ffmpeg — sudo apt install -y ffmpeg" >&2
    exit 1
fi

if ! systemctl is-enabled touch-patch-browser.service >/dev/null 2>&1; then
    echo "record-screen: touch-patch-browser.service not installed (MPE_UI_MODE=touch?)" >&2
    exit 1
fi

cleanup() {
    local code=$?
    if [ -n "$FFPID" ] && kill -0 "$FFPID" 2>/dev/null; then
        kill -INT "$FFPID" 2>/dev/null || true
        wait "$FFPID" 2>/dev/null || true
    fi
    rm -f "$ENV_FILE" "$PIPE" 2>/dev/null || true
    if systemctl is-active touch-patch-browser.service >/dev/null 2>&1; then
        sudo systemctl restart touch-patch-browser.service 2>/dev/null || true
    elif systemctl is-enabled touch-patch-browser.service >/dev/null 2>&1; then
        echo "record-screen: restarting touch-patch-browser…" >&2
        sudo systemctl start touch-patch-browser.service 2>/dev/null || true
    fi
    if [ "$code" -eq 0 ] && [ -f "$OUT" ]; then
        echo "record-screen: saved $OUT ($(du -h "$OUT" | cut -f1))" >&2
    fi
    exit "$code"
}
trap cleanup EXIT INT TERM

rm -f "$PIPE"
mkfifo "$PIPE"
chmod 666 "$PIPE" 2>/dev/null || true

echo "record-screen: ffmpeg → $OUT @ ${FPS}fps (${WIDTH}x${HEIGHT})" >&2
ffmpeg -y -loglevel warning \
    -f rawvideo -pix_fmt rgb24 -s "${WIDTH}x${HEIGHT}" -r "$FPS" -i "$PIPE" \
    -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p \
    "$OUT" &
FFPID=$!

tee "$ENV_FILE" >/dev/null <<EOF
MPE_SCREEN_RECORD=1
MPE_SCREEN_RECORD_PIPE=$PIPE
MPE_SCREEN_RECORD_FPS=$FPS
MPE_SCREEN_RECORD_WIDTH=$WIDTH
MPE_SCREEN_RECORD_HEIGHT=$HEIGHT
EOF
chmod 644 "$ENV_FILE"

echo "record-screen: restarting touch-patch-browser (Ctrl+C to stop)…" >&2
sudo systemctl restart touch-patch-browser.service

while kill -0 "$FFPID" 2>/dev/null; do
    if ! systemctl is-active touch-patch-browser.service >/dev/null 2>&1; then
        echo "record-screen: touch-patch-browser exited — stopping ffmpeg" >&2
        kill -INT "$FFPID" 2>/dev/null || true
        wait "$FFPID" 2>/dev/null || true
        FFPID=""
        break
    fi
    sleep 1
done
