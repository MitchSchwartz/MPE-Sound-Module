#!/bin/bash
# Setup symlinks on Pi to git repo (avoid duplication, enable instant git sync)

set -e

PI_HOST="${PI_HOST:-surge.local}"
PI_USER="${PI_USER:-mitch}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/surge_pi_key}"

echo "========================================="
echo "  Setup Pi Symlinks to Git Repo"
echo "========================================="
echo ""
echo "Target: $PI_USER@$PI_HOST"
echo ""

# Test connection
echo "Testing connection..."
if ! ssh -i "$SSH_KEY" -o ConnectTimeout=5 "$PI_USER@$PI_HOST" "echo 'Connected'" > /dev/null 2>&1; then
    echo "❌ ERROR: Cannot connect to Pi"
    exit 1
fi
echo "✓ Connected"
echo ""

# Run setup commands on Pi
echo "Setting up symlinks on Pi..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" << 'REMOTE_SCRIPT'
set -e

GIT_REPO="/home/mitch/MPE-Module"
SURGE_RESOURCES="/home/mitch/surge/resources/data"
SURGE_DOCS="/home/mitch/Documents/Surge XT"

echo "Step 1/3: Validating git repo..."
if [ ! -d "$GIT_REPO" ]; then
    echo "❌ ERROR: Git repo not found at $GIT_REPO"
    exit 1
fi

# Check patches exist
if [ ! -d "$GIT_REPO/assets/patches/patches_factory" ]; then
    echo "❌ ERROR: Factory patches not found in git repo"
    exit 1
fi

if [ ! -d "$GIT_REPO/assets/patches/third-party/patches_3rdparty" ]; then
    echo "❌ ERROR: Third-party patches not found in git repo"
    exit 1
fi

if [ ! -d "$GIT_REPO/assets/user-data/Patches" ]; then
    echo "❌ ERROR: User patches not found in git repo"
    exit 1
fi

echo "✓ Git repo validated"
echo ""

# Backup and replace factory patches
echo "Step 2/3: Setting up factory patches symlink..."
if [ -L "$SURGE_RESOURCES/patches_factory" ]; then
    echo "  Already a symlink, removing old link"
    rm "$SURGE_RESOURCES/patches_factory"
elif [ -d "$SURGE_RESOURCES/patches_factory" ]; then
    BACKUP_PATH="$SURGE_RESOURCES/patches_factory.backup.$(date +%s)"
    mv "$SURGE_RESOURCES/patches_factory" "$BACKUP_PATH"
    echo "  Backed up to $(basename "$BACKUP_PATH")"
fi

ln -s "$GIT_REPO/assets/patches/patches_factory" "$SURGE_RESOURCES/patches_factory"
echo "✓ Factory patches symlink created"

# Backup and replace third-party patches
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

ln -s "$GIT_REPO/assets/patches/third-party/patches_3rdparty" "$SURGE_RESOURCES/patches_3rdparty"
echo "✓ Third-party patches symlink created"

# Backup and replace user patches
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

ln -s "$GIT_REPO/assets/user-data/Patches" "$SURGE_DOCS/Patches"
echo "✓ User patches symlink created"
echo ""

# Verify symlinks
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
echo "All Surge patch directories now point to git repo:"
echo "  • Factory patches → git repo"
echo "  • Third-party patches → git repo"
echo "  • User patches → git repo"
echo ""
echo "Benefits:"
echo "  ✓ No duplication (patches only exist in git repo)"
echo "  ✓ Instant sync via git pull/push"
echo "  ✓ Single source of truth"
echo ""
echo "To deploy patch updates:"
echo "  1. Edit patches on Windows (auto-tracked in git)"
echo "  2. Commit and push: git commit && git push"
echo "  3. On Pi: ssh $PI_USER@$PI_HOST 'cd /home/mitch/MPE-Module && git pull'"
echo "  4. Restart service: ssh $PI_USER@$PI_HOST 'sudo systemctl restart surge-xt-cli'"
echo ""
echo "Or use the updated deploy-patches.sh script (coming next)"
echo ""
