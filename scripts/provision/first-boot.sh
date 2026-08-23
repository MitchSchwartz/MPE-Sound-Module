#!/usr/bin/env bash
# First-boot provisioning on a freshly flashed or cloned Pi 4 image.
# Run ON the Pi once (or via flash-and-provision.sh from the laptop).
#
#   sudo ./scripts/provision/first-boot.sh
#   MPE_HOSTNAME=mpe-bench ./scripts/provision/first-boot.sh
#
# Idempotent — skips if /var/lib/mpe/first-boot.stamp exists unless --force.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
STAMP="/var/lib/mpe/first-boot.stamp"
FORCE=false

case "${1:-}" in
    --force) FORCE=true ;;
    "") ;;
    *) echo "Usage: $0 [--force]" >&2; exit 2 ;;
esac

if [ "$(id -u)" -ne 0 ] && [ "$FORCE" != true ]; then
    echo "ERROR: run as root (sudo) on the Pi." >&2
    exit 1
fi

if [ -f "$STAMP" ] && [ "$FORCE" != true ]; then
    echo "first-boot: already completed ($STAMP)"
    echo "  Re-run with --force to repeat."
    exit 0
fi

# shellcheck source=../lib/paths.sh
source "$SCRIPT_DIR/../lib/paths.sh"

APPLIANCE_USER="${MPE_PI_USER:-$(logname 2>/dev/null || echo mitch)}"
if id "$APPLIANCE_USER" &>/dev/null; then
    :
else
    echo "ERROR: user $APPLIANCE_USER does not exist." >&2
    exit 1
fi

if [ -n "${MPE_HOSTNAME:-}" ]; then
    echo "Setting hostname: $MPE_HOSTNAME"
    hostnamectl set-hostname "$MPE_HOSTNAME"
fi

echo "=== configure-pi-paths (units + /etc/mpe/mpe.env paths) ==="
sudo -u "$APPLIANCE_USER" env MPE_PI_USER="$APPLIANCE_USER" HOME="/home/$APPLIANCE_USER" \
    bash -lc "cd '$REPO_ROOT' && ./scripts/configure-pi-paths.sh --local --force"

echo "=== player env parity (Pi 4 control tuning) ==="
sudo -u "$APPLIANCE_USER" bash -lc "cd '$REPO_ROOT' && ./scripts/apply-player-env-parity.sh"

echo "=== install systemd units ==="
"$REPO_ROOT/scripts/install-units.sh"

echo "=== appliance hygiene ==="
"$REPO_ROOT/scripts/apply-appliance-hygiene.sh"

echo "=== udev rules ==="
sudo -u "$APPLIANCE_USER" bash -lc "cd '$REPO_ROOT' && ./scripts/install-udev-rules.sh" || true

echo "=== enable core services ==="
# shellcheck source=../lib/mpe-services.sh
source "$SCRIPT_DIR/../lib/mpe-services.sh"
mpe_enable_core_services

if grep -q '^MPE_PEAK_METER=1' /etc/mpe/mpe.env 2>/dev/null; then
    systemctl enable mpe-peak-meter.service 2>/dev/null || true
fi

mkdir -p /var/lib/mpe
date -Iseconds >"$STAMP"
echo "hostname=$(hostname)" >>"$STAMP"
echo "user=$APPLIANCE_USER" >>"$STAMP"
echo "repo=$REPO_ROOT" >>"$STAMP"
git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null >>"$STAMP" || true

echo ""
echo "first-boot: complete"
echo "  Stamp: $STAMP"
echo "  Optional: ./scripts/provision/apply-external-state.sh --state <capture> --local"
echo "  Reboot recommended: sudo reboot"
