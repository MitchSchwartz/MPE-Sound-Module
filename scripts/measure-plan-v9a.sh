#!/bin/bash
# Plan V9-a — ramp sustained-clean vs 60 s confirm (duration sensitivity).
#
# Usage: sudo ./scripts/measure-plan-v9a.sh [--artifact-dir DIR] [--quick-select DIR]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
RUN_AS_USER="${MPE_PI_USER:-mitch}"
USER_HOME="$(getent passwd "$RUN_AS_USER" | cut -d: -f6)"
ARTIFACT_DIR="${USER_HOME}/plan-v9a-$(date +%Y%m%d-%H%M%S)"
QUICK_SELECT="${USER_HOME}/Documents/Surge XT/Patches/Quick Select"
ENV_FILE="/etc/mpe/mpe.env"
PROBE_SEC=8
CONFIRM_SEC=60

PATCHES=(
    "Crystals"
    "Cloud Horn"
    "Closed Hat"
)

while [ $# -gt 0 ]; do
    case "$1" in
        --artifact-dir) ARTIFACT_DIR="${2:?}"; shift 2 ;;
        --quick-select) QUICK_SELECT="${2:?}"; shift 2 ;;
        -h | --help) sed -n '2,6p' "$0"; exit 0 ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

mkdir -p "$ARTIFACT_DIR"
exec > >(tee -a "${ARTIFACT_DIR}/plan-v9a.log") 2>&1

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

_parse_sustained_clean() {
    local log="$1" tag="$2"
    awk -v t="$tag" '
        $0 ~ ("RESULT tag=" t " ") && /sustained_clean=/ {
            for (i = 1; i <= NF; i++) {
                if ($i ~ /^sustained_clean=/) {
                    split($i, a, "=")
                    print a[2]
                    exit
                }
            }
        }
    ' "$log"
}

_parse_xruns() {
    local log="$1"
    awk '/^RESULT tag=.* xruns=/ { xr=$0; sub(/^.* xruns=/, "", xr); sub(/ .*/, "", xr); last=xr }
         END { print last+0 }' "$log"
}

echo "=== Plan V9-a $(date -Is) artifacts=$ARTIFACT_DIR ==="
echo "quick_select=$QUICK_SELECT"
echo "git=$(git -C "$SCRIPT_DIR/.." rev-parse --short HEAD 2>/dev/null || echo unknown)"

_set_env_var MPE_POLY_GOVERNOR 0
_set_env_var MPE_POLY_CEILING 64
_set_env_var MPE_POLY_FLOOR 64
systemctl stop surge-poly-governor.service 2>/dev/null || true
systemctl disable surge-poly-governor.service 2>/dev/null || true

SUMMARY="${ARTIFACT_DIR}/v9a-summary.tsv"
printf 'patch\tramp_sustained_clean\tconfirm_voices\tconfirm_sec\txruns\tdsp_median\tdsp_p99\tdsp_max\tconfirm_clean\tstep_down\n' >"$SUMMARY"

