#!/bin/bash
# Quick deployment script for audio robustness upgrade
# Run this from your Windows machine with Git Bash or WSL

set -e  # Exit on error

PI_HOST="${PI_HOST:-surge.local}"
PI_USER="${PI_USER:-mitch}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/surge_pi_key}"

echo "======================================="
echo "  Audio Robustness Deployment"
echo "======================================="
echo ""
echo "Target: $PI_USER@$PI_HOST"
echo "SSH Key: $SSH_KEY"
echo ""

# Test SSH connection
echo "Testing SSH connection..."
if ! ssh -i "$SSH_KEY" -o ConnectTimeout=5 "$PI_USER@$PI_HOST" "echo 'Connection OK'"; then
    echo "❌ ERROR: Cannot connect to Pi"
    echo ""
    echo "Troubleshooting:"
    echo "  1. Check Pi is powered on and connected to network"
    echo "  2. Try: ssh -i $SSH_KEY $PI_USER@$PI_HOST"
    echo "  3. Or use IP: export PI_HOST=192.168.1.203"
    exit 1
fi
echo "✓ Connected to Pi"
echo ""

# Create backups
echo "Step 1: Creating backups..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" << 'ENDSSH'
mkdir -p ~/backups
cp ~/start-surge-cli.sh ~/backups/start-surge-cli.sh.backup-$(date +%Y%m%d-%H%M%S) 2>/dev/null || true
sudo cp /etc/systemd/system/surge-xt-cli.service ~/backups/surge-xt-cli.service.backup-$(date +%Y%m%d-%H%M%S) 2>/dev/null || true
echo "✓ Backups created in ~/backups/"
ENDSSH

# Create scripts directory
echo "Step 2: Creating scripts directory..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "mkdir -p ~/scripts"
echo "✓ Scripts directory ready"
echo ""

# Upload scripts
echo "Step 3: Uploading scripts..."
scp -i "$SSH_KEY" scripts/detect-audio-device.sh "$PI_USER@$PI_HOST:~/scripts/" || exit 1
scp -i "$SSH_KEY" scripts/test-audio-detection.sh "$PI_USER@$PI_HOST:~/scripts/" || exit 1
scp -i "$SSH_KEY" scripts/start-surge-cli.sh "$PI_USER@$PI_HOST:~/scripts/" || exit 1
scp -i "$SSH_KEY" config/surge-xt-cli.service "$PI_USER@$PI_HOST:~/" || exit 1
echo "✓ Scripts uploaded"
echo ""

# Set permissions
echo "Step 4: Setting permissions..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" << 'ENDSSH'
chmod +x ~/scripts/detect-audio-device.sh
chmod +x ~/scripts/test-audio-detection.sh
chmod +x ~/scripts/start-surge-cli.sh
echo "✓ Permissions set"
ENDSSH

# Update startup script link
echo "Step 5: Updating startup script link..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" << 'ENDSSH'
rm -f ~/start-surge-cli.sh
ln -s ~/scripts/start-surge-cli.sh ~/start-surge-cli.sh
echo "✓ Startup script linked"
ENDSSH

# Test detection
echo ""
echo "Step 6: Testing audio detection..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "~/scripts/test-audio-detection.sh" || {
    echo "⚠️  Warning: Detection test had issues, but continuing..."
}
echo ""

# Update systemd service
echo "Step 7: Updating systemd service..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" << 'ENDSSH'
sudo cp ~/surge-xt-cli.service /etc/systemd/system/
sudo systemctl daemon-reload
echo "✓ Service updated"
ENDSSH

# Restart service
echo ""
echo "Step 8: Restarting Surge service..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" << 'ENDSSH'
sudo systemctl stop surge-xt-cli
sleep 2
echo "" > ~/surge-cli.log
sudo systemctl start surge-xt-cli
sleep 3
ENDSSH
echo "✓ Service restarted"
echo ""

# Check status
echo "Step 9: Checking service status..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" << 'ENDSSH'
if systemctl is-active --quiet surge-xt-cli; then
    echo "✅ Service is running!"
    echo ""
    echo "Recent logs:"
    tail -15 ~/surge-cli.log
else
    echo "❌ Service failed to start"
    echo ""
    echo "Service status:"
    sudo systemctl status surge-xt-cli --no-pager -l
    echo ""
    echo "Recent logs:"
    tail -30 ~/surge-cli.log
    exit 1
fi
ENDSSH

echo ""
echo "======================================="
echo "  ✅ Deployment Complete!"
echo "======================================="
echo ""
echo "Next steps:"
echo "  1. Test audio by playing your Roli Seaboard"
echo "  2. Check logs: ssh -i $SSH_KEY $PI_USER@$PI_HOST 'tail -30 ~/surge-cli.log'"
echo "  3. Verify tier selection in logs"
echo ""
echo "Optional: Install USB hot-plug support"
echo "  scp -i $SSH_KEY config/99-usb-audio.rules $PI_USER@$PI_HOST:~/"
echo "  ssh -i $SSH_KEY $PI_USER@$PI_HOST 'sudo cp ~/99-usb-audio.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules'"
echo ""
