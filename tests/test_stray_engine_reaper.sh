#!/bin/bash
# Stray engine reaper (audio-engine.sh) — real processes, real cgroup checks.
#
# Regression guard for 2026-08-26: a hand-started SooperLooper in an SSH
# session's cgroup survived every `systemctl restart`, so a DAC replug produced
# a second engine beside it. Both claimed the JACK client name; the stale one
# stalled the graph and all audio stopped.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=../scripts/lib/audio-engine.sh
source "$ROOT/scripts/lib/audio-engine.sh"

FAILED=0
ok()   { echo "  ok   - $1"; }
fail() { echo "  FAIL - $1" >&2; FAILED=1; }
check() { if [ "$1" = "$2" ]; then ok "$3"; else fail "$3 (got='$1' want='$2')"; fi; }

# A stand-in for the engine: a COPY of bash named `fake-engine`, so the kernel
# reports comm="fake-engine" and `pgrep -x` matches the same way it matches
# `sooperlooper` in production.
#
# Not a symlink to `sleep` (coreutils is multi-call and refuses to run under
# another name — which starts no process and makes every kill assertion pass
# vacuously), and not a #!/bin/bash script (comm would be "bash", so pgrep -x
# would never see it).
TMP="$(mktemp -d)"
trap 'pkill -x fake-engine 2>/dev/null; rm -rf "$TMP"' EXIT
cp "$(command -v bash)" "$TMP/fake-engine"

# Redirect the child's stdio. A backgrounded process that inherits the
# command substitution's stdout holds that pipe open, so `$(start_fake)`
# blocks until the child exits — which is never. Same trap that
# restart-sooperlooper.sh documents for the SSH channel.
start_fake() {
    "$TMP/fake-engine" -c 'while :; do sleep 1; done' </dev/null >/dev/null 2>&1 &
    echo $!
}

# This test process is not in any mpe unit's cgroup, so every fake it spawns is
# a "stray" by definition — which is exactly the shape being tested.
UNIT="mpe-sooperlooper.service"

echo "test_stray_engine_reaper.sh"

# --- nothing running -------------------------------------------------------
check "$(mpe_stray_engine_pids fake-engine "$UNIT")" "" "no processes -> no strays"
mpe_reap_stray_engines fake-engine "$UNIT" "test" >/dev/null 2>&1
check "$?" "0" "reaping nothing succeeds"

# --- a stray is found and killed -------------------------------------------
PID1="$(start_fake)"
sleep 0.3
check "$(mpe_stray_engine_pids fake-engine "$UNIT")" "$PID1" "stray outside the unit cgroup is found"

mpe_reap_stray_engines fake-engine "$UNIT" "test" >/dev/null 2>&1
check "$?" "0" "reap reports success"
sleep 0.3
if kill -0 "$PID1" 2>/dev/null; then fail "stray was killed"; else ok "stray was killed"; fi

# --- several at once -------------------------------------------------------
P1="$(start_fake)"; P2="$(start_fake)"; P3="$(start_fake)"
sleep 0.3
check "$(mpe_stray_engine_pids fake-engine "$UNIT" | wc -l)" "3" "all strays enumerated"
mpe_reap_stray_engines fake-engine "$UNIT" "test" >/dev/null 2>&1
sleep 0.3
check "$(mpe_stray_engine_pids fake-engine "$UNIT")" "" "all strays reaped"
for p in "$P1" "$P2" "$P3"; do
    if kill -0 "$p" 2>/dev/null; then fail "pid $p killed"; fi
done
ok "no survivors"

# --- kill switch -----------------------------------------------------------
# Bench work deliberately runs an engine outside systemd; it must be able to opt
# out rather than fight the reaper.
PID2="$(start_fake)"
sleep 0.3
MPE_REAP_STRAY=0 mpe_reap_stray_engines fake-engine "$UNIT" "test" >/dev/null 2>&1
check "$?" "0" "MPE_REAP_STRAY=0 returns success"
if kill -0 "$PID2" 2>/dev/null; then ok "MPE_REAP_STRAY=0 leaves the process alone"; else fail "MPE_REAP_STRAY=0 leaves the process alone"; fi
kill "$PID2" 2>/dev/null

# --- never reaps a process the unit owns ------------------------------------
# The real guard: membership decides, not name or user. Passing this process's
# OWN cgroup as the unit must make its children non-strays.
OWN_CGROUP="$(awk -F/ '{print $NF}' /proc/$$/cgroup | tail -1)"
if [ -n "$OWN_CGROUP" ]; then
    PID3="$(start_fake)"
    sleep 0.3
    check "$(mpe_stray_engine_pids fake-engine "$OWN_CGROUP")" "" \
        "process inside the named cgroup is NOT a stray"
    kill "$PID3" 2>/dev/null
else
    echo "  skip - cgroup path unavailable"
fi

# --- bad arguments are inert ------------------------------------------------
check "$(mpe_stray_engine_pids "" "$UNIT")" "" "empty process name -> no strays"
check "$(mpe_stray_engine_pids fake-engine "")" "" "empty unit -> no strays (never mass-kill)"

if [ "$FAILED" -ne 0 ]; then
    echo "FAILED test_stray_engine_reaper.sh" >&2
    exit 1
fi
echo "OK test_stray_engine_reaper.sh"
