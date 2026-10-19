#!/bin/bash
# Plan V8 — patch capacity survey (1024×3) + playable 1024×2 test.
#
# Usage: sudo ./scripts/measure-plan-v8.sh [--artifact-dir DIR] [--quick-select DIR]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"
# shellcheck source=lib/measure-run-as-user.sh
source "$SCRIPT_DIR/lib/measure-run-as-user.sh"
# shellcheck source=lib/appliance-user.sh
source "$SCRIPT_DIR/lib/appliance-user.sh"
RUN_AS_USER="$(mpe_appliance_user)" || exit 1
USER_HOME="$(getent passwd "$RUN_AS_USER" | cut -d: -f6)"
ARTIFACT_DIR="${USER_HOME}/plan-v8-$(date +%Y%m%d-%H%M%S)"
QUICK_SELECT="${USER_HOME}/Documents/Surge XT/Patches/Quick Select"
# shellcheck source=lib/measure-env.sh
source "$SCRIPT_DIR/lib/measure-env.sh"
PROBE_SEC=8

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
exec > >(tee -a "${ARTIFACT_DIR}/plan-v8.log") 2>&1

echo "=== Plan V8 $(date -Is) artifacts=$ARTIFACT_DIR ==="
echo "quick_select=$QUICK_SELECT"
echo "git=$(git -C "$SCRIPT_DIR/.." rev-parse --short HEAD 2>/dev/null || echo unknown)"

# Edits only when they differ; restored byte for byte on exit (lib/measure-env.sh).
mpe_env_arm
mpe_env_set MPE_POLY_GOVERNOR 0
mpe_env_set MPE_POLY_CEILING 64
mpe_env_set MPE_POLY_FLOOR 64
mpe_unit_stop_for_measure surge-poly-governor.service
# The touch UI owns the stage gain and re-sends it every second: a patch loaded
# here would play at the UI patch's level (load-patch-osc.py refuses while it runs).
for _ui_unit in touch-patch-browser.service patch-browser.service; do
    mpe_unit_stop_for_measure "$_ui_unit"
done
if systemctl is-enabled --quiet surge-poly-governor.service 2>/dev/null; then
    mpe_env_on_restore systemctl enable surge-poly-governor.service
    systemctl disable surge-poly-governor.service 2>/dev/null || true
fi

V8A_LOG="${ARTIFACT_DIR}/v8a-survey.log"
: >"$V8A_LOG"

mapfile -t PATCHES < <(find "$QUICK_SELECT" -maxdepth 1 -name '*.fxp' | sort)
echo "patch_count=${#PATCHES[@]}"

idx=0
for fxp in "${PATCHES[@]}"; do
    idx=$((idx + 1))
    name="$(basename "$fxp" .fxp)"
    tag="V8a-$(printf '%03d' "$idx")-${name// /_}"
    echo ""
    echo "=== V8-a patch ${idx}/${#PATCHES[@]}: ${name} ==="
    extra=()
    [ "$idx" -gt 1 ] && extra+=(--skip-setup)
    "$SCRIPT_DIR/measure-capacity-ramp.sh" \
        --buffer 1024 --periods 3 --tag "$tag" \
        --output "$V8A_LOG" --patch-name "$name" --patch-path "$fxp" \
        --probe-sec "$PROBE_SEC" --skip-confirm --start-voice 1 \
        "${extra[@]}"
    sleep 2
done

echo ""
echo "=== V8-b: pick mid-weight patch from survey ==="
V8B_TABLE="${ARTIFACT_DIR}/v8b-pick.tsv"
awk '
    /^=== capacity-ramp/ {
        patch=""
        sub(/^.* patch=/, "")
        sub(/ [0-9]{4}-.*$/, "")
        patch=$0
    }
    /^\{/ { meta=$0 }
    /RESULT tag=V8a-.* first_overrun=/ {
        fo=""
        sc=0
        for (i = 1; i <= NF; i++) {
            if ($i ~ /^first_overrun=/) {
                split($i, a, "=")
                fo=a[2]
            }
            if ($i ~ /^sustained_clean=/) {
                split($i, b, "=")
                sc=b[2]+0
            }
        }
        if (patch != "") {
            print patch "\t" sc "\t" fo "\t" meta
        }
    }
' "$V8A_LOG" >"$V8B_TABLE"

V8B_PICK="$(awk -F'\t' '$2 >= 3 && $2 <= 10 && $3 != "1" && $3 != "2" { print $2, $1 }' "$V8B_TABLE" | sort -n | awk 'NR==int((NR+1)/2){print $2; exit}')"
V8B_VOICES="$(awk -F'\t' -v p="$V8B_PICK" '$1==p { print $2; exit }' "$V8B_TABLE")"

if [ -z "$V8B_PICK" ]; then
    V8B_PICK="$(awk -F'\t' '$2 >= 2 { print $2, $1 }' "$V8B_TABLE" | sort -n | tail -1 | awk '{ print $2 }')"
    V8B_VOICES="$(awk -F'\t' -v p="$V8B_PICK" '$1==p { print $2; exit }' "$V8B_TABLE")"
fi

[ -n "$V8B_PICK" ] || V8B_PICK="Rhody EP"
[ -n "$V8B_VOICES" ] || V8B_VOICES=4
if [ "$V8B_VOICES" -gt 15 ]; then
    V8B_VOICES=15
fi

V8B_PATH="$QUICK_SELECT/${V8B_PICK}.fxp"
echo "V8-b patch=${V8B_PICK} voices=${V8B_VOICES} path=${V8B_PATH}"
[ -f "$V8B_PATH" ] || { echo "ERROR: V8-b patch file missing: ${V8B_PATH}" >&2; exit 1; }

MPE_RUN_AS_USER="$RUN_AS_USER"; MPE_RUN_AS_USER_HOME="$USER_HOME"; mpe_load_patch_osc "$V8B_PATH" "$SCRIPT_DIR"
sleep 1

echo ""
echo "=== V8-b 1024×2 @ playable voices (n=3, 45s) ==="
V8B_X2="${ARTIFACT_DIR}/v8b-1024x2.log"
"$SCRIPT_DIR/measure-latency-run.sh" \
    --buffer 1024 --periods 2 --condition A --runs 3 --seconds 45 \
    --hold-voices "$V8B_VOICES" --provenance-patch "$V8B_PICK" --provenance-voices "$V8B_VOICES" \
    --output "$V8B_X2" --no-restore-buffer

echo ""
echo "=== V8-b baseline 1024×3 (same patch/voices) ==="
V8B_X3="${ARTIFACT_DIR}/v8b-1024x3.log"
"$SCRIPT_DIR/measure-latency-run.sh" \
    --buffer 1024 --periods 3 --condition A --runs 3 --seconds 45 \
    --hold-voices "$V8B_VOICES" --provenance-patch "$V8B_PICK" --provenance-voices "$V8B_VOICES" \
    --output "$V8B_X3"

echo ""
# Softmode: each measure-latency-run.sh restores the file it edited, and this
# script's exit trap restores the governor keys, byte for byte.

echo "SENTINEL plan-v8-complete $(date -Is)"
echo "v8a_log=$V8A_LOG"
echo "v8b_patch=$V8B_PICK"
echo "v8b_voices=$V8B_VOICES"
