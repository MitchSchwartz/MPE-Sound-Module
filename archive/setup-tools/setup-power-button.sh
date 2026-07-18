#!/bin/bash
#
# Setup Power Button for Raspberry Pi
# Configures GPIO 22 (encoder button) to wake/power on the Pi
#

set -e

echo "========================================"
echo "Power Button Setup for Raspberry Pi"
echo "========================================"
echo ""
echo "This script configures the encoder button (GPIO 22)"
echo "to power on the Raspberry Pi when held for 3+ seconds"
echo "while the system is off."
echo ""

# Check if running on Pi
if [ ! -f /boot/firmware/config.txt ]; then
    echo "ERROR: /boot/firmware/config.txt not found"
    echo "This script must run on the Raspberry Pi"
    exit 1
fi

# Backup config.txt
echo "Backing up /boot/firmware/config.txt..."
sudo cp /boot/firmware/config.txt /boot/firmware/config.txt.backup.$(date +%Y%m%d_%H%M%S)
echo "✓ Backup created"
echo ""

# Check if dtoverlay already exists
if grep -q "^dtoverlay=gpio-shutdown" /boot/firmware/config.txt; then
    echo "gpio-shutdown overlay already configured in config.txt"
    echo "Current configuration:"
    grep "gpio-shutdown" /boot/firmware/config.txt
    echo ""
    read -p "Do you want to replace it? (y/n) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        # Remove existing line
        sudo sed -i '/^dtoverlay=gpio-shutdown/d' /boot/firmware/config.txt
    else
        echo "Keeping existing configuration"
        exit 0
    fi
fi

# Add gpio-shutdown overlay
echo "Adding gpio-shutdown overlay to config.txt..."
echo "" | sudo tee -a /boot/firmware/config.txt > /dev/null
echo "# Power button configuration (encoder button on GPIO 22)" | sudo tee -a /boot/firmware/config.txt > /dev/null
echo "# Hold button for 3+ seconds when off to power on" | sudo tee -a /boot/firmware/config.txt > /dev/null
echo "# Active low with pull-up (button connects to ground)" | sudo tee -a /boot/firmware/config.txt > /dev/null
echo "dtoverlay=gpio-shutdown,gpio_pin=22,active_low=1,gpio_pull=up,debounce=3000" | sudo tee -a /boot/firmware/config.txt > /dev/null
echo "✓ Configuration added"
echo ""

# Show what was added
echo "Added configuration:"
echo "---"
tail -5 /boot/firmware/config.txt
echo "---"
echo ""

echo "========================================"
echo "Setup Complete!"
echo "========================================"
echo ""
echo "Configuration:"
echo "  • GPIO Pin: 22 (encoder button)"
echo "  • Active: Low (button press connects to ground)"
echo "  • Pull: Up (internal pull-up resistor enabled)"
echo "  • Debounce: 3000ms (3 seconds - must hold button)"
echo ""
echo "Behavior:"
echo "  • When Pi is OFF: Hold button 3+ seconds to power on"
echo "  • When Pi is ON: 8-second hold triggers shutdown (via patch browser)"
echo ""
echo "IMPORTANT: You must reboot for this to take effect!"
echo ""
read -p "Reboot now? (y/n) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Rebooting in 5 seconds... (Ctrl+C to cancel)"
    sleep 5
    sudo reboot
else
    echo "Remember to reboot later: sudo reboot"
fi
