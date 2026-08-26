#!/bin/bash
# Restart the whole appliance stack, in dependency order, without a reboot.
#
# The "get me out of trouble" action (#112). Deliberately COARSE: one sequence,
# no per-subsystem granularity. A single action you can trust while something is
# broken beats a menu you have to reason about under stress. The narrow case
# already ships separately as the `surge_restart` settings row.
#
# Runs as its own unit (mpe-restart-bench.service) rather than inside the touch
# browser, because step 8 restarts the browser — a sequence hosted there would
# kill itself before finishing. The browser fires this and returns immediately.
#
# Usage:
#   restart-bench.sh              # run the sequence
#   restart-bench.sh --explain    # print the plan and exit, touching nothing
#
# Result is written incrementally to /run/mpe/restart-bench.result — never only
# at the end, so a crash mid-sequence still leaves evidence of how far it got.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

EXPLAIN=false
[ "${1:-}" = "--explain" ] && EXPLAIN=true

RUN_DIR="$(mpe_run_dir)"
RESULT_FILE="${MPE_RESTART_BENCH_RESULT:-$RUN_DIR/restart-bench.result}"
LOCK_DIR="${MPE_RESTART_BENCH_LOCK:-$RUN_DIR/restart-bench.lock}"
UNIT_TIMEOUT_S="${MPE_RESTART_BENCH_UNIT_TIMEOUT_S:-20}"

# Ordered from the live unit graph (systemctl show -p After/Requires/Wants).
# "gate" is the readiness predicate that must pass before moving on — never a
# fixed sleep, which is how boot-ordering bugs get baked in as timing luck.
#
#   mpe-pressure-remap  no deps; restart first so stale ALSA MIDI handles are
#                       gone before Surge reopens Midi Through
#   mpe-jackd           owns the hardware
#   surge-xt-cli        the synth; gate on it actually reaching the JACK graph
#   ...clients of the above...
#   touch-patch-browser LAST — it restores the last patch on startup, so this
#                       doubles as the "reload what I had" step
#
# MPE_RESTART_BENCH_UNITS overrides the list, space-separated "unit:gate" pairs.
# TEST ONLY — it exists because the failure paths are otherwise unreachable
# without breaking a real unit on a live appliance, and an untested failure path
# in a recovery tool is the thing most likely to be wrong when it finally runs.
UNITS=(
    "mpe-pressure-remap:active"
    "mpe-jackd:jack"
    "surge-xt-cli:surge"
    "mpe-sooperlooper:active"
    "mpe-peak-meter:active"
    "surge-poly-governor:active"
    "surge-watchdog:active"
    "touch-patch-browser:active"
)

if [ -n "${MPE_RESTART_BENCH_UNITS:-}" ]; then
    # shellcheck disable=SC2206  # deliberate word-splitting: space-separated pairs
    UNITS=(${MPE_RESTART_BENCH_UNITS})
fi

log() { echo "restart-bench: $*"; }

if [ "$EXPLAIN" = true ]; then
    echo "restart-bench: would restart, in order:"
    n=0
    for entry in "${UNITS[@]}"; do
        n=$((n + 1))
        printf '  %d. %-22s gate=%s\n' "$n" "${entry%%:*}" "${entry##*:}"
    done
    echo "restart-bench: pre-step — reap sooperlooper processes outside mpe-sooperlooper.service"
    echo "restart-bench: result file -> $RESULT_FILE"
    echo "restart-bench: no action taken (--explain)"
    exit 0
fi

mkdir -p "$RUN_DIR" 2>/dev/null || true

# Re-entrancy guard. mkdir is atomic, so a second tap while running loses the
# race and exits successfully — a no-op, not a second sequence, and not an error
# the user has to interpret.
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    log "already running (lock held) — nothing to do"
    exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

: >"$RESULT_FILE"
printf 'started=%s\n' "$EPOCHSECONDS" >>"$RESULT_FILE"

# Tell the UI something is happening, so it is not blank while the stack cycles.
mpe_engine_graph_recovery_begin "restart-bench" || true

# Wait for a unit's readiness predicate. Bounded; returns non-zero on timeout so
# the caller records a failure rather than silently continuing into a stack that
# is not actually up.
wait_gate() {
    local gate="$1" waited=0
    case "$gate" in
        jack)
            mpe_wait_for_jack_server >/dev/null 2>&1
            return $?
            ;;
        surge)
            while [ "$waited" -lt "$UNIT_TIMEOUT_S" ]; do
                mpe_surge_on_jack_graph >/dev/null 2>&1 && return 0
                sleep 1
                waited=$((waited + 1))
            done
            return 1
            ;;
        active | *)
            return 0
            ;;
    esac
}

# Reap before restarting anything. An engine outside systemd's cgroups survives
# every unit restart in the loop below, so without this the one action built to
# unwedge the stack cannot fix the wedge that actually happened on 2026-08-26:
# a hand-started SooperLooper holding the `mpe-looper` JACK client name and
# stalling the graph. Recorded in the result file either way — a reap that
# happened is the single most useful line for explaining what was wrong.
overall=ok

# Distinguish "nothing to reap" from "reaped something": they mean opposite
# things to whoever reads this file afterwards. `none` is a healthy stack;
# `reaped=N` names the wedge that was just cleared.
strays="$(mpe_stray_engine_pids sooperlooper "mpe-sooperlooper.service")"
if [ -z "$strays" ]; then
    reaped=none
elif mpe_reap_stray_engines sooperlooper "mpe-sooperlooper.service" "restart-bench"; then
    reaped="reaped-$(echo $strays | wc -w)"
    log "cleared $reaped stray sooperlooper process(es)"
else
    reaped=failed
    overall=partial
    log "WARNING stray sooperlooper survived reap (continuing)"
fi
printf 'reap.sooperlooper=%s\n' "$reaped" >>"$RESULT_FILE"

for entry in "${UNITS[@]}"; do
    unit="${entry%%:*}"
    gate="${entry##*:}"
    status=ok

    log "restarting $unit"
    if ! mpe_systemctl restart "$unit.service" 2>/dev/null; then
        status=restart-failed
    else
        # `systemctl restart` returning 0 is not proof the thing works — gate on
        # what the next unit actually needs from this one.
        if ! wait_gate "$gate"; then
            status=not-ready
        elif ! mpe_systemctl is-active --quiet "$unit.service" 2>/dev/null; then
            status=inactive
        fi
    fi

    printf 'unit.%s=%s\n' "$unit" "$status" >>"$RESULT_FILE"
    if [ "$status" != ok ]; then
        # Record and continue. Aborting here would leave the stack half-cycled,
        # which is worse than a stack where one component is known-bad and named.
        log "WARNING $unit -> $status (continuing)"
        overall=partial
    fi
done

# The browser restores the last patch on startup (_pending_last_patch), so the
# step-8 restart is also the "reload what I had" step. Recorded so the result
# file states it rather than leaving the reader to infer it.
printf 'patch_reload=%s\n' "delegated-to-browser-startup" >>"$RESULT_FILE"

if [ "$overall" = ok ]; then
    log "stack restarted cleanly"
else
    log "stack restarted with failures — see $RESULT_FILE"
fi

printf 'finished=%s\n' "$EPOCHSECONDS" >>"$RESULT_FILE"
printf 'result=%s\n' "$overall" >>"$RESULT_FILE"
exit 0
