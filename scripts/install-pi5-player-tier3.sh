#!/usr/bin/env bash
# Pi 5 player Tier 3 — touch UI + Roli MIDI deps. Run AFTER platform comparison (day 0) OR
# when building a player-only box (skip measurement confound concern).
# Canon: docs/PI5-PLAYER-SETUP-LOG.md · docs/measurements/PROMPT-PI5-DAY0.md §1a Tier 3
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== Pi 5 player Tier 3 (touch UI + MIDI) ==="

sudo apt update
sudo apt install -y \
    python3-pygame python3-pip python3-rtmidi \
    libsdl2-2.0-0 libsdl2-image-2.0-0 libsdl2-mixer-2.0-0 libsdl2-ttf-2.0-0 \
    libegl1 libegl-mesa0 libgles2 libgl1-mesa-dri mesa-vulkan-drivers

# Pi 4 uses apt python3-rtmidi — do not pip-install python-rtmidi (different backend defaults).
echo "=== pip (requirements minus python-rtmidi) ==="
grep -v '^python-rtmidi' "$REPO_ROOT/requirements.txt" | grep -v '^#' | grep -v '^$' > /tmp/mpe-requirements-no-rtmidi.txt || true
if [ -s /tmp/mpe-requirements-no-rtmidi.txt ]; then
    if pip3 install --break-system-packages -r /tmp/mpe-requirements-no-rtmidi.txt 2>/dev/null; then
        :
    else
        pip3 install -r /tmp/mpe-requirements-no-rtmidi.txt
    fi
fi

echo "OK: python3-rtmidi $(dpkg-query -W -f='${Version}' python3-rtmidi 2>/dev/null || echo '?')"
echo "Tier 3 complete. Next: setup-touch-pi.sh or configure-pi-paths.sh --local --force"
