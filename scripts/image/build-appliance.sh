#!/usr/bin/env bash
# Build a touch appliance from fresh Lite OS + private assets — no .img, no Surge compile.
#
# Board-neutral base; platform selects day0 script, git ref, and player parity profile.
#
#   ./scripts/image/build-appliance.sh --platform auto
#   ./scripts/image/build-appliance.sh --platform pi4 --state state/raspberrypi2-2026-08-23
#   ./scripts/image/build-appliance.sh --platform pi5 --git-ref dev
#
# See docs/PI4-GOLDEN-IMAGE.md Workflow D

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

STATE=""
PLATFORM="auto"
GIT_REF=""
HOSTNAME=""
WITH_LOOPER=false
SKIP_STATE=false
WAIT_S=300
DRY=false

usage() {
    echo "Usage: $0 [--platform pi4|pi5|auto] [--state DIR] [--git-ref REF] [--hostname NAME] [--with-looper] [--skip-state] [--wait SEC] [--dry-run]" >&2
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --platform) PLATFORM="${2:-}"; shift 2 ;;
        --state) STATE="${2:-}"; shift 2 ;;
        --git-ref) GIT_REF="${2:-}"; shift 2 ;;
        --hostname) HOSTNAME="${2:-}"; shift 2 ;;
        --with-looper) WITH_LOOPER=true; shift ;;
        --skip-state) SKIP_STATE=true; shift ;;
        --wait) WAIT_S="${2:-}"; shift 2 ;;
        --dry-run) DRY=true; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1" >&2; usage ;;
    esac
done

# shellcheck source=../lib/paths.sh
source "$SCRIPT_DIR/../lib/paths.sh"
# shellcheck source=../lib/detect-pi-platform.sh
source "$SCRIPT_DIR/../lib/detect-pi-platform.sh"

if [ -z "${PI_USER:-}" ] || [ -z "${PI_HOST:-}" ]; then
    echo "ERROR: set PI_USER and PI_HOST in config/mpe.env" >&2
    exit 1
fi

mpe_require_personal

_ssh() {
    ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new \
        -i "$SSH_KEY" "$PI_USER@$PI_HOST" "$@"
}

_run_remote() {
    if [ "$DRY" = true ]; then
        echo "would remote: $*"
    else
        _ssh "$@"
    fi
}

_remote_platform() {
    _ssh "tr -d '\\0' < /proc/device-tree/model 2>/dev/null || echo unknown" | {
        read -r model || true
        case "$model" in
            *"Raspberry Pi 5"*) echo pi5 ;;
            *"Raspberry Pi 4"*) echo pi4 ;;
            *) echo unknown ;;
        esac
    }
}

_resolve_platform() {
    case "$PLATFORM" in
        auto) _remote_platform ;;
        pi4|pi5) echo "$PLATFORM" ;;
        *) echo "ERROR: --platform must be pi4, pi5, or auto" >&2; exit 2 ;;
    esac
}

echo "Waiting for SSH (up to ${WAIT_S}s) ..."
deadline=$((SECONDS + WAIT_S))
while [ "$SECONDS" -lt "$deadline" ]; do
    if _ssh "echo ok" >/dev/null 2>&1; then
        echo "SSH up."
        break
    fi
    sleep 5
done
if ! _ssh "echo ok" >/dev/null 2>&1; then
    echo "ERROR: SSH not reachable at $PI_HOST" >&2
    exit 1
fi

PLAT="$(_resolve_platform)"
if [ "$PLAT" = unknown ]; then
    echo "ERROR: could not detect platform — pass --platform pi4 or pi5" >&2
    exit 1
fi

if [ -z "$GIT_REF" ]; then
    GIT_REF="${MPE_APPLIANCE_GIT_REF:-$(mpe_appliance_git_ref "$PLAT" "$REPO_ROOT")}"
fi

case "$PLAT" in
    pi4) DAY0="./scripts/image/install-pi4-day0-tier1.sh" ;;
    pi5) DAY0="./scripts/install-pi5-day0-tier1.sh" ;;
esac

echo "======================================"
echo "  Appliance build (from assets)"
echo "======================================"
echo "Target:   $PI_USER@$PI_HOST"
echo "Platform: $PLAT"
echo "Git ref:  $GIT_REF"
echo "Assets:   $MPE_ASSETS_DIR"
echo ""

