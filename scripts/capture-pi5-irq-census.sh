#!/usr/bin/env bash
# Pi 5 IRQ census capture — Phase 0 of docs/measurements/PI5-IRQ-INVESTIGATION-PLAN.md
# Run ON the Pi. Read-only except writing output dir under repo or $OUT_DIR.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATE_TAG="$(date +%Y-%m-%d)"
OUT="${OUT_DIR:-$REPO_ROOT/appliance-state/pi5-irq-census-$DATE_TAG}"

mkdir -p "$OUT"

{
    echo "# Pi 5 IRQ census snapshot"
    echo "Captured: $(date -Is)"
    echo "Hostname: $(hostname)"
    echo ""
} >"$OUT/README.md"

cat /proc/interrupts >"$OUT/interrupts-idle.txt"
cat /proc/softirqs >"$OUT/softirqs-idle.txt"
lsusb -t >"$OUT/lsusb-t.txt" 2>&1 || true
cp /boot/firmware/cmdline.txt "$OUT/cmdline.txt" 2>/dev/null || cp /boot/cmdline.txt "$OUT/cmdline.txt"
cp /boot/firmware/config.txt "$OUT/config.txt" 2>/dev/null || true

{
    echo "=== IRQ affinity (key lines) ==="
    for irq in 106 131 136 148 161 162; do
        echo "--- IRQ $irq ---"
        grep -E "^[[:space:]]*${irq}:" /proc/interrupts || true
        for f in effective_affinity_list smp_affinity_list; do
            p="/proc/irq/$irq/$f"
            if [ -f "$p" ]; then
                echo "$f=$(cat "$p") writable=$([ -w "$p" ] && echo yes || echo no)"
            fi
        done
    done
    echo ""
    echo "=== unit CPUAffinity ==="
    for u in mpe-jackd surge-xt-cli mpe-sooperlooper surge-poly-governor mpe-peak-meter touch-patch-browser; do
        printf '%s: ' "$u"
        systemctl show "$u" -p CPUAffinity --value 2>/dev/null || echo n/a
    done
    echo ""
    echo "=== services (hygiene) ==="
    for u in bluetooth avahi-daemon cron NetworkManager; do
        printf '%s: ' "$u"
        systemctl is-enabled "$u" 2>/dev/null || echo n/a
    done
    if command -v iw >/dev/null 2>&1; then
        echo ""
        iw dev wlan0 get power_save 2>/dev/null || true
    fi
} >"$OUT/summary.txt"

cat "$OUT/summary.txt" >>"$OUT/README.md"
echo "" >>"$OUT/README.md"
echo "Files: interrupts-idle.txt, softirqs-idle.txt, lsusb-t.txt, cmdline.txt, config.txt, summary.txt" >>"$OUT/README.md"

echo "Wrote $OUT"
echo "For loaded capture: run Surge load, then:"
echo "  cp /proc/interrupts $OUT/interrupts-loaded.txt"
echo "  cp /proc/softirqs $OUT/softirqs-loaded.txt"
