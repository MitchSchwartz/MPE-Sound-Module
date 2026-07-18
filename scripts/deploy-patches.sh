#!/bin/bash
# Deploy custom user patches only (fast daily workflow)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
cd "$MPE_MODULE_REPO"

PI_HOST="${PI_HOST:-surge.local}"
PI_USER="${PI_USER:-mitch}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/surge_pi_key}"
PATCHES_SRC="$MPE_ASSETS_DIR/user-data/Patches"

echo "======================================="
echo "  Deploy Custom Patches"
echo "======================================="
echo ""
echo "Target: $PI_USER@$PI_HOST"
echo ""

if [ ! -d "$PATCHES_SRC" ]; then
    echo "❌ ERROR: $PATCHES_SRC not found"
    echo "Run from repo root."
    exit 1
fi

if ! ssh -i "$SSH_KEY" -o ConnectTimeout=5 "$PI_USER@$PI_HOST" "echo 'Connected'" > /dev/null; then
    echo "❌ ERROR: Cannot connect to Pi"
    echo ""
    echo "Troubleshooting:"
    echo "  1. Check Pi is powered on"
    echo "  2. Try: ssh -i $SSH_KEY $PI_USER@$PI_HOST"
    echo "  3. Or use IP: export PI_HOST=your.pi.address"
    exit 1
fi

echo "Packaging patches..."
cd "$MPE_ASSETS_DIR/user-data" && tar czf /tmp/user-patches.tar.gz Patches/

echo "Uploading..."
scp -i "$SSH_KEY" /tmp/user-patches.tar.gz "$PI_USER@$PI_HOST:/tmp/"

echo "Installing on Pi..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" << 'EOF'
mkdir -p "/home/mitch/Documents/Surge XT"
cd "/home/mitch/Documents/Surge XT"
tar xzf /tmp/user-patches.tar.gz
rm /tmp/user-patches.tar.gz
EOF

rm /tmp/user-patches.tar.gz

echo "Restarting services..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" << 'EOF'
sudo systemctl restart surge-xt-cli 2>/dev/null || true
sudo systemctl restart patch-browser 2>/dev/null || true
EOF

echo ""
echo "✅ Custom patches deployed"
echo ""
echo "Tip: if Pi symlinks point at cloned MPE-Personal, git pull there instead of re-uploading."
echo "Personal repo: $MPE_PERSONAL_REPO"
echo ""
