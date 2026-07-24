#!/bin/bash
# Deploy boot animation + patch browser via git pull + configure-pi-paths

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

echo "Boot animation deploy → $PI_USER@$PI_HOST"
echo ""

mpe_pi_ssh "echo Connected" > /dev/null || { echo "Cannot connect"; exit 1; }

if [ -n "$PI_MPE_MODULE" ]; then
    mpe_pi_ssh "cd '$PI_MPE_MODULE' && git pull"
    mpe_pi_ssh "cd '$PI_MPE_MODULE' && ./scripts/configure-pi-paths.sh --local --force"
else
    mpe_pi_ssh 'cd "${MPE_MODULE_REPO:-$HOME/MPE-Module}" && git pull'
    mpe_pi_ssh 'cd "${MPE_MODULE_REPO:-$HOME/MPE-Module}" && ./scripts/configure-pi-paths.sh --local --force'
fi

mpe_pi_ssh "$(mpe_pi_source_line); source $(mpe_pi_repo_path)/scripts/lib/mpe-services.sh; mpe_restart_core_services"

echo "Testing animation (5s)..."
mpe_pi_ssh bash -s <<EOF
$(mpe_pi_source_line)
python3 "\$MPE_MODULE_REPO/boot_animation.py" --duration 5 || true
EOF

echo ""
echo "✅ Done. Reboot Pi to see full boot sequence."
echo ""
