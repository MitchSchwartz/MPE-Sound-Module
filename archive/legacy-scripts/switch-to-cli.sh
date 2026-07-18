#!/bin/bash
# Switch from GUI mode back to CLI service (headless performance mode)
# Usage: ./switch-to-cli.sh

echo "Checking for running Surge XT GUI processes..."

# Kill any running GUI instances
if pgrep -f "Surge XT" > /dev/null; then
    echo "Found running Surge XT GUI, stopping it..."
    pkill -f "Surge XT"
    sleep 2
else
    echo "No GUI instances running."
fi

echo "Starting Surge XT CLI service..."
sudo systemctl start surge-xt-cli

# Wait a moment for service to start
sleep 2

# Check status
if systemctl is-active --quiet surge-xt-cli; then
    echo ""
    echo "✅ Surge XT CLI service is now running!"
    echo ""
    echo "Check status: systemctl status surge-xt-cli"
    echo "View logs: tail -f ~/surge-cli.log"
else
    echo ""
    echo "⚠️  Warning: Service may not have started correctly."
    echo "Check status: sudo systemctl status surge-xt-cli"
    echo "View logs: sudo journalctl -u surge-xt-cli -n 50"
fi
