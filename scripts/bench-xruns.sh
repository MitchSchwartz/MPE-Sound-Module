#!/bin/bash
# Audio stability bench — play for N seconds at a given JACK period, count xruns, PASS/FAIL.
#
# This exists because every crackle regression so far was found by ear, argued about in
# prose, and recorded in docs/measurements/*.md as a sentence. A claim like "256 x 3,
# zero xruns" should be a command anyone can re-run, not a memory.
#
# Usage:
#   sudo ./scripts/bench-xruns.sh                      # 120 s at the current period
#   sudo ./scripts/bench-xruns.sh --seconds 300        # longer soak
#   sudo ./scripts/bench-xruns.sh --buffer 512         # bench one period
#   sudo ./scripts/bench-xruns.sh --sweep              # 128/256/512/1024, restores original
#   sudo ./scripts/bench-xruns.sh --strict             # jackd without softmode (names the offender)
#
# Play the instrument during the run. An idle bench proves nothing — the failures that
# matter show up under dense MPE polyphony, not silence.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

SECONDS_PER_RUN=120
BUFFERS=""
SWEEP=false
STRICT=false
# A gig is minutes long; anything above zero is a defect worth naming. Kept as a knob
# so a soak can tolerate a known-bad baseline while it is being driven down.
MAX_XRUNS="${MPE_BENCH_MAX_XRUNS:-0}"

while [ $# -gt 0 ]; do
    case "$1" in
        --seconds) SECONDS_PER_RUN="${2:?--seconds requires a value}"; shift 2 ;;
        --buffer) BUFFERS="${2:?--buffer requires a value}"; shift 2 ;;
        --sweep) SWEEP=true; shift ;;
        --strict) STRICT=true; shift ;;
        --max-xruns) MAX_XRUNS="${2:?--max-xruns requires a value}"; shift 2 ;;
        -h | --help) sed -n '2,17p' "$0"; exit 0 ;;
        *) echo "Unknown argument: $1 (try --help)" >&2; exit 2 ;;
    esac
done

mpe_source_appliance_env

ORIGINAL_BUFFER="$(mpe_jack_period)"
if [ "$SWEEP" = true ]; then
    BUFFERS="128 256 512 1024"
elif [ -z "$BUFFERS" ]; then
    BUFFERS="$ORIGINAL_BUFFER"
fi

ENV_FILE="/etc/mpe/mpe.env"

# jackd reads its environment from EnvironmentFile=/etc/mpe/mpe.env, NOT from this
# shell. `export MPE_JACK_SOFTMODE=0` therefore did nothing at all — the flag never
# reached the server, --strict was a no-op, and softmode stayed on. That matters
# because softmode is what suppresses jackd's xrun message, so the bench was counting
# a signal the server had been told not to emit: a reading that looks identical
# whether the appliance is healthy or on fire.
_set_env_var() {
    local key="$1" value="$2" tmp
    tmp="$(mktemp)"
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed "s|^${key}=.*|${key}=${value}|" "$ENV_FILE" >"$tmp"
    else
        cat "$ENV_FILE" >"$tmp" 2>/dev/null || true
        printf '\n%s=%s\n' "$key" "$value" >>"$tmp"
    fi
    install -m 0644 "$tmp" "$ENV_FILE"
    rm -f "$tmp"
}

_restore_softmode() {
    if [ "${_SOFTMODE_CHANGED:-0}" = 1 ]; then
        echo "Restoring softmode (MPE_JACK_SOFTMODE=1)..."
        _set_env_var MPE_JACK_SOFTMODE 1
        _SOFTMODE_CHANGED=0
    fi
}
trap _restore_softmode EXIT INT TERM

if [ "$STRICT" = true ]; then
    _set_env_var MPE_JACK_SOFTMODE 0
    _SOFTMODE_CHANGED=1
    echo "Strict mode: MPE_JACK_SOFTMODE=0 written to $ENV_FILE (restored on exit)."
    echo "jackd will zombify a client that misses its deadline, and will report xruns."
fi

# Refuse to report a number the server cannot produce. In softmode jackd suppresses
# the xrun message, so "0 xruns" is indistinguishable from "not looking".
_assert_xrun_reporting_live() {
    local cmdline
    cmdline="$(ps -o args= -C jackd 2>/dev/null | head -1)"
    case " $cmdline " in
        *" -s "*)
            echo
            echo "WARNING: jackd is running in SOFTMODE (-s), which suppresses its xrun" >&2
            echo "         messages. A result of 0 xruns from this run means only that" >&2
            echo "         nothing was reported — it is NOT evidence of a clean graph." >&2
            echo "         Re-run with --strict for a number you can trust." >&2
            echo
            return 1
            ;;
    esac
    return 0
}

