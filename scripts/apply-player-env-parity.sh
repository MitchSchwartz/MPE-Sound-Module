#!/usr/bin/env bash
# Merge config/platform/player-env-parity.env into /etc/mpe/mpe.env (Pi 4 parity keys).
# Preserves path keys written by configure-pi-paths.sh. Strips RTMIDI_API if present.
#
# Usage (on Pi): ./scripts/apply-player-env-parity.sh [--dry-run]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PARITY="$REPO_ROOT/config/platform/player-env-parity.env"
TARGET="/etc/mpe/mpe.env"
DRY=false
[ "${1:-}" = "--dry-run" ] && DRY=true

PATH_KEY_RE='^(MPE_PI_USER|MPE_HOME|MPE_MODULE_REPO|MPE_PERSONAL_REPO|MPE_SURGE_ROOT|MPE_SURGE_DOCS|MPE_SURGE_LOG)='

if [ ! -f "$PARITY" ]; then
    echo "ERROR: missing $PARITY" >&2
    exit 1
fi

tmp="$(mktemp)"
{
    if [ -f "$TARGET" ]; then
        grep -E "$PATH_KEY_RE" "$TARGET" || true
        echo ""
    fi
    echo "# Player parity (Pi 4 control) — applied $(date -Iseconds)"
    grep -v '^#' "$PARITY" | grep -v '^$'
} > "$tmp"

if [ "$DRY" = true ]; then
    cat "$tmp"
    rm -f "$tmp"
    exit 0
fi

sudo cp "$tmp" "$TARGET"
rm -f "$tmp"
echo "Wrote $TARGET"
echo "  Paths preserved from prior file (if any); tuning keys from player-env-parity.env"
echo "  RTMIDI_API not set — match Pi 4 (apt python3-rtmidi, ALSA Midi Through chain)"
echo "If MPE_PEAK_METER=1: sudo systemctl enable --now mpe-peak-meter.service"
