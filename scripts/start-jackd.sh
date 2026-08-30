#!/bin/bash
# ExecStart for mpe-jackd.service — jackd2 on tier-selected DAC (spec D1, D6).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

DEVICE_FILE="${MPE_JACK_DEVICE_FILE:-$(mpe_run_dir)/jack-device}"
if [ ! -f "$DEVICE_FILE" ]; then
    echo "ERROR: missing $DEVICE_FILE — jackd-prestart must run first" >&2
    exit 1
fi

# shellcheck disable=SC1090
source "$DEVICE_FILE"
HW_DEV="${JACK_DEVICE:?JACK_DEVICE missing from $DEVICE_FILE}"

JACK_BUFFER="$(mpe_jack_period)"
JACK_PERIODS="$(mpe_jack_periods)"
JACK_RATE="$(mpe_jack_rate)"
JACK_PRIO="$(mpe_jack_rt_priority)"

# Idle-sink period floor.
#
# The snd-aloop idle sink cannot run the appliance's configured 64-frame period.
# MEASURED 2026-08-30, three attempts each, waiting 7s for the driver thread:
#
#     -p 64   -> "LockedTimedWait ... Connection timed out / Driver is not
#                 running", every time; jackd stays up but no client can attach
#     -p 128  -> driver runs, clients attach
#     -p 192  -> driver runs, clients attach
#
# So on a Pi 5 with no DAC the choice was a 64-frame period and NO audio graph
# at all, versus a 128-frame period on a sink whose output is inaudible by
# definition. The floor applies ONLY while bound to the loopback: the moment a
# real DAC appears, restart-audio-graph.sh sees the desired card differ from the
# bound one and restarts the graph, and this branch does not run.
#
# It is loud on purpose. A silently-doubled period is a latency change the
# player feels and cannot account for, and it is exactly the kind of reading
# that looks identical whether it was intended or not.
MPE_IDLE_SINK_MIN_PERIOD="${MPE_IDLE_SINK_MIN_PERIOD:-128}"
if [ "${JACK_CARD_ID:-}" = "Loopback" ] && [ "$JACK_BUFFER" -lt "$MPE_IDLE_SINK_MIN_PERIOD" ]; then
    echo "start-jackd: idle sink (Loopback) cannot run ${JACK_BUFFER} frames — raising to ${MPE_IDLE_SINK_MIN_PERIOD} for this binding only" >&2
    JACK_BUFFER="$MPE_IDLE_SINK_MIN_PERIOD"
fi

if ! command -v jackd >/dev/null 2>&1; then
    echo "ERROR: jackd not installed (apt install jackd2)" >&2
    exit 1
fi

SOFTMODE_ARGS=()
SOFTMODE_LABEL="strict — clients that miss the deadline get zombified"
if mpe_jack_softmode_enabled; then
    SOFTMODE_ARGS=(-s)
    SOFTMODE_LABEL="softmode"
fi

echo "Starting jackd on $HW_DEV — ${JACK_BUFFER} x ${JACK_PERIODS} @ ${JACK_RATE} Hz (${SOFTMODE_LABEL})"
mpe_jack_state_write "$HW_DEV" "$JACK_BUFFER" "$JACK_PERIODS" "$JACK_RATE"
# Do not clobber Surge's ok/failed — only publish recovering when nothing more
# specific is already published.
current_state="$(mpe_engine_state_get state)"
case "$current_state" in
    ok | failed | recovering) ;;
    *)
        mpe_engine_state_write "$MPE_ENGINE_NAME" none recovering jackd-starting "$(mpe_looper_state_label)"
        ;;
esac

exec jackd -R -P"$JACK_PRIO" "${SOFTMODE_ARGS[@]}" \
    -d alsa -P "$HW_DEV" -r "$JACK_RATE" -p "$JACK_BUFFER" -n "$JACK_PERIODS"
