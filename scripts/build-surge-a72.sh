#!/bin/bash
# P8 build-only: Surge XT CLI at the installed revision with -mcpu=cortex-a72.
# Does NOT install. Artifact: $BUILD_DIR/surge_xt_products/surge-xt-cli
#
# Revert (Phase B, before install):
#   cp ~/surge-xt-cli.pre-a72 "$SURGE_CLI" && systemctl restart surge-xt-cli
#
# Measurement (Phase B, after install) — confirm harness, three cells @ stock 1800 MHz:
#   Crystals @ 3, Cloud Horn @ 5, Duduk @ 3 — report separately, not averaged.
# Pre-registration: >5% dsp_med headroom gain on any cell = win; <3% = no-effect.

set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

SURGE_COMMIT="${SURGE_COMMIT:-253f8d86}"
SURGE_SRC="${SURGE_SRC:-$HOME/surge-src}"
BUILD_DIR="${BUILD_DIR:-$SURGE_SRC/build-a72}"
LOG="${LOG:-$HOME/surge-build-a72.log}"
JOBS="${JOBS:-$(nproc)}"

log() { echo "$@" | tee -a "$LOG"; }

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        log "ERROR: required command not found: $1 (PATH=$PATH)"
        exit 1
    fi
}

{
    echo "=== build-surge-a72 $(date -Is) attempt=${BUILD_ATTEMPT:-1} ==="
    echo "PROVENANCE commit=${SURGE_COMMIT} flag=-mcpu=cortex-a72 jobs=${JOBS}"
    echo "disk_before: $(df -h / | tail -1)"
    echo "temp_start: $(vcgencmd measure_temp 2>/dev/null || true)"
    echo "throttle_start: $(vcgencmd get_throttled 2>/dev/null || true)"
} >>"$LOG"

require_cmd git
require_cmd cmake
require_cmd make
require_cmd g++

if [ ! -d "$SURGE_SRC/.git" ]; then
    log "Cloning Surge source to ${SURGE_SRC}..."
    git clone https://github.com/surge-synthesizer/surge.git "$SURGE_SRC"
fi

cd "$SURGE_SRC"
git fetch origin --tags
git checkout "$SURGE_COMMIT"
actual="$(git rev-parse --short=8 HEAD)"
if [ "$actual" != "${SURGE_COMMIT:0:8}" ]; then
    log "ERROR: wanted ${SURGE_COMMIT}, got ${actual} ($(git rev-parse HEAD))"
    exit 1
fi
log "Source at $(git describe --tags --always) ($(git rev-parse HEAD))"

mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

if [ ! -f CMakeCache.txt ]; then
    cmake "$SURGE_SRC" \
        -DCMAKE_BUILD_TYPE=Release \
        -DLINUX_ON_ARM=TRUE \
        -DSURGE_BUILD_LV2=FALSE \
        -DSURGE_BUILD_VST3=FALSE \
        -DSURGE_BUILD_CLAP=FALSE \
        -DSURGE_BUILD_STANDALONE=TRUE \
        -DSURGE_BUILD_TESTRUNNER=FALSE \
        -DCMAKE_CXX_FLAGS="-mcpu=cortex-a72" \
        -DCMAKE_C_FLAGS="-mcpu=cortex-a72"
fi

make surge-xt-cli -j"$JOBS"

artifact="$BUILD_DIR/surge_xt_products/surge-xt-cli"
if [ ! -x "$artifact" ]; then
    log "ERROR: missing artifact $artifact"
    exit 1
fi

{
    echo "=== build complete $(date -Is) ==="
    echo "artifact=$artifact"
    echo "version=$("$artifact" --version 2>&1 || true)"
    echo "sha256=$(sha256sum "$artifact" | awk '{print $1}')"
    echo "disk_after: $(df -h / | tail -1)"
    echo "temp_end: $(vcgencmd measure_temp 2>/dev/null || true)"
    echo "throttle_end: $(vcgencmd get_throttled 2>/dev/null || true)"
    echo "SENTINEL build-a72-complete"
} >>"$LOG"
