#!/bin/bash
# V12 — compare 1024×2 vs 512×2 xrun rate at Cloud Horn @5.
#
# Default governor off (Pi 4 canonical per PI4-CLOSEOUT-2026-08-23). Pass
# --governor on for G2/B3 governor-on path. Between arms: jack+surge restart
# and loaded preflight for the next buffer (2026-08-23 handoff fix).
#
# Orchestrates two measure-soak-instrument arms. Does NOT emit PASS/FAIL — see
# docs/measurements/PROMPT-V12-certify-buffer.md.
#
# Usage:
#   sudo ./scripts/measure-v12-buffer-compare.sh [--minutes 30] [--order random|alternate|1024-first|512-first] \
#       [--output-dir DIR] [--governor off|on] [--pilot]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
MINUTES=30
ORDER=alternate
OUTPUT_DIR="${HOME}/v12-buffer-$(date +%Y%m%d-%H%M%S)"
PILOT=0
PATCH_NAME="Cloud Horn"
VOICES=5
GOVERNOR=off

while [ $# -gt 0 ]; do
    case "$1" in
        --minutes) MINUTES="${2:?}"; shift 2 ;;
        --order) ORDER="${2:?}"; shift 2 ;;
        --output-dir) OUTPUT_DIR="${2:?}"; shift 2 ;;
        --governor) GOVERNOR="${2:?}"; shift 2 ;;
        --pilot) PILOT=1; shift ;;
        --patch-name) PATCH_NAME="${2:?}"; shift 2 ;;
        --voices) VOICES="${2:?}"; shift 2 ;;
        -h | --help)
            sed -n '2,14p' "$0"
            exit 0
            ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

case "$GOVERNOR" in
    on | off) ;;
    *) echo "ERROR: --governor must be on or off (got: $GOVERNOR)" >&2; exit 2 ;;
esac

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

if [ "$PILOT" -eq 1 ]; then
    MINUTES=2
    echo "V12 pilot: ${MINUTES} min single arm @ 1024×2 governor=${GOVERNOR}"
    mkdir -p "$OUTPUT_DIR"
    exec "$SCRIPT_DIR/measure-soak-instrument.sh" \
        --minutes "$MINUTES" --buffer 1024 --periods 2 \
        --governor "$GOVERNOR" --patch-name "$PATCH_NAME" --voices "$VOICES" \
        --label v12-pilot-1024x2 \
        --output "${OUTPUT_DIR}/v12-pilot-1024x2.log"
fi

case "$ORDER" in
    random)
        if [ $((RANDOM % 2)) -eq 0 ]; then
            FIRST=1024
            SECOND=512
        else
            FIRST=512
            SECOND=1024
        fi
        ;;
    alternate | 1024-first)
        FIRST=1024
        SECOND=512
        ;;
    512-first)
        FIRST=512
        SECOND=1024
        ;;
    *)
        echo "ERROR: --order must be random|alternate|1024-first|512-first" >&2
        exit 2
        ;;
esac

mkdir -p "$OUTPUT_DIR"
SUMMARY="${OUTPUT_DIR}/v12-summary.txt"

{
    echo "=== V12 buffer compare $(date -Is) ==="
    echo "minutes_per_arm=${MINUTES} order=${ORDER} first=${FIRST} governor=${GOVERNOR} patch=${PATCH_NAME} voices=${VOICES}"
    echo "SENTINEL v12-start"
} | tee "$SUMMARY"

_inter_arm_handoff() {
    local buf="$1" per="$2" tag="$3"
    local pf_log="${OUTPUT_DIR}/v12-preflight-${tag}.log"
    echo "--- inter-arm handoff → buffer=${buf} periods=${per} ---" | tee -a "$SUMMARY"
    echo "inter-arm: systemctl restart mpe-jackd surge-xt-cli" | tee -a "$SUMMARY"
    systemctl restart mpe-jackd.service
    sleep 4
    systemctl restart surge-xt-cli.service
    sleep 6
    echo "inter-arm: preflight buffer=${buf} → ${pf_log}" | tee -a "$SUMMARY"
    if ! "$SCRIPT_DIR/measure-soak-preflight.sh" \
        --buffer "$buf" --periods "$per" \
        --governor "$GOVERNOR" --patch-name "$PATCH_NAME" --voices "$VOICES" \
        --output "$pf_log"; then
        {
            echo "ERROR: inter-arm preflight FAILED buffer=${buf} periods=${per} tag=${tag}"
            echo "See log: ${pf_log}"
            echo "SENTINEL v12-aborted preflight buffer=${buf}"
        } | tee -a "$SUMMARY" >&2
        exit 1
    fi
    echo "inter-arm preflight PASS buffer=${buf} tag=${tag}" | tee -a "$SUMMARY"
}

