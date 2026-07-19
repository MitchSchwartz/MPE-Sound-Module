#!/bin/bash
# Sync changes from Pi back to MPE-Personal (private backup repo)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
mpe_require_personal
ASSETS="$MPE_ASSETS_DIR"
mkdir -p "$ASSETS/configs/active"

echo "======================================="
echo "  Syncing Changes from Device"
echo "======================================="
echo ""
echo "Source: $PI_USER@$PI_HOST"
echo "Destination: $MPE_PERSONAL_REPO"
echo ""

if ! mpe_pi_ssh "echo Connected" > /dev/null; then
    echo "❌ ERROR: Cannot connect to Pi"
    exit 1
fi

echo "Syncing active configs..."
for f in surge-xt-cli.service patch-browser.service boot-animation.service surge-watchdog.service; do
    scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/systemd/system/$f" \
        "$ASSETS/configs/active/" 2>/dev/null && echo "  ✓ $f" || echo "  ($f not found)"
done
for f in 99-roli-seaboard.rules 99-usb-audio.rules; do
    scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/udev/rules.d/$f" \
        "$ASSETS/configs/active/" 2>/dev/null && echo "  ✓ $f" || echo "  ($f not found)"
done

echo ""
echo "Syncing user data..."
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:.local/share/Surge\ XT/SurgeXTUserDefaults.xml" \
    "$ASSETS/user-data/" 2>/dev/null && echo "  ✓ SurgeXTUserDefaults.xml" || echo "  (not found)"

echo ""
echo "Syncing user-created patches..."
if ! mpe_pi_ssh bash -s <<EOF
$(mpe_pi_source_line)
if [ -d "\$MPE_SURGE_DOCS/Patches" ]; then
    cd "\$MPE_SURGE_DOCS" && tar czf /tmp/custom-patches.tar.gz Patches/
else
    exit 1
fi
EOF
then
    echo "  (No custom patches directory found)"
    echo ""
    echo "✅ Sync Complete (configs/prefs only)"
    exit 0
fi

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/tmp/custom-patches.tar.gz" "$ASSETS/user-data/" && {
    cd "$ASSETS/user-data" && tar xzf custom-patches.tar.gz && rm custom-patches.tar.gz
    echo "  ✓ Custom patches synced"
}
mpe_pi_ssh "rm -f /tmp/custom-patches.tar.gz" 2>/dev/null || true

echo ""
echo "✅ Sync Complete — commit in MPE-Personal:"
echo "  cd $MPE_PERSONAL_REPO && git status"
echo ""
