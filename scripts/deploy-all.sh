#!/bin/bash
# Deploy complete system from git repo to Pi

set -e

PI_HOST="${PI_HOST:-surge.local}"
PI_USER="${PI_USER:-mitch}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/surge_pi_key}"

echo "======================================"
echo "  Complete System Deployment"
echo "======================================"
echo ""
echo "Target: $PI_USER@$PI_HOST"
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

# Create directories on Pi
echo "Step 1/8: Creating directories..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" << 'EOF'
mkdir -p ~/surge/build/surge_xt_products
mkdir -p ~/surge/resources/data
mkdir -p ~/scripts
mkdir -p ~/.local/share/Surge\ XT
EOF
echo "✓ Directories created"
echo ""

# Deploy Surge binary
echo "Step 2/8: Deploying Surge binary (24MB)..."
if [ -f "assets/binaries/surge-xt-cli" ]; then
    scp -i "$SSH_KEY" assets/binaries/surge-xt-cli \
        "$PI_USER@$PI_HOST:/home/mitch/surge/build/surge_xt_products/"
    ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "chmod +x ~/surge/build/surge_xt_products/surge-xt-cli"
    echo "✓ Binary deployed"
else
    echo "⚠️  Warning: Surge binary not found in assets/binaries/"
fi
echo ""

# Deploy factory patches
echo "Step 3/8: Deploying factory patches (47MB)..."
if [ -d "assets/patches/patches_factory" ]; then
    echo "Creating archive..."
    cd assets/patches && tar czf /tmp/patches_factory.tar.gz patches_factory/ && cd ../..
    echo "Uploading..."
    scp -i "$SSH_KEY" /tmp/patches_factory.tar.gz "$PI_USER@$PI_HOST:/tmp/"
    echo "Extracting on Pi..."
    ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" << 'EOF'
cd ~/surge/resources/data
tar xzf /tmp/patches_factory.tar.gz
rm /tmp/patches_factory.tar.gz
EOF
    rm /tmp/patches_factory.tar.gz
    echo "✓ Factory patches deployed"
else
    echo "⚠️  Warning: Factory patches not found in assets/patches/patches_factory"
fi
echo ""

# Deploy third-party patches
echo "Step 4/8: Deploying third-party patches (375MB, this may take a few minutes)..."
if [ -d "assets/patches/third-party" ]; then
    echo "Creating archive..."
    cd assets/patches && tar czf /tmp/patches_3rdparty.tar.gz third-party/ && cd ../..
    echo "Uploading..."
    scp -i "$SSH_KEY" /tmp/patches_3rdparty.tar.gz "$PI_USER@$PI_HOST:/tmp/"
    echo "Extracting on Pi..."
    ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" << 'EOF'
cd ~/surge/resources/data
tar xzf /tmp/patches_3rdparty.tar.gz
mv third-party patches_3rdparty
rm /tmp/patches_3rdparty.tar.gz
EOF
    rm /tmp/patches_3rdparty.tar.gz
    echo "✓ Third-party patches deployed"
else
    echo "⚠️  Warning: Third-party patches not found in assets/patches/third-party"
fi
echo ""

# Deploy scripts
echo "Step 5/8: Deploying scripts..."
scp -i "$SSH_KEY" scripts/*.sh "$PI_USER@$PI_HOST:~/scripts/" 2>/dev/null || true
if [ -f "scripts/start-surge-cli.sh" ]; then
    scp -i "$SSH_KEY" scripts/start-surge-cli.sh "$PI_USER@$PI_HOST:~/"
fi
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "chmod +x ~/scripts/*.sh ~/start-surge-cli.sh 2>/dev/null || true"
echo "✓ Scripts deployed"
echo ""

# Deploy Python scripts
echo "Step 6/8: Deploying Python scripts..."
scp -i "$SSH_KEY" *.py "$PI_USER@$PI_HOST:~/" 2>/dev/null || echo "  (No Python scripts found)"
echo ""

# Deploy systemd services
echo "Step 7/8: Deploying systemd services..."
if [ -d "config" ]; then
    scp -i "$SSH_KEY" config/*.service "$PI_USER@$PI_HOST:~/" 2>/dev/null && {
        ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" << 'EOF'
sudo cp ~/*.service /etc/systemd/system/ 2>/dev/null
sudo systemctl daemon-reload
sudo systemctl enable surge-xt-cli 2>/dev/null || true
sudo systemctl enable patch-browser 2>/dev/null || true
sudo systemctl enable boot-animation 2>/dev/null || true
sudo systemctl enable surge-watchdog 2>/dev/null || true
EOF
        echo "✓ Services deployed and enabled"
    } || echo "  (No service files found)"

    # Deploy udev rules
    scp -i "$SSH_KEY" config/99-*.rules "$PI_USER@$PI_HOST:~/" 2>/dev/null && {
        ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" << 'EOF'
sudo cp ~/99-*.rules /etc/udev/rules.d/ 2>/dev/null || true
sudo udevadm control --reload-rules 2>/dev/null || true
sudo udevadm trigger 2>/dev/null || true
EOF
        echo "✓ Udev rules deployed and activated"
    } || echo "  (No udev rules found)"
else
    echo "  (No config files found in config/)"
fi
echo ""

# Deploy user data
echo "Step 8/8: Deploying user data..."
if [ -f "assets/user-data/SurgeXTUserDefaults.xml" ]; then
    scp -i "$SSH_KEY" assets/user-data/SurgeXTUserDefaults.xml \
        "$PI_USER@$PI_HOST:.local/share/Surge\ XT/"
    echo "✓ User preferences deployed"
else
    echo "  (No user preferences found)"
fi

if [ -d "assets/user-data/custom-patches" ]; then
    ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "mkdir -p '/home/mitch/Documents/Surge XT/Patches'"
    cd assets/user-data && tar czf /tmp/custom-patches.tar.gz custom-patches/ && cd ../..
    scp -i "$SSH_KEY" /tmp/custom-patches.tar.gz "$PI_USER@$PI_HOST:/tmp/"
    ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" << 'EOF'
cd "/home/mitch/Documents/Surge XT"
tar xzf /tmp/custom-patches.tar.gz
mv custom-patches/* Patches/ 2>/dev/null || true
rmdir custom-patches 2>/dev/null || true
rm /tmp/custom-patches.tar.gz
EOF
    rm /tmp/custom-patches.tar.gz
    echo "✓ Custom patches deployed"
else
    echo "  (No custom patches found)"
fi
echo ""

# Start services
echo "Starting services..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" << 'EOF'
sudo systemctl restart surge-xt-cli 2>/dev/null || echo "  (surge-xt-cli service not found)"
sudo systemctl restart patch-browser 2>/dev/null || echo "  (patch-browser service not found)"
sudo systemctl restart boot-animation 2>/dev/null || echo "  (boot-animation service not found)"
EOF
echo ""

echo "======================================"
echo "  ✅ Deployment Complete!"
echo "======================================"
echo ""
echo "Next steps:"
echo "  1. Check status: ssh -i $SSH_KEY $PI_USER@$PI_HOST 'systemctl status surge-xt-cli'"
echo "  2. View logs: ssh -i $SSH_KEY $PI_USER@$PI_HOST 'tail -30 ~/surge-cli.log'"
echo "  3. Test device: Play your MIDI controller!"
echo ""
