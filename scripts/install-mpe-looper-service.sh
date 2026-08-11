#!/usr/bin/env bash
# Install mpe-looper.service (does not enable — looper-audio-route.sh on does that).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
MPE_MODULE_REPO="${MPE_MODULE_REPO:-$REPO_ROOT}"
MPE_PI_USER="${MPE_PI_USER:-$USER}"

chmod +x "$REPO_ROOT/scripts/mpe-looper.py" "$REPO_ROOT/scripts/mpe-looper-service.sh"

sed -e "s|@MPE_MODULE_REPO@|${MPE_MODULE_REPO}|g" \
    -e "s|@MPE_PI_USER@|${MPE_PI_USER}|g" \
    "$REPO_ROOT/config/mpe-looper.service" | sudo tee /etc/systemd/system/mpe-looper.service >/dev/null

sudo systemctl daemon-reload
echo "mpe-looper.service installed (enable via: sudo ./scripts/looper-audio-route.sh on)"
