#!/bin/bash
# Pull all assets from Pi to MPE-Personal (private backup repo)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
mpe_require_personal
ASSETS="$MPE_ASSETS_DIR"
mkdir -p "$ASSETS/binaries" "$ASSETS/patches" "$ASSETS/user-data" "$ASSETS/configs/active"

echo "======================================="
echo "  Pulling Complete Device Backup"
echo "======================================="
echo ""
echo "Source: $PI_USER@$PI_HOST"
echo "Destination: $MPE_PERSONAL_REPO"
echo ""

if ! mpe_pi_ssh "echo Connected" > /dev/null; then
    echo "❌ ERROR: Cannot connect to Pi"
    exit 1
fi
echo "✓ Connected"
echo ""

echo "Step 1/5: Pulling Surge binary..."
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:~/surge/build/surge_xt_products/surge-xt-cli" \
    "$ASSETS/binaries/" 2>/dev/null || echo "⚠️  binary not found on Pi"

echo "Step 2/5: Pulling factory patches..."
mpe_pi_ssh bash -s <<EOF
$(mpe_pi_source_line)
cd "\$MPE_SURGE_RESOURCES" && tar czf /tmp/patches_factory.tar.gz patches_factory/
EOF
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/tmp/patches_factory.tar.gz" "$ASSETS/patches/" 2>/dev/null || true
mpe_pi_ssh "rm -f /tmp/patches_factory.tar.gz"
[ -f "$ASSETS/patches/patches_factory.tar.gz" ] && \
    cd "$ASSETS/patches" && tar xzf patches_factory.tar.gz && rm patches_factory.tar.gz

echo "Step 3/5: Pulling third-party patches..."
mpe_pi_ssh bash -s <<EOF
$(mpe_pi_source_line)
cd "\$MPE_SURGE_RESOURCES" && tar czf /tmp/patches_3rdparty.tar.gz patches_3rdparty/
EOF
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/tmp/patches_3rdparty.tar.gz" "$ASSETS/patches/" 2>/dev/null || true
mpe_pi_ssh "rm -f /tmp/patches_3rdparty.tar.gz"
[ -f "$ASSETS/patches/patches_3rdparty.tar.gz" ] && \
    cd "$ASSETS/patches" && tar xzf patches_3rdparty.tar.gz && mv patches_3rdparty third-party && rm patches_3rdparty.tar.gz

echo "Step 4/5: Pulling configs..."
for f in surge-xt-cli.service patch-browser.service boot-animation.service surge-watchdog.service; do
    scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/systemd/system/$f" "$ASSETS/configs/active/" 2>/dev/null || true
done
for f in 99-roli-seaboard.rules 99-usb-audio.rules; do
    scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/udev/rules.d/$f" "$ASSETS/configs/active/" 2>/dev/null || true
done

echo "Step 5/5: Pulling user data..."
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:.local/share/Surge\ XT/SurgeXTUserDefaults.xml" \
    "$ASSETS/user-data/" 2>/dev/null || true
mpe_pi_ssh bash -s <<EOF
$(mpe_pi_source_line)
[ -d "\$MPE_SURGE_DOCS/Patches" ] && cd "\$MPE_SURGE_DOCS" && tar czf /tmp/custom-patches.tar.gz Patches/
EOF
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/tmp/custom-patches.tar.gz" "$ASSETS/user-data/" 2>/dev/null && {
    cd "$ASSETS/user-data" && tar xzf custom-patches.tar.gz && rm custom-patches.tar.gz
} || true
mpe_pi_ssh "rm -f /tmp/custom-patches.tar.gz" 2>/dev/null || true

echo ""
echo "✅ Pull Complete — commit in MPE-Personal:"
echo "  cd $MPE_PERSONAL_REPO && git add assets/ && git commit -m 'Backup $(date +%Y-%m-%d)'"
echo ""
