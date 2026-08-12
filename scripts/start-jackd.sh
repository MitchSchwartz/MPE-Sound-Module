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

echo "Starting jackd on $HW_DEV — ${JACK_BUFFER} x ${JACK_PERIODS} @ ${JACK_RATE} Hz (softmode)"
mpe_jack_state_write "$HW_DEV" "$JACK_BUFFER" "$JACK_PERIODS" "$JACK_RATE"
# Do not clobber Surge's degraded/ok — only publish recovering when nothing
# more specific is already published (e.g. Surge fell back to ALSA at boot).
current_state="$(mpe_engine_state_get state)"
case "$current_state" in
    ok | degraded | failed | recovering) ;;
    *)
        mpe_engine_state_write jack none recovering jackd-starting "$(mpe_looper_state_label)"
        ;;
esac

exec jackd -R -P"$JACK_PRIO" -s \
    -d alsa -d "$HW_DEV" -r "$JACK_RATE" -p "$JACK_BUFFER" -n "$JACK_PERIODS"
