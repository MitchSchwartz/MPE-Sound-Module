#!/bin/bash
# Disable GUI mode and switch back to headless CLI

echo "Switching to CLI mode..."

# Remove GUI auto-start configs
rm -f ~/.bash_profile ~/.xinitrc

# Kill GUI processes
pkill -f "Surge XT"
pkill -f x11vnc
pkill -f openbox
pkill -f xinit

# Enable and start CLI service
sudo systemctl enable surge-xt-cli
sudo systemctl start surge-xt-cli

echo ""
echo "✅ CLI mode enabled!"
echo ""
echo "Headless CLI service is now running."
echo "System will boot to CLI mode from now on."
echo ""
echo "To switch back to GUI mode, run: ~/enable-gui.sh"
echo ""
read -p "Reboot now to complete switch? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo reboot
else
    echo "CLI service is running. Reboot manually when ready."
fi
