#!/bin/bash
# Install templated udev rules — substitutes @MPE_MODULE_REPO@ in every rule.
#
# Called by configure-pi-paths.sh, deploy-all.sh, and setup-touch-pi.sh so no
# installer can clobber a correctly-templated rule with a literal placeholder.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

_install_one() {
    local rule="$1"
    local dest="/etc/udev/rules.d/$(basename "$rule")"
    if grep -q '@MPE_MODULE_REPO@' "$rule" 2>/dev/null; then
        sed "s|@MPE_MODULE_REPO@|$MPE_MODULE_REPO|g" "$rule" | sudo tee "$dest" > /dev/null
    else
        sudo cp "$rule" "$dest"
    fi
    echo "  ✓ $(basename "$rule")"
}

echo "Installing udev rules (MPE_MODULE_REPO=$MPE_MODULE_REPO)..."
for rule in "$MPE_MODULE_REPO/config/99-backlight-permissions.rules" \
            "$MPE_MODULE_REPO/config/99-usb-audio.rules" \
            "$MPE_MODULE_REPO/config/99-roli-seaboard.rules"; do
    if [ -f "$rule" ]; then
        _install_one "$rule"
    fi
done
sudo udevadm control --reload-rules
sudo udevadm trigger
