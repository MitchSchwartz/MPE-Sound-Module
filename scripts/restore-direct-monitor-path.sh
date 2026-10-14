#!/usr/bin/env bash
# Put the fan-out source back wherever nothing reaches. Idempotent, never fails.
#
# This is the fail-open path that mpe-live-monitor takes away while it is
# carrying audio. The client restores it itself on SIGTERM, but a segfault, an
# OOM kill or `systemctl kill -s KILL` cannot run that code — and what is left
# behind is an instrument that is silent with nothing in any log. So the unit
# runs this as ExecStopPost, which systemd runs however the process died.
#
# Exits 0 always, deliberately: this runs while something else is already going
# wrong, and a non-zero exit here would only add a failed unit to the picture.
# It is also safe to run when the monitor is healthy — jack_connect on an
# existing connection is a no-op, and a healthy client re-detaches within its
# next wiring pass (2 s). That second half was false for one revision, when the
# client would only ever detach once; if you are reading this after changing
# `ensure_wiring`, the guarantee this sentence makes is the one you have to
# keep, because it is what stops a repair here from leaving two copies of the
# live signal behind.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

# Every rule about where Surge's output goes lives in the routing authority.
ROUTING="$SCRIPT_DIR/../patch_browser/audio_routing.py"

# The same answer the client's own writer gets. This runs as ExecStopPost, which
# does not inherit what the start script exported, so resolving it here rather
# than defaulting to /run/mpe is what keeps writer, eraser and reader pointed at
# one file when MPE_RUN_DIR is set or /run/mpe is not writable.
RUN_DIR="$(mpe_run_dir)"

if ! command -v jack_connect >/dev/null 2>&1; then
    echo "restore-direct-monitor-path: jack_connect not found — cannot restore" >&2
    exit 0
fi

# Nothing to restore onto. Since 2026-10-11 the inserts exit non-zero when JACK
# shuts down, so this runs on every Restart=on-failure cycle while jackd is
# coming back; skipping the python3 fork here keeps that cycle cheap.
if ! mpe_jack_server_running; then
    echo "restore-direct-monitor-path: jackd not running — nothing to restore onto"
    exit 0
fi

# Put the fan-out source back on every destination nothing from Surge reaches:
# playback (unless the live monitor is still carrying it) and, when the looper
# is up, every loop input. The source is Surge, or the FX host while it is on
# the trunk -- the authority decides, from the graph as it stands. It never
# proposes a destination that is already reached, so this cannot leave two
# copies of the live signal behind, and a failed `jack_lsp` is answered
# fail-open (everything direct). That makes this safe as ExecStopPost for
# either insert and as the watchdog's repair.
failures=0
monitor_carrying=0
plan="$({ mpe_jack_lsp -c 2>/dev/null || true; } \
        | python3 "$ROUTING" plan --report-monitor)" && rc=0 || rc=$?
if [ "$rc" -ne 0 ]; then
    failures=$((failures + 1))
    echo "restore-direct-monitor-path: FAILED routing authority exited $rc" >&2
    plan=""
fi
while IFS=$'\t' read -r src dst; do
    case "$src" in
        '#monitor_carrying='*) monitor_carrying="${src#*=}"; continue ;;
        '') continue ;;
    esac
    out="$(jack_connect "$src" "$dst" 2>&1)" && rc=0 || rc=$?
    case "$out" in
        *"already connected"*) rc=0 ;;
    esac
    if [ "$rc" -eq 0 ]; then
        echo "restore-direct-monitor-path: $src -> $dst"
    else
        failures=$((failures + 1))
        echo "restore-direct-monitor-path: FAILED $src -> $dst: ${out:-exit $rc}" >&2
    fi
done <<< "$plan"

# The claim this file made — "the direct path is detached" — is no longer true,
# and the process that wrote it may be gone and unable to say so. So say it
# here, on its behalf.
#
# Only when the live monitor is NOT carrying: then the playback leg is direct
# (just restored, or already there). When it IS carrying -- this ran as the FX
# host's ExecStopPost, or as a repair beneath a healthy monitor -- the monitor
# is alive, owns this file, and detached=0 would be a lie about it.
#
# Rewritten rather than deleted, which was the first version and was wrong in a
# way nothing executed: `wait_for_live_path()` decides whether this repair took
# by reading this same file, so deleting it made every genuine repair report
# "repair did not take" — and the deletion doubled as the only evidence a
# spurious repair had happened. A file saying detached=0 answers both questions
# honestly. Written only on full success, because a half-restored graph is
# still a problem the watchdog should keep seeing.
STATE_FILE="${RUN_DIR}/live-monitor.state"
if [ "$failures" -eq 0 ] && [ "$monitor_carrying" != 1 ] \
        && [ -d "$(dirname "$STATE_FILE")" ]; then
    tmp="${STATE_FILE}.tmp.$$"
    {
        echo "carrying=0"
        echo "detached=0"
        echo "restored_by=restore-direct-monitor-path"
        echo "updated=$(date +%s)"
    } > "$tmp" 2>/dev/null &&
        mv -f "$tmp" "$STATE_FILE" 2>/dev/null &&
        echo "restore-direct-monitor-path: ${STATE_FILE} now says detached=0"
    rm -f "$tmp" 2>/dev/null || true
fi

exit 0