repo_path="$(mpe_pi_repo_path)"

echo "[1/9] Clone or update MPE-Module on Pi ..."
if [ -n "$GIT_REF" ]; then
    _run_remote "if [ -d '$repo_path/.git' ]; then cd '$repo_path' && git fetch origin && git checkout '$GIT_REF' && git pull; \
        else git clone https://github.com/MitchSchwartz/MPE-Module.git '$repo_path' && cd '$repo_path' && git checkout '$GIT_REF'; fi"
else
    _run_remote "if [ -d '$repo_path/.git' ]; then cd '$repo_path' && git pull; \
        else git clone https://github.com/MitchSchwartz/MPE-Module.git '$repo_path'; fi"
fi

echo ""
echo "[2/9] Day-0 apt ($PLAT) ..."
_run_remote "cd '$repo_path' && $DAY0"

if [ "$PLAT" = pi5 ]; then
    echo ""
    echo "[3/9] Pi 5 player Tier 3 (touch deps) ..."
    _run_remote "cd '$repo_path' && ./scripts/install-pi5-player-tier3.sh"
else
    echo ""
    echo "[3/9] Pi 5 Tier 3 — skipped (pi4)"
fi

echo ""
echo "[4/9] Deploy Surge binary + patches from private assets ..."
if [ "$DRY" = true ]; then
    echo "would: deploy-all.sh (binary + patches + user-data)"
else
    PI_HOST="$PI_HOST" PI_USER="$PI_USER" SSH_KEY="$SSH_KEY" \
        "$REPO_ROOT/scripts/deploy-all.sh"
fi
if [ "$PLAT" = pi5 ]; then
    echo "  NOTE: Pi 5 player should use an a76 Surge binary when available — see scripts/build-surge-a72.sh / build-surge-a76.sh"
fi

echo ""
echo "[5/9] Touch UI setup (MPE_UI_MODE=touch) ..."
_run_remote "cd '$repo_path' && \
    grep -q '^MPE_UI_MODE=touch' config/mpe.env 2>/dev/null || echo 'MPE_UI_MODE=touch' >> config/mpe.env; \
    export MPE_UI_MODE=touch && ./scripts/setup-touch-pi.sh"

echo ""
echo "[6/9] Native instruments (peak meter, xrun probe) ..."
_run_remote "cd '$repo_path' && ./scripts/build-mpe-peak-meter.sh --required && ./scripts/build-mpe-xrun-probe.sh"

if [ "$WITH_LOOPER" = true ]; then
    echo ""
    echo "[7/9] SooperLooper (optional — manual build) ..."
    _run_remote "[ -d \"\$HOME/src/sooperlooper-1.7.9\" ] && echo '  found existing tree' || echo '  no tree — build manually'"
else
    echo ""
    echo "[7/9] SooperLooper — skipped (use --with-looper to check for existing build)"
fi

echo ""
echo "[8/9] first-boot (units, hygiene, parity, DSI, audio.conf) ..."
extra_env=""
[ -n "$HOSTNAME" ] && extra_env="MPE_HOSTNAME=$HOSTNAME"
_run_remote "cd '$repo_path' && sudo env $extra_env ./scripts/provision/first-boot.sh"

if [ "$SKIP_STATE" = false ] && [ -n "$STATE" ]; then
    echo ""
    echo "[9/9] Apply external state ..."
    if [ "$DRY" = true ]; then
        echo "would: apply-external-state.sh --state $STATE"
    else
        PI_HOST="$PI_HOST" PI_USER="$PI_USER" SSH_KEY="$SSH_KEY" \
            "$REPO_ROOT/scripts/provision/apply-external-state.sh" --state "$STATE"
    fi
else
    echo ""
    echo "[9/9] External state — skipped"
    echo "  Capture: ./scripts/provision/capture-external-state.sh"
    echo "  Re-run with: --state state/<host>-YYYY-MM-DD"
fi

echo ""
echo "======================================"
echo "  Build pass complete ($PLAT)"
echo "======================================"
echo ""
echo "Per-unit setup (NOT in capture — by design):"
echo "  - SSH: add laptop public key to ~/.ssh/authorized_keys"
echo "  - Tailscale: sudo tailscale up"
echo "  - WiFi: Imager or nmcli on device"
echo ""
echo "Verify: mpe ping && mpe status — then play it."
