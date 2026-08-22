#!/bin/bash
# Plan V7 capacity curve + V3 1024x2.
#
# Usage: sudo ./scripts/measure-plan-v7.sh [--artifact-dir DIR] [--patch-name NAME]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
ARTIFACT_DIR="${HOME}/plan-v7-$(date +%Y%m%d-%H%M%S)"
PATCH_NAME="Crystals"
ENV_FILE="/etc/mpe/mpe.env"

while [ $# -gt 0 ]; do
    case "$1" in
        --artifact-dir) ARTIFACT_DIR="${2:?}"; shift 2 ;;
        --patch-name) PATCH_NAME="${2:?}"; shift 2 ;;
        -h | --help) sed -n '2,6p' "$0"; exit 0 ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

mkdir -p "$ARTIFACT_DIR"
exec > >(tee -a "${ARTIFACT_DIR}/plan-v7.log") 2>&1

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

echo "=== Plan V7 + V3 $(date -Is) artifacts=$ARTIFACT_DIR patch=${PATCH_NAME} ==="
echo "poly_state=$(cat /home/mitch/.patch_browser_poly_state.json 2>/dev/null || echo missing)"

# Standing conditions
_set_env_var MPE_POLY_GOVERNOR 0
_set_env_var MPE_POLY_CEILING 64
_set_env_var MPE_POLY_FLOOR 64
systemctl stop surge-poly-governor.service 2>/dev/null || true
systemctl disable surge-poly-governor.service 2>/dev/null || true

V7_LOG="${ARTIFACT_DIR}/v7-capacity.log"
: >"$V7_LOG"

echo ""
echo "=== V7 capacity curve ==="
for buf in 1024 512 256; do
    tag="V7-${buf}"
    "$SCRIPT_DIR/measure-capacity-ramp.sh" \
        --buffer "$buf" --periods 3 --tag "$tag" \
        --output "$V7_LOG" --patch-name "$PATCH_NAME"
    sleep 5
done

echo ""
echo "=== V3 1024x2 (n=3, cond A, 60s) ==="
V3_LOG="${ARTIFACT_DIR}/v3-1024x2.log"
"$SCRIPT_DIR/measure-latency-run.sh" \
    --buffer 1024 --periods 2 --condition A --runs 3 --seconds 60 \
    --output "$V3_LOG" --no-restore-buffer

echo ""
echo "=== V3 baseline 1024x3 (n=3, same session) ==="
V3_BASE="${ARTIFACT_DIR}/v3-baseline-1024x3.log"
"$SCRIPT_DIR/measure-latency-run.sh" \
    --buffer 1024 --periods 3 --condition A --runs 3 --seconds 60 \
    --output "$V3_BASE"

echo ""
echo "=== restore shipping softmode ==="
_set_env_var MPE_JACK_SOFTMODE 1
systemctl restart mpe-jackd.service
sleep 4
systemctl restart surge-xt-cli.service
sleep 4

echo "SENTINEL plan-v7-complete $(date -Is)"
