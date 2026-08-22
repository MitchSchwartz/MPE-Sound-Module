#!/bin/bash
# Unattended P8 build watch: install missing deps, retry on failure, stop on success.
# Hardware abort: throttling flags under-voltage or active throttle (not 0x0).
# Does NOT install binary, reboot, or edit config.txt.

set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

REPO="${MPE_MODULE_REPO:-$HOME/MPE-Module}"
LOG="${LOG:-$HOME/surge-build-a72.log}"
WATCH_LOG="${WATCH_LOG:-$HOME/surge-build-a72-watch.log}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-4}"

log() { echo "$(date -Is) $*" | tee -a "$WATCH_LOG"; }

abort_if_hardware_risk() {
    local throttled temp hex cur
    throttled="$(vcgencmd get_throttled 2>/dev/null || echo throttled=unknown)"
    temp="$(vcgencmd measure_temp 2>/dev/null || echo temp=unknown)"
    hex="${throttled#throttled=0x}"
    cur=$((16#${hex:-0}))
    # Low nibble = currently undervoltage / capped / throttled / soft-limit
    if (( (cur & 0xF) != 0 )); then
        log "ABORT hardware risk (active): $throttled $temp — not retrying"
        exit 2
    fi
}

ensure_cmake() {
    if command -v cmake >/dev/null 2>&1; then
        return 0
    fi
    log "Installing cmake (missing — prior build failed here)"
    sudo apt-get update -qq
    sudo apt-get install -y cmake
}

ensure_cmake

if grep -q "SENTINEL build-a72-complete" "$LOG" 2>/dev/null; then
    log "Already complete — nothing to do"
    exit 0
fi

attempt=1
while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
    abort_if_hardware_risk
    avail_g="$(df -BG / | awk 'NR==2 {gsub(/G/,"",$4); print $4}')"
    if [ "${avail_g:-0}" -lt 5 ] 2>/dev/null; then
        log "ABORT disk: only ${avail_g}G free on /"
        exit 3
    fi

    jobs="${JOBS:-$(nproc)}"
    if [ "$attempt" -ge 3 ]; then
        jobs=2
        log "Attempt $attempt: reducing parallel jobs to 2 (prior failures)"
    fi

    log "Attempt $attempt/$MAX_ATTEMPTS starting (jobs=$jobs)"
    if BUILD_ATTEMPT="$attempt" JOBS="$jobs" "$REPO/scripts/build-surge-a72.sh"; then
        if grep -q "SENTINEL build-a72-complete" "$LOG"; then
            log "SUCCESS"
            tail -8 "$LOG" | tee -a "$WATCH_LOG"
            exit 0
        fi
    fi

    if grep -q "SENTINEL build-a72-complete" "$LOG" 2>/dev/null; then
        log "SUCCESS (sentinel in log)"
        exit 0
    fi

    last_err="$(grep "^ERROR:" "$LOG" 2>/dev/null | tail -1 || true)"
    log "Attempt $attempt failed${last_err:+ — $last_err}"

    if grep -q "required command not found: cmake" "$LOG" 2>/dev/null; then
        ensure_cmake
    fi

    # CMake missing libs — one-shot install of Surge ARM build deps
    if grep -qiE "Could NOT find|Package .* required|missing dependency" "$LOG" 2>/dev/null; then
        log "Installing Surge build dependencies (cmake reported missing libs)"
        sudo apt-get install -y \
            libcairo2-dev libxkbcommon-x11-dev libxkbcommon-dev \
            libxcb-cursor-dev libxcb-keysyms1-dev libxcb-util-dev \
            libxrandr-dev libxinerama-dev libxcursor-dev \
            libasound2-dev libjack-jackd2-dev libfreetype6-dev libglu1-mesa-dev \
            2>&1 | tee -a "$WATCH_LOG" || true
        rm -f "$HOME/surge-src/build-a72/CMakeCache.txt"
    fi

    attempt=$((attempt + 1))
    sleep 30
done

log "FAILED after $MAX_ATTEMPTS attempts"
exit 1
