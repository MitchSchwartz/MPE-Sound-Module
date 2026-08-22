#!/usr/bin/env bash
# audit-storage-rt.sh — READ-ONLY audit of storage, swap, journald and RT scheduler config.
#
# Changes nothing. Safe to run on the appliance at any time EXCEPT inside an open
# measurement window (see memory: no-pi-queries-during-measurement).
#
# Part of Step 0 of docs/measurements/PROMPT-find-the-600us.md.
#
# Usage:
#   ./audit-storage-rt.sh              # single snapshot
#   ./audit-storage-rt.sh --delta 60   # 60s delta on diskstats + swap counters
#
# Motivation: docs/STORAGE-ROBUSTNESS.md records that the appliance runs a full mutable
# Raspberry Pi OS on ext4 with a persistent journal. IRQ 41 carries BOTH the SD card and
# the SDIO WiFi, so every SD write shares an interrupt line with the network. If swap is
# live, a cold page touched inside the audio callback becomes an SD-backed major fault --
# device-independent, load-correlated, and invisible to callback-lateness instrumentation.

set -uo pipefail
DELTA=0
[[ "${1:-}" == "--delta" ]] && DELTA="${2:-60}"

h() { printf '\n=== %s ===\n' "$1"; }

h "swap"
swapon --show 2>/dev/null || echo "(swapon: none)"
free -h 2>/dev/null | sed -n '1p;3p'
echo "dphys-swapfile enabled: $(systemctl is-enabled dphys-swapfile 2>/dev/null || echo n/a)"
echo "dphys-swapfile active:  $(systemctl is-active  dphys-swapfile 2>/dev/null || echo n/a)"
echo "vm.swappiness = $(sysctl -n vm.swappiness 2>/dev/null || echo '?')"

h "swap activity (cumulative since boot)"
# pswpin/pswpout > 0 means swap has actually been used, not merely configured.
grep -E '^(pswpin|pswpout|pgmajfault)' /proc/vmstat 2>/dev/null || echo "(unavailable)"

h "major faults by audio processes"
# maj_flt growing during play is the smoking gun. A one-shot value only tells you
# about startup; use --delta to see whether it is still climbing.
pids=$(pgrep -d, -f 'jackd|surge-xt|sooperlooper' 2>/dev/null)
if [[ -n "$pids" ]]; then ps -o pid,comm,maj_flt,min_flt -p "$pids"; else echo "(audio stack not running)"; fi

h "RT scheduler throttling"
# Default 950000/1000000 throttles RT tasks at 95% of each period. -1 disables.
for k in kernel.sched_rt_runtime_us kernel.sched_rt_period_us; do
    echo "$k = $(sysctl -n "$k" 2>/dev/null || echo '?')"
done

h "journald"
echo "disk usage: $(journalctl --disk-usage 2>/dev/null || echo '?')"
grep -rhs '^[^#]*Storage=' /etc/systemd/journald.conf /etc/systemd/journald.conf.d/ 2>/dev/null \
    || echo "Storage= not set explicitly -> defaults to 'auto' (persistent if /var/log/journal exists)"
[[ -d /var/log/journal ]] && echo "/var/log/journal EXISTS -> journal is PERSISTENT (SD writes)" \
                          || echo "/var/log/journal absent -> journal is volatile (tmpfs)"

h "root filesystem mount options"
findmnt -no SOURCE,FSTYPE,OPTIONS / 2>/dev/null || echo "(findmnt unavailable)"

h "modprobe.d (untracked in repo -- record verbatim)"
grep -rs . /etc/modprobe.d/ 2>/dev/null | grep -v '^\s*#' | grep -v '^\S*:\s*$' || echo "(empty)"

h "snd_usb_audio module parameters"
for f in /sys/module/snd_usb_audio/parameters/*; do
    [[ -e "$f" ]] && echo "$(basename "$f") = $(cat "$f" 2>/dev/null)"
done 2>/dev/null || echo "(module not loaded)"

h "IRQ 41 (mmc0/mmc1 -- SD card AND SDIO WiFi share this line)"
grep -E '^\s*41:' /proc/interrupts 2>/dev/null || echo "(not found)"
echo "effective_affinity: $(cat /proc/irq/41/effective_affinity 2>/dev/null || echo '?')"

if (( DELTA > 0 )); then
    h "SD write delta over ${DELTA}s"
    r() { grep -w mmcblk0 /proc/diskstats | awk '{print $6, $10}'; }   # sectors read, sectors written
    before=$(r); sw_b=$(grep -E '^pswpin|^pswpout' /proc/vmstat | awk '{print $2}' | paste -sd,)
    sleep "$DELTA"
    after=$(r);  sw_a=$(grep -E '^pswpin|^pswpout' /proc/vmstat | awk '{print $2}' | paste -sd,)
    echo "sectors (read written) before: $before"
    echo "sectors (read written) after:  $after"
    awk -v b="$before" -v a="$after" -v d="$DELTA" \
        'BEGIN{split(b,B," ");split(a,A," ");
         printf "delta: read %d sectors (%.1f KB/s), written %d sectors (%.1f KB/s)\n",
            A[1]-B[1], (A[1]-B[1])*512/1024/d, A[2]-B[2], (A[2]-B[2])*512/1024/d}'
    echo "pswpin,pswpout before: $sw_b"
    echo "pswpin,pswpout after:  $sw_a   <-- any increase means swap is live in this window"
fi

h "verdict inputs"
cat <<'TXT'
Decide on these numbers, not on whether swap is merely configured:
  * pswpin/pswpout increasing, or audio-process maj_flt climbing during play
        -> swap is live in the audio path. Escalate above Steps 1-2.
  * both flat and swap off
        -> ruled out. Record it and drop it from the list.
Run once idle and once with a stream up (in its own window, NOT inside a counted one).
TXT
