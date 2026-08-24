#!/usr/bin/env bash
# Restore device-specific state from a capture-external-state.sh tree.
#
#   ./scripts/provision/apply-external-state.sh --state state/raspberrypi2-2026-08-23
#   ./scripts/provision/apply-external-state.sh --state ./state/foo --local
#   ./scripts/provision/apply-external-state.sh --parity-only [--local]
#
# Does not install the OS, Surge binary, or systemd units — see docs/PI4-GOLDEN-IMAGE.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

STATE=""
LOCAL=false
PARITY_ONLY=false
DRY=false

usage() {
    echo "Usage: $0 --state DIR [--local] [--dry-run]" >&2
    echo "       $0 --parity-only [--local] [--dry-run]" >&2
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --state) STATE="${2:-}"; shift 2 ;;
        --local) LOCAL=true; shift ;;
        --parity-only) PARITY_ONLY=true; shift ;;
        --dry-run) DRY=true; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1" >&2; usage ;;
    esac
done

_run() {
    if [ "$DRY" = true ]; then
        echo "would: $*"
    else
        "$@"
    fi
}

_apply_parity() {
    if [ "$DRY" = true ]; then
        echo "would: apply-player-env-parity.sh"
        return 0
    fi
    if [ "$LOCAL" = true ]; then
        "$REPO_ROOT/scripts/apply-player-env-parity.sh"
    else
        # shellcheck source=../lib/paths.sh
        source "$SCRIPT_DIR/../lib/paths.sh"
        mpe_pi_ssh "cd '$(mpe_pi_repo_path)' && ./scripts/apply-player-env-parity.sh"
    fi
}

_read_paths() {
    local list="${MPE_EXTERNAL_STATE_PATHS_LIST:-$REPO_ROOT/config/platform/external-state-paths.list}"
    [ -f "$list" ] || { echo "ERROR: missing $list" >&2; exit 1; }
    grep -v '^#' "$list" | grep -v '^[[:space:]]*$' || true
}

_apply_tree_on_appliance() {
    local state_dir="$1"
    local home dest_src dest_sub rel unit drop_src

    # shellcheck source=../lib/paths.sh
    source "$SCRIPT_DIR/../lib/paths.sh"
    mpe_apply_pi_home
    home="${MPE_HOME:-$HOME}"

    if [ ! -f "$state_dir/MANIFEST.md" ]; then
        echo "ERROR: $state_dir/MANIFEST.md missing — not a capture tree?" >&2
        exit 1
    fi

    echo "Applying external state from: $state_dir"

    while IFS= read -r rel; do
        [ -n "$rel" ] || continue
        if [[ "$rel" == /* ]]; then
            case "$rel" in
                /etc/mpe/mpe.env)
                    dest_src="$state_dir/etc/mpe/mpe.env"
                    if [ ! -f "$dest_src" ]; then
                        continue
                    fi
                    echo "  /etc/mpe/mpe.env"
                    if [ "$DRY" = true ]; then
                        echo "would: sudo cp capture -> /etc/mpe/mpe.env"
                    else
                        sudo mkdir -p /etc/mpe
                        sudo cp -a "$dest_src" /etc/mpe/mpe.env
                    fi
                    ;;
            esac
            continue
        fi

        dest_src="$state_dir/home/$rel"
        [ -e "$dest_src" ] || continue
        dest_sub="$home/$rel"
        echo "  ~/$rel"
        mkdir -p "$(dirname "$dest_sub")"
        if [ -d "$dest_src" ]; then
            _run rm -rf "$dest_sub"
            _run cp -a "$dest_src" "$dest_sub"
        else
            _run cp -a "$dest_src" "$dest_sub"
        fi
    done < <(_read_paths)

    if [ -d "$state_dir/etc/systemd-dropins" ]; then
        shopt -s nullglob
        for drop_src in "$state_dir/etc/systemd-dropins"/*.service.d; do
            [ -d "$drop_src" ] || continue
            unit="$(basename "$drop_src")"
            echo "  systemd drop-in: $unit"
            if [ "$DRY" = true ]; then
                echo "would: sudo cp -> /etc/systemd/system/$unit"
            else
                sudo mkdir -p "/etc/systemd/system/$unit"
                sudo cp -a "$drop_src/." "/etc/systemd/system/$unit/"
            fi
        done
        shopt -u nullglob
        if [ "$DRY" = false ]; then
            sudo systemctl daemon-reload 2>/dev/null || true
        fi
    fi

    echo ""
    echo "External state applied."
    echo "Next: sudo systemctl restart mpe-jackd surge-xt-cli touch-patch-browser 2>/dev/null || true"
}

if [ "$PARITY_ONLY" = true ]; then
    _apply_parity
    exit 0
fi

[ -n "$STATE" ] || usage

if [[ "$STATE" != /* ]]; then
    STATE="$REPO_ROOT/$STATE"
fi
[ -d "$STATE" ] || { echo "ERROR: state dir not found: $STATE" >&2; exit 1; }

if [ "$LOCAL" = true ]; then
    _apply_tree_on_appliance "$STATE"
    _apply_parity
    exit 0
fi

# shellcheck source=../lib/paths.sh
source "$SCRIPT_DIR/../lib/paths.sh"

if [ -z "${PI_USER:-}" ]; then
    echo "ERROR: PI_USER not set." >&2
    exit 1
fi

remote="/tmp/mpe-apply-state-$$"
state_name="$(basename "$STATE")"
mpe_pi_ssh "rm -rf '$remote' && mkdir -p '$remote'"
scp -qr -i "$SSH_KEY" "$STATE" "$PI_USER@$PI_HOST:$remote/"

dry_flag=""
[ "$DRY" = true ] && dry_flag="--dry-run"

mpe_pi_ssh "cd '$(mpe_pi_repo_path)' && ./scripts/provision/apply-external-state.sh \
    --state '$remote/$state_name' --local $dry_flag && rm -rf '$remote'"

echo ""
echo "Applied to $PI_USER@$PI_HOST"
