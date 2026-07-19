#!/bin/bash
# Deploy custom user patches only (fast daily workflow)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
mpe_require_personal

PATCHES_SRC="$MPE_ASSETS_DIR/user-data/Patches"

echo "======================================="
echo "  Deploy Custom Patches"
echo "======================================="
echo ""
echo "Target: $PI_USER@$PI_HOST"
echo ""

if [ ! -d "$PATCHES_SRC" ]; then
    echo "❌ ERROR: $PATCHES_SRC not found"
    exit 1
fi

if ! mpe_pi_ssh "echo Connected" > /dev/null; then
    echo "❌ ERROR: Cannot connect to Pi"
    exit 1
fi

cd "$MPE_ASSETS_DIR/user-data" && tar czf /tmp/user-patches.tar.gz Patches/
scp -i "$SSH_KEY" /tmp/user-patches.tar.gz "$PI_USER@$PI_HOST:/tmp/"

mpe_pi_ssh bash -s <<EOF
$(mpe_pi_source_line)
mkdir -p "\$MPE_SURGE_DOCS"
cd "\$MPE_SURGE_DOCS"
tar xzf /tmp/user-patches.tar.gz
rm /tmp/user-patches.tar.gz
EOF

rm /tmp/user-patches.tar.gz

mpe_pi_ssh 'sudo systemctl restart surge-xt-cli patch-browser 2>/dev/null || true'

echo ""
echo "✅ Custom patches deployed"
echo "Personal repo: $MPE_PERSONAL_REPO"
echo ""
