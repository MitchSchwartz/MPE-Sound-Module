#!/bin/bash
# Wrapper for mpe-shutdown-splash.service — honors MPE_SHUTDOWN_SKIP_SPLASH.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
cd "$MPE_MODULE_REPO"

if [ -f /etc/mpe/mpe.env ]; then
    # shellcheck disable=SC1091
    set -a
    source /etc/mpe/mpe.env
    set +a
fi

case "${MPE_SHUTDOWN_SKIP_SPLASH:-}" in
    1|true|yes|TRUE|YES)
        exec /usr/bin/python3 -u -c "
from patch_browser.shutdown_trace import log_shutdown_event
log_shutdown_event('shutdown_splash_unit_skipped', reason='MPE_SHUTDOWN_SKIP_SPLASH')
"
        ;;
esac

exec /usr/bin/python3 -u "$MPE_MODULE_REPO/touch_shutdown_splash.py" --hold "$@"
