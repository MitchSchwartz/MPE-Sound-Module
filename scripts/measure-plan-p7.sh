#!/bin/bash
# P7 — 2000 MHz overclock diagnostic (DSP scales with clock?).
#
# Confirm harness only. Baseline 3× per patch @ 1800, then arm_freq=2000 + 3× @ 2000.
# Reverts config and reboots to stock before exit (soak-safe).
#
# Usage:
#   sudo ./scripts/measure-plan-p7.sh [--artifact-dir DIR] [--baseline-only]
#   sudo ./scripts/measure-plan-p7.sh --phase oc --artifact-dir DIR  # after manual reboot to 2000

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/clock-stamp.sh
source "$SCRIPT_DIR/lib/clock-stamp.sh"

RUN_AS_USER="${MPE_PI_USER:-mitch}"
USER_HOME="$(getent passwd "$RUN_AS_USER" | cut -d: -f6)"
ARTIFACT_DIR="${USER_HOME}/plan-p7-$(date +%Y%m%d-%H%M%S)"
PHASE="full"
CONFIRM_SEC=45
BASELINE_RUNS=3
EXPECT_BASELINE_MHZ=1800
EXPECT_OC_MHZ=2000

PATCHES=(
    "Crystals:3"
    "Cloud Horn:5"
    "Duduk:3"
)

while [ $# -gt 0 ]; do
    case "$1" in
        --artifact-dir) ARTIFACT_DIR="${2:?}"; shift 2 ;;
        --phase) PHASE="${2:?}"; shift 2 ;;
        --baseline-only) PHASE="baseline"; shift ;;
        -h | --help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

mkdir -p "$ARTIFACT_DIR"
LOG="${ARTIFACT_DIR}/plan-p7.log"
exec > >(tee -a "$LOG") 2>&1

# Read buffer/periods from live jackd — hold identical across halves
BUFFER="$(ps -o args= -C jackd 2>/dev/null | grep -oP '\-p \K[0-9]+' | head -1 || echo 1024)"
PERIODS="$(ps -o args= -C jackd 2>/dev/null | grep -oP '\-n \K[0-9]+' | head -1 || echo 2)"

_run_patch_triple() {
    local phase="$1" expect_mhz="$2"
    local name="$3" voices="$4"
    local slug="${name// /_}"
    local out="${ARTIFACT_DIR}/p7-${phase}-${slug}.log"
    local run=1

    : >"$out"
    while [ "$run" -le "$BASELINE_RUNS" ]; do
        local tag="P7-${phase}-${slug}-run${run}"
        local run_out="${ARTIFACT_DIR}/p7-${phase}-${slug}-run${run}.log"
        echo ""
        echo "=== ${tag} patch=${name} voices=${voices} expect_mhz=${expect_mhz} ==="
        clock_stamp "before-${tag}" "$expect_mhz" | tee -a "$out"
        "$SCRIPT_DIR/measure-confirm-at-voices.sh" \
            --patch-name "$name" --voices "$voices" --seconds "$CONFIRM_SEC" \
            --buffer "$BUFFER" --periods "$PERIODS" \
            --output "$run_out" --tag "$tag"
        clock_stamp "after-${tag}" "$expect_mhz" | tee -a "$out"
        awk -v t="$tag" '
            $0 ~ /^RESULT tag=.* xruns=/ {
                for (i = 1; i <= NF; i++) {
                    if ($i ~ /^xruns=/) { split($i, a, "="); xr=a[2] }
                    if ($i ~ /^dsp_p99=/) { split($i, b, "="); p99=b[2] }
                    if ($i ~ /^dsp_max=/) { split($i, c, "="); mx=c[2] }
                }
            }
            END { printf "SUMMARY tag=%s xruns=%s dsp_p99=%s dsp_max=%s\n", t, xr+0, p99+0, mx+0 }
        ' "$run_out" | tee -a "$out"
        run=$((run + 1))
        sleep 5
    done
}

echo "=== Plan P7 $(date -Is) artifacts=${ARTIFACT_DIR} phase=${PHASE} ==="
echo "git=$(git -C "$SCRIPT_DIR/.." rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "buffer=${BUFFER} periods=${PERIODS} confirm_sec=${CONFIRM_SEC} runs=${BASELINE_RUNS}"

clock_assert_idle || exit 1

# P7 holds governor off — one variable (G2 may have left it on).
systemctl stop surge-poly-governor.service 2>/dev/null || true

{
    echo "PREDICTION clock_gain_pct=11.1 expect_dsp_p99_drop_pct=~10 if_compute_bound"
    echo "PREDICTION falsifier=dsp_p99_within_baseline_spread -> clock_not_binding"
    echo "PREDICTION alarm=dsp_p99_drop_much_gt_11pct -> comparison_broken"
} | tee "${ARTIFACT_DIR}/p7-prediction.txt"

if [ "$PHASE" = "full" ] || [ "$PHASE" = "baseline" ]; then
    echo ""
    echo "=== P7 baseline @ ~${EXPECT_BASELINE_MHZ} MHz ==="
    clock_stamp "baseline-phase-start" "$EXPECT_BASELINE_MHZ"
    for spec in "${PATCHES[@]}"; do
        name="${spec%%:*}"
        voices="${spec##*:}"
        _run_patch_triple "baseline" "$EXPECT_BASELINE_MHZ" "$name" "$voices"
    done
    clock_stamp "baseline-phase-end" "$EXPECT_BASELINE_MHZ"
fi

if [ "$PHASE" = "baseline" ]; then
    echo "SENTINEL p7-baseline-only complete"
    exit 0
fi

if [ "$PHASE" = "full" ]; then
    echo ""
    echo "=== P7 apply arm_freq=2000 (no over_voltage) ==="
    "$SCRIPT_DIR/pi-overclock-config.sh" backup
    "$SCRIPT_DIR/pi-overclock-config.sh" apply-2000
    echo "REBOOT REQUIRED — re-run: sudo $0 --phase oc --artifact-dir ${ARTIFACT_DIR}"
    echo "SENTINEL p7-await-reboot"
    exit 0
fi

if [ "$PHASE" = "oc" ]; then
    echo ""
    echo "=== P7 overclock @ ~${EXPECT_OC_MHZ} MHz ==="
    clock_stamp "oc-phase-start" "$EXPECT_OC_MHZ" || {
        echo "ERROR: achieved clock not ~2000 — do not measure" >&2
        exit 1
    }
    for spec in "${PATCHES[@]}"; do
        name="${spec%%:*}"
        voices="${spec##*:}"
        _run_patch_triple "oc" "$EXPECT_OC_MHZ" "$name" "$voices"
    done
    clock_stamp "oc-phase-end" "$EXPECT_OC_MHZ"

    echo ""
    echo "=== P7 revert to stock ==="
    "$SCRIPT_DIR/pi-overclock-config.sh" revert
    echo "REBOOT REQUIRED — then verify: sudo scripts/pi-overclock-config.sh status (expect ~1800, throttled=0x0)"
    echo "SENTINEL p7-oc-complete await-final-reboot"
    exit 0
fi

echo "ERROR: unknown phase ${PHASE}" >&2
exit 2
