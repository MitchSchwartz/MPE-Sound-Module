#!/bin/bash
# Switch from Surge CLI service to GUI mode for patch editing
# Usage: ./switch-to-gui.sh

echo "Stopping Surge XT CLI service..."
sudo systemctl stop surge-xt-cli

echo "Waiting for service to stop..."
sleep 2

# Check if GUI binary exists
GUI_BINARY="$HOME/surge/build/src/surge-xt/surge-xt_artefacts/Release/Standalone/Surge XT"

if [ ! -f "$GUI_BINARY" ]; then
    echo "ERROR: GUI binary not found at: $GUI_BINARY"
    echo "Starting CLI service again..."
    sudo systemctl start surge-xt-cli
    exit 1
fi

echo "Surge XT CLI stopped."
echo ""
echo "To start the GUI, you have two options:"
echo ""
echo "Option 1 - VNC (requires VNC server running):"
echo "  DISPLAY=:0 '$GUI_BINARY' &"
echo ""
echo "Option 2 - X11 forwarding (from Windows with X server):"
echo "  ssh -X mitch@surge.local"
echo "  Then run: '$GUI_BINARY' &"
echo ""
echo "When done editing, run: ./switch-to-cli.sh"
echo ""
echo "Note: MPE settings in GUI won't persist. This is for patch editing only."
