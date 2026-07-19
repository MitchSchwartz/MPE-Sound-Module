#!/bin/bash
# Start Patch Browser UI — stops boot animation, runs patch_browser_ui.py

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

if systemctl is-active --quiet boot-animation.service; then
    echo "Stopping boot animation..."
    sudo systemctl stop boot-animation.service
fi

sleep 0.5

echo "Starting patch browser UI..."
cd "$MPE_MODULE_REPO"
python3 -u "$MPE_MODULE_REPO/patch_browser_ui.py"
