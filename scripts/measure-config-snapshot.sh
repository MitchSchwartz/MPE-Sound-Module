#!/bin/bash
# Capture appliance config for I3 baseline diff. Read-only except stdout.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"

echo "=== measure-config-snapshot $(date -Is) ==="
if [ -d "$MPE_MODULE_REPO/.git" ]; then
    echo "repo: $(git -C "$MPE_MODULE_REPO" log --oneline -1 2>/dev/null || echo unknown)"
fi
echo "kernel: $(uname -r)"
echo "cmdline: $(tr '\0' ' ' < /proc/cmdline 2>/dev/null || true)"
echo

echo "--- /etc/mpe/mpe.env (audio-relevant) ---"
for key in MPE_PEAK_METER MPE_JACK_SOFTMODE MPE_SURGE_BUFFER_SIZE MPE_JACK_BUFFER MPE_JACK_PERIODS MPE_SL_LOOPS; do
    v="$(mpe_read_appliance_env_var "$key" 2>/dev/null || true)"
    echo "${key}=${v:-<unset>}"
done
echo

echo "--- systemd CPUAffinity ---"
for u in mpe-jackd.service surge-xt-cli.service mpe-sooperlooper.service; do
    echo -n "$u: "
    systemctl show "$u" -p CPUAffinity --value 2>/dev/null || echo n/a
done
echo

echo "--- jackd ---"
if pid="$(pgrep -x jackd | head -1)"; then
    echo "pid=$pid taskset=$(taskset -cp "$pid" 2>/dev/null | sed 's/.*: //')"
    tr '\0' '\n' < "/proc/$pid/cmdline" | paste -sd' '
    echo
else
    echo "jackd: not running"
fi
echo

echo "--- governor (cpu0) ---"
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo n/a
echo

echo "--- peak meter ---"
systemctl is-active mpe-peak-meter.service 2>/dev/null || true
if [ -r /run/mpe/meter.state ]; then
    echo "meter.state size=$(wc -c </run/mpe/meter.state)"
else
    echo "meter.state: missing"
fi
