#!/usr/bin/env bash
# Remove per-device identity and secrets before golden imaging or clone export.
# Run ON the Pi as root (sudo).
#
#   sudo ./scripts/provision/sanitize-for-clone.sh [--dry-run]
#   sudo ./scripts/provision/sanitize-for-clone.sh --verify
#
# Removes: machine-id (+ dbus copy when not symlinked), SSH host keys, Tailscale
# credentials, NetworkManager WiFi profiles (PSKs), shell history, first-boot stamp.
# Does NOT remove ~/.ssh/authorized_keys — add --strip-authorized-keys for a blank SSH slate.
#
# --verify: assert clone-safe properties; exit 1 if anything forbidden remains.
#           Does not modify the system.
#
# See docs/PI4-GOLDEN-IMAGE.md § "Never in an image"

set -euo pipefail

DRY=false
VERIFY=false
STRIP_AUTHORIZED_KEYS=false

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=true; shift ;;
        --verify) VERIFY=true; shift ;;
        --strip-authorized-keys) STRIP_AUTHORIZED_KEYS=true; shift ;;
        *) echo "Usage: $0 [--dry-run] [--verify] [--strip-authorized-keys]" >&2; exit 2 ;;
    esac
done

if [ "$VERIFY" = true ] && [ "$DRY" = true ]; then
    echo "ERROR: --verify and --dry-run are mutually exclusive." >&2
    exit 2
fi

if [ "$(id -u)" -ne 0 ] && [ "$VERIFY" = false ]; then
    echo "ERROR: run as root (sudo)." >&2
    exit 1
fi

FAIL=0

_verify_fail() {
    echo "VERIFY FAIL: $*" >&2
    FAIL=1
}

_run() {
    if [ "$DRY" = true ]; then
        echo "would: $*"
    else
        "$@"
    fi
}

_sanitize_machine_ids() {
    echo "--- machine-id ---"
    _run truncate -s 0 /etc/machine-id
    if [ -L /var/lib/dbus/machine-id ]; then
        echo "  /var/lib/dbus/machine-id → symlink (etc/machine-id is authoritative)"
    elif [ -f /var/lib/dbus/machine-id ]; then
        echo "  /var/lib/dbus/machine-id is a regular file — truncating"
        _run truncate -s 0 /var/lib/dbus/machine-id
    fi
}

_sanitize_ssh_host_keys() {
    echo "--- SSH host keys (regenerated on next boot) ---"
    _run rm -f /etc/ssh/ssh_host_*
}

_sanitize_authorized_keys() {
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
}

_sanitize_tailscale() {
    echo "--- Tailscale node credentials (never ship in an image) ---"
    if [ "$DRY" = false ] && [ "$VERIFY" = false ]; then
        if command -v tailscale >/dev/null 2>&1; then
            tailscale logout 2>/dev/null || true
            systemctl stop tailscaled 2>/dev/null || true
        fi
    elif [ "$DRY" = true ]; then
        echo "would: tailscale logout (if logged in)"
        echo "would: systemctl stop tailscaled"
    fi
    if [ -d /var/lib/tailscale ] && [ "$VERIFY" = false ]; then
        _run rm -rf /var/lib/tailscale/*
    fi
}

_sanitize_wifi_profiles() {
    echo "--- NetworkManager WiFi profiles (contain PSKs) ---"
    local nm_dir="/etc/NetworkManager/system-connections"
    if [ -d "$nm_dir" ]; then
        if [ "$VERIFY" = true ]; then
            :
        else
            _run rm -f "$nm_dir"/*.nmconnection 2>/dev/null || true
            _run rm -f "$nm_dir"/* 2>/dev/null || true
        fi
    else
        echo "  (no $nm_dir — skip)"
    fi
}

_sanitize_shell_history() {
    echo "--- shell history ---"
    for home in /root /home/*; do
        [ -d "$home" ] || continue
        for hist in .bash_history .zsh_history; do
            local f="$home/$hist"
            if [ -f "$f" ]; then
                if [ "$VERIFY" = true ]; then
                    :
                else
                    _run truncate -s 0 "$f"
                fi
            fi
        done
    done
}

_sanitize_first_boot_stamp() {
    echo "--- MPE first-boot stamp ---"
    _run rm -f /var/lib/mpe/first-boot.stamp
}

_verify() {
    echo "=== sanitize-for-clone --verify ==="

    if [ -s /etc/machine-id ]; then
        _verify_fail "/etc/machine-id is non-empty"
    fi
    if [ -f /var/lib/dbus/machine-id ] && [ ! -L /var/lib/dbus/machine-id ] && [ -s /var/lib/dbus/machine-id ]; then
        _verify_fail "/var/lib/dbus/machine-id is a non-empty regular file (not symlinked to /etc/machine-id)"
    fi

    if compgen -G '/etc/ssh/ssh_host_*' >/dev/null 2>&1; then
        _verify_fail "SSH host keys still present under /etc/ssh/"
    fi

    if [ -d /var/lib/tailscale ] && [ -n "$(ls -A /var/lib/tailscale 2>/dev/null || true)" ]; then
        _verify_fail "/var/lib/tailscale/ is not empty"
    fi

    local nm_dir="/etc/NetworkManager/system-connections"
    if [ -d "$nm_dir" ] && [ -n "$(ls -A "$nm_dir" 2>/dev/null || true)" ]; then
        _verify_fail "NetworkManager profiles remain in $nm_dir (WiFi PSKs)"
    fi

    for home in /root /home/*; do
        [ -d "$home" ] || continue
        for hist in .bash_history .zsh_history; do
            local f="$home/$hist"
            if [ -s "$f" ]; then
                _verify_fail "non-empty shell history: $f"
            fi
        done
    done

    if [ -f /var/lib/mpe/first-boot.stamp ]; then
        _verify_fail "first-boot stamp still present"
    fi

    if [ "$FAIL" -eq 0 ]; then
        echo "sanitize-for-clone --verify: ok (clone-safe)"
        exit 0
    fi
    echo "sanitize-for-clone --verify: FAILED ($FAIL check(s))" >&2
    exit 1
}

if [ "$VERIFY" = true ]; then
    _verify
fi

echo "=== sanitize-for-clone ==="

_sanitize_machine_ids
_sanitize_ssh_host_keys
_sanitize_authorized_keys
_sanitize_tailscale
_sanitize_wifi_profiles
_sanitize_shell_history
_sanitize_first_boot_stamp

echo ""
echo "sanitize-for-clone: done"
echo "  Tailscale: run 'sudo tailscale up' on each new unit after flash."
echo "  WiFi: reconfigure per site (profiles were stripped)."
echo "  SSH: add your laptop public key to ~/.ssh/authorized_keys on each new unit."
echo "  Run 'sudo $0 --verify' before imaging to confirm clone-safe state."
