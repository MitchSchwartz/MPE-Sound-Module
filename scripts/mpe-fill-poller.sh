#!/bin/bash
# Instrument 3 — ALSA playback fill telemetry (W1 / permanent harness).
#
# One persistent process; no per-sample subprocess forks. Logs raw pointers only;
# convert to fill_frames / ms in summarize-fill-trace.sh.
#
# Usage:
#   taskset -c 1 nice -n 19 ./scripts/mpe-fill-poller.sh STATUS_FILE LOG SECONDS
#
# Poll rate: 10 Hz (sleep 0.1). Pin/off-audio-core per PROMPT-W1-instrumented-window.md.

set -euo pipefail

STATUS_FILE="${1:?status file required}"
LOG="${2:?log path required}"
DURATION="${3:?duration seconds required}"

if [ ! -r "$STATUS_FILE" ]; then
    echo "mpe-fill-poller: unreadable $STATUS_FILE" >&2
    exit 1
fi

: >"$LOG"
printf 'FILL_POLL_START status=%s duration=%ss hz=10\n' "$STATUS_FILE" "$DURATION" >>"$LOG"

end=$((SECONDS + DURATION))
while [ "$SECONDS" -lt "$end" ]; do
    ts="${EPOCHREALTIME:-$SECONDS}"
    state=""
    appl=""
    hw=""
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            state:*)
                state="${line#state:}"
                state="${state#"${state%%[![:space:]]*}"}"
                ;;
            *appl_ptr*)
                appl="${line##*:}"
                appl="${appl#"${appl%%[![:space:]]*}"}"
                ;;
            *hw_ptr*)
                hw="${line##*:}"
                hw="${hw#"${hw%%[![:space:]]*}"}"
                ;;
        esac
    done <"$STATUS_FILE"
    printf '%s %s %s %s\n' "$ts" "$state" "$appl" "$hw" >>"$LOG"
    sleep 0.1
done

printf 'FILL_POLL_END\n' >>"$LOG"
