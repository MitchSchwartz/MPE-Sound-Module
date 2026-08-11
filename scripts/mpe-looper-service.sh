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
exec python3 "$MPE_MODULE_REPO/scripts/mpe-looper.py"
