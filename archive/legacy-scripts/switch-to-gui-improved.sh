#!/bin/bash
# Switch from CLI to GUI mode for patch editing
# Does NOT require reboot - instant switch
# Usage: ./switch-to-gui-improved.sh

set -e

GUI_BINARY="$HOME/surge/build/src/surge-xt/surge-xt_artefacts/Release/Standalone/Surge XT"
LOG_FILE="$HOME/surge-gui.log"

echo "=== Switching to Surge XT GUI Mode ==="
echo ""

# 1. Check if GUI binary exists
if [ ! -f "$GUI_BINARY" ]; then
    echo "❌ ERROR: GUI binary not found at: $GUI_BINARY"
    exit 1
fi

# 2. Check if VNC is available
if ! systemctl is-active --quiet wayvnc; then
    echo "⚠️  Warning: VNC server not running (will start automatically)"
fi

# 3. Stop CLI service
echo "Stopping Surge XT CLI service..."
sudo systemctl stop surge-xt-cli

# Wait for clean shutdown
sleep 2

# Verify CLI is stopped
if systemctl is-active --quiet surge-xt-cli; then
    echo "❌ ERROR: Failed to stop CLI service"
    exit 1
fi

# 3a. Disable udev auto-restart rules (prevents CLI from starting during GUI session)
echo "Disabling udev auto-restart rules..."
if [ -f /etc/udev/rules.d/99-usb-audio.rules ]; then
    sudo mv /etc/udev/rules.d/99-usb-audio.rules /etc/udev/rules.d/99-usb-audio.rules.disabled || true
fi
if [ -f /etc/udev/rules.d/99-roli-seaboard.rules ]; then
    sudo mv /etc/udev/rules.d/99-roli-seaboard.rules /etc/udev/rules.d/99-roli-seaboard.rules.disabled || true
fi
sudo udevadm control --reload-rules

# 4. Kill any lingering GUI processes (just in case)
if pgrep -f "Surge XT" > /dev/null; then
    echo "Cleaning up existing GUI processes..."
    pkill -f "Surge XT"
    sleep 1
fi

# 5. Make user defaults writable (GUI needs to save patch edits)
USER_DEFAULTS="$HOME/.local/share/Surge XT/SurgeXTUserDefaults.xml"
if [ -f "$USER_DEFAULTS" ]; then
    echo "Making user defaults writable for GUI patch editing..."
    chmod 644 "$USER_DEFAULTS"

    # Verify permissions were set correctly
    ACTUAL_PERMS=$(stat -c %a "$USER_DEFAULTS" 2>/dev/null || echo "000")
    if [ "$ACTUAL_PERMS" != "644" ]; then
        echo "⚠️  WARNING: Failed to set permissions to 644 (got $ACTUAL_PERMS)"
        echo "   GUI may not be able to save patches"
    fi
fi

# 5a. Create symlinks to factory patches (if not already present)
PATCHES_DIR="$HOME/Documents/Surge XT/Patches"
if [ -d "$PATCHES_DIR" ]; then
    if [ ! -e "$PATCHES_DIR/Factory" ]; then
        echo "Creating symlink to factory patches..."
        ln -sf "$HOME/surge/resources/data/patches_factory" "$PATCHES_DIR/Factory"
    fi
    if [ ! -e "$PATCHES_DIR/Third Party" ]; then
        echo "Creating symlink to 3rd party patches..."
        ln -sf "$HOME/surge/resources/data/patches_3rdparty" "$PATCHES_DIR/Third Party"
    fi
fi

# 6. Start GUI on existing display
echo "Starting Surge XT GUI..."
echo "$(date): Starting GUI mode..." >> "$LOG_FILE"

# Start GUI on DISPLAY :0 (assumes X server already running or GUI creates its own)
DISPLAY=:0 "$GUI_BINARY" >> "$LOG_FILE" 2>&1 &
GUI_PID=$!

sleep 3

# 7. Verify GUI is running
if ! ps -p $GUI_PID > /dev/null 2>&1; then
    echo "❌ ERROR: GUI failed to start"
    echo "Check log: tail -f $LOG_FILE"
    echo ""
    echo "Restarting CLI service..."
    sudo systemctl start surge-xt-cli
    exit 1
fi

# 8. Start VNC server for remote access
echo "Starting VNC server..."

# Start wayvnc (Wayland VNC server)
sudo systemctl start wayvnc

# Wait for VNC to initialize
sleep 2

# Verify VNC started
if systemctl is-active --quiet wayvnc; then
    echo "✅ VNC server started successfully"
    echo "   Connect to: surge.local:5900"
else
    echo "⚠️  WARNING: VNC server failed to start"
    journalctl -u wayvnc -n 10 --no-pager
    echo "   GUI is still accessible via HDMI"
fi

# 8. Verify only GUI is running (not CLI)
echo ""
echo "✅ Successfully switched to GUI mode!"
echo ""
echo "Running processes:"
ps aux | grep -E "surge-xt-cli|Surge XT" | grep -v grep
echo ""
echo "GUI PID: $GUI_PID"
echo "Log file: $LOG_FILE"
echo ""
echo "⚠️  IMPORTANT: Only use GUI for patch editing."
echo "   When done, run: ./switch-to-cli.sh"
echo ""
echo "Connect via VNC to edit patches."
