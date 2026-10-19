#!/usr/bin/env bash
# Start the trunk insert: the stage gain, then the effects.
#
# Always, whatever MPE_FX_HOST says. The insert carries the trunk's level
# (per-patch normalization, device mode's headroom and the trim-mode Vol
# fader: patch_normalization.stage_db), so without it every patch plays as
# authored and the fader does nothing. MPE_FX_HOST gates only the effects: the
# policy, its UI and the CC 1 route (fx_host_protocol.enabled). With the flag
# off nothing sends `chain`, and the insert is the stage alone.
#
# --check answers "would this start?" and exits 0 without reaching the binary —
# the same reason as start-mpe-live-monitor.sh: a test that ran this for real on
# a machine with jackd would move the trunk and then SIGKILL the one process
# that could give it back.
set -euo pipefail

CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then
    CHECK_ONLY=1
fi

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

if [ "$CHECK_ONLY" -eq 1 ]; then
    echo "mpe-fx-host: enabled (the stage gain runs whatever MPE_FX_HOST=${MPE_FX_HOST:-0} says)"
    exit 0
fi

BIN="$MPE_MODULE_REPO/native/mpe-fx-host/mpe-fx-host"
if [ ! -x "$BIN" ]; then
    "$MPE_MODULE_REPO/scripts/build-mpe-fx-host.sh" --required
fi
if [ ! -x "$BIN" ]; then
    echo "mpe-fx-host: binary missing after build" >&2
    exit 1
fi

exec "$BIN"
