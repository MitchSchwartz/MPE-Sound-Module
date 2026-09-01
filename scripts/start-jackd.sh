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

REQUESTED_BUFFER="$JACK_BUFFER"

# Binding a virtual card is a legitimate state (usb-host idle, no DAC yet) and an
# inaudible one. Say so once, loudly, at the moment it happens -- otherwise the
# only difference between this and a working instrument is that no sound comes
# out, which is not a diagnostic anyone can act on at a gig.
if [ -n "${JACK_CARD_ID:-}" ] && mpe_card_is_virtual "$JACK_CARD_ID"; then
    echo "WARNING: bound '$JACK_CARD_ID' (tier ${TIER:-unknown}) — this is the idle sink." \
         "NOTHING WILL BE AUDIBLE until a real DAC is connected."
fi
# Do not clobber Surge's ok/failed — only publish recovering when nothing more
# specific is already published.
current_state="$(mpe_engine_state_get state)"
case "$current_state" in
    ok | failed | recovering) ;;
    *)
        mpe_engine_state_write "$MPE_ENGINE_NAME" none recovering jackd-starting "$(mpe_looper_state_label)"
        ;;
esac

# --- the period ladder -------------------------------------------------------
#
# jackd stays ALIVE when its driver thread fails to start, so an exit code
# proves nothing. MEASURED 2026-09-01 on the Apple full-speed dongle at -p 64:
#
#   configuring for 48000Hz, period = 64 frames (1.3 ms)
#   JackPosixProcessSync::LockedTimedWait error usec = 5000000
#   Driver is not running / Cannot create new client
#
# systemd reported the unit active, engine.state read ok, and Surge retried
# forever against a server that could never accept it. The only honest probe is
# to do what a client does: connect, and require the driver's own ports.
JACK_PROBE_TIMEOUT="${MPE_JACK_PROBE_TIMEOUT:-12}"

_driver_is_running() {
    local deadline=$(( SECONDS + JACK_PROBE_TIMEOUT )) out
    # No probe tool => no opinion. Assume the driver is fine rather than tearing
    # down a graph that may be perfectly healthy: a broken instrument must not
    # be able to manufacture a failure it cannot actually observe.
    if ! command -v jack_lsp >/dev/null 2>&1; then
        echo "WARNING: jack_lsp not found — cannot verify the driver started;" \
             "period fallback is disabled for this run." >&2
        sleep 2
        kill -0 "$JACKD_PID" 2>/dev/null && return 0
        return 1
    fi
    while [ "$SECONDS" -lt "$deadline" ]; do
        kill -0 "$JACKD_PID" 2>/dev/null || return 1   # died outright
        # jack_lsp blocks ~5s against a dead driver, so this doubles as the wait.
        out="$(jack_lsp 2>/dev/null || true)"
        case "$out" in *system:playback_*) return 0 ;; esac
        sleep 1
    done
    return 1
}

_stop_jackd() {
    [ -n "${JACKD_PID:-}" ] || return 0
    kill -TERM "$JACKD_PID" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
        kill -0 "$JACKD_PID" 2>/dev/null || return 0
        sleep 1
    done
    kill -KILL "$JACKD_PID" 2>/dev/null || true
    wait "$JACKD_PID" 2>/dev/null || true
}

JACKD_PID=""
trap '_stop_jackd' TERM INT

while IFS= read -r CANDIDATE; do
    [ -n "$CANDIDATE" ] || continue
    echo "Starting jackd on $HW_DEV — ${CANDIDATE} x ${JACK_PERIODS} @ ${JACK_RATE} Hz (${SOFTMODE_LABEL})"

    jackd -R -P"$JACK_PRIO" "${SOFTMODE_ARGS[@]}" \
        -d alsa -P "$HW_DEV" -r "$JACK_RATE" -p "$CANDIDATE" -n "$JACK_PERIODS" &
    JACKD_PID=$!

    if _driver_is_running; then
        if [ "$CANDIDATE" != "$REQUESTED_BUFFER" ]; then
            # Loud, once, naming both numbers. A period the player did not choose
            # is latency they will feel and be unable to account for.
            echo "WARNING: ${REQUESTED_BUFFER} x ${JACK_PERIODS} would not start a driver on" \
                 "'${JACK_CARD_ID:-$HW_DEV}' — running at ${CANDIDATE} instead." \
                 "Latency is higher than configured. This DAC cannot sustain ${REQUESTED_BUFFER}."
        fi
        mpe_jack_state_write "$HW_DEV" "$CANDIDATE" "$JACK_PERIODS" "$JACK_RATE" \
            "${JACK_CARD_ID:-}" "${TIER:-}" "$REQUESTED_BUFFER"
        # `set -e` would abort on a non-zero wait before we could report it.
        _rc=0; wait "$JACKD_PID" || _rc=$?
        exit "$_rc"
    fi

    echo "WARNING: no driver at ${CANDIDATE} x ${JACK_PERIODS} on '${JACK_CARD_ID:-$HW_DEV}'" \
         "after ${JACK_PROBE_TIMEOUT}s — jackd was up but no client could attach." >&2
    _stop_jackd
    JACKD_PID=""
done < <(mpe_jack_fallback_ladder "$JACK_BUFFER")

echo "ERROR: no period in the ladder started a driver on '${JACK_CARD_ID:-$HW_DEV}'." \
     "Tried: $(mpe_jack_fallback_ladder "$JACK_BUFFER" | tr '\n' ' ')" >&2
mpe_engine_state_write "$MPE_ENGINE_NAME" none failed no-driver "$(mpe_looper_state_label)"
exit 1
