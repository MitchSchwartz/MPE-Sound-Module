#!/bin/bash
# V11 — 512×2 and 256×3 at confirm-verified voice counts (PROGRESS #1).
#
# Patches: Crystals @ 3, Cloud Horn @ 5, Duduk @ 3 — governor off, stock 1800 MHz.
# Confirm harness only; xruns must be 0 at these counts (sanity, not the readout).
#
# Usage: sudo ./scripts/measure-plan-v11.sh [--artifact-dir DIR]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"
RUN_AS_USER="${MPE_PI_USER:-mitch}"
USER_HOME="$(getent passwd "$RUN_AS_USER" | cut -d: -f6)"
QUICK_SELECT="${USER_HOME}/Documents/Surge XT/Patches/Quick Select"
ARTIFACT_DIR="${USER_HOME}/plan-v11-$(date +%Y%m%d-%H%M%S)"
ENV_FILE="/etc/mpe/mpe.env"
SECONDS_HOLD=45
RUNS=3

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

mkdir -p "$ARTIFACT_DIR"
exec > >(tee -a "${ARTIFACT_DIR}/plan-v11.log") 2>&1

echo "=== Plan V11 $(date -Is) artifacts=$ARTIFACT_DIR ==="
echo "PROVENANCE governor=off clock=1800 cells=512x2,256x3 patches=Crystals@3,CloudHorn@5,Duduk@3"

_set_env_var MPE_POLY_GOVERNOR 0
systemctl stop surge-poly-governor.service 2>/dev/null || true

_run_config() {
    local name="$1" voices="$2" buffer="$3" periods="$4"
    local slug="${name// /_}"
    local tag="v11-${buffer}x${periods}-${slug}-v${voices}"
    local out="${ARTIFACT_DIR}/${tag}.log"
    local patch_path="${QUICK_SELECT}/${name}.fxp"

    [ -f "$patch_path" ] || { echo "ERROR: missing $patch_path" >&2; exit 1; }

    echo ""
    echo "=== V11 patch=${name} voices=${voices} buffer=${buffer} periods=${periods} ==="

    if ! mpe_meter_xruns_read >/dev/null 2>&1; then
        echo "ERROR: peak meter blind before ${tag}" >&2
        exit 1
    fi

    sudo -u "$RUN_AS_USER" python3 "$SCRIPT_DIR/load-patch-osc.py" "$patch_path"
    sleep 1

    "$SCRIPT_DIR/measure-latency-run.sh" \
        --buffer "$buffer" --periods "$periods" --condition A \
        --runs "$RUNS" --seconds "$SECONDS_HOLD" \
        --hold-voices "$voices" \
        --provenance-patch "$name" --provenance-voices "$voices" \
        --output "$out" --no-restore-buffer

    local xr dsp
    xr="$(awk '/^RESULT tag=.* xruns=/ { sub(/^.* xruns=/, ""); sub(/ .*/, ""); last=$0 } END { print last+0 }' "$out")"
    dsp="$(awk '/^RESULT tag=.* dsp_med=/ { match($0, /dsp_med=[0-9.]+/); if (RSTART) print substr($0, RSTART+8, RLENGTH-8) }' "$out" | tail -1)"
    echo "V11_SUMMARY patch=${name} voices=${voices} ${buffer}x${periods} xruns=${xr} dsp_med=${dsp:-unknown} log=${out}"
    if [ "${xr:-1}" -ne 0 ]; then
        echo "WARN: non-zero xruns at confirm count — treat cell as failed"
    fi
}

_run_patch() {
    local name="$1" voices="$2"
    _run_config "$name" "$voices" 512 2
    _run_config "$name" "$voices" 256 3
}

_run_patch "Crystals" 3
_run_patch "Cloud Horn" 5
_run_patch "Duduk" 3

echo "SENTINEL v11-complete artifacts=$ARTIFACT_DIR"
