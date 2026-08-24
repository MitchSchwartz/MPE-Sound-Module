#!/usr/bin/env bash
# Merge board-specific player-env-parity.pi*.env into /etc/mpe/mpe.env.
# Preserves path keys from configure-pi-paths.sh; may preserve appliance-tuned buffer keys.
# MPE_POLY_* keys: all-or-nothing from the board file — never partial carry-forward.
#
# Usage (on Pi):
#   ./scripts/apply-player-env-parity.sh [--dry-run] [--platform pi4|pi5|auto]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=lib/detect-pi-platform.sh
source "$SCRIPT_DIR/lib/detect-pi-platform.sh"

TARGET="/etc/mpe/mpe.env"
DRY=false
PLATFORM="auto"

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=true; shift ;;
        --platform)
            PLATFORM="${2:?--platform requires pi4, pi5, or auto}"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [--dry-run] [--platform pi4|pi5|auto]" >&2
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

PATH_KEY_RE='^(MPE_PI_USER|MPE_HOME|MPE_MODULE_REPO|MPE_PERSONAL_REPO|MPE_SURGE_ROOT|MPE_SURGE_DOCS|MPE_SURGE_LOG)='
PRESERVE_KEY_RE='^(MPE_JACK_BUFFER|MPE_JACK_PERIODS|MPE_SURGE_BUFFER_SIZE|MPE_SURGE_SAMPLE_RATE|MPE_DAC_VOLUME_DB)='
POLY_KEY_RE='^MPE_POLY_'

_resolve_platform() {
    case "$PLATFORM" in
        auto) mpe_detect_pi_platform ;;
        pi4|pi5) echo "$PLATFORM" ;;
        *) echo "ERROR: --platform must be pi4, pi5, or auto (got $PLATFORM)" >&2; exit 2 ;;
    esac
}

plat="$(_resolve_platform)"
PARITY="$REPO_ROOT/config/platform/player-env-parity.${plat}.env"

if [ "$plat" = unknown ]; then
    echo "ERROR: could not detect Pi platform — pass --platform pi4 or pi5" >&2
    echo "  model: $(mpe_pi_model_string || echo '?')" >&2
    exit 1
fi

if [ ! -f "$PARITY" ]; then
    echo "ERROR: missing $PARITY" >&2
    exit 1
fi

_read_preserved() {
    local key="$1"
    if [ ! -f "$TARGET" ]; then
        return 1
    fi
    grep -E "^${key}=" "$TARGET" 2>/dev/null | tail -1 || return 1
}

tmp="$(mktemp)"
{
    if [ -f "$TARGET" ]; then
        grep -E "$PATH_KEY_RE" "$TARGET" || true
        echo ""
    fi
    echo "# Player parity ($plat) — applied $(date -Iseconds)"
    while IFS= read -r line; do
        case "$line" in
            ''|\#*) continue ;;
            *=*) ;;
            *) continue ;;
        esac
        key="${line%%=*}"
        case "$key" in
            MPE_JACK_BUFFER|MPE_JACK_PERIODS|MPE_SURGE_BUFFER_SIZE|MPE_SURGE_SAMPLE_RATE|MPE_DAC_VOLUME_DB)
                preserved="$(_read_preserved "$key" || true)"
                if [ -n "$preserved" ]; then
                    echo "$preserved"
                else
                    echo "$line"
                fi
                ;;
            MPE_POLY_*)
                echo "$line"
                ;;
            *)
                echo "$line"
                ;;
        esac
    done < <(grep -v '^#' "$PARITY" | grep -v '^$')
} > "$tmp"

if [ "$DRY" = true ]; then
    echo "# platform=$plat source=$PARITY"
    cat "$tmp"
    rm -f "$tmp"
    exit 0
fi

sudo cp "$tmp" "$TARGET"
rm -f "$tmp"
echo "Wrote $TARGET (platform=$plat, source=$(basename "$PARITY"))"
echo "  Paths preserved; buffer keys kept if already set on appliance"
echo "  MPE_POLY_* applied all-or-nothing from board file"
if [ "$plat" = pi5 ]; then
    if grep -qE '^MPE_POLY_CPU_(HIGH|LOW)=' "$TARGET" 2>/dev/null; then
        echo "WARNING: stale Pi 4 v1 keys MPE_POLY_CPU_HIGH/LOW still present — re-run failed?" >&2
    fi
fi
echo "  RTMIDI_API not set — apt python3-rtmidi, ALSA Midi Through chain"
echo "If MPE_PEAK_METER=1: sudo systemctl enable --now mpe-peak-meter.service"
