#!/usr/bin/env bash
# Pi 5 day-0 Tier 1 apt — build + JACK smoke path. No touch UI (Tier 3).
# Canon: docs/measurements/PROMPT-PI5-DAY0.md §1a
#
# Run ON the Pi (Mitch gate: sudo apt). Idempotent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== Pi 5 day-0 Tier 1 apt ==="

# jackd2 RT limits — non-interactive "yes" (auto-no silently breaks latency)
if command -v debconf-set-selections >/dev/null 2>&1; then
    echo "jackd2 jackd/tweak_rt_limits boolean true" | sudo debconf-set-selections
fi

sudo apt update
sudo apt install -y \
    build-essential cmake git jackd2 alsa-utils rt-tests \
    libcairo2-dev libxkbcommon-x11-dev libxkbcommon-dev \
    libxcb-cursor-dev libxcb-keysyms1-dev libxcb-util-dev \
    libxrandr-dev libxinerama-dev libxcursor-dev \
    libasound2-dev libjack-jackd2-dev libfreetype6-dev libglu1-mesa-dev

PI_USER="${MPE_PI_USER:-$(id -un)}"
if ! id -nG "$PI_USER" 2>/dev/null | tr ' ' '\n' | grep -qx audio; then
    echo "Adding $PI_USER to group audio ..."
    sudo usermod -aG audio "$PI_USER"
    echo "NOTE: log out and back in (or reboot) so ulimit -r applies to $PI_USER"
fi

echo ""
echo "=== RT / JACK verification ==="
if ! "$REPO_ROOT/scripts/verify-jack-rt-limits.sh" "$PI_USER"; then
    echo ""
    echo "Fix RT limits before trusting any latency number." >&2
    exit 1
fi

echo ""
echo "=== Memory (swap guidance) ==="
free -h
total_mb="$(free -m | awk '/^Mem:/{print $2}')"
if [ "${total_mb:-0}" -ge 7000 ] 2>/dev/null; then
    echo "OK: ${total_mb}MB RAM — skip swap for Surge build on 8GB Pi 5"
else
    echo "NOTE: ${total_mb}MB RAM — see SURGE_ARM_BUILD.md swap section if build OOMs"
fi

echo ""
echo "Tier 1 complete. Next (day 0): scripts/build-surge.sh --arch a76"
echo "Instruments (Tier 2): scripts/build-mpe-peak-meter.sh --required && scripts/build-mpe-xrun-probe.sh"
