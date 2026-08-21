#!/usr/bin/env bash
# T5-pre acceptance: prove stale-meter alarms fork-free (no jack_lsp).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WATCHDOG="${REPO}/scripts/sooperlooper/sl-watchdog.py"
OUT="${1:-/tmp/t5-pre-demo.log}"

: >"$OUT"

_log() { echo "$*" | tee -a "$OUT"; }

_count_jack_lsp() {
    pgrep -x jack_lsp 2>/dev/null | wc -l | tr -d ' '
}

_run_once() {
    local label="$1"
    _log ""
    _log "=== ${label} ==="
    local before after
    before="$(_count_jack_lsp)"
    sudo -u mitch python3 "$WATCHDOG" --once --skip-source-check 2>&1 | tee -a "$OUT" || true
    sleep 0.5
    after="$(_count_jack_lsp)"
    _log "jack_lsp processes before=${before} after=${after}"
    if [ "$before" != "$after" ] || [ "$after" != "0" ]; then
        _log "FAIL: jack_lsp spawned (before=${before} after=${after})"
        return 1
    fi
    _log "PASS: no jack_lsp spawned"
}

_log "T5-pre demo $(date -Is)"
_log "commit: $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"

# Case 1: meter stopped, jackd running
sudo systemctl stop mpe-peak-meter.service
sleep 2
if ! pgrep -x jackd >/dev/null; then
    _log "ERROR: jackd not running — start audio stack first" >&2
    exit 1
fi
_run_once "meter stopped, jackd running" || exit 1
grep -q "meter fault" "$OUT" || grep -q "peak-meter stale" "$OUT" || {
    _log "FAIL: expected meter-fault alarm text"
    exit 1
}

# Case 2: meter stopped, jackd stopped
sudo systemctl stop mpe-jackd.service
sleep 2
_run_once "meter stopped, jackd stopped" || exit 1
grep -q "JACK down" "$OUT" || {
    _log "FAIL: expected JACK-down alarm text"
    exit 1
}

# Restore
sudo systemctl start mpe-jackd.service
sleep 6
sudo systemctl start mpe-peak-meter.service
sleep 2
_log ""
_log "T5-pre demo complete → ${OUT}"
