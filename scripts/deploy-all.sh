#!/bin/bash
# Deploy complete system from git repo to Pi

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
mpe_require_personal
cd "$MPE_MODULE_REPO"
ASSETS="$MPE_ASSETS_DIR"

echo "======================================"
echo "  Complete System Deployment"
echo "======================================"
echo ""
echo "Target: $PI_USER@$PI_HOST"
echo ""

if ! mpe_pi_ssh "echo Connected" > /dev/null 2>&1; then
    echo "❌ ERROR: Cannot connect to Pi"
    exit 1
fi
echo "✓ Connected"
echo ""

# Patch steps extract tarballs over the Surge patch roots. If those are links into the
# personal library (or we cannot tell), skip ONLY those steps: code, units and the
# binary still deploy. Patches then travel through the library (git), not tar.
PATCH_DEPLOY=1
if ! { echo "$(mpe_pi_source_line) || exit 2"; /bin/cat "$SCRIPT_DIR/lib/refuse-linked-roots.sh"; } | mpe_pi_ssh bash -s; then
    PATCH_DEPLOY=0
    echo "⚠️  Skipping the patch steps (4, 5 and the user patches in 7): the roots are linked to the personal library, or could not be checked."
    echo "    Code and units deploy as usual. Update patches via the library (docs/LIBRARY-MIGRATION.md)."
    echo ""
fi

echo "Step 1/7: git pull MPE-Module on Pi..."
if [ -n "$PI_MPE_MODULE" ]; then
    mpe_pi_ssh "cd '$PI_MPE_MODULE' && git pull" || echo "⚠️  git pull failed — ensure repo exists on Pi"
else
    mpe_pi_ssh 'cd "${MPE_MODULE_REPO:-$HOME/MPE-Module}" && git pull' || echo "⚠️  git pull failed — ensure repo exists on Pi"
fi
echo ""

echo "Step 2/7: Creating directories..."
mpe_pi_ssh 'mkdir -p ~/surge/build/surge_xt_products ~/surge/resources/data ~/.local/share/Surge\ XT'
echo "✓ Directories created"
echo ""

echo "Step 3/7: Deploying Surge binary (24MB)..."
if [ -f "$ASSETS/binaries/surge-xt-cli" ]; then
    scp -i "$SSH_KEY" "$ASSETS/binaries/surge-xt-cli" \
        "$PI_USER@$PI_HOST:~/surge/build/surge_xt_products/"
    mpe_pi_ssh "chmod +x ~/surge/build/surge_xt_products/surge-xt-cli"
    echo "✓ Binary deployed"
else
    echo "⚠️  Warning: Surge binary not found in $ASSETS/binaries/"
fi
echo ""

echo "Step 4/7: Deploying factory patches (47MB)..."
if [ "$PATCH_DEPLOY" = 1 ] && [ -d "$ASSETS/patches/patches_factory" ]; then
    cd "$ASSETS/patches" && tar czf /tmp/patches_factory.tar.gz patches_factory/
    scp -i "$SSH_KEY" /tmp/patches_factory.tar.gz "$PI_USER@$PI_HOST:/tmp/"
    mpe_pi_ssh "$(mpe_pi_source_line); cd \"\$MPE_SURGE_RESOURCES\" && tar xzf /tmp/patches_factory.tar.gz && rm /tmp/patches_factory.tar.gz"
    rm /tmp/patches_factory.tar.gz
    echo "✓ Factory patches deployed"
else
    echo "⚠️  Factory patches skipped or not found"
fi
echo ""

echo "Step 5/7: Deploying third-party patches (375MB)..."
# One level deep: the archive holds patches_3rdparty/<author>/..., extracted like the
# factory one. (Unpacking the whole third-party/ dir used to nest the authors under
# patches_3rdparty/patches_3rdparty/ and put that segment in every thirdparty key.)
# A device still on that nested layout is left alone: flattening it orphans its
# settings unless they are re-keyed first (docs/LIBRARY-MIGRATION.md).
if [ "$PATCH_DEPLOY" = 1 ] && [ -d "$ASSETS/patches/third-party/patches_3rdparty" ]; then
    cd "$ASSETS/patches/third-party" && tar czf /tmp/patches_3rdparty.tar.gz patches_3rdparty/
    scp -i "$SSH_KEY" /tmp/patches_3rdparty.tar.gz "$PI_USER@$PI_HOST:/tmp/"
    mpe_pi_ssh "$(mpe_pi_source_line); cd \"\$MPE_SURGE_RESOURCES\" && { if [ -d patches_3rdparty/patches_3rdparty ]; then echo 'nested third-party layout on the device: step 5 skipped (see docs/LIBRARY-MIGRATION.md)'; else tar xzf /tmp/patches_3rdparty.tar.gz && echo '✓ Third-party patches deployed'; fi; rc=\$?; rm -f /tmp/patches_3rdparty.tar.gz; exit \$rc; }"
    rm /tmp/patches_3rdparty.tar.gz
else
    echo "⚠️  Third-party patches skipped or not found"
fi
echo ""

echo "Step 6/7: Configuring systemd services..."
if [ -n "$PI_MPE_MODULE" ]; then
    mpe_pi_ssh "cd '$PI_MPE_MODULE' && ./scripts/configure-pi-paths.sh --local --force"
else
    mpe_pi_ssh 'cd "${MPE_MODULE_REPO:-$HOME/MPE-Module}" && ./scripts/configure-pi-paths.sh --local --force'
fi
mpe_pi_ssh 'sudo systemctl enable surge-xt-cli surge-watchdog 2>/dev/null || true'
mpe_pi_ssh "$(mpe_pi_source_line); source $(mpe_pi_repo_path)/scripts/lib/mpe-services.sh; mpe_enable_core_services"
mpe_pi_ssh "cd '$(mpe_pi_repo_path)' && ./scripts/install-udev-rules.sh" || true
echo "✓ Services configured"
echo ""

echo "Step 7/7: Deploying user data..."
if [ -f "$ASSETS/user-data/SurgeXTUserDefaults.xml" ]; then
    scp -i "$SSH_KEY" "$ASSETS/user-data/SurgeXTUserDefaults.xml" \
        "$PI_USER@$PI_HOST:.local/share/Surge\ XT/"
fi
if [ "$PATCH_DEPLOY" = 1 ] && [ -d "$ASSETS/user-data/Patches" ]; then
    cd "$ASSETS/user-data" && tar czf /tmp/user-patches.tar.gz Patches/
    scp -i "$SSH_KEY" /tmp/user-patches.tar.gz "$PI_USER@$PI_HOST:/tmp/"
    mpe_pi_ssh bash -s <<EOF
$(mpe_pi_source_line)
mkdir -p "\$MPE_SURGE_DOCS"
cd "\$MPE_SURGE_DOCS"
tar xzf /tmp/user-patches.tar.gz
rm /tmp/user-patches.tar.gz
EOF
    rm /tmp/user-patches.tar.gz
    echo "✓ Custom patches deployed"
fi
echo ""

echo "Starting services..."
mpe_pi_ssh "$(mpe_pi_source_line); source $(mpe_pi_repo_path)/scripts/lib/mpe-services.sh; mpe_restart_core_services"
echo ""
echo "======================================"
echo "  ✅ Deployment Complete!"
echo "======================================"
echo ""
echo "Next: ./scripts/setup-pi-symlinks.sh  (if assets repo cloned on Pi)"
echo ""
