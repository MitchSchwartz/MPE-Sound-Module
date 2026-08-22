#!/bin/bash
# Plan V — V0 pre-checks + V1 silence + V2 client-count (~35 min).
#
# Usage: sudo ./scripts/measure-plan-v.sh [--artifact-dir DIR]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
ARTIFACT_DIR="${HOME}/plan-v-$(date +%Y%m%d-%H%M%S)"
ENV_FILE="/etc/mpe/mpe.env"

while [ $# -gt 0 ]; do
    case "$1" in
        --artifact-dir) ARTIFACT_DIR="${2:?}"; shift 2 ;;
        -h | --help) sed -n '2,8p' "$0"; exit 0 ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

mkdir -p "$ARTIFACT_DIR"
MASTER="${ARTIFACT_DIR}/plan-v.log"
exec > >(tee -a "$MASTER") 2>&1

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

echo "=== Plan V V0+V1+V2 $(date -Is) artifacts=$ARTIFACT_DIR ==="

echo ""
echo "=== V0-a governor ==="
echo "MPE_CPU_GOVERNOR=$(grep ^MPE_CPU_GOVERNOR= "$ENV_FILE" 2>/dev/null || echo 'unset (commented in example)')"
for cpu in /sys/devices/system/cpu/cpu[0-9]*; do
    [ -f "$cpu/cpufreq/scaling_governor" ] || continue
    echo "$(basename "$cpu") governor=$(cat "$cpu/cpufreq/scaling_governor") mhz=$(( $(cat "$cpu/cpufreq/scaling_cur_freq") / 1000 ))"
done
if grep -q '^arm_boost=1' /boot/firmware/config.txt 2>/dev/null || grep -q '^arm_boost=1' /boot/config.txt 2>/dev/null; then
    echo "config.txt: arm_boost=1 already set (V6 baseline may already be active)"
fi

echo ""
echo "=== V0-b poly governor ==="
echo "surge-poly-governor: $(systemctl is-active surge-poly-governor 2>/dev/null || echo inactive)"
echo "MPE_POLY_GOVERNOR=$(grep ^MPE_POLY_GOVERNOR= "$ENV_FILE" 2>/dev/null || echo unset)"
echo "MPE_POLY_CEILING=$(grep ^MPE_POLY_CEILING= "$ENV_FILE" 2>/dev/null || echo unset)"
echo "MPE_POLY_FLOOR=$(grep ^MPE_POLY_FLOOR= "$ENV_FILE" 2>/dev/null || echo unset)"

echo ""
echo "=== V0-c softmode ==="
echo "MPE_JACK_SOFTMODE=$(grep ^MPE_JACK_SOFTMODE= "$ENV_FILE" 2>/dev/null || echo unset)"
pgrep -a jackd | head -1 || true

echo ""
echo "=== V0 actions: disable poly governor, pin poly, stop governor unit ==="
_set_env_var MPE_POLY_GOVERNOR 0
_set_env_var MPE_POLY_CEILING 16
_set_env_var MPE_POLY_FLOOR 16
systemctl stop surge-poly-governor.service 2>/dev/null || true
systemctl disable surge-poly-governor.service 2>/dev/null || true
echo "poly governor stopped; MPE_POLY_GOVERNOR=0 MPE_POLY_CEILING=16 MPE_POLY_FLOOR=16"
echo "NOTE: CPU governor unchanged for V0 (V5 is separate). softmode reverted by measure-dsp-sample strict restart."

V1_LOG="${ARTIFACT_DIR}/v1-silence.log"
: >"$V1_LOG"

echo ""
echo "=== V1 silence test (Surge on, zero notes, n=3 x 30s per buffer) ==="
for buf in 1024 512 256; do
    "$SCRIPT_DIR/measure-dsp-sample.sh" \
        --buffer "$buf" --periods 3 --seconds 30 --runs 3 \
        --tag "V1-silence-${buf}" --output "$V1_LOG" --surge on
done

V2_LOG="${ARTIFACT_DIR}/v2-client-count.log"
: >"$V2_LOG"

echo ""
echo "=== V2 client-count @ 1024x3 (n=3 x 30s) ==="
echo "--- surge OFF (engine baseline) ---"
"$SCRIPT_DIR/measure-dsp-sample.sh" \
    --buffer 1024 --periods 3 --seconds 30 --runs 3 \
    --tag "V2-no-clients" --output "$V2_LOG" --surge off

echo "--- surge ON (Surge alone) ---"
"$SCRIPT_DIR/measure-dsp-sample.sh" \
    --buffer 1024 --periods 3 --seconds 30 --runs 3 \
    --tag "V2-surge-only" --output "$V2_LOG" --surge on

systemctl start surge-xt-cli.service 2>/dev/null || true
sleep 4
"$SCRIPT_DIR/set-surge-audio.sh" --buffer 1024 --periods 3
sleep 4

echo ""
echo "=== restore softmode for shipping ==="
_set_env_var MPE_JACK_SOFTMODE 1
systemctl restart mpe-jackd.service
sleep 4
systemctl restart surge-xt-cli.service 2>/dev/null || true
sleep 4

echo "SENTINEL plan-v-v0-v1-v2-complete $(date -Is)"
echo "artifacts=$ARTIFACT_DIR"
