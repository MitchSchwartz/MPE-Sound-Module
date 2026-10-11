#!/usr/bin/env bash
# Give the trunk back to Surge after mpe-fx-host stops. Idempotent; never fails.
#
# mpe-fx-host moves Surge's trunk edges (playback, every loop input) onto its
# own output while it carries audio, and moves them back on SIGTERM. A
# segfault, an OOM kill or SIGKILL cannot run that code, and what it leaves is
# an instrument that is silent AND records silence. So the unit runs this as
# ExecStopPost, which systemd runs however the process died.
#
# It knows no destinations of its own. The client writes each edge to
# fx-host.state BEFORE taking it, and this replays exactly that list:
# `edge=<channel> <destination port>` -> connect Surge:out_<channel> to it.
# Undoing what was recorded, rather than re-deriving the graph here, keeps this
# from becoming another place that decides routing (issue #142).
#
# Exits 0 always, as restore-direct-monitor-path.sh does: this runs while
# something else is already going wrong.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

SURGE_CLIENT="${MPE_SL_SURGE_CLIENT:-Surge XT}"
RUN_DIR="$(mpe_run_dir)"
STATE_FILE="${RUN_DIR}/fx-host.state"

if [ ! -f "$STATE_FILE" ]; then
    echo "restore-fx-trunk: no ${STATE_FILE} — nothing was taken"
    exit 0
fi
if ! command -v jack_connect >/dev/null 2>&1; then
    echo "restore-fx-trunk: jack_connect not found — cannot restore" >&2
    exit 0
fi

failures=0
restored=0
remaining=()
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        edge=*) ;;
        *) continue ;;
    esac
    rest="${line#edge=}"
    ch="${rest%% *}"
    dest="${rest#* }"
    case "$ch" in
        '' | *[!0-9]*) continue ;;
    esac
    if [ -z "$dest" ] || [ "$dest" = "$rest" ]; then
        continue
    fi
    out="$(jack_connect "${SURGE_CLIENT}:out_${ch}" "$dest" 2>&1)" && rc=0 || rc=$?
    case "$out" in
        *"already connected"*) rc=0 ;;
    esac
    if [ "$rc" -eq 0 ]; then
        restored=$((restored + 1))
        echo "restore-fx-trunk: ${SURGE_CLIENT}:out_${ch} -> ${dest}"
    else
        failures=$((failures + 1))
        remaining+=("edge=${ch} ${dest}")
        echo "restore-fx-trunk: FAILED ${SURGE_CLIENT}:out_${ch} -> ${dest}: ${out:-exit $rc}" >&2
    fi
done < "$STATE_FILE"

# Rewrite the claim on the dead process's behalf: nothing is carried any more,
# and only the edges that could not be restored are still owed. A file that
# kept saying carrying=1 would be an alarm that never clears.
tmp="${STATE_FILE}.tmp.$$"
{
    echo "carrying=0"
    echo "taken=${#remaining[@]}"
    for edge in "${remaining[@]}"; do
        echo "$edge"
    done
    echo "restored_by=restore-fx-trunk"
    echo "updated=$(date +%s)"
} > "$tmp" 2>/dev/null && mv -f "$tmp" "$STATE_FILE" 2>/dev/null
rm -f "$tmp" 2>/dev/null || true

echo "restore-fx-trunk: ${restored} edge(s) restored, ${failures} failed"
exit 0
