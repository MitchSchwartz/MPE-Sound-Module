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
    sleep 0.3
    if [ -n "$FFPID" ] && kill -0 "$FFPID" 2>/dev/null; then
        kill -TERM "$FFPID" 2>/dev/null || true
        local i=0
        while kill -0 "$FFPID" 2>/dev/null && [ "$i" -lt 20 ]; do
            sleep 0.25
            i=$((i + 1))
        done
        if kill -0 "$FFPID" 2>/dev/null; then
            kill -KILL "$FFPID" 2>/dev/null || true
        fi
        wait "$FFPID" 2>/dev/null || true
    fi
    rm -f "$ENV_FILE" "$PIPE" 2>/dev/null || true
    if [ "$code" -eq 0 ] && [ -f "$OUT" ]; then
        echo "record-screen: saved $OUT ($(du -h "$OUT" | cut -f1))" >&2
    fi
    exit "$code"
}
# HUP matters as much as INT here: `mpe record` runs this over ssh, and Ctrl+C
# there can drop the connection before SIGINT is delivered. The remote process
# group then gets SIGHUP, and without it in the trap ffmpeg dies unfinalised —
# leaving a 0-byte .mkv that still matches the `mpe pull-videos` glob, plus a
# stale env file and FIFO. A recording that looks like a recording and holds no
# frames is the failure shape this project keeps meeting; it should not survive
# in the tool used to record the evidence. (MEASURED 2026-08-30: INT -> 28KB
# valid file, HUP -> 0 bytes.)
trap cleanup EXIT INT TERM HUP

rm -f "$PIPE"
mkfifo "$PIPE"
chmod 666 "$PIPE" 2>/dev/null || true

echo "record-screen: ffmpeg → $OUT @ ${FPS}fps (${WIDTH}x${HEIGHT})" >&2
# ffmpeg must ignore both SIGINT and SIGHUP from the terminal, and the trap
# above is not enough on its own.
# `mpe record` runs this over ssh; a Ctrl+C there can drop the connection
# before SIGINT arrives, and SIGHUP then goes to the whole process group.
# ffmpeg would die mid-stream with no EBML header written.
#
# A terminal Ctrl+C is worse and is the case that actually bites: SIGINT goes
# to the whole foreground process group, so ffmpeg receives it directly, and
# then cleanup's own `kill` arrives as a SECOND signal. Two SIGINTs is
# ffmpeg's documented abort-now gesture — it exits without finalising, and
# you get a 256KiB unflushed buffer that ffprobe rejects.
#
# So ffmpeg ignores INT and HUP from the group, and cleanup shuts it down
# with a single SIGTERM instead, which ffmpeg also finalises on. Exactly one
# shutdown signal, sent deliberately.
#
# `timeout -s INT` does NOT reproduce this — it signals one process, not the
# group — which is why the first fix looked correct and was not. Test this
# path with a real pty and a real 0x03, or the instrument lies to you.
#
# Do NOT pass `-r "$FPS"` on the INPUT. That asserts a frame rate instead of
# measuring one, and the UI does not honour it: `_draw()` calls
# `write_frame()` once per draw-loop iteration, so the writer runs at whatever
# `frame_pacing.frame_rate_for()` returns — IDLE_FPS (20) when idle, higher
# when busy. Telling ffmpeg 30 made every idle capture play 1.5x too fast and
# report 2/3 of its real length. (MEASURED 2026-08-30: 85s of capture ->
# 1692 frames -> a file claiming 56.4s. 85*20=1700, 1692/30=56.4.)
#
# Pinning the draw rate while recording would fix the ratio but raise idle CPU
# exactly during a capture, and telling ffmpeg IDLE_FPS would be wrong the
# moment the UI goes busy — which is the normal case for a demo. Both assume
# a constant rate. It is not constant; that assumption is the bug.
#
# So timestamp frames on arrival and let the container carry variable timing.
( trap '' HUP INT; exec ffmpeg -y -loglevel warning \
    -use_wallclock_as_timestamps 1 \
    -f rawvideo -pix_fmt rgb24 -s "${WIDTH}x${HEIGHT}" -i "$PIPE" \
    -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p \
    -fps_mode vfr \
    "$OUT" ) &
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
        kill -TERM "$FFPID" 2>/dev/null || true
        wait "$FFPID" 2>/dev/null || true
        FFPID=""
        break
    fi
    sleep 1
done
