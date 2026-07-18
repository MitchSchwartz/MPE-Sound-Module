#!/bin/bash
# Pull all assets from Pi to git repo

set -e

PI_HOST="${PI_HOST:-surge.local}"
PI_USER="${PI_USER:-mitch}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/surge_pi_key}"

echo "======================================="
echo "  Pulling Complete Device Backup"
echo "======================================="
echo ""
echo "Source: $PI_USER@$PI_HOST"
echo "SSH Key: $SSH_KEY"
echo ""

# Test connection
echo "Testing connection..."
if ! ssh -i "$SSH_KEY" -o ConnectTimeout=5 "$PI_USER@$PI_HOST" "echo 'Connected'" > /dev/null; then
    echo "❌ ERROR: Cannot connect to Pi"
    echo ""
    echo "Troubleshooting:"
    echo "  1. Check Pi is powered on"
    echo "  2. Try: ssh -i $SSH_KEY $PI_USER@$PI_HOST"
    echo "  3. Or use IP: export PI_HOST=192.168.1.203"
    exit 1
fi
echo "✓ Connected"
echo ""

# Pull Surge binary
echo "Step 1/5: Pulling Surge binary (24MB)..."
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/home/mitch/surge/build/surge_xt_products/surge-xt-cli" \
    assets/binaries/ || {
    echo "⚠️  Warning: Could not pull Surge binary"
}
echo ""

# Pull factory patches
echo "Step 2/5: Pulling factory patches (47MB, 639 patches)..."
echo "Creating tar archive on Pi and downloading..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "cd /home/mitch/surge/resources/data && tar czf /tmp/patches_factory.tar.gz patches_factory/" || {
    echo "⚠️  Warning: Could not create factory patches archive"
}
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/tmp/patches_factory.tar.gz" assets/patches/ || {
    echo "⚠️  Warning: Could not download factory patches"
}
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "rm -f /tmp/patches_factory.tar.gz"
if [ -f "assets/patches/patches_factory.tar.gz" ]; then
    echo "Extracting factory patches..."
    cd assets/patches && tar xzf patches_factory.tar.gz && rm patches_factory.tar.gz && cd ../..
fi
echo ""

# Pull third-party patches
echo "Step 3/5: Pulling third-party patches (375MB, 2,553 patches)..."
echo "Creating tar archive on Pi and downloading (this will take several minutes)..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "cd /home/mitch/surge/resources/data && tar czf /tmp/patches_3rdparty.tar.gz patches_3rdparty/" || {
    echo "⚠️  Warning: Could not create third-party patches archive"
}
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/tmp/patches_3rdparty.tar.gz" assets/patches/ || {
    echo "⚠️  Warning: Could not download third-party patches"
}
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "rm -f /tmp/patches_3rdparty.tar.gz"
if [ -f "assets/patches/patches_3rdparty.tar.gz" ]; then
    echo "Extracting third-party patches..."
    cd assets/patches && tar xzf patches_3rdparty.tar.gz && mv patches_3rdparty third-party && rm patches_3rdparty.tar.gz && cd ../..
fi
echo ""

# Pull active configs
echo "Step 4/5: Pulling active system configs..."
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/systemd/system/surge-xt-cli.service" \
    assets/configs/active/ 2>/dev/null || echo "  (surge-xt-cli.service not found)"

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/systemd/system/patch-browser.service" \
    assets/configs/active/ 2>/dev/null || echo "  (patch-browser.service not found)"

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/systemd/system/boot-animation.service" \
    assets/configs/active/ 2>/dev/null || echo "  (boot-animation.service not found)"

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/systemd/system/surge-watchdog.service" \
    assets/configs/active/ 2>/dev/null || echo "  (surge-watchdog.service not found)"

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/udev/rules.d/99-roli-seaboard.rules" \
    assets/configs/active/ 2>/dev/null || echo "  (99-roli-seaboard.rules not found)"

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/etc/udev/rules.d/99-usb-audio.rules" \
    assets/configs/active/ 2>/dev/null || echo "  (99-usb-audio.rules not found)"
echo ""

# Pull user data
echo "Step 5/5: Pulling user data..."
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:.local/share/Surge\ XT/SurgeXTUserDefaults.xml" \
    assets/user-data/ 2>/dev/null || echo "  (SurgeXTUserDefaults.xml not found)"

# Pull user-created patches if they exist
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "[ -d '/home/mitch/Documents/Surge XT/Patches' ] && cd '/home/mitch/Documents/Surge XT' && tar czf /tmp/custom-patches.tar.gz Patches/ 2>/dev/null" || echo "  (No custom patches directory found)"
scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/tmp/custom-patches.tar.gz" assets/user-data/ 2>/dev/null && {
    echo "  Extracting custom patches..."
    cd assets/user-data && tar xzf custom-patches.tar.gz && mv Patches custom-patches && rm custom-patches.tar.gz && cd ../..
} || echo "  (No custom patches to download)"
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "rm -f /tmp/custom-patches.tar.gz" 2>/dev/null
echo ""

echo "======================================="
echo "  ✅ Pull Complete!"
echo "======================================="
echo ""
echo "Files pulled to:"
echo "  - assets/binaries/surge-xt-cli"
echo "  - assets/patches/factory/ (639 patches)"
echo "  - assets/patches/third-party/ (2,553 patches)"
echo "  - assets/configs/active/ (systemd services, udev rules)"
echo "  - assets/user-data/ (preferences, custom patches)"
echo ""
echo "Next steps:"
echo "  1. Review changes: git status"
echo "  2. Commit to git: git add assets/ && git commit -m 'Initial backup from device'"
echo "  3. Push to GitHub: git push"
echo ""
echo "Note: First push will be large (~450MB) and may take a few minutes."
echo ""
