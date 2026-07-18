#!/bin/bash
# Setup symlinks on Pi to MPE-Personal (patches live in private repo, not MPE-Module)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

PI_HOST="${PI_HOST:-surge.local}"
PI_USER="${PI_USER:-mitch}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/surge_pi_key}"

# Path on Pi — clone MPE-Personal beside MPE-Module under /home/mitch/
PI_PERSONAL="${PI_MPE_PERSONAL:-/home/mitch/MPE-Personal}"

echo "========================================="
echo "  Setup Pi Symlinks to MPE-Personal"
echo "========================================="
echo ""
echo "Target: $PI_USER@$PI_HOST"
echo "Personal repo on Pi: $PI_PERSONAL"
echo "Local personal repo: $MPE_PERSONAL_REPO"
echo ""

echo "Testing connection..."
if ! ssh -i "$SSH_KEY" -o ConnectTimeout=5 "$PI_USER@$PI_HOST" "echo 'Connected'" > /dev/null 2>&1; then
    echo "❌ ERROR: Cannot connect to Pi"
    exit 1
fi
echo "✓ Connected"
echo ""

echo "Setting up symlinks on Pi..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "PI_PERSONAL='$PI_PERSONAL'" bash -s << 'REMOTE_SCRIPT'
set -e

PERSONAL_REPO="$PI_PERSONAL"
ASSETS="$PERSONAL_REPO/assets"
SURGE_RESOURCES="/home/mitch/surge/resources/data"
SURGE_DOCS="/home/mitch/Documents/Surge XT"

echo "Step 1/3: Validating MPE-Personal..."
if [ ! -d "$PERSONAL_REPO" ]; then
    echo "❌ ERROR: MPE-Personal not found at $PERSONAL_REPO"
    echo "Clone it: git clone git@github.com:M-Ferda/MPE-Personal.git ~/MPE-Personal"
    exit 1
fi

if [ ! -d "$ASSETS/patches/patches_factory" ]; then
    echo "❌ ERROR: Factory patches not found in $ASSETS/patches/patches_factory"
    exit 1
fi

if [ ! -d "$ASSETS/patches/third-party/patches_3rdparty" ]; then
    echo "❌ ERROR: Third-party patches not found in $ASSETS/patches/third-party/patches_3rdparty"
    exit 1
fi

if [ ! -d "$ASSETS/user-data/Patches" ]; then
    echo "❌ ERROR: User patches not found in $ASSETS/user-data/Patches"
    exit 1
fi

echo "✓ MPE-Personal validated"
echo ""

echo "Step 2/3: Setting up factory patches symlink..."
if [ -L "$SURGE_RESOURCES/patches_factory" ]; then
    echo "  Already a symlink, removing old link"
    rm "$SURGE_RESOURCES/patches_factory"
elif [ -d "$SURGE_RESOURCES/patches_factory" ]; then
    BACKUP_PATH="$SURGE_RESOURCES/patches_factory.backup.$(date +%s)"
    mv "$SURGE_RESOURCES/patches_factory" "$BACKUP_PATH"
    echo "  Backed up to $(basename "$BACKUP_PATH")"
fi

ln -s "$ASSETS/patches/patches_factory" "$SURGE_RESOURCES/patches_factory"
echo "✓ Factory patches symlink created"

echo ""
echo "Setting up third-party patches symlink..."
if [ -L "$SURGE_RESOURCES/patches_3rdparty" ]; then
    echo "  Already a symlink, removing old link"
    rm "$SURGE_RESOURCES/patches_3rdparty"
elif [ -d "$SURGE_RESOURCES/patches_3rdparty" ]; then
    BACKUP_PATH="$SURGE_RESOURCES/patches_3rdparty.backup.$(date +%s)"
    mv "$SURGE_RESOURCES/patches_3rdparty" "$BACKUP_PATH"
    echo "  Backed up to $(basename "$BACKUP_PATH")"
fi

ln -s "$ASSETS/patches/third-party/patches_3rdparty" "$SURGE_RESOURCES/patches_3rdparty"
echo "✓ Third-party patches symlink created"

echo ""
echo "Step 3/3: Setting up user patches symlink..."
if [ -L "$SURGE_DOCS/Patches" ]; then
    echo "  Already a symlink, removing old link"
    rm "$SURGE_DOCS/Patches"
elif [ -d "$SURGE_DOCS/Patches" ]; then
    BACKUP_PATH="$SURGE_DOCS/Patches.backup.$(date +%s)"
    mv "$SURGE_DOCS/Patches" "$BACKUP_PATH"
    echo "  Backed up to $(basename "$BACKUP_PATH")"
fi

ln -s "$ASSETS/user-data/Patches" "$SURGE_DOCS/Patches"
echo "✓ User patches symlink created"
echo ""

echo "Verifying symlinks..."
if [ -L "$SURGE_RESOURCES/patches_factory" ]; then
    FACTORY_COUNT=$(find "$SURGE_RESOURCES/patches_factory" -name "*.fxp" -type f 2>/dev/null | wc -l)
    echo "✓ Factory: $FACTORY_COUNT patches accessible"
fi

if [ -L "$SURGE_RESOURCES/patches_3rdparty" ]; then
    THIRDPARTY_COUNT=$(find "$SURGE_RESOURCES/patches_3rdparty" -name "*.fxp" -type f 2>/dev/null | wc -l)
    echo "✓ Third-party: $THIRDPARTY_COUNT patches accessible"
fi

if [ -L "$SURGE_DOCS/Patches" ]; then
    USER_COUNT=$(find "$SURGE_DOCS/Patches" -name "*.fxp" -type f 2>/dev/null | wc -l)
    echo "✓ User: $USER_COUNT patches accessible"
fi
REMOTE_SCRIPT

echo ""
echo "Restarting Surge CLI service..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "sudo systemctl restart surge-xt-cli"
sleep 2

if ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "systemctl is-active surge-xt-cli" > /dev/null 2>&1; then
    echo "✓ Service restarted successfully"
else
    echo "⚠️  Warning: Service may not have started"
fi

echo ""
echo "========================================="
echo "  ✅ Setup Complete!"
echo "========================================="
echo ""
echo "Patch directories on Pi now point at MPE-Personal:"
echo "  $PI_PERSONAL/assets/"
echo ""
echo "To deploy patch updates:"
echo "  1. Commit/push in MPE-Personal (custom patches)"
echo "  2. On Pi: ssh $PI_USER@$PI_HOST 'cd $PI_PERSONAL && git pull'"
echo "  3. Restart: ssh $PI_USER@$PI_HOST 'sudo systemctl restart surge-xt-cli patch-browser'"
echo ""
echo "Or deploy directly from this machine:"
echo "  ./scripts/deploy-patches.sh"
echo ""
