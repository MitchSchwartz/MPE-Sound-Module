#!/usr/bin/env bash
# Parameterised Surge XT CLI build — same source revision, arch is the only variable.
# Usage: ./scripts/build-surge.sh --arch {a72|a76|generic}
#
# Pi 4 closeout A7. a72 delegates to build-surge-a72.sh. Does NOT install.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH=""
SURGE_COMMIT="${SURGE_COMMIT:-253f8d86}"

usage() {
    echo "Usage: $0 --arch {a72|a76|generic} [--commit SHA]" >&2
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --arch) ARCH="${2:?}"; shift 2 ;;
        --commit) SURGE_COMMIT="${2:?}"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Unknown arg: $1" >&2; usage ;;
    esac
done

[ -n "$ARCH" ] || usage

case "$ARCH" in
    a72)
        export SURGE_COMMIT
        exec "$ROOT/scripts/build-surge-a72.sh"
        ;;
    a76)
        MCPUFLAG="-mcpu=cortex-a76"
        BUILD_SUFFIX=a76
        ;;
    generic)
        MCPUFLAG=""
        BUILD_SUFFIX=generic
        ;;
    *)
        echo "ERROR: unknown arch: $ARCH (want a72|a76|generic)" >&2
        exit 1
        ;;
esac

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
SURGE_SRC="${SURGE_SRC:-$HOME/surge-src}"
BUILD_DIR="${BUILD_DIR:-$SURGE_SRC/build-${BUILD_SUFFIX}}"
LOG="${LOG:-$HOME/surge-build-${BUILD_SUFFIX}.log}"
JOBS="${JOBS:-$(nproc)}"

{
    echo "=== build-surge --arch=${ARCH} $(date -Is) ==="
    echo "PROVENANCE commit=${SURGE_COMMIT} arch=${ARCH} jobs=${JOBS}"
} >>"$LOG"

require_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: missing $1" >&2; exit 1; }; }
for c in git cmake make g++; do require_cmd "$c"; done

[ -d "$SURGE_SRC/.git" ] || git clone https://github.com/surge-synthesizer/surge.git "$SURGE_SRC"
cd "$SURGE_SRC"
git fetch origin --tags
git checkout "$SURGE_COMMIT"
git submodule update --init --recursive

mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"
rm -rf CMakeCache.txt CMakeFiles

CMAKE_ARGS=(
    -DCMAKE_BUILD_TYPE=Release
    -DLINUX_ON_ARM=TRUE
    -DSURGE_BUILD_LV2=FALSE
    -DSURGE_BUILD_VST3=FALSE
    -DSURGE_BUILD_CLAP=FALSE
    -DSURGE_BUILD_STANDALONE=TRUE
    -DSURGE_BUILD_TESTRUNNER=FALSE
)
if [ -n "$MCPUFLAG" ]; then
    CMAKE_ARGS+=(-DCMAKE_CXX_FLAGS="$MCPUFLAG" -DCMAKE_C_FLAGS="$MCPUFLAG")
fi

cmake "$SURGE_SRC" "${CMAKE_ARGS[@]}" 2>&1 | tee -a "$LOG"
make surge-xt-cli -j"$JOBS" 2>&1 | tee -a "$LOG"

artifact=""
for candidate in \
    "$BUILD_DIR/surge_xt_products/surge-xt-cli" \
    "$BUILD_DIR/src/surge-xt/surge-xt_artefacts/Release/CLI/surge-xt-cli"; do
    if [ -x "$candidate" ]; then
        artifact="$candidate"
        break
    fi
done
if [ ! -x "$artifact" ]; then
    {
        echo "ERROR: missing surge-xt-cli artifact (searched surge_xt_products and surge-xt_artefacts/Release/CLI)"
    } >>"$LOG"
    exit 1
fi

{
    echo "=== build complete $(date -Is) ==="
    echo "artifact=$artifact"
    echo "version=$("$artifact" --version 2>&1 || true)"
    echo "sha256=$(sha256sum "$artifact" | awk '{print $1}')"
    echo "SENTINEL build-${BUILD_SUFFIX}-complete"
    echo "Next: ./scripts/install-surge-from-build.sh --arch ${ARCH}"
} >>"$LOG"
