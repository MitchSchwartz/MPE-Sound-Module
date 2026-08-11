#!/bin/bash
# Pull looper branch on the Pi and restart mpe-looper.service, plus the patch
# browser UI when the pull touched it (the looper HUD is drawn by that process).
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

# The looper transport and the HUD that displays it are two processes: this
# script restarts mpe-looper.service, but patch_browser/ code is loaded by the
# patch browser unit and stays on its old in-memory copy until that restarts.
_restart_ui_if_changed() {
    local before="$1"
    local after browser
    after="$(git rev-parse HEAD)"
    if [ "$before" = "$after" ]; then
        return 0
    fi
    if git diff --quiet "$before" "$after" -- patch_browser/ 2>/dev/null; then
        return 0
    fi
    browser="$(mpe_patch_browser_unit)"
    if systemctl is-active --quiet "$browser" 2>/dev/null; then
        sudo systemctl restart "$browser"
        echo "UI restarted: $browser (patch_browser/ changed)"
    else
        echo "UI not running: $browser — start it to pick up the patch_browser/ changes"
    fi
}

_deploy_on_pi() {
    local branch="$1"
    if [ ! -d "$MPE_MODULE_REPO/.git" ]; then
        echo "ERROR: not a git repo: $MPE_MODULE_REPO" >&2
        exit 1
    fi
    cd "$MPE_MODULE_REPO"
    echo "Looper deploy: fetch $branch ..."
    local before
    before="$(git rev-parse HEAD)"
    git fetch origin "$branch"
    git checkout "$branch"
    git pull origin "$branch"
    chmod +x scripts/mpe-looper.py scripts/mpe-looper-service.sh scripts/looper-deploy.sh 2>/dev/null || true

    if ! python3 -c "import audioop" 2>/dev/null && ! python3 -c "import audioop_lts" 2>/dev/null; then
        echo "Installing audioop-lts (Python 3.13+ mixer backport) ..."
        python3 -m pip install --user --break-system-packages 'audioop-lts>=0.2.1'
    fi

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

    _restart_ui_if_changed "$before"
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    cat <<EOF
Usage: $0 [branch]

Default branch: yolo/looper-phase0
Pull on Pi and restart mpe-looper.service when MPE_LOOPER_ENABLED=1.
Also restarts the patch browser UI unit when the pull changed patch_browser/.
EOF
    exit 0
fi

if _is_pi; then
    _deploy_on_pi "$BRANCH"
else
    echo "Looper deploy → $PI_USER@$PI_HOST ($BRANCH)"
    mpe_pi_ssh "cd ~/MPE-Module && ./scripts/looper-deploy.sh $(printf '%q' "$BRANCH")"
fi
