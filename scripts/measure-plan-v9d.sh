#!/bin/bash
# V9-d — 1024×2 vs ×3 at verified-clean load on 2 more bounded patches.
#
# Usage: sudo ./scripts/measure-plan-v9d.sh [--artifact-dir DIR]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
RUN_AS_USER="${MPE_PI_USER:-mitch}"
USER_HOME="$(getent passwd "$RUN_AS_USER" | cut -d: -f6)"
ARTIFACT_DIR="${USER_HOME}/plan-v9d-$(date +%Y%m%d-%H%M%S)"

while [ $# -gt 0 ]; do
    case "$1" in
        --artifact-dir) ARTIFACT_DIR="${2:?}"; shift 2 ;;
        -h | --help) sed -n '2,5p' "$0"; exit 0 ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

mkdir -p "$ARTIFACT_DIR"
exec > >(tee -a "${ARTIFACT_DIR}/plan-v9d.log") 2>&1

echo "=== Plan V9-d $(date -Is) artifacts=$ARTIFACT_DIR ==="

_run_pair() {
    local name="$1" voices="$2"
    local slug="${name// /_}"
    echo ""
    echo "=== V9-d patch=${name} voices=${voices} ==="
    "$SCRIPT_DIR/measure-v8b-playable.sh" \
        --patch-name "$name" --voices "$voices" --artifact-dir "$ARTIFACT_DIR" \
        --log-slug "$slug"
}

# V8-a bounded @ 1024, confirmed stable class (clean=3)
_run_pair "Duduk" 3
_run_pair "Brave New World" 3

echo "SENTINEL v9d-complete artifacts=$ARTIFACT_DIR"
