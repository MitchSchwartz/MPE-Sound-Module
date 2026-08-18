#!/usr/bin/env bash
# Start compiled OUT peak meter — exits cleanly when MPE_PEAK_METER is off.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

case "${MPE_PEAK_METER:-0}" in
    1 | true | yes | on | TRUE | YES | ON)
        ;;
    *)
        echo "mpe-peak-meter: disabled (MPE_PEAK_METER=${MPE_PEAK_METER:-0})"
        exit 0
        ;;
esac

BIN="$MPE_MODULE_REPO/native/mpe-peak-meter/mpe-peak-meter"
if [ ! -x "$BIN" ]; then
    "$MPE_MODULE_REPO/scripts/build-mpe-peak-meter.sh" --required
fi
if [ ! -x "$BIN" ]; then
    echo "mpe-peak-meter: binary missing after build" >&2
    exit 1
fi

export MPE_RUN_DIR="$(mpe_run_dir)"
exec "$BIN"
