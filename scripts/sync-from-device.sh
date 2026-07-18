#!/bin/bash
# Sync changes from Pi back to git repo (for ongoing backups)

set -e

PI_HOST="${PI_HOST:-surge.local}"
PI_USER="${PI_USER:-mitch}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/surge_pi_key}"

echo "======================================="
echo "  Syncing Changes from Device"
echo "======================================="
echo ""
echo "Source: $PI_USER@$PI_HOST"
echo ""

# Test connection
if ! ssh -i "$SSH_KEY" -o ConnectTimeout=5 "$PI_USER@$PI_HOST" "echo 'Connected'" > /dev/null; then
    echo "❌ ERROR: Cannot connect to Pi"
    exit 1
fi

echo "Syncing active configs..."
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/systemd/system/surge-xt-cli.service" \
    assets/configs/active/ 2>/dev/null && echo "  ✓ surge-xt-cli.service" || echo "  (not found)"

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/systemd/system/patch-browser.service" \
    assets/configs/active/ 2>/dev/null && echo "  ✓ patch-browser.service" || echo "  (not found)"

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/systemd/system/boot-animation.service" \
    assets/configs/active/ 2>/dev/null && echo "  ✓ boot-animation.service" || echo "  (not found)"

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/systemd/system/surge-watchdog.service" \
    assets/configs/active/ 2>/dev/null && echo "  ✓ surge-watchdog.service" || echo "  (not found)"

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/udev/rules.d/99-roli-seaboard.rules" \
    assets/configs/active/ 2>/dev/null && echo "  ✓ 99-roli-seaboard.rules" || echo "  (not found)"

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/udev/rules.d/99-usb-audio.rules" \
    assets/configs/active/ 2>/dev/null && echo "  ✓ 99-usb-audio.rules" || echo "  (not found)"

echo ""
echo "Syncing user data..."
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:.local/share/Surge\ XT/SurgeXTUserDefaults.xml" \
    assets/user-data/ 2>/dev/null && echo "  ✓ SurgeXTUserDefaults.xml" || echo "  (not found)"

echo ""
echo "Syncing user-created patches..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "[ -d '/home/mitch/Documents/Surge XT/Patches' ] && cd '/home/mitch/Documents/Surge XT' && tar czf /tmp/custom-patches.tar.gz Patches/ 2>/dev/null" || {
    echo "  (No custom patches directory found)"
    echo ""
    echo "======================================="
    echo "  ✅ Sync Complete!"
    echo "======================================="
    echo ""
    echo "Review changes:"
    echo "  git status"
    echo ""
    echo "Commit and push:"
    echo "  git add -A"
    echo "  git commit -m 'Sync from device $(date +%Y-%m-%d)'"
    echo "  git push"
    echo ""
    exit 0
}

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/tmp/custom-patches.tar.gz" assets/user-data/ 2>/dev/null && {
    echo "  Extracting custom patches..."
    cd assets/user-data && tar xzf custom-patches.tar.gz && rm custom-patches.tar.gz && cd ../..
    echo "  ✓ Custom patches synced to assets/user-data/Patches"
} || echo "  (No custom patches to download)"

ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "rm -f /tmp/custom-patches.tar.gz" 2>/dev/null

echo ""
echo "======================================="
echo "  ✅ Sync Complete!"
echo "======================================="
echo ""
echo "Review changes:"
echo "  git status"
echo ""
echo "Commit and push:"
echo "  git add -A"
echo "  git commit -m 'Sync from device $(date +%Y-%m-%d)'"
echo "  git push"
echo ""
