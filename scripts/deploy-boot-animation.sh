#!/bin/bash
#
# Deploy Boot Animation to Raspberry Pi
# Copies files and installs systemd services for OLED boot animation
#

set -e  # Exit on error

# Configuration
PI_HOST="surge.local"
PI_USER="mitch"
PI_HOME="/home/mitch"

echo "========================================"
echo "Boot Animation Deployment Script"
echo "========================================"
echo ""
echo "Target: ${PI_USER}@${PI_HOST}"
echo ""

# Check if we can reach the Pi
echo "Testing connection to Pi..."
if ! ssh -o ConnectTimeout=5 ${PI_USER}@${PI_HOST} "echo 'Connection successful'"; then
    echo "ERROR: Cannot connect to ${PI_USER}@${PI_HOST}"
    echo "Please check:"
    echo "  - Pi is powered on"
    echo "  - Network connection is working"
    echo "  - Hostname 'surge.local' resolves (or use IP address)"
    exit 1
fi
echo ""

# Step 1: Copy Python script
echo "[1/6] Copying boot_animation.py..."
scp boot_animation.py ${PI_USER}@${PI_HOST}:${PI_HOME}/
echo "✓ boot_animation.py copied"
echo ""

# Step 2: Copy startup script
echo "[2/6] Copying start-patch-browser.sh..."
scp scripts/start-patch-browser.sh ${PI_USER}@${PI_HOST}:${PI_HOME}/
echo "✓ start-patch-browser.sh copied"
echo ""

# Step 3: Copy systemd service files
echo "[3/6] Copying systemd service files..."
scp config/boot-animation.service ${PI_USER}@${PI_HOST}:${PI_HOME}/
scp config/patch-browser.service ${PI_USER}@${PI_HOST}:${PI_HOME}/
echo "✓ Service files copied"
echo ""

# Step 4: Make scripts executable
echo "[4/6] Making scripts executable..."
ssh ${PI_USER}@${PI_HOST} "chmod +x ${PI_HOME}/boot_animation.py ${PI_HOME}/start-patch-browser.sh"
echo "✓ Scripts are now executable"
echo ""

# Step 5: Install systemd services
echo "[5/6] Installing systemd services..."
ssh ${PI_USER}@${PI_HOST} << 'ENDSSH'
    echo "  - Installing boot-animation.service..."
    sudo cp ~/boot-animation.service /etc/systemd/system/

    echo "  - Installing patch-browser.service..."
    sudo cp ~/patch-browser.service /etc/systemd/system/

    echo "  - Reloading systemd daemon..."
    sudo systemctl daemon-reload

    echo "  - Enabling boot-animation.service..."
    sudo systemctl enable boot-animation.service

    echo "  - Enabling patch-browser.service..."
    sudo systemctl enable patch-browser.service
ENDSSH
echo "✓ Services installed and enabled"
echo ""

# Step 6: Test boot animation (optional)
echo "[6/6] Testing boot animation..."
echo "Running 5-second test animation..."
ssh ${PI_USER}@${PI_HOST} "python3 ${PI_HOME}/boot_animation.py --duration 5" || {
    echo "WARNING: Test animation failed. Check OLED display connection."
    echo "The animation may still work on boot if the display is connected properly."
}
echo ""

# Final instructions
echo "========================================"
echo "Deployment Complete!"
echo "========================================"
echo ""
echo "Services installed:"
echo "  ✓ boot-animation.service - Shows loading animation on boot"
echo "  ✓ patch-browser.service - Runs patch browser UI"
echo ""
echo "To see the boot animation in action:"
echo "  ssh ${PI_USER}@${PI_HOST}"
echo "  sudo reboot"
echo ""
echo "To check service status:"
echo "  sudo systemctl status boot-animation.service"
echo "  sudo systemctl status patch-browser.service"
echo ""
echo "To view logs:"
echo "  sudo journalctl -u boot-animation.service -f"
echo "  sudo journalctl -u patch-browser.service -f"
echo ""
echo "Full documentation: BOOT_ANIMATION_SETUP.md"
echo ""
