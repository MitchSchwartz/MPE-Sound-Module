#!/usr/bin/env bash
# Pull device-specific state off the appliance into a portable state/ tree.
#
#   ./scripts/provision/capture-external-state.sh [OUTPUT_DIR]
#   ./scripts/provision/capture-external-state.sh --check [OUTPUT_DIR]
#   ./scripts/provision/capture-external-state.sh --local [OUTPUT_DIR]
#
# Default OUTPUT_DIR (laptop): state/<host-label>-YYYY-MM-DD beside repo
# Default OUTPUT_DIR (--local on Pi): state/local-YYYY-MM-DD beside repo
#
# Superset of backup-appliance-state.sh — use this for golden-image workflows.
# See docs/PI4-GOLDEN-IMAGE.md

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PATHS_LIST="${MPE_EXTERNAL_STATE_PATHS_LIST:-$REPO_ROOT/config/platform/external-state-paths.list}"

# Bump when capture output shape changes — _verify_capture_artifacts enforces this.
CAPTURE_SCHEMA_VERSION=2

CHECK=false
LOCAL=false
OUTPUT=""

while [ $# -gt 0 ]; do
    case "$1" in
        --check) CHECK=true; shift ;;
        --local) LOCAL=true; shift ;;
        -*) echo "Unknown option: $1" >&2; exit 2 ;;
        *)
            if [ -n "$OUTPUT" ]; then
                echo "ERROR: unexpected argument: $1" >&2
                exit 2
            fi
            OUTPUT="$1"
            shift
            ;;
    esac
done

_read_paths() {
    [ -f "$PATHS_LIST" ] || { echo "ERROR: missing $PATHS_LIST" >&2; exit 1; }
    grep -v '^#' "$PATHS_LIST" | grep -v '^[[:space:]]*$' || true
}

