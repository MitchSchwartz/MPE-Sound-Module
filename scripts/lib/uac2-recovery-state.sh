#!/bin/bash
# Shared USB-host recovery state for stall watchdog + touch UI.

MPE_UAC2_RECOVERY_STATE="${MPE_UAC2_RECOVERY_STATE:-/tmp/mpe-uac2-recovery.state}"

uac2_recovery_set() {
    local state="${1:?state required}"
    printf '%s %s\n' "$state" "$(date +%s)" >"$MPE_UAC2_RECOVERY_STATE"
    chmod 0644 "$MPE_UAC2_RECOVERY_STATE" 2>/dev/null || true
}

uac2_recovery_clear() {
    rm -f "$MPE_UAC2_RECOVERY_STATE"
}
