#!/bin/bash
# Apply MPE_DAC_VOLUME_DB / MPE_DAC_SPEAKER_RAW to the Sound Blaster Speaker control.
#
# Usage:
#   ./scripts/set-dac-volume.sh              # from /etc/mpe/mpe.env
#   MPE_DAC_VOLUME_DB=-6 ./scripts/set-dac-volume.sh
#   ./scripts/set-dac-volume.sh --show       # read current hardware level

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"
# shellcheck source=lib/dac-volume.sh
source "$SCRIPT_DIR/lib/dac-volume.sh"

SHOW=false
for arg in "$@"; do
    case "$arg" in
        --show) SHOW=true ;;
        -h|--help)
            echo "Usage: set-dac-volume.sh [--show]"
            echo "Env: MPE_DAC_VOLUME_DB (default -6) or MPE_DAC_SPEAKER_RAW (0–88)"
            exit 0
            ;;
    esac
done

mpe_source_appliance_env

if [ "$SHOW" = true ]; then
    card="$(sound_blaster_card_index || true)"
    if [ -z "$card" ]; then
        echo "Sound Blaster not present"
        exit 1
    fi
    amixer -c "$card" sget "$MPE_DAC_SPEAKER_CONTROL"
    exit 0
fi

mpe_apply_dac_volume
