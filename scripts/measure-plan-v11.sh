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
# shellcheck source=lib/measure-run-as-user.sh
source "$SCRIPT_DIR/lib/measure-run-as-user.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"
# shellcheck source=lib/appliance-user.sh
source "$SCRIPT_DIR/lib/appliance-user.sh"
RUN_AS_USER="$(mpe_appliance_user)" || exit 1
USER_HOME="$(getent passwd "$RUN_AS_USER" | cut -d: -f6)"
QUICK_SELECT="${USER_HOME}/Documents/Surge XT/Patches/Quick Select"
ARTIFACT_DIR="${USER_HOME}/plan-v11-$(date +%Y%m%d-%H%M%S)"
# shellcheck source=lib/measure-env.sh
source "$SCRIPT_DIR/lib/measure-env.sh"
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

mkdir -p "$ARTIFACT_DIR"
exec > >(tee -a "${ARTIFACT_DIR}/plan-v11.log") 2>&1

echo "=== Plan V11 $(date -Is) artifacts=$ARTIFACT_DIR ==="
echo "PROVENANCE governor=off clock=1800 cells=512x2,256x3 patches=Crystals@3,CloudHorn@5,Duduk@3"

# Edits only when they differ; restored byte for byte on exit (lib/measure-env.sh).
mpe_env_arm
mpe_env_set MPE_POLY_GOVERNOR 0
mpe_unit_stop_for_measure surge-poly-governor.service
if ! mpe_meter_xruns_read >/dev/null 2>&1; then
    echo "Peak meter blind — restarting audio graph"
    systemctl restart mpe-jackd.service
    sleep 4
    mpe_wait_for_jack_server 30
    systemctl restart surge-xt-cli.service
    sleep 6
    systemctl restart mpe-peak-meter.service 2>/dev/null || true
    sleep 3
    mpe_meter_assert_live || {
        echo "ERROR: peak meter still blind after graph restart" >&2
        exit 1
    }
fi

_run_config() {
    local name="$1" voices="$2" buffer="$3" periods="$4"
    local slug="${name// /_}"
    local tag="v11-${buffer}x${periods}-${slug}-v${voices}"
    local out="${ARTIFACT_DIR}/${tag}.log"
    local patch_path="${QUICK_SELECT}/${name}.fxp"

    [ -f "$patch_path" ] || { echo "ERROR: missing $patch_path" >&2; exit 1; }

    echo ""
    echo "=== V11 patch=${name} voices=${voices} buffer=${buffer} periods=${periods} ==="

    MPE_RUN_AS_USER="$RUN_AS_USER"; MPE_RUN_AS_USER_HOME="$USER_HOME"; mpe_load_patch_osc "$patch_path" "$SCRIPT_DIR"
    sleep 1

    "$SCRIPT_DIR/measure-latency-run.sh" \
        --buffer "$buffer" --periods "$periods" --condition A \
        --runs "$RUNS" --seconds "$SECONDS_HOLD" \
        --hold-voices "$voices" \
        --provenance-patch "$name" --provenance-voices "$voices" \
        --output "$out" --no-restore-buffer

    local xr dsp
    xr="$(awk '/^RESULT tag=.* xruns=/ { sub(/^.* xruns=/, ""); sub(/ .*/, ""); last=$0 } END { print last+0 }' "$out")"
    dsp="$(awk '/^RESULT tag=.* dsp_median=/ { match($0, /dsp_median=[0-9.]+/); if (RSTART) print substr($0, RSTART+12, RLENGTH-12) }' "$out" | tail -1)"
    echo "V11_SUMMARY patch=${name} voices=${voices} ${buffer}x${periods} xruns=${xr} dsp_median=${dsp:-unknown} log=${out}"
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
