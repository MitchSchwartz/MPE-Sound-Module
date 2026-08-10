#!/bin/bash
# Record the touch UI (pygame kmsdrm) by piping RGB frames to ffmpeg.
#
# Does NOT restart the browser — sends SIGUSR1 to attach recording to the
# running touch UI (avoids DRM blackout / "crash"). SIGUSR2 + cleanup on exit.
#
# Do NOT use fbdev (/dev/fb0) — that captures the Linux text console, not the GUI.
#
# Usage (on the Pi):
#   ./scripts/record-screen.sh [output.mkv] [fps]
#
# Operate the touch screen normally. Ctrl+C stops recording.
# Pull the file with scp (quote the remote glob for zsh):
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
RECORDING=0

browser_pid() {
    systemctl show touch-patch-browser.service -p MainPID --value 2>/dev/null || true
}

stop_browser_recorder() {
    local pid
    pid="$(browser_pid)"
    if [ -n "$pid" ] && [ "$pid" != "0" ]; then
        kill -USR2 "$pid" 2>/dev/null || true
    fi
}

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
    stop_browser_recorder
    if [ -n "$FFPID" ] && kill -0 "$FFPID" 2>/dev/null; then
        kill -INT "$FFPID" 2>/dev/null || true
        wait "$FFPID" 2>/dev/null || true
    fi
    rm -f "$ENV_FILE" "$PIPE" 2>/dev/null || true
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

BROWSER_PID="$(browser_pid)"
if systemctl is-active touch-patch-browser.service >/dev/null 2>&1 \
    && [ -n "$BROWSER_PID" ] && [ "$BROWSER_PID" != "0" ]; then
    echo "record-screen: attaching to running UI (SIGUSR1) — no restart" >&2
    kill -USR1 "$BROWSER_PID"
else
    echo "record-screen: starting touch-patch-browser (UI was not running)…" >&2
    sudo systemctl start touch-patch-browser.service
    sleep 2
    BROWSER_PID="$(browser_pid)"
    if [ -z "$BROWSER_PID" ] || [ "$BROWSER_PID" = "0" ]; then
        echo "record-screen: touch-patch-browser failed to start" >&2
        exit 1
    fi
fi
RECORDING=1

echo "record-screen: recording — use the touch screen; Ctrl+C to stop" >&2
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
