#!/bin/bash
# Start Touch Patch Browser — fullscreen pygame UI for 5" DSI displays

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

# Prefer KMS/DRM when running without a desktop session (typical Pi appliance setup).
if [ -z "${SDL_VIDEODRIVER:-}" ] && [ -z "${DISPLAY:-}" ]; then
    export SDL_VIDEODRIVER=kmsdrm
fi

# Windowed mode for development on a PC or Pi with desktop:
#   MPE_TOUCH_WINDOWED=1 ./scripts/start-touch-patch-browser.sh

if systemctl is-active --quiet boot-animation.service 2>/dev/null; then
    echo "Stopping boot animation..."
    sudo systemctl stop boot-animation.service
fi

if systemctl is-active --quiet patch-browser.service 2>/dev/null; then
    echo "Stopping OLED patch browser (touch build uses a separate service)..."
    sudo systemctl stop patch-browser.service
fi

sleep 0.5

echo "Starting touch patch browser..."
cd "$MPE_MODULE_REPO"
python3 -u "$MPE_MODULE_REPO/touch_patch_browser.py"
