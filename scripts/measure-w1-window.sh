#!/bin/bash
# W1 — four-instrument window (PROMPT-W1-instrumented-window.md).
#
# Order: poller sanity → control (1024x3 with/without poller) → W1-a/b/c ladder.
# Load: condition A only (75-voice midi-load via measure-latency-run.sh default).
#
# Usage: sudo ./scripts/measure-w1-window.sh [--artifact-dir DIR]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
ARTIFACT_DIR="${HOME}/w1-$(date +%Y%m%d-%H%M%S)"

while [ $# -gt 0 ]; do
    case "$1" in
        --artifact-dir) ARTIFACT_DIR="${2:?}"; shift 2 ;;
        -h | --help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

mkdir -p "$ARTIFACT_DIR"
MASTER="${ARTIFACT_DIR}/w1-run.log"
exec > >(tee -a "$MASTER") 2>&1

echo "=== W1 instrumented window $(date -Is) artifacts=$ARTIFACT_DIR ==="
echo "load=condition_A midi_load_voices=75 (measure-latency-run default)"

RESOLVE="$("$SCRIPT_DIR/resolve-alsa-playback-status.sh")"
CARD="$(printf '%s\n' "$RESOLVE" | awk -F= '/^CARD=/{print $2}')"
STATUS="$(printf '%s\n' "$RESOLVE" | awk -F= '/^STATUS=/{print $2}')"
echo "resolved CARD=$CARD STATUS=$STATUS"

echo ""
echo "=== poller sanity (10 s idle stream) ==="
SANITY_LOG="${ARTIFACT_DIR}/sanity-fill.log"
if ! pgrep -x jackd >/dev/null; then
    echo "ERROR: jackd not running — start stack before W1" >&2
    exit 1
fi
PERIOD="$(pgrep -a jackd | head -1 | sed -n 's/.*-p \([0-9][0-9]*\).*/\1/p')"
NPER="$(pgrep -a jackd | head -1 | sed -n 's/.*-n \([0-9][0-9]*\).*/\1/p')"
PERIOD="${PERIOD:-1024}"
NPER="${NPER:-3}"
taskset -c 1 nice -n 19 "$SCRIPT_DIR/mpe-fill-poller.sh" "$STATUS" "$SANITY_LOG" 10
if ! "$SCRIPT_DIR/summarize-fill-trace.sh" "$SANITY_LOG" "$PERIOD" "$NPER"; then
    echo "ERROR: fill poller sanity check failed — fix wrap arithmetic before W1" >&2
    exit 1
fi
echo "sanity ok period=$PERIOD nperiods=$NPER"

_run_cell() {
    local label="$1"
    local buffer="$2"
    local periods="${3:-3}"
    local use_fill="$4"
    local log="${ARTIFACT_DIR}/${label}.log"
    rm -f "$log"
    local args=(
        --buffer "$buffer"
        --periods "$periods"
        --condition A
        --runs 1
        --seconds 60
        --output "$log"
        --no-restore-buffer
    )
    if [ "$use_fill" = 1 ]; then
        args+=(--fill-log "${ARTIFACT_DIR}/${label}-fill")
    fi
    echo "--- cell ${label} buffer=${buffer} periods=${periods} fill=${use_fill} ---"
    "$SCRIPT_DIR/measure-latency-run.sh" "${args[@]}"
    grep '^RESULT tag=' "$log" | tail -6
    if [ "$use_fill" = 1 ]; then
        fill_log="${ARTIFACT_DIR}/${label}-fill-A-b${buffer}-p${periods}-l0-run1.log"
        if [ -f "$fill_log" ]; then
            "$SCRIPT_DIR/summarize-fill-trace.sh" "$fill_log" "$buffer" "$periods" || true
        fi
    fi
}

echo ""
echo "=== W0 (report only — prior run) ==="
echo "W0: 1024x2 opened on Pi 2026-08-21 (jackd -p 1024 -n 2 UP). Not re-run."

echo ""
echo "=== control: 1024x3 poller OFF ==="
_run_cell "control-no-fill" 1024 3 0
CTRL_NO_XR="$(grep 'RESULT tag=.* xruns=' "${ARTIFACT_DIR}/control-no-fill.log" | tail -1 | sed -n 's/.* xruns=\([0-9]*\).*/\1/p')"

echo ""
echo "=== control: 1024x3 poller ON ==="
_run_cell "control-fill" 1024 3 1
CTRL_FILL_XR="$(grep 'RESULT tag=.* xruns=' "${ARTIFACT_DIR}/control-fill.log" | tail -1 | sed -n 's/.* xruns=\([0-9]*\).*/\1/p')"
echo "control xruns: no_fill=${CTRL_NO_XR} fill_on=${CTRL_FILL_XR}"

echo ""
echo "=== W1 ladder (poller ON) ==="
_run_cell "W1-a" 1024 3 1
_run_cell "W1-b" 512 3 1
_run_cell "W1-c" 256 3 1

echo ""
echo "=== restore shipping 1024x3 ==="
"$SCRIPT_DIR/set-surge-audio.sh" --buffer 1024 --periods 3
sleep 4
pgrep -a jackd | head -1

echo "SENTINEL w1-complete $(date -Is) artifacts=$ARTIFACT_DIR"
