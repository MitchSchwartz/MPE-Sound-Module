#!/usr/bin/env bash
# Install MIDI clock systemd units (looper-as-master default; Pi-as-master optional).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
MPE_MODULE_REPO="${MPE_MODULE_REPO:-$REPO_ROOT}"
MPE_PI_USER="${MPE_PI_USER:-$USER}"

chmod +x "$REPO_ROOT/scripts/midi-clock-in.py" "$REPO_ROOT/scripts/midi-clock-out.py"

install_unit() {
    local name="$1"
    sed -e "s|@MPE_MODULE_REPO@|${MPE_MODULE_REPO}|g" \
        -e "s|@MPE_PI_USER@|${MPE_PI_USER:-$USER}|g" \
        "$REPO_ROOT/config/${name}.service" | sudo tee "/etc/systemd/system/${name}.service" >/dev/null
}

install_unit midi-clock-in
install_unit midi-clock-out
sudo systemctl daemon-reload

if [ "${MPE_MIDI_CLOCK_IN_ENABLED:-1}" = "1" ]; then
    sudo systemctl enable --now midi-clock-in.service
    echo "midi-clock-in.service enabled (looper as master)."
else
    sudo systemctl disable --now midi-clock-in.service 2>/dev/null || true
    echo "midi-clock-in.service installed but disabled."
fi

if [ "${MPE_MIDI_CLOCK_OUT_ENABLED:-0}" = "1" ]; then
    sudo systemctl enable --now midi-clock-out.service
    echo "midi-clock-out.service enabled (Pi as master)."
else
    sudo systemctl disable --now midi-clock-out.service 2>/dev/null || true
    echo "midi-clock-out.service installed but disabled."
fi

echo ""
echo "Useful commands:"
echo "  List IN ports:  $REPO_ROOT/scripts/midi-clock-in.py --list-ports"
echo "  List OUT ports: $REPO_ROOT/scripts/midi-clock-out.py --list-ports"
echo "  Logs (in):      journalctl -u midi-clock-in -f"
echo "  Logs (out):     journalctl -u midi-clock-out -f"
