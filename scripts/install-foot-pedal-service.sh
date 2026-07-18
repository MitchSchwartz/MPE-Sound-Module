#!/bin/bash
# Install foot pedal service with udev auto-start

set -e

echo "=== Installing Foot Pedal Service ==="
echo ""

# Copy service file
echo "1. Copying service file..."
sudo cp config/foot-pedal.service /etc/systemd/system/
echo "   ✓ Service file copied"

# Copy udev rule
echo ""
echo "2. Copying udev rule..."
sudo cp config/99-foot-pedal.rules /etc/udev/rules.d/
echo "   ✓ udev rule copied"

# Reload udev
echo ""
echo "3. Reloading udev rules..."
sudo udevadm control --reload-rules
sudo udevadm trigger
echo "   ✓ udev reloaded"

# Reload systemd
echo ""
echo "4. Reloading systemd daemon..."
sudo systemctl daemon-reload
echo "   ✓ Daemon reloaded"

# Check if pedal is connected and start if so
echo ""
echo "5. Checking for foot pedal..."
if [ -e /dev/input/by-id/usb-PCsensor_FootSwitch-event-kbd ]; then
    echo "   ✓ Foot pedal detected, starting service..."
    sudo systemctl start foot-pedal.service
    sleep 2
    sudo systemctl status foot-pedal.service --no-pager -l
else
    echo "   ⓘ Foot pedal not connected"
    echo "   ⓘ Service will auto-start when pedal is plugged in"
fi

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Behavior:"
echo "  - Service starts automatically when pedal is plugged in"
echo "  - Service stops automatically when pedal is unplugged"
echo "  - No resource usage when pedal is not connected"
echo ""
echo "Commands:"
echo "  Start:   sudo systemctl start foot-pedal"
echo "  Stop:    sudo systemctl stop foot-pedal"
echo "  Restart: sudo systemctl restart foot-pedal"
echo "  Status:  sudo systemctl status foot-pedal"
echo "  Logs:    journalctl -u foot-pedal -f"
