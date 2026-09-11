#!/bin/bash
# Deploy custom user patches only (fast daily workflow)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/patch-format-revision.sh
source "$SCRIPT_DIR/lib/patch-format-revision.sh"
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

# The appliance engine is pinned; a patch written by a newer Surge loads there
# and silently defaults what it cannot read. Refuse it here, before the network.
if ! mpe_patch_revision_check "$PATCHES_SRC"; then
    echo ""
    echo "❌ ERROR: patches above the pinned engine's format revision (max ${MPE_PATCH_REVISION_MAX})"
    echo ""
    echo "The Pi runs surge-xt-cli @ 253f8d86. It would load these and quietly"
    echo "default every parameter it does not recognise — no error, wrong sound."
    echo "Re-save them from a Surge at or below that revision, or move the pin."
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
