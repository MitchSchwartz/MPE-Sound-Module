#!/bin/bash
# Single patch + voice count + hold window (V9 confirm / regression cells).
#
# Usage: sudo ./scripts/measure-confirm-at-voices.sh \
#            --patch-name "Cloud Horn" --voices 7 --seconds 60 \
#            --output /path/confirm.log [--tag V9c-cloudhorn-7]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
RUN_AS_USER="${MPE_PI_USER:-mitch}"
USER_HOME="$(getent passwd "$RUN_AS_USER" | cut -d: -f6)"
QUICK_SELECT="${USER_HOME}/Documents/Surge XT/Patches/Quick Select"
PATCH_NAME=""
VOICES=""
SECONDS_HOLD=60
OUTPUT=""
TAG=""
BUFFER=1024
PERIODS=3
ENV_FILE="/etc/mpe/mpe.env"

while [ $# -gt 0 ]; do
    case "$1" in
        --patch-name) PATCH_NAME="${2:?}"; shift 2 ;;
        --voices) VOICES="${2:?}"; shift 2 ;;
        --seconds) SECONDS_HOLD="${2:?}"; shift 2 ;;
        --output) OUTPUT="${2:?}"; shift 2 ;;
        --tag) TAG="${2:?}"; shift 2 ;;
        --buffer) BUFFER="${2:?}"; shift 2 ;;
        --periods) PERIODS="${2:?}"; shift 2 ;;
        -h | --help) sed -n '2,8p' "$0"; exit 0 ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

[ -n "$PATCH_NAME" ] && [ -n "$VOICES" ] && [ -n "$OUTPUT" ] || {
    echo "ERROR: --patch-name --voices --output required" >&2
    exit 2
}

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

PATCH_PATH="${QUICK_SELECT}/${PATCH_NAME}.fxp"
[ -f "$PATCH_PATH" ] || { echo "ERROR: missing $PATCH_PATH" >&2; exit 1; }

TAG="${TAG:-confirm-${PATCH_NAME// /_}-v${VOICES}}"

_set_env_var() {
    local key="$1" value="$2" tmp
    tmp="$(mktemp)"
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed "s|^${key}=.*|${key}=${value}|" "$ENV_FILE" >"$tmp"
    else
        cat "$ENV_FILE" >"$tmp" 2>/dev/null || true
        printf '\n%s=%s\n' "$key" "$value" >>"$tmp"
    fi
    install -m 0644 "$tmp" "$ENV_FILE"
    rm -f "$tmp"
}

_set_env_var MPE_POLY_GOVERNOR 0
systemctl stop surge-poly-governor.service 2>/dev/null || true

{
    echo "=== measure-confirm-at-voices tag=${TAG} patch=${PATCH_NAME} voices=${VOICES} sec=${SECONDS_HOLD} $(date -Is) ==="
    sudo -u "$RUN_AS_USER" python3 "$SCRIPT_DIR/load-patch-osc.py" "$PATCH_PATH"
    sleep 1
} >>"$OUTPUT"

"$SCRIPT_DIR/measure-latency-run.sh" \
    --buffer "$BUFFER" --periods "$PERIODS" --condition A --runs 1 --seconds "$SECONDS_HOLD" \
    --hold-voices "$VOICES" \
    --provenance-patch "$PATCH_NAME" --provenance-voices "$VOICES" \
    --output "$OUTPUT" >/dev/null

xr="$(awk '/^RESULT tag=.* xruns=/ { sub(/^.* xruns=/, ""); sub(/ .*/, ""); last=$0 } END { print last+0 }' "$OUTPUT")"
echo "RESULT tag=${TAG} patch=${PATCH_NAME} voices=${VOICES} sec=${SECONDS_HOLD} xruns=${xr} clean=$([ "$xr" -eq 0 ] && echo yes || echo no)" >&2
echo "$xr"
