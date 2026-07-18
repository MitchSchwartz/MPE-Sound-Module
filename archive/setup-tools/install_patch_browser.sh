#!/bin/bash
#
# Install script for Pi-Surge-MPE Patch Browser UI
#
# This script installs all dependencies and configures the patch browser
# to run automatically on boot via systemd.
#
# Usage: ./install_patch_browser.sh

set -e  # Exit on error

echo "=========================================="
echo "Pi-Surge-MPE Patch Browser UI Installer"
echo "=========================================="
echo ""

# Check if running on Raspberry Pi
if [ ! -f /proc/device-tree/model ]; then
    echo "Warning: This doesn't appear to be a Raspberry Pi"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Check if running as root (we don't want that)
if [ "$EUID" -eq 0 ]; then
    echo "Error: Do not run this script as root (don't use sudo)"
    echo "The script will prompt for sudo when needed"
    exit 1
fi

echo "Step 1: Enabling I2C interface..."
echo "--------------------------------------"
# Enable I2C in /boot/config.txt if not already enabled
if ! grep -q "^dtparam=i2c_arm=on" /boot/config.txt 2>/dev/null; then
    echo "Enabling I2C in /boot/config.txt..."
    echo "dtparam=i2c_arm=on" | sudo tee -a /boot/config.txt
    REBOOT_REQUIRED=true
else
    echo "I2C already enabled"
fi

# Load I2C kernel module
if ! lsmod | grep -q i2c_dev; then
    echo "Loading I2C kernel module..."
    sudo modprobe i2c-dev
fi

# Add to /etc/modules for automatic loading
if ! grep -q "^i2c-dev" /etc/modules 2>/dev/null; then
    echo "i2c-dev" | sudo tee -a /etc/modules
fi

echo ""
echo "Step 2: Installing system dependencies..."
echo "--------------------------------------"
sudo apt-get update
sudo apt-get install -y \
    python3-pip \
    python3-pil \
    i2c-tools \
    python3-dev \
    libjpeg-dev \
    zlib1g-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libopenjp2-7 \
    libtiff6 || sudo apt-get install -y libtiff5

echo ""
echo "Step 3: Installing Python dependencies..."
echo "--------------------------------------"
pip3 install --upgrade pip
pip3 install -r requirements.txt

echo ""
echo "Step 4: Adding user to GPIO and I2C groups..."
echo "--------------------------------------"
sudo usermod -a -G gpio,i2c $USER
echo "Added $USER to gpio and i2c groups"

echo ""
echo "Step 5: Testing I2C connection..."
echo "--------------------------------------"
if command -v i2cdetect &> /dev/null; then
    echo "Scanning I2C bus 1..."
    sudo i2cdetect -y 1
    echo ""
    read -p "Do you see a device at address 3c or 3d? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Warning: OLED display not detected!"
        echo "Please check your wiring and try again."
        echo "See docs/HARDWARE_WIRING.md for wiring instructions."
        read -p "Continue anyway? (y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
else
    echo "Warning: i2cdetect not available, skipping I2C check"
fi

echo ""
echo "Step 6: Creating systemd service..."
echo "--------------------------------------"

# Create systemd service file
sudo tee /etc/systemd/system/patch-browser.service > /dev/null <<EOF
[Unit]
Description=Pi-Surge-MPE Patch Browser UI
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$HOME
ExecStart=/usr/bin/python3 $HOME/patch_browser_ui.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

# Ensure GPIO access
SupplementaryGroups=gpio i2c

[Install]
WantedBy=multi-user.target
EOF

echo "Created /etc/systemd/system/patch-browser.service"

# Reload systemd
sudo systemctl daemon-reload

echo ""
echo "Step 7: Testing patch browser (manual run)..."
echo "--------------------------------------"
echo "Starting patch browser for 5 seconds..."
echo "You should see the OLED display initialize."
echo "Try rotating the encoder and clicking the button."
echo ""
read -p "Press Enter to start the test..."

# Kill any existing instances
pkill -f patch_browser_ui.py 2>/dev/null || true

# Run for 5 seconds then kill
timeout 5 python3 ~/patch_browser_ui.py || true

echo ""
read -p "Did the display work and encoder respond? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Warning: Patch browser test failed"
    echo "Please check:"
    echo "  - OLED display wiring (see docs/HARDWARE_WIRING.md)"
    echo "  - Encoder wiring"
    echo "  - I2C address (0x3C or 0x3D)"
    echo "  - Display driver (SH1106 vs SSD1306)"
    read -p "Continue with service installation anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo ""
echo "Step 8: Enabling auto-start service..."
echo "--------------------------------------"
sudo systemctl enable patch-browser.service
sudo systemctl start patch-browser.service

echo ""
echo "Checking service status..."
sleep 2
sudo systemctl status patch-browser.service --no-pager || true

echo ""
echo "=========================================="
echo "Installation Complete!"
echo "=========================================="
echo ""
echo "The patch browser is now running and will auto-start on boot."
echo ""
echo "Useful commands:"
echo "  sudo systemctl status patch-browser    # Check status"
echo "  sudo systemctl stop patch-browser      # Stop service"
echo "  sudo systemctl start patch-browser     # Start service"
echo "  sudo systemctl restart patch-browser   # Restart service"
echo "  sudo journalctl -u patch-browser -f    # View live logs"
echo ""

if [ "$REBOOT_REQUIRED" = true ]; then
    echo "IMPORTANT: A reboot is required for I2C changes to take effect."
    read -p "Reboot now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        sudo reboot
    else
        echo "Please reboot manually when ready: sudo reboot"
    fi
else
    echo "No reboot required. Group membership changes will take effect on next login."
    echo "To apply group changes now, log out and back in."
fi

echo ""
echo "For more information, see:"
echo "  - docs/PATCH_BROWSER_SETUP.md"
echo "  - docs/HARDWARE_WIRING.md"
echo ""
