#!/bin/bash
# A2 — frozen reference suite for Pi 4 control / Pi 5 replication.
#
# Confirm harness only. Condition A, governor off, strict mode via measure-latency-run.
# Emits one structured JSON result file per pass — platform is a field, not just the filename.
#
# Usage:
#   sudo ./scripts/measure-reference-suite.sh --platform pi4 [--pass 1] [--artifact-dir DIR]
#   sudo ./scripts/measure-reference-suite.sh --platform pi4 --no-conformance  # if C0 already passed this session
#
# Contract: PROMPT-PI4-CLOSEOUT.md §A2 · PI5-TRANSITION-PLAN.md §1.1
#
# Rule 0.5 (structural): _pilot_loaded_cell runs one strict loaded cell before the
# full pass — catches parser/threshold mismatches in ~2 min, not after the silence block.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"
# shellcheck source=lib/measurement-result.sh
source "$SCRIPT_DIR/lib/measurement-result.sh"

RUN_AS_USER="${MPE_PI_USER:-mitch}"
USER_HOME="$(getent passwd "$RUN_AS_USER" | cut -d: -f6)"
QUICK_SELECT="${USER_HOME}/Documents/Surge XT/Patches/Quick Select"
ENV_FILE="/etc/mpe/mpe.env"

PLATFORM=""
PASS=1
ARTIFACT_DIR=""
RUN_CONFORMANCE=1
SECONDS_HOLD=25
RUNS=2
export MPE_EXPECT_SAMPLES=$SECONDS_HOLD

while [ $# -gt 0 ]; do
    case "$1" in
        --platform) PLATFORM="${2:?}"; shift 2 ;;
        --pass) PASS="${2:?}"; shift 2 ;;
        --artifact-dir) ARTIFACT_DIR="${2:?}"; shift 2 ;;
        --no-conformance) RUN_CONFORMANCE=0; shift ;;
        -h | --help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

[ -n "$PLATFORM" ] || {
    echo "ERROR: --platform required (e.g. pi4, pi5)" >&2
    exit 2
}

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

ARTIFACT_DIR="${ARTIFACT_DIR:-${USER_HOME}/reference-suite-${PLATFORM}-$(date +%Y%m%d-%H%M%S)}"
JSON_OUT="${ARTIFACT_DIR}/reference-suite-${PLATFORM}-pass${PASS}.json"
TSV_OUT="${ARTIFACT_DIR}/reference-suite-cells.tsv"
mkdir -p "$ARTIFACT_DIR"
exec > >(tee -a "${ARTIFACT_DIR}/reference-suite.log") 2>&1

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

_clock_mhz() {
    local raw
    raw="$(vcgencmd measure_clock arm 2>/dev/null | sed 's/arm frequency=//;s/Hz//')" || true
    if [ -n "$raw" ] && [[ "$raw" =~ ^[0-9]+$ ]]; then
        echo $((raw / 1000000))
        return 0
    fi
    cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null | awk '{printf "%.0f", $1/1000}' || echo "unknown"
}

_cpu_model() {
    awk -F: '/^Model|^Hardware|^CPU implementer/ { gsub(/^ +/, "", $2); print $1"="$2 }' /proc/cpuinfo 2>/dev/null \
        | paste -sd';' - || echo "unknown"
}

_surge_revision() {
    local cli="${SURGE_XT_CLI:-${USER_HOME}/.local/bin/surge-xt-cli}"
    [ -x "$cli" ] || cli="$(command -v surge-xt-cli 2>/dev/null || true)"
    if [ -n "$cli" ] && [ -x "$cli" ]; then
        "$cli" --version 2>/dev/null | head -1 || basename "$cli"
    else
        echo "unknown"
    fi
}

_jack_version() {
    jackd --version 2>/dev/null | head -1 || ps -o args= -C jackd 2>/dev/null | head -1 || echo "unknown"
}

_collect_meta() {
    local repo_commit kernel machine model governor clock throttle jack surge
    repo_commit="$(git -C "$MPE_MODULE_REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    kernel="$(uname -r)"
    machine="$(uname -m)"
    model="$(_cpu_model)"
    governor="$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo unknown)"
    clock="$(_clock_mhz)"
    throttle="$(vcgencmd get_throttled 2>/dev/null || echo unknown)"
    jack="$(_jack_version)"
    surge="$(_surge_revision)"
    cat <<EOF
{
  "platform": "${PLATFORM}",
  "pass": ${PASS},
  "recorded_at": "$(date -Is)",
  "machine": "${machine}",
  "cpu_model": "${model}",
  "kernel": "${kernel}",
  "repo_commit": "${repo_commit}",
  "surge_revision": "${surge}",
  "jack": $(printf '%s' "$jack" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))'),
  "governor": "${governor}",
  "clock_mhz": "${clock}",
  "throttle": "${throttle}",
  "condition": "A",
  "governor_policy": "off",
  "seconds_per_run": ${SECONDS_HOLD},
  "runs_per_cell": ${RUNS}
}
EOF
}

