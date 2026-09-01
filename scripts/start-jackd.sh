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

echo "Starting jackd on $HW_DEV — ${JACK_BUFFER} x ${JACK_PERIODS} @ ${JACK_RATE} Hz (${SOFTMODE_LABEL})"
mpe_jack_state_write "$HW_DEV" "$JACK_BUFFER" "$JACK_PERIODS" "$JACK_RATE" \
    "${JACK_CARD_ID:-}" "${TIER:-}"

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

exec jackd -R -P"$JACK_PRIO" "${SOFTMODE_ARGS[@]}" \
    -d alsa -P "$HW_DEV" -r "$JACK_RATE" -p "$JACK_BUFFER" -n "$JACK_PERIODS"
