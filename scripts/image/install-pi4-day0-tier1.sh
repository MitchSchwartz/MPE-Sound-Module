#!/usr/bin/env bash
# Pi 4 day-0 apt — JACK, build deps, touch pygame. Run ON the Pi.
# Canon sibling: scripts/install-pi5-day0-tier1.sh
#
#   ./scripts/image/install-pi4-day0-tier1.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Pi 4 day-0 Tier 1 apt ==="

if command -v debconf-set-selections >/dev/null 2>&1; then
    echo "jackd2 jackd/tweak_rt_limits boolean true" | sudo debconf-set-selections
fi

sudo apt update
sudo apt install -y \
    build-essential cmake git jackd2 alsa-utils rt-tests \
    python3-pip python3-pygame \
    libsdl2-2.0-0 libsdl2-image-2.0-0 libsdl2-mixer-2.0-0 libsdl2-ttf-2.0-0 \
    libcairo2-dev libxkbcommon-x11-dev libxkbcommon-dev \
    libxcb-cursor-dev libxcb-keysyms1-dev libxcb-util-dev \
    libxrandr-dev libxinerama-dev libxcursor-dev \
    libasound2-dev libjack-jackd2-dev libfreetype6-dev libglu1-mesa-dev \
    python3-rtmidi

PI_USER="${MPE_PI_USER:-$(id -un)}"
if ! id -nG "$PI_USER" 2>/dev/null | tr ' ' '\n' | grep -qx audio; then
    echo "Adding $PI_USER to group audio ..."
    sudo usermod -aG audio "$PI_USER"
    echo "NOTE: log out and back in (or reboot) so ulimit -r applies to $PI_USER"
fi

echo ""
echo "=== JACK RT limits file (repair path) ==="
sudo "$REPO_ROOT/scripts/install-jack-audio-limits.sh"

echo ""
echo "=== RT / JACK verification ==="
if ! "$REPO_ROOT/scripts/verify-jack-rt-limits.sh" "$PI_USER"; then
    echo ""
    echo "Fix RT limits before trusting any latency number." >&2
    exit 1
fi

echo ""
echo "=== Python requirements (OSC, etc.) ==="
if pip3 install --break-system-packages -r "$REPO_ROOT/requirements.txt" 2>/dev/null; then
    :
elif pip3 install -r "$REPO_ROOT/requirements.txt"; then
    :
else
    echo "WARNING: pip install failed — touch/OSC features may be incomplete." >&2
fi

echo ""
echo "Tier 1 complete. Next: deploy Surge binary from private assets (build-pi4-appliance.sh)."
echo "Surge source build on Pi is optional fallback only — see docs/PI4-GOLDEN-IMAGE.md."