_parse_last_run() {
    local log="$1"
    local relaxed="${2:-0}"
    local tag
    tag="$(grep '^SENTINEL run-complete' "$log" | tail -1 | sed 's/.*tag=//;s/ xruns=.*//')"
    [ -n "$tag" ] || { echo "ERROR: no run-complete in ${log}" >&2; return 1; }
    if [ "$relaxed" = 1 ]; then
        awk -v want="$tag" '
            $0 ~ ("^RESULT tag=" want " xruns=") {
                for (i = 1; i <= NF; i++) {
                    if ($i ~ /^xruns=/) xr = substr($i, 7)
                    if ($i ~ /^dsp_median=/) dm = substr($i, 12)
                    if ($i ~ /^dsp_p99=/) dp = substr($i, 9)
                    if ($i ~ /^dsp_max=/) dx = substr($i, 9)
                    if ($i ~ /^samples=/) sm = substr($i, 9)
                    if ($i ~ /^temp=/) tp = $i
                    if ($i ~ /^throttled=/) th = $i
                }
            }
            END {
                if (xr == "") exit 1
                print xr "\t" dm "\t" dp "\t" dx "\t" sm "\t" tp "\t" th
            }
        ' "$log" || return 1
        return 0
    fi
    mpe_result_load_tag "$log" "$tag" || return 1
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "${MPE_R_xruns}" "${MPE_R_dsp_median}" "${MPE_R_dsp_p99}" "${MPE_R_dsp_max}" \
        "${MPE_R_samples:-}" "${MPE_R_temp:-unknown}" "${MPE_R_throttled:-unknown}"
}

_run_cell() {
    local cell_id="$1" patch="$2" voices="$3" buffer="$4" periods="$5"
    local slug log row xr dsp_med dsp_p99 dsp_max samples temp thr relaxed=0
    slug="${patch:-silence}"
    slug="${slug// /_}"
    log="${ARTIFACT_DIR}/cell-${cell_id}-${slug}-b${buffer}-p${periods}-v${voices}.log"

    echo ""
    echo "=== reference cell=${cell_id} patch=${patch:-silence} voices=${voices} buffer=${buffer} periods=${periods} ==="

    if [ "$voices" -eq 0 ]; then
        relaxed=1
    fi

    if [ -n "$patch" ] && [ "$patch" != "silence" ]; then
        local patch_path="${QUICK_SELECT}/${patch}.fxp"
        [ -f "$patch_path" ] || { echo "ERROR: missing $patch_path" >&2; exit 1; }
        sudo -u "$RUN_AS_USER" python3 "$SCRIPT_DIR/load-patch-osc.py" "$patch_path"
        sleep 1
    fi

    "$SCRIPT_DIR/measure-latency-run.sh" \
        --buffer "$buffer" --periods "$periods" --condition A \
        --runs "$RUNS" --seconds "$SECONDS_HOLD" \
        --hold-voices "$voices" \
        --provenance-patch "${patch:-silence}" --provenance-voices "$voices" \
        --output "$log" --no-restore-buffer

    IFS=$'\t' read -r xr dsp_med dsp_p99 dsp_max samples temp thr < <(_parse_last_run "$log" "$relaxed")

    printf '%s\t%s\t%s\t%s\t%d\t%d\t%d\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$PLATFORM" "$PASS" "$cell_id" "${patch:-silence}" "$voices" "$buffer" "$periods" \
        "$xr" "$dsp_med" "$dsp_p99" "$dsp_max" "$samples" "$temp" "$thr" "$log" \
        >>"$TSV_OUT"

    echo "CELL_SUMMARY id=${cell_id} patch=${patch:-silence} voices=${voices} ${buffer}x${periods} xruns=${xr} dsp_median=${dsp_med} log=${log}"
    if [ "${xr:-1}" -ne 0 ]; then
        echo "WARN: non-zero xruns in cell ${cell_id}"
    fi
}

