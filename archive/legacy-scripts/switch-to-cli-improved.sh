#!/bin/bash
# Switch from GUI to CLI mode for live performance
# Does NOT require reboot - instant switch
# Usage: ./switch-to-cli-improved.sh

set -e

echo "=== Switching to Surge XT CLI Mode ==="
echo ""

# 1. Stop VNC server
if systemctl is-active --quiet wayvnc; then
    echo "Stopping VNC server..."
    sudo systemctl stop wayvnc
    sleep 1
fi

# 2. Kill any running GUI instances
if pgrep -f "Surge XT" > /dev/null; then
    echo "Stopping Surge XT GUI processes..."
    pkill -f "Surge XT"

    # Wait for clean shutdown
    sleep 2

    # Force kill if still running
    if pgrep -f "Surge XT" > /dev/null; then
        echo "Force killing GUI..."
        pkill -9 -f "Surge XT"
        sleep 1
    fi
else
    echo "No GUI instances running."
fi

# 2. Verify GUI is stopped
if pgrep -f "Surge XT" > /dev/null; then
    echo "❌ ERROR: Failed to stop GUI"
    ps aux | grep -F "Surge XT" | grep -v grep
    exit 1
fi

# 3. Start CLI service
echo "Starting Surge XT CLI service..."
sudo systemctl start surge-xt-cli

# Wait for service to initialize
sleep 3

# 4. Verify CLI is running
if ! systemctl is-active --quiet surge-xt-cli; then
    echo "❌ ERROR: CLI service failed to start"
    echo ""
    echo "Check status: sudo systemctl status surge-xt-cli"
    echo "Check logs: sudo journalctl -u surge-xt-cli -n 50"
    exit 1
fi

# 5. Ensure user defaults are writable (OSC patch loading requires write access)
USER_DEFAULTS="$HOME/.local/share/Surge XT/SurgeXTUserDefaults.xml"
if [ -f "$USER_DEFAULTS" ]; then
    echo "Ensuring user defaults are writable for OSC patch loading..."
    chmod 644 "$USER_DEFAULTS"

    # Verify permissions were set correctly
    ACTUAL_PERMS=$(stat -c %a "$USER_DEFAULTS" 2>/dev/null || echo "000")
    if [ "$ACTUAL_PERMS" != "644" ]; then
        echo "⚠️  WARNING: Failed to set permissions to 644 (got $ACTUAL_PERMS)"
        echo "   OSC patch loading may not work"
    fi
fi

# 6. Re-enable udev auto-restart rules (needed for audio device hot-plug in CLI mode)
echo "Re-enabling udev auto-restart rules..."
if [ -f /etc/udev/rules.d/99-usb-audio.rules.disabled ]; then
    sudo mv /etc/udev/rules.d/99-usb-audio.rules.disabled /etc/udev/rules.d/99-usb-audio.rules || true
fi
if [ -f /etc/udev/rules.d/99-roli-seaboard.rules.disabled ]; then
    sudo mv /etc/udev/rules.d/99-roli-seaboard.rules.disabled /etc/udev/rules.d/99-roli-seaboard.rules || true
fi
sudo udevadm control --reload-rules

# 8. Get CLI PID
CLI_PID=$(pgrep -f surge-xt-cli)

# 9. Verify only CLI is running (not GUI)
echo ""
echo "✅ Successfully switched to CLI mode!"
echo ""
echo "Running processes:"
ps aux | grep -E "surge-xt-cli|Surge XT" | grep -v grep
echo ""
echo "CLI PID: $CLI_PID"
echo "Service status: $(systemctl is-active surge-xt-cli)"
echo ""
echo "Monitor with:"
echo "  tail -f ~/surge-cli.log"
echo "  sudo systemctl status surge-xt-cli"
echo ""
echo "✅ Patch browser can now control Surge via OSC"
