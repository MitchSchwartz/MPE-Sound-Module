#!/bin/bash
# Print stop timeline from the previous boot (journal + shutdown splash log).
# Usage: ./scripts/shutdown-analyze-last.sh

set -euo pipefail

SPLASH_LOG="${MPE_SHUTDOWN_SPLASH_LOG:-/tmp/mpe-shutdown-splash.log}"

echo "=== Previous boot: systemd stop phase (reverse) ==="
journalctl -b -1 -o short-precise --no-pager \
    -u surge-xt-cli.service \
    -u touch-patch-browser.service \
    -u mpe-shutdown-splash.service \
    -u usb-audio-gadget.service \
    -u surge-watchdog.service \
    -u foot-pedal.service \
    2>/dev/null | grep -E 'Stopping|Stopped|Deactivated|Killing|timeout|SIGTERM|SIGKILL' || \
    echo "(no matching journal lines — first boot or logs rotated)"

echo ""
echo "=== Previous boot: shutdown target ==="
journalctl -b -1 -o short-precise --no-pager \
    -u shutdown.target -u halt.target -u reboot.target \
    2>/dev/null | tail -20 || true

echo ""
echo "=== Shutdown splash log (last 40 lines) ==="
if [ -f "$SPLASH_LOG" ]; then
    tail -40 "$SPLASH_LOG"
else
    echo "(no $SPLASH_LOG)"
fi

echo ""
echo "=== Hint ==="
echo "Compare Stopping/Stopped timestamps; large gaps = slow unit teardown."
echo "Re-run after a shutdown test: sudo systemctl poweroff (or UI power menu)."
