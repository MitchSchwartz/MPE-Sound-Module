#!/bin/bash
# Pi clock + throttle stamp for measurement windows (P7+).
# Source from measure scripts — do not poll during a window.
#
# Usage:
#   source scripts/lib/clock-stamp.sh
#   clock_stamp before-run1 1800   # validates ~1800 MHz + throttled=0x0
#   clock_stamp after-run1 0       # 0 = throttle check only

clock_stamp() {
    local label="${1:?label required}"
    local expect_arm_mhz="${2:-0}"

    local arm_raw arm_hz arm_mhz scaling scaling_mhz thr temp
    arm_raw="$(vcgencmd measure_clock arm 2>/dev/null || echo 'frequency(48)=0')"
    arm_hz="$(printf '%s' "$arm_raw" | sed -n 's/.*=\([0-9]*\).*/\1/p')"
    arm_hz="${arm_hz:-0}"
    arm_mhz=$((arm_hz / 1000000))
    scaling="$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null || echo 0)"
    scaling_mhz=$((scaling / 1000))
    thr="$(vcgencmd get_throttled 2>/dev/null || echo 'throttled=?')"
    temp="$(vcgencmd measure_temp 2>/dev/null || echo 'temp=?')"

    echo "CLOCK label=${label} measure_clock_arm=${arm_raw} arm_mhz=${arm_mhz} scaling_cur_khz=${scaling} scaling_mhz=${scaling_mhz} ${thr} ${temp}"

    if [ "$thr" != "throttled=0x0" ]; then
        echo "ERROR: throttling non-zero at ${label}: ${thr} — run INVALID" >&2
        return 1
    fi

    if [ "$expect_arm_mhz" -gt 0 ]; then
        local lo=$((expect_arm_mhz - 80))
        local hi=$((expect_arm_mhz + 80))
        if [ "$arm_mhz" -lt "$lo" ] || [ "$arm_mhz" -gt "$hi" ]; then
            echo "ERROR: achieved arm_mhz=${arm_mhz} outside ~${expect_arm_mhz} at ${label}" >&2
            return 1
        fi
    fi
    return 0
}

clock_assert_idle() {
    if pgrep -f 'measure-soak-instrument|measure-latency-run|midi-load-hold\.py' >/dev/null 2>&1; then
        echo "ERROR: measurement process still running — Pi not idle" >&2
        pgrep -af 'measure-soak-instrument|measure-latency-run|midi-load-hold' >&2 || true
        return 1
    fi
    return 0
}
