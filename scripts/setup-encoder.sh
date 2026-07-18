#!/bin/bash
#
# Setup rotary encoder with kernel-level device tree overlay
# This script configures the encoder to use Linux kernel driver for reliable operation
#

set -e

echo "========================================="
echo "Rotary Encoder Setup (Kernel Driver)"
echo "========================================="
echo ""

# Detect config.txt location (varies by Pi model/OS)
if [ -f /boot/firmware/config.txt ]; then
    CONFIG_FILE="/boot/firmware/config.txt"
elif [ -f /boot/config.txt ]; then
    CONFIG_FILE="/boot/config.txt"
else
    echo "Error: Cannot find config.txt"
    echo "Checked: /boot/firmware/config.txt and /boot/config.txt"
    exit 1
fi

echo "Using config file: $CONFIG_FILE"
echo ""

# Encoder overlay configuration
# steps-per-period=2 is CRITICAL for KY-040 (generates 2 events per detent)
ENCODER_OVERLAY="dtoverlay=rotary-encoder,pin_a=17,pin_b=27,relative_axis=1,encoding=gray,steps-per-period=2"

# Check if overlay already configured
if grep -q "^dtoverlay=rotary-encoder" "$CONFIG_FILE"; then
    echo "✓ Rotary encoder overlay already configured"
    echo "  Current setting:"
    grep "^dtoverlay=rotary-encoder" "$CONFIG_FILE"
    echo ""
else
    echo "Adding rotary encoder overlay to $CONFIG_FILE..."

    # Backup config file
    sudo cp "$CONFIG_FILE" "${CONFIG_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
    echo "  Backup created: ${CONFIG_FILE}.backup.$(date +%Y%m%d_%H%M%S)"

    # Add overlay configuration
    echo "" | sudo tee -a "$CONFIG_FILE" > /dev/null
    echo "# Rotary encoder (KY-040) on GPIO 17/27 - kernel-level handling" | sudo tee -a "$CONFIG_FILE" > /dev/null
    echo "$ENCODER_OVERLAY" | sudo tee -a "$CONFIG_FILE" > /dev/null

    echo "  ✓ Rotary encoder overlay added"
    echo ""
fi

# Add user to input group for device access
echo "Configuring permissions..."
if groups | grep -q "\binput\b"; then
    echo "✓ User already in 'input' group"
else
    echo "Adding user to 'input' group..."
    sudo usermod -a -G input "$USER"
    echo "  ✓ User added to input group"
    echo "  Note: You must log out and back in (or reboot) for group changes to take effect"
fi
echo ""

# Install python-evdev if not already installed
echo "Checking dependencies..."
if python3 -c "import evdev" 2>/dev/null; then
    echo "✓ python-evdev already installed"
else
    echo "Installing python-evdev..."
    pip3 install python-evdev
    echo "  ✓ python-evdev installed"
fi
echo ""

echo "========================================="
echo "Setup Complete!"
echo "========================================="
echo ""
echo "Configuration summary:"
echo "  • Encoder pins: GPIO 17 (CLK), GPIO 27 (DT)"
echo "  • Device tree overlay: rotary-encoder"
echo "  • Driver: Linux kernel input subsystem"
echo "  • Python library: python-evdev"
echo ""
echo "Next steps:"
echo "  1. Reboot the system: sudo reboot"
echo "  2. After reboot, verify device: ls -l /dev/input/by-path/"
echo "  3. Test encoder events: evtest /dev/input/event*"
echo "  4. Run patch browser - it will automatically use kernel driver"
echo ""
echo "The patch browser will automatically detect and use the kernel"
echo "encoder driver. If the driver is unavailable, it will fall back"
echo "to software-based handling (gpiozero)."
echo ""
