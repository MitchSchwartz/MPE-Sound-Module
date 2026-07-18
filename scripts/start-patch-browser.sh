#!/bin/bash
#
# Start Patch Browser UI
# Stops the boot animation and starts the patch browser
#

# Stop boot animation if running
if systemctl is-active --quiet boot-animation.service; then
    echo "Stopping boot animation..."
    sudo systemctl stop boot-animation.service
fi

# Wait a moment for display to clear
sleep 0.5

# Start patch browser
echo "Starting patch browser UI..."
cd /home/mitch/MPE-Module
# Use -u flag for unbuffered output so logs appear immediately
python3 -u /home/mitch/MPE-Module/patch_browser_ui.py
