#!/bin/bash
# Sync changes from Pi back to MPE-Personal (private backup repo)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
ASSETS="$MPE_ASSETS_DIR"
mkdir -p "$ASSETS/configs/active"

PI_HOST="${PI_HOST:-surge.local}"
PI_USER="${PI_USER:-mitch}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/surge_pi_key}"

echo "======================================="
echo "  Syncing Changes from Device"
echo "======================================="
echo ""
echo "Source: $PI_USER@$PI_HOST"
echo "Destination: $MPE_PERSONAL_REPO"
echo ""

if ! ssh -i "$SSH_KEY" -o ConnectTimeout=5 "$PI_USER@$PI_HOST" "echo 'Connected'" > /dev/null; then
    echo "❌ ERROR: Cannot connect to Pi"
    exit 1
fi

echo "Syncing active configs..."
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/systemd/system/surge-xt-cli.service" \
    "$ASSETS/configs/active/" 2>/dev/null && echo "  ✓ surge-xt-cli.service" || echo "  (not found)"

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/systemd/system/patch-browser.service" \
    "$ASSETS/configs/active/" 2>/dev/null && echo "  ✓ patch-browser.service" || echo "  (not found)"

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/systemd/system/boot-animation.service" \
    "$ASSETS/configs/active/" 2>/dev/null && echo "  ✓ boot-animation.service" || echo "  (not found)"

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/systemd/system/surge-watchdog.service" \
    "$ASSETS/configs/active/" 2>/dev/null && echo "  ✓ surge-watchdog.service" || echo "  (not found)"

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/udev/rules.d/99-roli-seaboard.rules" \
    "$ASSETS/configs/active/" 2>/dev/null && echo "  ✓ 99-roli-seaboard.rules" || echo "  (not found)"

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/udev/rules.d/99-usb-audio.rules" \
    "$ASSETS/configs/active/" 2>/dev/null && echo "  ✓ 99-usb-audio.rules" || echo "  (not found)"

echo ""
echo "Syncing user data..."
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:.local/share/Surge\ XT/SurgeXTUserDefaults.xml" \
    "$ASSETS/user-data/" 2>/dev/null && echo "  ✓ SurgeXTUserDefaults.xml" || echo "  (not found)"

echo ""
echo "Syncing user-created patches..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "[ -d '/home/mitch/Documents/Surge XT/Patches' ] && cd '/home/mitch/Documents/Surge XT' && tar czf /tmp/custom-patches.tar.gz Patches/ 2>/dev/null" || {
    echo "  (No custom patches directory found)"
    echo ""
    echo "======================================="
    echo "  ✅ Sync Complete!"
    echo "======================================="
    echo ""
    echo "Review changes in MPE-Personal:"
    echo "  cd $MPE_PERSONAL_REPO && git status"
    echo ""
    exit 0
}

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/tmp/custom-patches.tar.gz" "$ASSETS/user-data/" 2>/dev/null && {
    echo "  Extracting custom patches..."
    cd "$ASSETS/user-data" && tar xzf custom-patches.tar.gz && rm custom-patches.tar.gz
    echo "  ✓ Custom patches synced to $ASSETS/user-data/Patches"
} || echo "  (No custom patches to download)"

ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "rm -f /tmp/custom-patches.tar.gz" 2>/dev/null

echo ""
echo "======================================="
echo "  ✅ Sync Complete!"
echo "======================================="
echo ""
echo "Review and commit in MPE-Personal:"
echo "  cd $MPE_PERSONAL_REPO"
echo "  git status"
echo "  git add -A"
echo "  git commit -m 'Sync from device $(date +%Y-%m-%d)'"
echo "  git push"
echo ""
