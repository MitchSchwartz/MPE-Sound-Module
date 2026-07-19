#!/bin/bash
# Deploy GUI crash fixes — git pull + configure; optional surge-permissions.service

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

echo "=== Deploying GUI crash fixes to $PI_HOST ==="
echo ""

[ -f "$SSH_KEY" ] || { echo "SSH key not found: $SSH_KEY"; exit 1; }

if [ -n "$PI_MPE_MODULE" ]; then
    mpe_pi_ssh "cd '$PI_MPE_MODULE' && git pull"
    mpe_pi_ssh "cd '$PI_MPE_MODULE' && ./scripts/configure-pi-paths.sh --local --force"
else
    mpe_pi_ssh 'cd "${MPE_MODULE_REPO:-$HOME/MPE-Module}" && git pull'
    mpe_pi_ssh 'cd "${MPE_MODULE_REPO:-$HOME/MPE-Module}" && ./scripts/configure-pi-paths.sh --local --force'
fi

if [ -f "$MPE_MODULE_REPO/config/surge-permissions.service" ]; then
    scp -i "$SSH_KEY" "$MPE_MODULE_REPO/config/surge-permissions.service" "$PI_USER@$PI_HOST:/tmp/"
    mpe_pi_ssh "sudo mv /tmp/surge-permissions.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable surge-permissions.service"
fi

mpe_pi_ssh "sudo systemctl restart surge-watchdog 2>/dev/null || true"
echo ""
echo "✅ Deployment complete (scripts run from MPE-Module clone on Pi)."
echo ""
