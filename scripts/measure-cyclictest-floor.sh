#!/bin/bash
# Record cyclictest wakeup latency floor — Step 0 of low-latency-512-256-spec.md
#
# Appends results; never truncates the output file. Exits non-zero if cyclictest
# is missing or the run fails.
#
# Usage:
#   ./scripts/measure-cyclictest-floor.sh [--output FILE] [--label TAG]
#
# Default command (from spec):
#   cyclictest -m -t1 -p 80 -i 200 -l 300000
#
# NOTE: rt-tests 2.6 removed -n (clock_nanosleep is the default; -x opts out to
# POSIX timers). Passing -n makes cyclictest print usage and exit non-zero.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

OUTPUT="${MPE_CYCLICTEST_LOG:-$HOME/latency-cyclictest.log}"
LABEL="stock-kernel"

while [ $# -gt 0 ]; do
    case "$1" in
        --output) OUTPUT="${2:?--output requires a path}"; shift 2 ;;
        --label) LABEL="${2:?--label requires a value}"; shift 2 ;;
        -h | --help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

if ! command -v cyclictest >/dev/null 2>&1; then
    echo "ERROR: cyclictest not found — install rt-tests (sudo apt install rt-tests)" >&2
    exit 1
fi

# Run FIRST, validate, and only then append. A previous version piped cyclictest
# straight into the log: when -n became invalid in rt-tests 2.6, it wrote the
# tool's usage text into the measurement file, printed a success message and
# exited 0. Per docs/measurements/README.md, a reading must not look the same
# broken or fine.
RAW="$(cyclictest -m -t1 -p 80 -i 200 -l 300000 2>&1)"
RC=$?

if [ "$RC" -ne 0 ]; then
    echo "ERROR: cyclictest exited $RC — nothing logged" >&2
    printf '%s\n' "$RAW" | head -5 >&2
    exit 1
fi

# A real run reports per-thread "T: 0 (...) P:80 ... Min: N Act: N Avg: N Max: N".
# Usage text, a permissions failure, or a truncated run will not match.
# NOTE: a here-string, not `printf ... | grep -q`. With pipefail set, grep -q exits on
# the first match, the writer takes SIGPIPE (141), and pipefail reports that as the
# pipeline's status -- so a SUCCESSFUL match reads as failure. It only bites once the
# output is big enough that the writer has not finished first: a 44 KB run passed, the
# real 875 KB run did not.
if ! grep -qE 'Min:[[:space:]]*[0-9]+.*Max:[[:space:]]*[0-9]+' <<<"$RAW"; then
    echo "ERROR: cyclictest produced no Min/Max latency line — nothing logged" >&2
    printf '%s\n' "$RAW" | head -5 >&2
    exit 1
fi

MAXUS="$(grep -oE 'Max:[[:space:]]*[0-9]+' <<<"$RAW" | grep -oE '[0-9]+' | sort -n | tail -1)"

{
    echo "=== cyclictest floor label=${LABEL} $(date -Is) ==="
    echo "SENTINEL cyclictest-start"
    if [ -d "$MPE_MODULE_REPO/.git" ]; then
        git -C "$MPE_MODULE_REPO" log --oneline -1 2>/dev/null || true
        git -C "$MPE_MODULE_REPO" status --porcelain 2>/dev/null || true
    fi
    uname -r
    tr '\0' ' ' < /proc/cmdline 2>/dev/null || true
    echo
    printf '%s\n' "$RAW"
    echo "WORST_CASE_USEC=${MAXUS}"
    echo "SENTINEL cyclictest-end"
    echo
} >>"$OUTPUT"

echo "Appended cyclictest floor to $OUTPUT (worst case ${MAXUS} us)"
echo "SENTINEL cyclictest-logged"
