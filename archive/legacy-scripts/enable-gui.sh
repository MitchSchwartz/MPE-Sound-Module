#\!/bin/bash
# Enable GUI mode (with VNC and Surge GUI)

echo "Enabling GUI mode..."

# Copy GUI configs
cp ~/.bash_profile.gui_backup ~/.bash_profile
cp ~/.xinitrc.gui_backup ~/.xinitrc

# Stop CLI service
sudo systemctl stop surge-xt-cli
sudo systemctl disable surge-xt-cli

echo ""
echo "✅ GUI mode enabled\!"
echo ""
echo "System will reboot to GUI mode with VNC access."
echo "After reboot, connect via VNC to: $(hostname -I | awk '{print $1}'):5900"
echo ""
echo "To switch back to CLI mode, run: ~/disable-gui.sh"
echo ""
read -p "Reboot now? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo reboot
else
    echo "GUI will start on next boot. Reboot manually when ready."
fi
