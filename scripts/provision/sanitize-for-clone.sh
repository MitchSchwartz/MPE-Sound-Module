#!/usr/bin/env bash
# Remove per-device identity before golden imaging or clone export.
# Run ON the Pi as root (sudo).
#
#   sudo ./scripts/provision/sanitize-for-clone.sh [--dry-run]
#
# Removes: machine-id, SSH host keys, Tailscale node credentials, first-boot stamp.
# Does NOT remove ~/.ssh/authorized_keys — add that with --strip-authorized-keys if
# the image must boot with zero SSH access until Imager keys are re-applied.
#
# See docs/PI4-GOLDEN-IMAGE.md § "Never in an image"

set -euo pipefail

DRY=false
STRIP_AUTHORIZED_KEYS=false

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=true; shift ;;
        --strip-authorized-keys) STRIP_AUTHORIZED_KEYS=true; shift ;;
        *) echo "Usage: $0 [--dry-run] [--strip-authorized-keys]" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run as root (sudo)." >&2
    exit 1
fi

_run() {
    if [ "$DRY" = true ]; then
        echo "would: $*"
    else
        "$@"
    fi
}

echo "=== sanitize-for-clone ==="

echo "--- machine-id ---"
_run truncate -s 0 /etc/machine-id

echo "--- SSH host keys (regenerated on next boot) ---"
_run rm -f /etc/ssh/ssh_host_*

if [ "$STRIP_AUTHORIZED_KEYS" = true ]; then
    echo "--- authorized_keys (optional) ---"
    for home in /home/*; do
        [ -d "$home" ] || continue
        ak="$home/.ssh/authorized_keys"
        if [ -f "$ak" ]; then
            _run rm -f "$ak"
        fi
    done
    _run rm -f /root/.ssh/authorized_keys
fi

echo "--- Tailscale node credentials (never ship in an image) ---"
if command -v tailscale >/dev/null 2>&1; then
    if [ "$DRY" = true ]; then
        echo "would: tailscale logout (if logged in)"
        echo "would: systemctl stop tailscaled"
    else
        tailscale logout 2>/dev/null || true
        systemctl stop tailscaled 2>/dev/null || true
    fi
else
    echo "  tailscale CLI not installed — skip logout"
fi
_run rm -rf /var/lib/tailscale/*
# Legacy/alternate paths seen on some installs
_run rm -f /var/lib/tailscale/tailscaled.state
_run rm -rf /var/lib/tailscale/*

echo "--- MPE first-boot stamp ---"
_run rm -f /var/lib/mpe/first-boot.stamp

echo ""
echo "sanitize-for-clone: done"
echo "  Tailscale: run 'sudo tailscale up' on each new unit after flash."
echo "  SSH: add your laptop public key to ~/.ssh/authorized_keys on each new unit."
