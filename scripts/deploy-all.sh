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
if [ -d "$ASSETS/patches/patches_factory" ]; then
    cd "$ASSETS/patches" && tar czf /tmp/patches_factory.tar.gz patches_factory/
    scp -i "$SSH_KEY" /tmp/patches_factory.tar.gz "$PI_USER@$PI_HOST:/tmp/"
    mpe_pi_ssh 'cd ~/surge/resources/data && tar xzf /tmp/patches_factory.tar.gz && rm /tmp/patches_factory.tar.gz'
    rm /tmp/patches_factory.tar.gz
    echo "✓ Factory patches deployed"
else
    echo "⚠️  Warning: Factory patches not found"
fi
echo ""

echo "Step 5/7: Deploying third-party patches (375MB)..."
if [ -d "$ASSETS/patches/third-party" ]; then
    cd "$ASSETS/patches" && tar czf /tmp/patches_3rdparty.tar.gz third-party/
    scp -i "$SSH_KEY" /tmp/patches_3rdparty.tar.gz "$PI_USER@$PI_HOST:/tmp/"
    mpe_pi_ssh 'cd ~/surge/resources/data && tar xzf /tmp/patches_3rdparty.tar.gz && mv third-party patches_3rdparty && rm /tmp/patches_3rdparty.tar.gz'
    rm /tmp/patches_3rdparty.tar.gz
    echo "✓ Third-party patches deployed"
else
    echo "⚠️  Warning: Third-party patches not found"
fi
echo ""

echo "Step 6/7: Configuring systemd services..."
if [ -n "$PI_MPE_MODULE" ]; then
    mpe_pi_ssh "cd '$PI_MPE_MODULE' && ./scripts/configure-pi-paths.sh --local --force"
else
    mpe_pi_ssh 'cd "${MPE_MODULE_REPO:-$HOME/MPE-Module}" && ./scripts/configure-pi-paths.sh --local --force'
fi
mpe_pi_ssh 'sudo systemctl enable surge-xt-cli patch-browser boot-animation surge-watchdog 2>/dev/null || true'
scp -i "$SSH_KEY" config/99-*.rules "$PI_USER@$PI_HOST:~/" 2>/dev/null && \
    mpe_pi_ssh 'sudo cp ~/99-*.rules /etc/udev/rules.d/ 2>/dev/null; sudo udevadm control --reload-rules; sudo udevadm trigger' || true
echo "✓ Services configured"
echo ""

echo "Step 7/7: Deploying user data..."
if [ -f "$ASSETS/user-data/SurgeXTUserDefaults.xml" ]; then
    scp -i "$SSH_KEY" "$ASSETS/user-data/SurgeXTUserDefaults.xml" \
        "$PI_USER@$PI_HOST:.local/share/Surge\ XT/"
fi
if [ -d "$ASSETS/user-data/Patches" ]; then
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
mpe_pi_ssh 'sudo systemctl restart surge-xt-cli patch-browser boot-animation 2>/dev/null || true'
echo ""
echo "======================================"
echo "  ✅ Deployment Complete!"
echo "======================================"
echo ""
echo "Next: ./scripts/setup-pi-symlinks.sh  (if assets repo cloned on Pi)"
echo ""
