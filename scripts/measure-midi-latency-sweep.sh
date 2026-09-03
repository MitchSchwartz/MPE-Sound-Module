#!/bin/bash
# Cell B — does Surge's MIDI->audio leg scale with the JACK period?
#
# Cell A measured 159 frames at period 96. That single point cannot distinguish
# "a constant 159 frames" from "~1.5 x period + c", and the two differ by 240
# frames (5 ms) at period 256. This sweeps several periods so the slope is
# fitted rather than assumed, and so a third point can contradict a line drawn
# through the first two.
#
# RESTORE IS A TRAP, NOT A FINAL LINE. An abort in the middle must not leave the
# appliance sitting on a period the player did not choose.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RESTORE_PERIOD="${RESTORE_PERIOD:-96}"
TRIALS="${TRIALS:-30}"
PERIODS="${PERIODS:-48 192 256}"
OUT_DIR="${OUT_DIR:-/tmp/mpe-lat-sweep}"

mkdir -p "$OUT_DIR"

_restore() {
    echo
    echo "=== restoring period ${RESTORE_PERIOD} ==="
    sudo "$SCRIPT_DIR/set-surge-audio.sh" --buffer "$RESTORE_PERIOD" 2>&1 | tail -3
    echo "SENTINEL sweep-restored period=${RESTORE_PERIOD}"
}
trap _restore EXIT

# Surge must be back in the graph before the probe can find its port. Polling
# for the port is the only honest readiness test -- the unit being "active" is
# what let a dead driver look healthy in the first place.
_wait_for_surge() {
    local deadline=$((SECONDS + 60))
    while [ "$SECONDS" -lt "$deadline" ]; do
        if jack_lsp 2>/dev/null | grep -q '^Surge XT:out_1$'; then
            sleep 2   # let the engine settle past its first buffers
            return 0
        fi
        sleep 1
    done
    echo "ERROR: Surge XT:out_1 never appeared within 60s" >&2
    return 1
}

for P in $PERIODS; do
    echo
    echo "############ period ${P} ############"
    if ! sudo "$SCRIPT_DIR/set-surge-audio.sh" --buffer "$P" 2>&1 | tail -3; then
        echo "SENTINEL sweep-cell-skipped period=${P} reason=set-failed"
        continue
    fi
    if ! _wait_for_surge; then
        echo "SENTINEL sweep-cell-skipped period=${P} reason=no-surge-port"
        continue
    fi

    # The harness reports JACK's OWN blocksize, so a ladder fallback shows up as
    # a mismatch here instead of being silently labelled with the period we
    # asked for. That exact confusion -- a run labelled 512 that ran at 1024 --
    # is in this project's history.
    # The harness refuses to run without this: it is a measurement instrument
    # that attaches a Python callback to the live graph, and it must never be
    # reachable by accident. A driver run BY HAND is the intended caller.
    MPE_ALLOW_GRAPH_PROBE=1 \
    python3 "$REPO_ROOT/scripts/measure-midi-audio-latency.py" \
        --trials "$TRIALS" \
        --label "sweep-p${P}" \
        --out "$OUT_DIR/lat-p${P}.json" 2>&1 | grep -vE '^  trial'
done

echo
echo "SENTINEL sweep-complete periods=$(echo "$PERIODS" | tr ' ' ',')"
