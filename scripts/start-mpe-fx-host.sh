#!/usr/bin/env bash
# Start the trunk effects insert — gates on MPE_FX_HOST from EnvironmentFile.
#
# Off unless asked for. Once carrying audio this client becomes the fan-out
# source (playback or the live monitor, and every loop input), so starting it
# when nothing will drive it changes the audio path for no benefit.
#
# --check answers "would this start?" and exits: 0 for yes, 1 for no, without
# reaching the binary — the same reason as start-mpe-live-monitor.sh: a test
# that ran this for real on a machine with jackd would move the trunk and then
# SIGKILL the one process that could give it back.
set -euo pipefail

CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then
    CHECK_ONLY=1
fi

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

# One law, shared with fx_host_protocol.enabled() in Python: lowercase, then
# match these four words.
case "$(printf '%s' "${MPE_FX_HOST:-0}" | tr '[:upper:]' '[:lower:]')" in
    1 | true | yes | on)
        ;;
    *)
        echo "mpe-fx-host: disabled (MPE_FX_HOST=${MPE_FX_HOST:-0})" >&2
        [ "$CHECK_ONLY" -eq 1 ] && exit 1
        exit 0
        ;;
esac

if [ "$CHECK_ONLY" -eq 1 ]; then
    echo "mpe-fx-host: enabled (MPE_FX_HOST=${MPE_FX_HOST:-0})"
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
