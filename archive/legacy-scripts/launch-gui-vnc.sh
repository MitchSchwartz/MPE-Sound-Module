#!/bin/bash
# Launch Surge XT GUI via VNC (assumes VNC server is running)
# Run this AFTER switch-to-gui.sh
# Usage: ./launch-gui-vnc.sh

GUI_BINARY="$HOME/surge/build/src/surge-xt/surge-xt_artefacts/Release/Standalone/Surge XT"
AUDIO_DEVICE="0.23"  # Sound Blaster Play! 3
LOG_FILE="$HOME/surge-gui.log"

# Check if CLI service is still running
if systemctl is-active --quiet surge-xt-cli; then
    echo "ERROR: CLI service is still running!"
    echo "Run ./switch-to-gui.sh first to stop the CLI service."
    exit 1
fi

# Check if GUI binary exists
if [ ! -f "$GUI_BINARY" ]; then
    echo "ERROR: GUI binary not found at: $GUI_BINARY"
    exit 1
fi

echo "Starting Surge XT GUI on VNC display..."
echo "$(date): Starting Surge XT GUI via VNC..." >> "$LOG_FILE"

# Launch GUI on VNC display :0
DISPLAY=:0 "$GUI_BINARY" >> "$LOG_FILE" 2>&1 &

GUI_PID=$!
echo "Surge XT GUI started with PID $GUI_PID"
echo "$(date): GUI started with PID $GUI_PID" >> "$LOG_FILE"
echo ""
echo "Connect via VNC to edit patches."
echo "When done, run: ./switch-to-cli.sh"
echo ""
echo "GUI log: tail -f $LOG_FILE"
