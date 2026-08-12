#!/bin/bash
# systemd ExecStart — run grid looper when MPE_LOOPER_ENABLED=1.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"

mpe_source_appliance_env

if [ "${MPE_LOOPER_ENABLED:-0}" != "1" ]; then
    echo "mpe-looper: MPE_LOOPER_ENABLED=0 — not starting"
    exit 0
fi

if [ "${MPE_AUDIO_PROFILE:-standalone}" != "standalone" ]; then
    echo "mpe-looper: standalone profile only (got ${MPE_AUDIO_PROFILE})" >&2
    exit 1
fi

cd "$MPE_MODULE_REPO"

export MPE_LOOPER_SERVICE=1

# Optional SCHED_FIFO for the looper. Off by default so appliances are unchanged.
# The mix loop blocks on the arecord pipe rather than spinning, so FIFO here is
# well behaved — but keep the priority modest and below Surge's: a runaway FIFO
# process can starve the touch UI and network stack. LimitRTPRIO in
# mpe-looper.service is what permits this without root.
LOOPER_LAUNCH_PREFIX=()
case "${MPE_LOOPER_RT_PRIORITY:-0}" in
    '' | 0) ;;
    *[!0-9]*)
        echo "mpe-looper: WARNING: MPE_LOOPER_RT_PRIORITY not a number — ignoring" >&2
        ;;
    *)
        if command -v chrt > /dev/null 2>&1; then
            LOOPER_LAUNCH_PREFIX=(chrt --fifo "$MPE_LOOPER_RT_PRIORITY")
            echo "mpe-looper: SCHED_FIFO priority $MPE_LOOPER_RT_PRIORITY"
        else
            echo "mpe-looper: WARNING: chrt not found — staying SCHED_OTHER" >&2
        fi
        ;;
esac

exec "${LOOPER_LAUNCH_PREFIX[@]}" python3 "$MPE_MODULE_REPO/scripts/mpe-looper.py"