_pilot_loaded_cell() {
    local pilot_log="${ARTIFACT_DIR}/pilot-loaded-P1-Crystals.log"
    local patch_path="${QUICK_SELECT}/Crystals.fxp"
    local xr dsp_med dsp_p99 dsp_max samples temp thr

    echo ""
    echo "=== Rule 0.5 pilot: one loaded cell (Crystals @3 1024x2, 1 run) before full pass ==="
    [ -f "$patch_path" ] || { echo "ERROR: missing $patch_path" >&2; exit 1; }
    export MPE_EXPECT_SAMPLES=$SECONDS_HOLD
    sudo -u "$RUN_AS_USER" python3 "$SCRIPT_DIR/load-patch-osc.py" "$patch_path"
    sleep 1
    "$SCRIPT_DIR/measure-latency-run.sh" \
        --buffer 1024 --periods 2 --condition A \
        --runs 1 --seconds "$SECONDS_HOLD" \
        --hold-voices 3 \
        --provenance-patch Crystals --provenance-voices 3 \
        --output "$pilot_log" --no-restore-buffer
    IFS=$'\t' read -r xr dsp_med dsp_p99 dsp_max samples temp thr < <(_parse_last_run "$pilot_log" 0)
    echo "SENTINEL pilot-loaded-cell-pass patch=Crystals voices=3 1024x2 xruns=${xr} dsp_median=${dsp_med} samples=${samples} expect=${MPE_EXPECT_SAMPLES}"
}

_emit_json() {
    local meta_file="$1"
    python3 - "$meta_file" "$TSV_OUT" "$JSON_OUT" <<'PY'
import json, sys
from pathlib import Path

meta = json.loads(Path(sys.argv[1]).read_text())
tsv = Path(sys.argv[2])
out = Path(sys.argv[3])
cells = []
if tsv.is_file():
    for line in tsv.read_text().splitlines():
        if not line.strip():
            continue
        (
            platform, pass_n, cell_id, patch, voices, buffer, periods,
            xruns, dsp_median, dsp_p99, dsp_max, samples, temp, throttle, log,
        ) = line.split("\t")
        cells.append({
            "cell_id": cell_id,
            "patch": patch,
            "voices": int(voices),
            "buffer": int(buffer),
            "periods": int(periods),
            "xruns": int(xruns),
            "dsp_median": float(dsp_median),
            "dsp_p99": float(dsp_p99),
            "dsp_max": float(dsp_max),
            "samples": int(samples) if samples.isdigit() else samples,
            "temp": temp,
            "throttle": throttle,
            "log": log,
        })
meta["cells"] = cells
out.write_text(json.dumps(meta, indent=2) + "\n")
print(f"Wrote {out} ({len(cells)} cells)")
PY
}

echo "=== reference-suite platform=${PLATFORM} pass=${PASS} $(date -Is) ==="
echo "artifacts=${ARTIFACT_DIR}"

if [ "$RUN_CONFORMANCE" -eq 1 ]; then
    echo "=== C0 preflight (full gate) ==="
    "$SCRIPT_DIR/instrument-conformance.sh"
fi

if [ ! -x "${MPE_MODULE_REPO}/native/mpe-xrun-probe/mpe-xrun-probe" ]; then
    echo "=== building mpe-xrun-probe ==="
    "$SCRIPT_DIR/build-mpe-xrun-probe.sh" --required
fi

_set_env_var MPE_POLY_GOVERNOR 0
systemctl stop surge-poly-governor.service 2>/dev/null || true

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

printf 'platform\tpass\tcell_id\tpatch\tvoices\tbuffer\tperiods\txruns\tdsp_median\tdsp_p99\tdsp_max\tsamples\ttemp\tthrottle\tlog\n' >"$TSV_OUT"

META_FILE="$(mktemp)"
_collect_meta >"$META_FILE"

_pilot_loaded_cell

# Silence @ 0 voices — fixed-cost isolation
_run_cell "S1" "silence" 0 1024 2
_run_cell "S2" "silence" 0 512 2
_run_cell "S3" "silence" 0 256 3

# Four patches at confirmed floors × buffer ladder
_run_cell "P1" "Crystals" 3 1024 2
_run_cell "P2" "Crystals" 3 512 2
_run_cell "P3" "Crystals" 3 256 3

_run_cell "P4" "Cloud Horn" 5 1024 2
_run_cell "P5" "Cloud Horn" 5 512 2
_run_cell "P6" "Cloud Horn" 5 256 3

_run_cell "P7" "Duduk" 3 1024 2
_run_cell "P8" "Duduk" 3 512 2
_run_cell "P9" "Duduk" 3 256 3

_run_cell "P10" "Brave New World" 3 1024 2
_run_cell "P11" "Brave New World" 3 512 2
_run_cell "P12" "Brave New World" 3 256 3

_emit_json "$META_FILE"
rm -f "$META_FILE"

echo "SENTINEL reference-suite-complete platform=${PLATFORM} pass=${PASS} json=${JSON_OUT}"
