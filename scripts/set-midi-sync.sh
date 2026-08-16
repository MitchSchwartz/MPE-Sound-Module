#!/bin/bash
# Persist looper MIDI sync settings and restart mpe-pressure-remap.
#
# Usage: sudo ./scripts/set-midi-sync.sh --quantize 16th
#        sudo ./scripts/set-midi-sync.sh --triplet 1
#        sudo ./scripts/set-midi-sync.sh --offset-auto 1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"

QUANTIZE=""
TRIPLET=""
OFFSET_AUTO=""

while [ $# -gt 0 ]; do
    case "$1" in
        --quantize)
            QUANTIZE="${2:?--quantize requires a value}"
            shift 2
            ;;
        --triplet)
            TRIPLET="${2:?--triplet requires a value}"
            shift 2
            ;;
        --offset-auto)
            OFFSET_AUTO="${2:?--offset-auto requires a value}"
            shift 2
            ;;
        *)
            echo "Usage: $0 --quantize VALUE | --triplet 0|1 | --offset-auto 0|1" >&2
            exit 1
            ;;
    esac
done

if [ -z "$QUANTIZE" ] && [ -z "$TRIPLET" ] && [ -z "$OFFSET_AUTO" ]; then
    echo "ERROR: specify at least one of --quantize, --triplet, --offset-auto" >&2
    exit 1
fi

is_valid_quantize() {
    case "$1" in
        off | beat | 8th | 16th | 32nd) return 0 ;;
        triplet)
            QUANTIZE="8th"
            TRIPLET="${TRIPLET:-1}"
            return 0
            ;;
        *) return 1 ;;
    esac
}

ENV_FILE="/etc/mpe/mpe.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found — run configure-pi-paths.sh first" >&2
    exit 1
fi

if [ -n "$QUANTIZE" ] && ! is_valid_quantize "$QUANTIZE"; then
    echo "ERROR: invalid quantize: $QUANTIZE" >&2
    exit 1
fi

if [ -n "$OFFSET_AUTO" ] && [ "$OFFSET_AUTO" != "0" ] && [ "$OFFSET_AUTO" != "1" ]; then
    echo "ERROR: --offset-auto must be 0 or 1" >&2
    exit 1
fi

if [ -n "$TRIPLET" ] && [ "$TRIPLET" != "0" ] && [ "$TRIPLET" != "1" ]; then
    echo "ERROR: --triplet must be 0 or 1" >&2
    exit 1
fi

_update_env_var() {
    local key="$1"
    local value="$2"
    local tmp
    tmp="$(mktemp)"
    if grep -q "^${key}=" "$ENV_FILE"; then
        sed "s/^${key}=.*/${key}=${value}/" "$ENV_FILE" >"$tmp"
    else
        cat "$ENV_FILE" >"$tmp"
        printf '\n%s=%s\n' "$key" "$value" >>"$tmp"
    fi
    install -m 0644 "$tmp" "$ENV_FILE"
    rm -f "$tmp"
}

if [ -n "$QUANTIZE" ]; then
    _update_env_var MPE_MIDI_QUANTIZE "$QUANTIZE"
fi
if [ -n "$TRIPLET" ]; then
    _update_env_var MPE_MIDI_QUANTIZE_TRIPLET "$TRIPLET"
fi
if [ -n "$OFFSET_AUTO" ]; then
    _update_env_var MPE_MIDI_OUTPUT_OFFSET_AUTO "$OFFSET_AUTO"
fi

systemctl restart mpe-pressure-remap.service

echo -n "Applied looper sync"
[ -n "$QUANTIZE" ] && echo -n " quantize=$QUANTIZE"
[ -n "$TRIPLET" ] && echo -n " triplet=$TRIPLET"
[ -n "$OFFSET_AUTO" ] && echo -n " offset_auto=$OFFSET_AUTO"
echo
