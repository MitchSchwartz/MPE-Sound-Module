#!/bin/bash
# Pin the CPU frequency governor (latency experiment support — see docs/LATENCY-SPIKE.md).
#
# No-op unless MPE_CPU_GOVERNOR is set in /etc/mpe/mpe.env, so existing
# appliances keep the distro default until the change is opted into.

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

TARGET="${MPE_CPU_GOVERNOR:-}"

if [ -z "$TARGET" ]; then
    echo "MPE_CPU_GOVERNOR unset — leaving governor at distro default"
    exit 0
fi

CPU0_DIR=/sys/devices/system/cpu/cpu0/cpufreq
if [ ! -d "$CPU0_DIR" ]; then
    echo "WARNING: no cpufreq support on this board — skipping" >&2
    exit 0
fi

AVAILABLE="$(cat "$CPU0_DIR/scaling_available_governors" 2>/dev/null)"
case " $AVAILABLE " in
    *" $TARGET "*) ;;
    *)
        echo "ERROR: governor '$TARGET' not available. Options: $AVAILABLE" >&2
        exit 1
        ;;
esac

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: must run as root (systemd unit does; use sudo for manual runs)" >&2
    exit 1
fi

_failed=0
for _gov in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor; do
    [ -w "$_gov" ] || continue
    if ! echo "$TARGET" > "$_gov" 2>/dev/null; then
        echo "WARNING: could not write $_gov" >&2
        _failed=1
    fi
done

echo "CPU governor: $(cat "$CPU0_DIR/scaling_governor" 2>/dev/null) (requested $TARGET)"
exit "$_failed"
