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

log "configuring looper env (stop-then-weld, 16 loops, scratch 15)"
_remove_env_key MPE_SL_TAIL_MODE
_ensure_env_kv MPE_SL_LOOPS 16
_ensure_env_kv MPE_SL_TAIL_CAPTURE 1
_ensure_env_kv MPE_SL_SEAM_WELD 1
_ensure_env_kv MPE_SL_SCRATCH_LOOP 15
_ensure_env_kv MPE_SL_SEAM_MERGE_SAMPLES 2048

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
