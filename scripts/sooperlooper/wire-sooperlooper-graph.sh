#!/usr/bin/env bash
# ExecStartPost for mpe-sooperlooper.service — grid sync + JACK graph, once the
# engine is actually on the bus.
#
# Separate from run-sooperlooper.sh because ExecStart must exec the engine itself
# (see that file). systemd runs ExecStartPost after ExecStart has been *spawned*,
# not after it is ready, so this waits for the JACK client rather than assuming it.
#
# Exits non-zero on a graph it could not wire. That fails the unit loudly, which is
# correct: a looper on the bus with no record path silently records silence, and the
# only symptom is pads that light but never play back.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"

JACK_CLIENT="${MPE_SL_JACK_CLIENT:-mpe-looper}"
LOOPS="${MPE_SL_LOOPS:-16}"
SURGE_CLIENT="${MPE_SL_SURGE_CLIENT:-Surge XT}"
WAIT_S="${MPE_SL_GRAPH_WAIT_S:-20}"

log() { echo "wire-sl: $*"; }

client_visible() {
    jack_lsp 2>/dev/null | grep -q "^${JACK_CLIENT}:"
}

record_path_ok() {
    jack_lsp -c "${JACK_CLIENT}:loop0_in_1" 2>/dev/null | grep -Fq "${SURGE_CLIENT}:out_1" \
        && jack_lsp -c "${JACK_CLIENT}:loop$((LOOPS - 1))_in_1" 2>/dev/null \
            | grep -Fq "${SURGE_CLIENT}:out_1"
}

playback_path_ok() {
    jack_lsp -c "system:playback_1" 2>/dev/null | grep -Fq "${JACK_CLIENT}:common_out_1"
}

waited=0
while ! client_visible; do
    if [ "$waited" -ge "$((WAIT_S * 4))" ]; then
        log "ERROR: ${JACK_CLIENT} never appeared on JACK after ${WAIT_S}s"
        exit 1
    fi
    sleep 0.25
    waited=$((waited + 1))
done
log "${JACK_CLIENT} is on the bus"

bash "${SCRIPT_DIR}/configure-grid-sync.sh" || log "WARN: grid-sync configure failed"
bash "${SCRIPT_DIR}/wire-jack-graph.sh" connect
sleep 0.5

if record_path_ok && playback_path_ok; then
    log "PASS — ${SURGE_CLIENT} -> loop0..$((LOOPS - 1))_in, common_out -> playback"
    exit 0
fi

log "ERROR: graph verify failed after wiring"
jack_lsp -c "${SURGE_CLIENT}:out_1" 2>/dev/null || true
exit 1
