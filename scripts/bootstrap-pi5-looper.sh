#!/usr/bin/env bash
# Pi 5 looper bring-up: env keys, binary check, engine restart, health gate.
# Invoked by scripts/looper-deploy.sh after `mpe looper deploy`.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOOP_BIN="${MPE_SOOPERLOOPER_BIN:-${HOME}/src/sooperlooper-1.7.9/src/sooperlooper}"
MPE_ENV="/etc/mpe/mpe.env"

log() { echo "bootstrap-pi5-looper: $*"; }

_ensure_env_kv() {
    local key="$1" val="$2"
    if [ ! -f "$MPE_ENV" ]; then
        echo "bootstrap-pi5-looper: missing $MPE_ENV — run configure-pi-paths.sh first" >&2
        exit 1
    fi
    sudo bash -c "
set -euo pipefail
if grep -q '^${key}=' '$MPE_ENV' 2>/dev/null; then
  sed -i 's|^${key}=.*|${key}=${val}|' '$MPE_ENV'
else
  echo '${key}=${val}' >> '$MPE_ENV'
fi
"
}

_remove_env_key() {
    local key="$1"
    [ -f "$MPE_ENV" ] || return 0
    sudo sed -i "/^${key}=/d" "$MPE_ENV" 2>/dev/null || true
}

log "configuring looper env (native ring-out overdub, 15 usable loops, no scratch)"

# Keys from the offline seam-weld pipeline, deleted 2026-08-26 when a single
# native `overdub` replaced it. They are removed rather than ignored because
# /etc/mpe/mpe.env persists across deploys: MPE_SL_SCRATCH_LOOP=14 was still
# live on the Pi a day after the code stopped wanting a scratch loop, and it
# does real damage — looper_songs skips that index and sl_hud_monitor hides it,
# so track 15 silently disappears from a 16-track instrument.
_remove_env_key MPE_SL_TAIL_MODE
_remove_env_key MPE_SL_TAIL_CAPTURE
_remove_env_key MPE_SL_SEAM_WELD
_remove_env_key MPE_SL_SCRATCH_LOOP
_ensure_env_kv MPE_SL_LOOPS 15

# Ring-out peak trace. TAIL_THRESH and TAIL_HOLD_MS were inherited from the
# seam-weld work on a different signal path and have never been checked against
# the patches actually played, so this records the decay curve of every take:
# one line per peak sample, buffered in memory and appended once when the tail
# ends. Costs a list append during ring-outs only -- the peak stream does not
# exist at any other time -- and one file append per take.
#
# It is ON because a threshold that is wrong in the quiet direction is
# invisible without it: every tail just exits on the cap, which is also what a
# DEAD peak meter looks like. Unset the key in /etc/mpe/mpe.env to stop.
_ensure_env_kv MPE_SL_TAIL_TRACE /home/pi/tail-trace.csv

# Classic-MIDI routing. Without this the router binds only MPE controllers, so
# a plain keyboard plugged into a cold-booted appliance is silent -- which is
# exactly the outcome the classic-MIDI work exists to remove. Set here rather
# than by hand so it survives a reflash; see docs/CLASSIC-MIDI-PLAN.md.
_ensure_env_kv MPE_ROUTE_CLASSIC 1

if [ ! -x "$SOOP_BIN" ]; then
    echo "bootstrap-pi5-looper: FAIL — SooperLooper binary missing: $SOOP_BIN" >&2
    echo "  Copy from Pi 4 (arm64 trixie build):" >&2
    echo "    mkdir -p ~/src/sooperlooper-1.7.9/src" >&2
    echo "    rsync -av pi4:~/src/sooperlooper-1.7.9/src/sooperlooper ~/src/sooperlooper-1.7.9/src/" >&2
    echo "  Or build from ~/src/sooperlooper-1.7.9 (liblo 0.32 patch — see sooperlooper-eval doc)." >&2
    exit 1
fi

if ldd "$SOOP_BIN" 2>/dev/null | grep -q 'not found'; then
    echo "bootstrap-pi5-looper: FAIL — missing shared libraries for $SOOP_BIN" >&2
    ldd "$SOOP_BIN" | grep 'not found' || true
    exit 1
fi

log "restarting SooperLooper + JACK graph"
bash "$REPO_ROOT/scripts/sooperlooper/restart-sooperlooper.sh"

log "sl-health"
python3 "$REPO_ROOT/scripts/sooperlooper/sl-health.py"

log "done — start APC bench: mpe looper sl-bench restart"