_run_arm() {
    local buf="$1" per="$2" tag="$3"
    local log="${OUTPUT_DIR}/v12-${tag}.log"
    echo "--- arm ${tag} buffer=${buf} periods=${per} ---" | tee -a "$SUMMARY"
    "$SCRIPT_DIR/measure-soak-instrument.sh" \
        --minutes "$MINUTES" --buffer "$buf" --periods "$per" \
        --governor "$GOVERNOR" --patch-name "$PATCH_NAME" --voices "$VOICES" \
        --label "v12-${tag}" \
        --output "$log"
    _analyze_arm "$log" "$tag" | tee -a "$SUMMARY"
}

_analyze_arm() {
    local log="$1" tag="$2"
    if [ ! -f "$log" ]; then
        echo "ERROR: missing log ${log}" >&2
        return 1
    fi
    awk -v tag="$tag" '
        /^SOAK minute=/ {
            for (i = 1; i <= NF; i++) {
                split($i, kv, "=")
                if (kv[1] == "xruns_minute") d = kv[2] + 0
                if (kv[1] == "xruns_total") total = kv[2] + 0
                if (kv[1] == "governor_engagements") gov = kv[2] + 0
            }
            n++
            sum += d
            sq += d * d
            if (d == 0) silent++
            if (d > max_min) max_min = d
            if (d == 0) {
                if (run > longest_silent) longest_silent = run
                run = 0
            } else {
                run++
            }
            gov_sum += gov
        }
        END {
            if (n == 0) {
                print "ARM " tag " ERROR: no SOAK minute lines"
                exit 1
            }
            if (run > longest_silent) longest_silent = run
            mean = sum / n
            var = (sq / n) - (mean * mean)
            if (var < 0) var = 0
            fano = (mean > 0) ? var / mean : 0
            silent_frac = silent / n
            rate = mean
            printf "ARM %s minutes=%d xruns_total=%d rate_per_min=%.3f fano=%.3f silent_minutes=%d silent_frac=%.3f longest_silent_run=%d largest_minute=%d governor_engagements=%d\n",
                tag, n, total, rate, fano, silent, silent_frac, longest_silent, max_min, gov_sum
        }
    ' "$log"
}

_run_arm "$FIRST" 2 "b${FIRST}-p2"
_inter_arm_handoff "$SECOND" 2 "b${SECOND}-p2"
_run_arm "$SECOND" 2 "b${SECOND}-p2"

{
    r1024="$(grep '^ARM b1024-p2 ' "$SUMMARY" | sed -n 's/.*rate_per_min=\([0-9.]*\).*/\1/p')"
    r512="$(grep '^ARM b512-p2 ' "$SUMMARY" | sed -n 's/.*rate_per_min=\([0-9.]*\).*/\1/p')"
    echo "--- headline ---"
    if [ -n "$r1024" ] && [ -n "$r512" ] && awk "BEGIN { exit !($r1024 > 0) }"; then
        ratio="$(awk "BEGIN { printf \"%.3f\", $r512 / $r1024 }")"
    else
        ratio="unknown"
    fi
    echo "rate_1024x2_per_min=${r1024:-unknown} rate_512x2_per_min=${r512:-unknown} ratio_512_over_1024=${ratio}"
    echo "NOTE: no PASS/FAIL — audibility is B3 (Mitch ear)"
    echo "SENTINEL v12-complete"
} | tee -a "$SUMMARY"

echo "V12 complete → ${SUMMARY}"
