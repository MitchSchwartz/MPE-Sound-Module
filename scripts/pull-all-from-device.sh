#!/bin/bash
# Pull all assets from Pi to MPE-Personal (private backup repo)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
ASSETS="$MPE_ASSETS_DIR"
mkdir -p "$ASSETS/binaries" "$ASSETS/patches" "$ASSETS/user-data" "$ASSETS/configs/active"

PI_HOST="${PI_HOST:-surge.local}"
PI_USER="${PI_USER:-mitch}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/surge_pi_key}"

echo "======================================="
echo "  Pulling Complete Device Backup"
echo "======================================="
echo ""
echo "Source: $PI_USER@$PI_HOST"
echo "Destination: $MPE_PERSONAL_REPO"
echo ""

if ! ssh -i "$SSH_KEY" -o ConnectTimeout=5 "$PI_USER@$PI_HOST" "echo 'Connected'" > /dev/null; then
    echo "❌ ERROR: Cannot connect to Pi"
    exit 1
fi
echo "✓ Connected"
echo ""

echo "Step 1/5: Pulling Surge binary (24MB)..."
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/home/mitch/surge/build/surge_xt_products/surge-xt-cli" \
    "$ASSETS/binaries/" 2>/dev/null || echo "⚠️  Warning: Could not pull Surge binary"
echo ""

echo "Step 2/5: Pulling factory patches (47MB)..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "cd /home/mitch/surge/resources/data && tar czf /tmp/patches_factory.tar.gz patches_factory/" 2>/dev/null || true
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/tmp/patches_factory.tar.gz" "$ASSETS/patches/" 2>/dev/null || true
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "rm -f /tmp/patches_factory.tar.gz"
if [ -f "$ASSETS/patches/patches_factory.tar.gz" ]; then
    cd "$ASSETS/patches" && tar xzf patches_factory.tar.gz && rm patches_factory.tar.gz
fi
echo ""

echo "Step 3/5: Pulling third-party patches (375MB)..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "cd /home/mitch/surge/resources/data && tar czf /tmp/patches_3rdparty.tar.gz patches_3rdparty/" 2>/dev/null || true
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/tmp/patches_3rdparty.tar.gz" "$ASSETS/patches/" 2>/dev/null || true
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "rm -f /tmp/patches_3rdparty.tar.gz"
if [ -f "$ASSETS/patches/patches_3rdparty.tar.gz" ]; then
    cd "$ASSETS/patches" && tar xzf patches_3rdparty.tar.gz && mv patches_3rdparty third-party && rm patches_3rdparty.tar.gz
fi
echo ""

echo "Step 4/5: Pulling active system configs..."
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/systemd/system/surge-xt-cli.service" \
    "$ASSETS/configs/active/" 2>/dev/null || true
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/systemd/system/patch-browser.service" \
    "$ASSETS/configs/active/" 2>/dev/null || true
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/systemd/system/boot-animation.service" \
    "$ASSETS/configs/active/" 2>/dev/null || true
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/systemd/system/surge-watchdog.service" \
    "$ASSETS/configs/active/" 2>/dev/null || true
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/udev/rules.d/99-roli-seaboard.rules" \
    "$ASSETS/configs/active/" 2>/dev/null || true
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/udev/rules.d/99-usb-audio.rules" \
    "$ASSETS/configs/active/" 2>/dev/null || true
echo ""

echo "Step 5/5: Pulling user data..."
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:.local/share/Surge\ XT/SurgeXTUserDefaults.xml" \
    "$ASSETS/user-data/" 2>/dev/null || true

ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "[ -d '/home/mitch/Documents/Surge XT/Patches' ] && cd '/home/mitch/Documents/Surge XT' && tar czf /tmp/custom-patches.tar.gz Patches/ 2>/dev/null" || true
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/tmp/custom-patches.tar.gz" "$ASSETS/user-data/" 2>/dev/null && {
    cd "$ASSETS/user-data" && tar xzf custom-patches.tar.gz && rm custom-patches.tar.gz
} || true
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "rm -f /tmp/custom-patches.tar.gz" 2>/dev/null

echo ""
echo "======================================="
echo "  ✅ Pull Complete!"
echo "======================================="
echo ""
echo "Commit in MPE-Personal:"
echo "  cd $MPE_PERSONAL_REPO"
echo "  git add assets/ && git commit -m 'Backup from device $(date +%Y-%m-%d)'"
echo "  git push"
echo ""
