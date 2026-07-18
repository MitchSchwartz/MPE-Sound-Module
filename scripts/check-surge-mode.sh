#!/bin/bash
# Check which Surge mode is currently running
# Usage: ./check-surge-mode.sh

echo "=== Surge XT Mode Status ==="
echo ""

CLI_RUNNING=false
GUI_RUNNING=false

# Check CLI
if systemctl is-active --quiet surge-xt-cli; then
    CLI_RUNNING=true
    CLI_PID=$(pgrep -f surge-xt-cli || echo "unknown")
fi

# Check GUI
if pgrep -f "Surge XT" > /dev/null; then
    GUI_RUNNING=true
    GUI_PID=$(pgrep -f "Surge XT" || echo "unknown")
fi

# Report status
if $CLI_RUNNING && $GUI_RUNNING; then
    echo "❌ CRITICAL: BOTH CLI AND GUI ARE RUNNING!"
    echo ""
    echo "This causes race conditions and XML corruption."
    echo "You must stop one immediately:"
    echo ""
    echo "To keep CLI (for patch browser):"
    echo "  pkill -f 'Surge XT'"
    echo ""
    echo "To keep GUI (for editing):"
    echo "  sudo systemctl stop surge-xt-cli"
    echo ""
    echo "Running processes:"
    ps aux | grep -E "surge-xt-cli|Surge XT" | grep -v grep
    exit 1

elif $CLI_RUNNING; then
    echo "✅ Mode: CLI (Headless)"
    echo "   PID: $CLI_PID"
    echo "   Service: $(systemctl is-active surge-xt-cli)"
    echo "   Purpose: Live performance with patch browser"
    echo ""
    echo "Switch to GUI: ./switch-to-gui-improved.sh"

elif $GUI_RUNNING; then
    echo "✅ Mode: GUI (VNC)"
    echo "   PID: $GUI_PID"
    echo "   Purpose: Patch editing via VNC"
    echo ""
    echo "Switch to CLI: ./switch-to-cli-improved.sh"

else
    echo "⚠️  Neither CLI nor GUI is running!"
    echo ""
    echo "Start CLI (for performance): sudo systemctl start surge-xt-cli"
    echo "Start GUI (for editing): ./switch-to-gui-improved.sh"
fi

echo ""
echo "UserDefaults file status:"
ls -lh ~/.local/share/Surge\ XT/SurgeXTUserDefaults.xml 2>/dev/null || echo "  File not found (this is OK if using read-only protection)"
