#!/bin/bash
# Sample stream-start variance: N jackd restarts × k windows each.
#
# Each stream is a separate measure-latency-run invocation (set-surge-audio at
# entry restarts jackd). Tags would collide in one append-only log, so this script
# writes one file per stream and a combined index.
#
# Usage:
#   sudo ./scripts/measure-stream-sample.sh \
#     --buffer 256 --condition A --streams 10 --runs-per-stream 3 \
#     --output ~/256-A-streams
#
# --output is a PREFIX; writes ${PREFIX}-stream-NN.log and ${PREFIX}-index.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"

BUFFER=""
CONDITION=""
STREAMS=10
RUNS_PER_STREAM=3
SECONDS_PER_RUN=60
OUTPUT_PREFIX=""
PLAYING_LOOPS=0
PERIODS=""

while [ $# -gt 0 ]; do
    case "$1" in
        --buffer) BUFFER="${2:?}"; shift 2 ;;
        --periods) PERIODS="${2:?}"; shift 2 ;;
        --condition) CONDITION="${2:?}"; shift 2 ;;
        --streams) STREAMS="${2:?}"; shift 2 ;;
        --runs-per-stream) RUNS_PER_STREAM="${2:?}"; shift 2 ;;
        --seconds) SECONDS_PER_RUN="${2:?}"; shift 2 ;;
        --output) OUTPUT_PREFIX="${2:?}"; shift 2 ;;
        --playing-loops) PLAYING_LOOPS="${2:?}"; shift 2 ;;
        -h | --help) sed -n '2,18p' "$0"; exit 0 ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$BUFFER" ] || [ -z "$CONDITION" ] || [ -z "$OUTPUT_PREFIX" ]; then
    echo "ERROR: --buffer, --condition, and --output (prefix) are required" >&2
    exit 2
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

INDEX="${OUTPUT_PREFIX}-index.log"
: >"$INDEX"
{
    echo "=== measure-stream-sample $(date -Is) ==="
    echo "buffer=${BUFFER} periods=${PERIODS:-default} condition=${CONDITION}"
    echo "streams=${STREAMS} runs_per_stream=${RUNS_PER_STREAM} seconds=${SECONDS_PER_RUN}"
    echo "playing_loops=${PLAYING_LOOPS}"
    echo "SENTINEL stream-sample-start"
} >>"$INDEX"

stream=1
while [ "$stream" -le "$STREAMS" ]; do
    stream_log="${OUTPUT_PREFIX}-stream-$(printf '%02d' "$stream").log"
    echo "=== stream ${stream}/${STREAMS} -> ${stream_log} $(date -Is) ===" | tee -a "$INDEX"
    args=(
        --buffer "$BUFFER"
        --condition "$CONDITION"
        --runs "$RUNS_PER_STREAM"
        --seconds "$SECONDS_PER_RUN"
        --output "$stream_log"
        --no-restore-buffer
    )
    [ -n "$PERIODS" ] && args+=(--periods "$PERIODS")
    [ "$PLAYING_LOOPS" -gt 0 ] && args+=(--playing-loops "$PLAYING_LOOPS")
    "${SCRIPT_DIR}/measure-latency-run.sh" "${args[@]}"
    echo "stream=${stream} log=${stream_log}" >>"$INDEX"
    stream=$((stream + 1))
    [ "$stream" -le "$STREAMS" ] && sleep 5
done

echo "=== restore shipping buffer 1024×3 ===" | tee -a "$INDEX"
"${SCRIPT_DIR}/set-surge-audio.sh" --buffer 1024 --periods 3 || true
echo "SENTINEL stream-sample-complete $(date -Is)" | tee -a "$INDEX"
