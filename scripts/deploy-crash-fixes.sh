#!/bin/bash
# Deploy all GUI crash fixes to Raspberry Pi
# Run this script from your local machine (not on the Pi)

set -e

PI_HOST="surge.local"
PI_USER="mitch"
SSH_KEY="$HOME/.ssh/surge_pi_key"

echo "=== Deploying GUI Crash Fixes to $PI_HOST ==="
echo ""

# Check prerequisites
if [ ! -f "$SSH_KEY" ]; then
    echo "❌ ERROR: SSH key not found at $SSH_KEY"
    exit 1
fi

echo "1. Deploying updated scripts..."
scp -i "$SSH_KEY" scripts/switch-to-gui-improved.sh "$PI_USER@$PI_HOST":~/scripts/
scp -i "$SSH_KEY" scripts/switch-to-cli-improved.sh "$PI_USER@$PI_HOST":~/scripts/
scp -i "$SSH_KEY" scripts/surge-watchdog.sh "$PI_USER@$PI_HOST":~/scripts/
scp -i "$SSH_KEY" scripts/start-surge-cli.sh "$PI_USER@$PI_HOST":~/scripts/

echo ""
echo "2. Deploying surge-permissions.service..."
scp -i "$SSH_KEY" config/surge-permissions.service "$PI_USER@$PI_HOST":/tmp/

echo ""
echo "3. Installing and enabling surge-permissions.service..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "sudo mv /tmp/surge-permissions.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable surge-permissions.service"

echo ""
echo "4. Restarting surge-watchdog to apply changes..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "sudo systemctl restart surge-watchdog"

echo ""
echo "5. Verifying deployment..."
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "ls -lh ~/scripts/switch-to-*.sh ~/scripts/surge-watchdog.sh ~/scripts/start-surge-cli.sh && systemctl is-enabled surge-permissions.service"

echo ""
echo "✅ Deployment complete!"
echo ""
echo "What was fixed:"
echo "  ✓ udev rules now disabled during GUI sessions (prevents CLI auto-restart)"
echo "  ✓ File permissions set on boot (chmod 444)"
echo "  ✓ Watchdog sets permissions after crash recovery"
echo "  ✓ Permission verification added to mode-switching scripts"
echo "  ✓ Defense-in-depth protection in CLI startup"
echo ""
echo "Next steps:"
echo "  1. Test GUI mode: ~/scripts/switch-to-gui-improved.sh"
echo "  2. Check udev disabled: ls /etc/udev/rules.d/*.disabled"
echo "  3. Connect via VNC and load 'Guitar + Wah' patch"
echo "  4. Try hot-plugging USB devices during GUI session"
echo "  5. Verify CLI does NOT auto-restart: ~/scripts/check-surge-mode.sh"
echo ""
