#!/bin/bash
# Pull looper branch on the Pi and restart mpe-looper.service.
#
# On Pi:   ./scripts/looper-deploy.sh [branch]
# Laptop:  ./scripts/looper-deploy.sh [branch]   (SSH to PI_HOST from config/mpe.env)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"

BRANCH="${1:-yolo/looper-phase0}"

_is_pi() {
    [ -f /proc/device-tree/model ] && grep -qi raspberry /proc/device-tree/model 2>/dev/null
}

_deploy_on_pi() {
    local branch="$1"
    if [ ! -d "$MPE_MODULE_REPO/.git" ]; then
        echo "ERROR: not a git repo: $MPE_MODULE_REPO" >&2
        exit 1
    fi
    cd "$MPE_MODULE_REPO"
    echo "Looper deploy: fetch $branch ..."
    git fetch origin "$branch"
    git checkout "$branch"
    git pull origin "$branch"
    chmod +x scripts/mpe-looper.py scripts/mpe-looper-service.sh scripts/looper-deploy.sh 2>/dev/null || true

    if [ ! -f /etc/systemd/system/mpe-looper.service ]; then
        ./scripts/install-mpe-looper-service.sh
    fi

    mpe_source_appliance_env
    if [ "${MPE_LOOPER_ENABLED:-0}" = "1" ]; then
        sudo systemctl enable mpe-looper.service 2>/dev/null || true
        sudo systemctl restart mpe-looper.service
        echo "Looper restarted: $(git rev-parse --short HEAD) — journalctl -u mpe-looper -f"
    else
        sudo systemctl stop mpe-looper.service 2>/dev/null || true
        pkill -f 'scripts/mpe-looper.py' 2>/dev/null || true
        echo "Deployed $(git rev-parse --short HEAD) — MPE_LOOPER_ENABLED=0 (run looper-audio-route.sh on)"
    fi
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    cat <<EOF
Usage: $0 [branch]

Default branch: yolo/looper-phase0
Pull on Pi and restart mpe-looper.service when MPE_LOOPER_ENABLED=1.
EOF
    exit 0
fi

if _is_pi; then
    _deploy_on_pi "$BRANCH"
else
    echo "Looper deploy → $PI_USER@$PI_HOST ($BRANCH)"
    mpe_pi_ssh "cd '${PI_MPE_MODULE:-\$HOME/MPE-Module}' && ./scripts/looper-deploy.sh '$BRANCH'"
fi
