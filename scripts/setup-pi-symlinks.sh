#!/bin/bash
# Setup symlinks on Pi to your private assets repo

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

echo "========================================="
echo "  Setup Pi Symlinks to Assets Repo"
echo "========================================="
echo ""
echo "Target: $PI_USER@$PI_HOST"
echo ""

if ! mpe_pi_ssh "echo Connected" > /dev/null 2>&1; then
    echo "❌ ERROR: Cannot connect to Pi"
    exit 1
fi
echo "✓ Connected"
echo ""

mpe_pi_ssh bash -s <<EOF
set -e
$(mpe_pi_source_line)
PERSONAL="\${MPE_PERSONAL_REPO:-${PI_MPE_PERSONAL:-\$HOME/MPE-Library}}"
ASSETS="\$PERSONAL/assets"

[ -d "\$ASSETS/patches/patches_factory" ] || { echo "Missing factory patches in \$ASSETS"; exit 1; }
[ -d "\$ASSETS/patches/third-party/patches_3rdparty" ] || { echo "Missing third-party patches"; exit 1; }
[ -d "\$ASSETS/user-data/Patches" ] || { echo "Missing user patches"; exit 1; }

_link() {
    local target="\$1" link="\$2"
    if [ -L "\$link" ]; then rm "\$link"
    elif [ -e "\$link" ]; then mv "\$link" "\${link}.backup.\$(date +%s)"; fi
    ln -s "\$target" "\$link"
}

_link "\$ASSETS/patches/patches_factory" "\$MPE_SURGE_RESOURCES/patches_factory"
_link "\$ASSETS/patches/third-party/patches_3rdparty" "\$MPE_SURGE_RESOURCES/patches_3rdparty"
_link "\$ASSETS/user-data/Patches" "\$MPE_SURGE_DOCS/Patches"
echo "✓ Symlinks created → \$PERSONAL/assets"
EOF

mpe_pi_ssh "sudo systemctl restart surge-xt-cli" || true
echo ""
echo "✅ Done. Clone your assets repo on Pi first if symlinks failed."
echo ""
