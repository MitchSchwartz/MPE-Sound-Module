#!/bin/bash
#
# Install shutdown animation service
#

set -e

echo "=== Installing Shutdown Animation ==="

# 1. Make shutdown script executable
chmod +x /home/mitch/MPE-Module/shutdown_animation.py

# 2. Copy service file to systemd
echo "Installing shutdown-animation.service..."
sudo cp /home/mitch/MPE-Module/config/shutdown-animation.service /etc/systemd/system/shutdown-animation.service

# 3. Reload systemd
sudo systemctl daemon-reload

# 4. Enable the service
sudo systemctl enable shutdown-animation.service

echo ""
echo "=== Installation Complete ==="
echo ""
echo "The OLED will now show shutdown messages when:"
echo "  - System shuts down: 'Shutting down...' → 'Goodbye!' → blank"
echo "  - System reboots: 'Shutting down...' → 'Goodbye!' → blank"
echo ""
echo "Test with: sudo systemctl start shutdown-animation.service"
echo "Or reboot: sudo reboot"
