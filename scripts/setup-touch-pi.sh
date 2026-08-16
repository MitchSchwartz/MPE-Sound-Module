#!/bin/bash
# Prepare a SmartiPi touch Pi for the touch patch browser — run ON the Pi.
#
# Prerequisites: Pi OS Lite (Trixie), repo cloned, Surge CLI built (BUILD-FROM-ZERO steps 1–3).
#
# Usage:
#   cd ~/MPE-Module
#   git checkout feature/touch-patch-browser-ui   # until merged to main
#   echo 'MPE_UI_MODE=touch' >> config/mpe.env    # or edit by hand
#   ./scripts/setup-touch-pi.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

if [ ! -f /proc/device-tree/model ] || ! grep -qi raspberry /proc/device-tree/model 2>/dev/null; then
    echo "WARNING: this script is intended to run on a Raspberry Pi."
fi

echo "Touch Pi setup"
echo "  Repo:         $MPE_MODULE_REPO"
echo "  MPE_UI_MODE:  ${MPE_UI_MODE:-oled}"
echo ""

if [ "${MPE_UI_MODE:-oled}" != touch ]; then
    echo "ERROR: Set MPE_UI_MODE=touch in config/mpe.env (or export it) before running."
    echo "  echo 'MPE_UI_MODE=touch' >> config/mpe.env"
    exit 1
fi

echo "[1/4] Installing OS packages (pygame, SDL, pip)..."
sudo apt update
sudo apt install -y \
    python3-pygame \
    libsdl2-2.0-0 \
    libsdl2-image-2.0-0 \
    libsdl2-mixer-2.0-0 \
    libsdl2-ttf-2.0-0 \
    python3-pip

echo "[2/4] Installing Python dependencies (OSC, etc.)..."
if pip3 install --break-system-packages -r "$MPE_MODULE_REPO/requirements.txt" 2>/dev/null; then
    :
elif pip3 install -r "$MPE_MODULE_REPO/requirements.txt"; then
    :
else
    echo "ERROR: pip install failed. On Trixie try: pip3 install --break-system-packages -r requirements.txt"
    exit 1
fi

echo "[3/4] Installing udev rules (backlight, USB audio, Roli)..."
"$MPE_MODULE_REPO/scripts/install-udev-rules.sh"

echo "[4/4] Installing systemd units and enabling touch browser..."
"$MPE_MODULE_REPO/scripts/configure-pi-paths.sh" --local --force

echo ""
echo "Smoke test (optional, over SSH with desktop or HDMI debug):"
echo "  MPE_TOUCH_WINDOWED=1 $MPE_MODULE_REPO/scripts/start-touch-patch-browser.sh"
echo ""
echo "Check services:"
echo "  systemctl status surge-xt-cli touch-patch-browser"
echo ""
echo "Touch UI sudoers (once): see docs/TOUCH_PATCH_BROWSER.md"
echo "  sudo visudo  →  $(whoami) ALL=(ALL) NOPASSWD: /sbin/poweroff, /sbin/reboot, /bin/systemctl, $MPE_MODULE_REPO/scripts/set-audio-profile.sh, $MPE_MODULE_REPO/scripts/set-surge-audio.sh"