_is_never_capture() {
    local rel="$1"
    case "$rel" in
        .ssh|.ssh/*|*/.ssh|*/.ssh/*) return 0 ;;
    esac
    case "$rel" in
        .mpe_clock_*.json) return 0 ;;
    esac
    case "$rel" in
        */tailscale/*|tailscale/*|var/lib/tailscale/*) return 0 ;;
    esac
    return 1
}

_credential_scan() {
    if grep -rqiE 'ghp_|github_pat_|tskey-|tskey-auth|BEGIN .*PRIVATE KEY' "$1" 2>/dev/null; then
        echo "ERROR: credential-shaped content in captured state — refusing to write." >&2
        echo "  (includes Tailscale auth keys and SSH/GitHub tokens)" >&2
        exit 1
    fi
}

_capture_systemd_dropins() {
    local dest="$1"
    local drop_dest="$dest/etc/systemd-dropins"
    local d unit base

    mkdir -p "$drop_dest"
    shopt -s nullglob
    for d in /etc/systemd/system/*.service.d; do
        unit="$(basename "$d")"
        base="${unit%.service.d}"
        case "$base" in
            mpe-*|surge-*|sl-*|touch-*)
                rm -rf "$drop_dest/$unit"
                cp -a "$d" "$drop_dest/$unit"
                echo "  captured systemd drop-in: $unit"
                ;;
        esac
    done
    shopt -u nullglob
}

_capture_boot_dsi_snippet() {
    local dest="$1"
    local cfg snip

    snip="$dest/boot/dsi-config.snippet"
    mkdir -p "$(dirname "$snip")"
    for cfg in /boot/firmware/config.txt /boot/config.txt; do
        if [ -f "$cfg" ]; then
            {
                echo "# source: $cfg"
                grep -E 'dtoverlay=|display_auto_detect' "$cfg" 2>/dev/null || true
            } >"$snip"
            echo "  captured boot DSI snippet from $cfg"
            return 0
        fi
    done
    echo "  skip (missing): boot config.txt"
}

_verify_capture_artifacts() {
    local dest="$1"
    local fail=0

    if [ ! -f "$dest/platform.json" ]; then
        echo "ERROR: capture incomplete — missing platform.json" >&2
        fail=1
    fi
    if [ ! -f "$dest/boot/dsi-config.snippet" ]; then
        echo "ERROR: capture incomplete — missing boot/dsi-config.snippet" >&2
        fail=1
    fi
    if [ ! -f "$dest/MANIFEST.md" ] || ! grep -q '## Platform / kernel' "$dest/MANIFEST.md" 2>/dev/null; then
        echo "ERROR: capture incomplete — MANIFEST missing platform section" >&2
        fail=1
    fi
    if [ "$fail" -ne 0 ]; then
        echo "  Capture schema v${CAPTURE_SCHEMA_VERSION} requires current scripts on the appliance." >&2
        echo "  On Pi: cd ~/MPE-Module && git fetch origin && git checkout dev && git pull --ff-only" >&2
        exit 1
    fi
}

_write_manifest() {
    local dest="$1"
    local surge_ver git_rev model plat_section restore_label

    # shellcheck source=../lib/detect-pi-platform.sh
    source "$SCRIPT_DIR/../lib/detect-pi-platform.sh"
    # shellcheck source=../lib/write-platform-manifest.sh
    source "$SCRIPT_DIR/../lib/write-platform-manifest.sh"

    surge_ver="$("${SURGE_CLI:-/nonexistent}" --version 2>/dev/null || echo unknown)"
    git_rev="$(cd "${MPE_MODULE_REPO:-$REPO_ROOT}" && git rev-parse --short HEAD 2>/dev/null || echo unknown)"
    model="$(mpe_pi_model_string 2>/dev/null || tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
    plat_section="$(mpe_platform_manifest_markdown)"
    restore_label="${MPE_STATE_RESTORE_LABEL:-state/$(basename "$dest")}"

    cat >"$dest/MANIFEST.md" <<EOF
# External state capture

*Captured: $(date -Iseconds)*

| Field | Value |
|---|---|
| Hostname | $(hostname) |
| User | $(id -un) |
| Model | $model |
| MPE-Module | $git_rev |
| Surge CLI | $surge_ver |
| Capture schema | v${CAPTURE_SCHEMA_VERSION} |

## Platform / kernel

$plat_section

Restore with:

\`\`\`bash
./scripts/provision/apply-external-state.sh --state ${restore_label}
\`\`\`

See \`docs/PI4-GOLDEN-IMAGE.md\`.
EOF
    mpe_platform_manifest_json >"$dest/platform.json"
}

_capture_on_appliance() {
    local dest="$1"
    local rel remote home dest_sub

    # shellcheck source=../lib/paths.sh
    source "$SCRIPT_DIR/../lib/paths.sh"
    mpe_apply_pi_home
    home="${MPE_HOME:-$HOME}"

    mkdir -p "$dest/home" "$dest/etc/mpe"

    while IFS= read -r rel; do
        [ -n "$rel" ] || continue
        if _is_never_capture "$rel"; then
            echo "  skip (excluded): $rel"
            continue
        fi
        if [[ "$rel" == /* ]]; then
            remote="$rel"
            case "$rel" in
                /etc/mpe/mpe.env) dest_sub="$dest/etc/mpe/mpe.env" ;;
                *)
                    echo "WARNING: skipping unsupported absolute path: $rel" >&2
                    continue
                    ;;
            esac
        else
            remote="$home/$rel"
            dest_sub="$dest/home/$rel"
        fi

        if [ ! -e "$remote" ]; then
            echo "  skip (missing): $rel"
            continue
        fi

        mkdir -p "$(dirname "$dest_sub")"
        if [ -d "$remote" ]; then
            rm -rf "$dest_sub"
            cp -a "$remote" "$dest_sub"
        else
            cp -a "$remote" "$dest_sub"
        fi
        echo "  captured: $rel"
    done < <(_read_paths)

    _capture_systemd_dropins "$dest"
    _capture_boot_dsi_snippet "$dest"
    _write_manifest "$dest"
    _verify_capture_artifacts "$dest"
    _credential_scan "$dest"
    echo ""
    echo "Captured into: $dest"
    find "$dest" -type f | sed "s|^|  |"
}

if [ "$LOCAL" = true ]; then
    if [ -z "$OUTPUT" ]; then
        OUTPUT="$REPO_ROOT/state/local-$(date +%Y-%m-%d)"
    fi
    if [ "$CHECK" = true ]; then
        echo "ERROR: --check is only supported from the laptop." >&2
        exit 2
    fi
    _capture_on_appliance "$OUTPUT"
    exit 0
fi

# shellcheck source=../lib/paths.sh
source "$SCRIPT_DIR/../lib/paths.sh"

if [ -z "$OUTPUT" ]; then
    host_label="${PI_HOST%%.*}"
    OUTPUT="$REPO_ROOT/state/${host_label}-$(date +%Y-%m-%d)"
fi

if [ -z "${PI_USER:-}" ]; then
    echo "ERROR: PI_USER not set. Set config/mpe.env or export PI_USER." >&2
    exit 1
fi

if ! mpe_pi_ssh "echo ok" >/dev/null 2>&1; then
    echo "ERROR: cannot reach $PI_USER@$PI_HOST" >&2
    exit 1
fi

remote_tmp="/tmp/mpe-external-state-$$"
repo_path="$(mpe_pi_repo_path)"
restore_label="state/$(basename "$OUTPUT")"
laptop_rev="$(cd "$REPO_ROOT" && git rev-parse HEAD 2>/dev/null || echo unknown)"

_ensure_pi_repo_for_capture() {
    echo "Ensuring Pi repo is current for capture schema v${CAPTURE_SCHEMA_VERSION} ..."
    if ! mpe_pi_ssh "cd '$repo_path' && git fetch origin dev 2>/dev/null || git fetch origin && \
        git checkout dev && git pull --ff-only origin dev"; then
        echo "ERROR: git pull failed on $PI_USER@$PI_HOST — capture would run stale scripts." >&2
        exit 1
    fi
    local pi_rev
    pi_rev="$(mpe_pi_ssh "cd '$repo_path' && git rev-parse HEAD" 2>/dev/null || echo unknown)"
    if [ "$pi_rev" != "$laptop_rev" ] && [ "$laptop_rev" != unknown ]; then
        echo "WARNING: Pi at $pi_rev, laptop at $laptop_rev — push laptop dev if Pi is behind." >&2
    fi
    echo "  Pi MPE-Module: $pi_rev"
}

_ensure_pi_repo_for_capture

if [ "$CHECK" = true ]; then
    if [ ! -d "$OUTPUT" ]; then
        echo "ERROR: no state dir at $OUTPUT" >&2
        exit 1
    fi
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    mpe_pi_ssh "rm -rf '$remote_tmp' && mkdir -p '$remote_tmp' && \
        cd '$repo_path' && MPE_STATE_RESTORE_LABEL='$restore_label' \
        ./scripts/provision/capture-external-state.sh --local '$remote_tmp'"
    scp -qr -i "$SSH_KEY" "$PI_USER@$PI_HOST:$remote_tmp/." "$tmp/"
    mpe_pi_ssh "rm -rf '$remote_tmp'"
    _verify_capture_artifacts "$tmp"
    if diff -rq "$OUTPUT" "$tmp" >/dev/null 2>&1; then
        echo "  no drift — $OUTPUT matches the appliance"
        exit 0
    fi
    echo "  DRIFT between $OUTPUT and the appliance:"
    diff -rq "$OUTPUT" "$tmp" 2>&1 | sed 's/^/    /'
    echo "  Re-run without --check to refresh."
    exit 1
fi

mpe_pi_ssh "rm -rf '$remote_tmp' && mkdir -p '$remote_tmp' && \
    cd '$repo_path' && MPE_STATE_RESTORE_LABEL='$restore_label' \
    ./scripts/provision/capture-external-state.sh --local '$remote_tmp'"

mkdir -p "$OUTPUT"
rm -rf "${OUTPUT:?}"/*
scp -qr -i "$SSH_KEY" "$PI_USER@$PI_HOST:$remote_tmp/." "$OUTPUT/"
mpe_pi_ssh "rm -rf '$remote_tmp'"

_verify_capture_artifacts "$OUTPUT"
_credential_scan "$OUTPUT"
echo ""
echo "Captured from $PI_USER@$PI_HOST into: $OUTPUT"