# Count xruns straight from the journal rather than a HUD file — the HUD is one of the
# things under test, and a bench must not read its verdict from the code it is judging.
_xruns_since() {
    local since="$1"
    journalctl -u mpe-jackd.service --since "$since" --no-pager 2>/dev/null \
        | grep -ci 'xrun' || true
}

_apply_buffer() {
    local target="$1"
    [ "$target" = "$(mpe_jack_period)" ] && return 0
    echo "  Applying period $target (restarts the graph; recorded loops are cleared)..."
    if ! "$SCRIPT_DIR/set-surge-audio.sh" --buffer "$target" >/dev/null; then
        echo "  ERROR: could not apply buffer $target" >&2
        return 1
    fi
    mpe_source_appliance_env
    return 0
}

FAILURES=0
RESULTS=()

for buffer in $BUFFERS; do
    echo
    echo "=== Bench: period ${buffer} x $(mpe_jack_periods) @ $(mpe_jack_rate) Hz, ${SECONDS_PER_RUN}s ==="
    if ! _apply_buffer "$buffer"; then
        RESULTS+=("${buffer}: SKIPPED (apply failed)")
        FAILURES=$((FAILURES + 1))
        continue
    fi

    if ! mpe_wait_for_jack_server 30; then
        echo "  ERROR: jackd did not come up" >&2
        RESULTS+=("${buffer}: SKIPPED (no server)")
        FAILURES=$((FAILURES + 1))
        continue
    fi
    if ! mpe_surge_on_jack_graph; then
        echo "  WARNING: Surge is not on the graph — playing now proves nothing" >&2
    fi

    # Timestamp AFTER the restart so the restart's own noise is outside the window.
    start_stamp="$(date '+%Y-%m-%d %H:%M:%S')"
    sleep 2

    echo "  PLAY NOW — dense MPE, hold chords, ride pressure. ${SECONDS_PER_RUN}s..."
    remaining="$SECONDS_PER_RUN"
    while [ "$remaining" -gt 0 ]; do
        step=10
        [ "$remaining" -lt 10 ] && step="$remaining"
        sleep "$step"
        remaining=$((remaining - step))
        printf '\r  %ds remaining, xruns so far: %s   ' \
            "$remaining" "$(_xruns_since "$start_stamp")"
    done
    printf '\n'

    xruns="$(_xruns_since "$start_stamp")"
    per_min="$(awk -v x="$xruns" -v s="$SECONDS_PER_RUN" 'BEGIN { printf "%.1f", (s>0)? x*60/s : 0 }')"

    if [ "$xruns" -le "$MAX_XRUNS" ] && ! _assert_xrun_reporting_live; then
        # Zero, from a server told not to report. Not a pass — an unknown.
        echo "  UNKNOWN — 0 reported, but xrun reporting is suppressed (softmode)"
        RESULTS+=("${buffer}: UNKNOWN (softmode — reporting suppressed)")
        FAILURES=$((FAILURES + 1))
    elif [ "$xruns" -le "$MAX_XRUNS" ]; then
        echo "  PASS — ${xruns} xruns (${per_min}/min)"
        RESULTS+=("${buffer}: PASS ${xruns} xruns (${per_min}/min)")
    else
        echo "  FAIL — ${xruns} xruns (${per_min}/min), budget ${MAX_XRUNS}"
        RESULTS+=("${buffer}: FAIL ${xruns} xruns (${per_min}/min)")
        FAILURES=$((FAILURES + 1))
    fi
done

if [ "$SWEEP" = true ] && [ "$(mpe_jack_period)" != "$ORIGINAL_BUFFER" ]; then
    echo
    echo "Restoring original period ${ORIGINAL_BUFFER}..."
    _apply_buffer "$ORIGINAL_BUFFER" || \
        echo "WARNING: could not restore ${ORIGINAL_BUFFER} — set it by hand" >&2
fi

echo
echo "=== Summary (jack_cpu_load / HUD deliberately not consulted) ==="
for line in "${RESULTS[@]}"; do
    echo "  $line"
done

if [ "$FAILURES" -gt 0 ]; then
    echo
    echo "$FAILURES configuration(s) failed. Re-run with --strict to make jackd name the"
    echo "client that is missing its deadline, and with MPE_PEAK_METER unset to rule the"
    echo "meter tap out."
    exit 1
fi
echo
echo "All configurations passed."
exit 0
