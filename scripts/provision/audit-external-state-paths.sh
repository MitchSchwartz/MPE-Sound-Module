#!/usr/bin/env bash
# Audit external-state-paths.list against files on the appliance.
#
#   ./scripts/provision/audit-external-state-paths.sh --local
#   ./scripts/provision/audit-external-state-paths.sh
#   MPE_CLI_CONFIG=~/.config/mpe/mpe.env.pi5 ./scripts/provision/audit-external-state-paths.sh
#
# Reports: listed paths missing on Pi, home files matching .patch_browser* not in list,
# systemd drop-ins present but not captured (informational).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PATHS_LIST="${MPE_EXTERNAL_STATE_PATHS_LIST:-$REPO_ROOT/config/platform/external-state-paths.list}"

LOCAL=false

while [ $# -gt 0 ]; do
    case "$1" in
        --local) LOCAL=true; shift ;;
        -h|--help)
            echo "Usage: $0 [--local]" >&2
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

_read_paths() {
    grep -v '^#' "$PATHS_LIST" | grep -v '^[[:space:]]*$' || true
}

_audit_on_appliance() {
    local rel remote home listed missing=0 extra=0
    # shellcheck source=../lib/paths.sh
    source "$SCRIPT_DIR/../lib/paths.sh"
    mpe_apply_pi_home
    home="${MPE_HOME:-$HOME}"

    echo "=== external-state-paths.list audit ==="
    echo "Home: $home"
    echo ""

    echo "--- Listed paths ---"
    while IFS= read -r rel; do
        [ -n "$rel" ] || continue
        if [[ "$rel" == /* ]]; then
            remote="$rel"
        else
            remote="$home/$rel"
        fi
        if [ -e "$remote" ]; then
            echo "  ok: $rel"
        else
            echo "  MISSING: $rel"
            missing=$((missing + 1))
        fi
    done < <(_read_paths)

    echo ""
    echo "--- .patch_browser* in home (not in list) ---"
    shopt -s nullglob
    for f in "$home"/.patch_browser*; do
        rel="${f#"$home"/}"
        if grep -Fxq "$rel" <(_read_paths); then
            continue
        fi
        echo "  EXTRA: $rel"
        extra=$((extra + 1))
    done
    shopt -u nullglob
    if [ "$extra" -eq 0 ]; then
        echo "  (none)"
    fi

    echo ""
    echo "--- systemd drop-ins (mpe/surge/sl/touch) ---"
    shopt -s nullglob
    for d in /etc/systemd/system/*.service.d; do
        unit="$(basename "$d")"
        base="${unit%.service.d}"
        case "$base" in
            mpe-*|surge-*|sl-*|touch-*)
                echo "  captured by provision: $unit"
                ;;
        esac
    done
    shopt -u nullglob

    echo ""
    echo "--- boot DSI snippet ---"
    for cfg in /boot/firmware/config.txt /boot/config.txt; do
        if [ -f "$cfg" ]; then
            grep -E 'dtoverlay=|display_auto_detect' "$cfg" 2>/dev/null | sed 's/^/  /' || true
            break
        fi
    done

    echo ""
    if [ "$missing" -eq 0 ] && [ "$extra" -eq 0 ]; then
        echo "Audit: clean (no missing listed paths, no extra .patch_browser files)"
        exit 0
    fi
    echo "Audit: $missing missing listed path(s), $extra extra .patch_browser file(s)"
    exit 1
}

if [ "$LOCAL" = true ]; then
    _audit_on_appliance
    exit $?
fi

# shellcheck source=../lib/paths.sh
source "$SCRIPT_DIR/../lib/paths.sh"

if [ -z "${PI_USER:-}" ]; then
    echo "ERROR: PI_USER not set." >&2
    exit 1
fi

repo_path="$(mpe_pi_repo_path)"
mpe_pi_ssh "cd '$repo_path' && ./scripts/provision/audit-external-state-paths.sh --local"
