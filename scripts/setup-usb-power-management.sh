#!/bin/bash
# Setup USB power management to prevent Roli disconnection issues
# Disables autosuspend for USB devices to prevent random disconnects

echo "=== USB Power Management Setup ==="
echo ""

# Check if running on Pi
if [[ $(hostname) != "surge" ]] && [[ $(hostname) != *"raspberrypi"* ]]; then
    echo "Not running on Raspberry Pi, skipping USB power management setup"
    exit 0
fi

echo "Disabling USB autosuspend for all USB devices..."
echo "This prevents USB devices from going to sleep and disconnecting"

# Create udev rule to disable autosuspend
UDEV_RULE="/etc/udev/rules.d/99-usb-no-autosuspend.rules"

sudo tee "$UDEV_RULE" > /dev/null << 'EOF'
# Disable USB autosuspend to prevent device disconnections
# This is especially important for MIDI devices like the Roli Seaboard

# Disable autosuspend for all USB devices
ACTION=="add", SUBSYSTEM=="usb", TEST=="power/control", ATTR{power/control}="on"
ACTION=="add", SUBSYSTEM=="usb", TEST=="power/autosuspend", ATTR{power/autosuspend}="-1"

# Specifically for Roli Seaboard
ACTION=="add", SUBSYSTEM=="usb", ATTRS{idVendor}=="2af4", ATTRS{idProduct}=="0700", ATTR{power/autosuspend}="-1"
EOF

echo "✓ Created udev rule: $UDEV_RULE"

# Reload udev rules
echo "Reloading udev rules..."
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb

echo "✓ USB power management configured"
echo ""
echo "To apply to currently connected devices, you may need to:"
echo "  1. Unplug and replug USB devices, OR"
echo "  2. Reboot the system"
echo ""

