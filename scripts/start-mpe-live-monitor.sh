#!/usr/bin/env bash
# Start the monitor-branch gain stage — gates on MPE_LIVE_MONITOR from EnvironmentFile.
#
# The gate matters more here than for the meter: this client takes the direct
# Surge -> playback connection out of the graph once it is carrying audio, so
# starting it when nothing intends to drive it changes the audio path for no
# benefit. Off unless asked for.
set -euo pipefail

# --check answers "would this start?" and exits: 0 for yes, 1 for no. It exists
# because the test that proves this gate agrees with live_monitor.enabled() used
# to run the script for real, and the script ends in `exec` — so on any machine
# with jackd (the Pi, or a laptop mid-session) running the unit suite inserted
# the gain stage, detached Surge from playback, and was then SIGKILLed by the
# test's timeout, which is the one signal that cannot restore it. Running the
# tests could silence the instrument.
CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then
    CHECK_ONLY=1
fi

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

# One law, shared with live_monitor.enabled() in Python: lowercase, then match
# these four words. Spelled this way because the two sides disagreed at first —
# `MPE_LIVE_MONITOR=False` started the gain stage's controller and not the gain
# stage, and half a feature running is worse than either state.
case "$(printf '%s' "${MPE_LIVE_MONITOR:-0}" | tr '[:upper:]' '[:lower:]')" in
    1 | true | yes | on)
        ;;
    *)
        echo "mpe-live-monitor: disabled (MPE_LIVE_MONITOR=${MPE_LIVE_MONITOR:-0})" >&2
        [ "$CHECK_ONLY" -eq 1 ] && exit 1
        exit 0
        ;;
esac

if [ "$CHECK_ONLY" -eq 1 ]; then
    echo "mpe-live-monitor: enabled (MPE_LIVE_MONITOR=${MPE_LIVE_MONITOR:-0})"
    exit 0
fi

BIN="$MPE_MODULE_REPO/native/mpe-live-monitor/mpe-live-monitor"
if [ ! -x "$BIN" ]; then
    "$MPE_MODULE_REPO/scripts/build-mpe-live-monitor.sh" --required
fi
if [ ! -x "$BIN" ]; then
    echo "mpe-live-monitor: binary missing after build" >&2
    exit 1
fi

# The client publishes live-monitor.state here — the sensor sl-watchdog.py reads
# to decide whether Surge still reaches playback. RuntimeDirectory=mpe in the
# unit creates it.
export MPE_RUN_DIR
MPE_RUN_DIR="$(mpe_run_dir)"
exec "$BIN"
