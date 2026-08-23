#!/usr/bin/env bash
# Build a Pi 4 appliance from fresh Lite OS + private assets — no .img, no Surge compile.
#
# Prerequisites:
#   - Raspberry Pi Imager: Lite 64-bit trixie, SSH on, username/password set
#   - Laptop: config/mpe.env with PI_HOST, PI_USER, SSH_KEY
#   - Private assets repo beside MPE-Module (see BACKUP_GUIDE.md):
#       assets/binaries/surge-xt-cli
#       assets/patches/patches_factory
#       assets/patches/third-party/
#       assets/user-data/Patches/  (Quick Select tree)
#
# Usage:
#   ./scripts/image/build-pi4-appliance.sh
#   ./scripts/image/build-pi4-appliance.sh --state state/raspberrypi2-2026-08-23
#   ./scripts/image/build-pi4-appliance.sh --git-ref main --hostname mpe-bench
#   ./scripts/image/build-pi4-appliance.sh --with-looper   # Mitch gate: long SooperLooper build
#
# Does NOT copy SSH keys, WiFi profiles, or Tailscale — configure those per unit after build.
# See docs/PI4-GOLDEN-IMAGE.md § "Captured vs built vs excluded"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

STATE=""
GIT_REF="${MPE_APPLIANCE_GIT_REF:-$(tr -d '[:space:]' < "$REPO_ROOT/config/platform/appliance-git-ref" 2>/dev/null || echo main)}"
HOSTNAME=""
WITH_LOOPER=false
SKIP_STATE=false
WAIT_S=300
DRY=false

usage() {
    echo "Usage: $0 [--state DIR] [--git-ref REF] [--hostname NAME] [--with-looper] [--skip-state] [--wait SEC] [--dry-run]" >&2
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
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

echo "======================================"
echo "  Pi 4 appliance build (from assets)"
echo "======================================"
echo "Target: $PI_USER@$PI_HOST"
echo "Git ref: $GIT_REF"
echo "Assets: $MPE_ASSETS_DIR"
echo ""

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

repo_path="$(mpe_pi_repo_path)"

echo ""
echo "[1/8] Clone or update MPE-Module on Pi ..."
if [ -n "$GIT_REF" ]; then
    _run_remote "if [ -d '$repo_path/.git' ]; then cd '$repo_path' && git fetch origin && git checkout '$GIT_REF' && git pull; \
        else git clone https://github.com/MitchSchwartz/MPE-Module.git '$repo_path' && cd '$repo_path' && git checkout '$GIT_REF'; fi"
else
    _run_remote "if [ -d '$repo_path/.git' ]; then cd '$repo_path' && git pull; \
        else git clone https://github.com/MitchSchwartz/MPE-Module.git '$repo_path'; fi"
fi

echo ""
echo "[2/8] Day-0 apt (JACK, pygame, RT limits) ..."
_run_remote "cd '$repo_path' && ./scripts/image/install-pi4-day0-tier1.sh"

echo ""
echo "[3/8] Deploy Surge binary + patches from private assets ..."
if [ "$DRY" = true ]; then
    echo "would: deploy-all.sh (binary + patches + user-data)"
else
    PI_HOST="$PI_HOST" PI_USER="$PI_USER" SSH_KEY="$SSH_KEY" \
        "$REPO_ROOT/scripts/deploy-all.sh"
fi

echo ""
echo "[4/8] Touch UI setup (MPE_UI_MODE=touch) ..."
_run_remote "cd '$repo_path' && \
    grep -q '^MPE_UI_MODE=touch' config/mpe.env 2>/dev/null || echo 'MPE_UI_MODE=touch' >> config/mpe.env; \
    export MPE_UI_MODE=touch && ./scripts/setup-touch-pi.sh"

echo ""
echo "[5/8] Native instruments (peak meter, xrun probe) ..."
_run_remote "cd '$repo_path' && ./scripts/build-mpe-peak-meter.sh --required && ./scripts/build-mpe-xrun-probe.sh"

if [ "$WITH_LOOPER" = true ]; then
    echo ""
    echo "[6/8] SooperLooper (optional — long build, Mitch gate) ..."
    echo "  Not automated in v1 — build on Pi per docs/measurements/archive/sooperlooper-eval-2026-08-14.md"
    echo "  Skipping unless SOOPERLOOPER_SRC is set on the Pi."
    _run_remote "[ -d \"\$HOME/src/sooperlooper-1.7.9\" ] && echo '  found existing tree' || echo '  no tree — build manually'"
else
    echo ""
    echo "[6/8] SooperLooper — skipped (use --with-looper to check for existing build)"
fi

echo ""
echo "[7/8] first-boot (units, hygiene, player parity) ..."
extra_env=""
[ -n "$HOSTNAME" ] && extra_env="MPE_HOSTNAME=$HOSTNAME"
_run_remote "cd '$repo_path' && sudo env $extra_env ./scripts/provision/first-boot.sh"

if [ "$SKIP_STATE" = false ] && [ -n "$STATE" ]; then
    echo ""
    echo "[8/8] Apply external state ..."
    if [ "$DRY" = true ]; then
        echo "would: apply-external-state.sh --state $STATE"
    else
        PI_HOST="$PI_HOST" PI_USER="$PI_USER" SSH_KEY="$SSH_KEY" \
            "$REPO_ROOT/scripts/provision/apply-external-state.sh" --state "$STATE"
    fi
else
    echo ""
    echo "[8/8] External state — skipped"
    echo "  Capture from reference unit: ./scripts/provision/capture-external-state.sh"
    echo "  Then re-run with: --state state/<host>-YYYY-MM-DD"
fi

echo ""
echo "======================================"
echo "  Build pass complete"
echo "======================================"
echo ""
echo "Per-unit setup (NOT in capture or image — by design):"
echo "  - SSH: add your laptop public key to ~/.ssh/authorized_keys"
echo "  - Tailscale: sudo tailscale up  (fresh node enrollment — never baked in)"
echo "  - WiFi: Imager advanced options, or nmcli / nmtui on device"
echo ""
echo "Verify: mpe ping && mpe status — then play it."
