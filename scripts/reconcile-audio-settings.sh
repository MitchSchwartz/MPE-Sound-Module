#!/bin/bash
# ExecStartPre for mpe-jackd.service — undo a settings change that never finished.
#
# Runs before every graph start, which is the one moment that always follows both
# failure modes it exists for: the SIGKILL mid-change, and the reboot into an
# untested value. Never fails the unit: leaving the appliance without an audio
# graph would be a worse outcome than the setting it is trying to fix.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/audio-settings-pending.sh
source "$SCRIPT_DIR/lib/audio-settings-pending.sh"

case "$(mpe_pending_status)" in
    none)
        exit 0
        ;;
    inflight)
        echo "reconcile-audio-settings: settings change in flight — leaving it alone"
        exit 0
        ;;
    stale)
        echo "reconcile-audio-settings: WARNING a settings change did not complete —" \
             "restoring the last values known to start the graph"
        mpe_pending_reconcile || true
        exit 0
        ;;
esac
exit 0