idx=0
for name in "${PATCHES[@]}"; do
    idx=$((idx + 1))
    tag="V9a-$(printf '%02d' "$idx")-${name// /_}"
    fxp="${QUICK_SELECT}/${name}.fxp"
    [ -f "$fxp" ] || { echo "ERROR: patch missing: $fxp" >&2; exit 1; }

    ramp_log="${ARTIFACT_DIR}/v9a-ramp-${name// /_}.log"
    : >"$ramp_log"

    echo ""
    echo "=== V9-a ramp patch=${name} tag=${tag} ==="
    extra=()
    [ "$idx" -gt 1 ] && extra+=(--skip-setup)
    "$SCRIPT_DIR/measure-capacity-ramp.sh" \
        --buffer 1024 --periods 3 --tag "$tag" \
        --output "$ramp_log" --patch-name "$name" --patch-path "$fxp" \
        --probe-sec "$PROBE_SEC" --skip-confirm --start-voice 1 \
        "${extra[@]}"

    ramp_clean="$(_parse_sustained_clean "$ramp_log" "$tag")"
    if [ -z "$ramp_clean" ] || [ "$ramp_clean" -eq 0 ]; then
        echo "ERROR: could not parse sustained_clean for ${name} tag=${tag}" >&2
        exit 1
    fi
    echo "PARSED ramp_sustained_clean=${ramp_clean} patch=${name}"

    confirm_voices="$ramp_clean"
    step_down=0
    confirm_log="${ARTIFACT_DIR}/v9a-confirm-${name// /_}.log"
    : >"$confirm_log"

    sudo -u "$RUN_AS_USER" python3 "$SCRIPT_DIR/load-patch-osc.py" "$fxp"
    sleep 1

    echo "=== V9-a confirm patch=${name} voices=${confirm_voices} sec=${CONFIRM_SEC} ==="
    "$SCRIPT_DIR/measure-latency-run.sh" \
        --buffer 1024 --periods 3 --condition A --runs 1 --seconds "$CONFIRM_SEC" \
        --hold-voices "$confirm_voices" \
        --provenance-patch "$name" --provenance-voices "$confirm_voices" \
        --output "$confirm_log" --no-restore-buffer

    xruns="$(_parse_xruns "$confirm_log")"
    read -r dsp_med dsp_p99 dsp_max < <(
        awk '/^RESULT tag=.* xruns=/ {
            for (i = 1; i <= NF; i++) {
                if ($i ~ /^dsp_median=/) { split($i, a, "="); med=a[2] }
                if ($i ~ /^dsp_p99=/) { split($i, b, "="); p99=b[2] }
                if ($i ~ /^dsp_max=/) { split($i, c, "="); mx=c[2] }
            }
        }
        END { printf "%s %s %s\n", med+0, p99+0, mx+0 }' "$confirm_log"
    )

    confirm_clean="yes"
    if [ "$xruns" -gt 0 ]; then
        confirm_clean="no"
        step_voices=$((confirm_voices - 2))
        if [ "$step_voices" -ge 1 ]; then
            step_down=1
            echo "=== V9-a step-down patch=${name} voices=${step_voices} (ramp was ${confirm_voices}) ==="
            sudo -u "$RUN_AS_USER" python3 "$SCRIPT_DIR/load-patch-osc.py" "$fxp"
            sleep 1
            step_log="${ARTIFACT_DIR}/v9a-confirm-stepdown-${name// /_}.log"
            : >"$step_log"
            "$SCRIPT_DIR/measure-latency-run.sh" \
                --buffer 1024 --periods 3 --condition A --runs 1 --seconds "$CONFIRM_SEC" \
                --hold-voices "$step_voices" \
                --provenance-patch "$name" --provenance-voices "$step_voices" \
                --output "$step_log" --no-restore-buffer
            confirm_voices="$step_voices"
            xruns="$(_parse_xruns "$step_log")"
            read -r dsp_med dsp_p99 dsp_max < <(
                awk '/^RESULT tag=.* xruns=/ {
                    for (i = 1; i <= NF; i++) {
                        if ($i ~ /^dsp_median=/) { split($i, a, "="); med=a[2] }
                        if ($i ~ /^dsp_p99=/) { split($i, b, "="); p99=b[2] }
                        if ($i ~ /^dsp_max=/) { split($i, c, "="); mx=c[2] }
                    }
                }
                END { printf "%s %s %s\n", med+0, p99+0, mx+0 }' "$step_log"
            )
            if [ "$xruns" -gt 0 ]; then
                confirm_clean="no"
            else
                confirm_clean="yes_stepdown"
            fi
        fi
    fi

    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$name" "$ramp_clean" "$confirm_voices" "$CONFIRM_SEC" "$xruns" \
        "$dsp_med" "$dsp_p99" "$dsp_max" "$confirm_clean" "$step_down" >>"$SUMMARY"

    echo "RESULT v9a patch=${name} ramp_clean=${ramp_clean} confirm_voices=${confirm_voices} xruns=${xruns} confirm_clean=${confirm_clean}"
    sleep 3
done

echo ""
echo "=== V9-a summary ==="
column -t -s $'\t' "$SUMMARY" || cat "$SUMMARY"
echo "SENTINEL v9a-complete artifacts=$ARTIFACT_DIR"
