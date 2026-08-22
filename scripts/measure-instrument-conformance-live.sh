#!/bin/bash
# Pi-only live instrument positive controls (Part 2a). Invoked from instrument-conformance --live.
#
# Rule 0.5: pilot this script on one cell and read the output before trusting CONFORMANCE PASS.
# Requires: jackd running, midi-load-hold.py, MPE_PEAK_METER=1, /run/mpe/meter.state.
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")/.." && pwd)"
# shellcheck source=lib/paths.sh
source "$ROOT/scripts/lib/paths.sh"
# shellcheck source=lib/audio-engine.sh
source "$ROOT/scripts/lib/audio-engine.sh"
# shellcheck source=lib/measurement-result.sh
source "$ROOT/scripts/lib/measurement-result.sh"

RUN_AS_USER="${MPE_PI_USER:-mitch}"
if [ "$(id -u)" -eq 0 ] && id "$RUN_AS_USER" >/dev/null 2>&1; then
    _as_user() { sudo -u "$RUN_AS_USER" -- "$@"; }
else
    _as_user() { "$@"; }
fi

CONFORMANCE_PROBE_SEC="${MPE_CONFORMANCE_PROBE_SEC:-8}"
CONFORMANCE_MAX_VOICES="${MPE_CONFORMANCE_MAX_VOICES:-24}"
CONFORMANCE_VOICE_STEP="${MPE_CONFORMANCE_VOICE_STEP:-5,8,11,14,17,20,24}"

if [ ! -r /run/mpe/meter.state ]; then
    echo "ERROR: measure-instrument-conformance-live: no /run/mpe/meter.state (not on Pi?)" >&2
    exit 1
fi

if ! mpe_meter_assert_live; then
    echo "ERROR: measure-instrument-conformance-live: meter not live" >&2
    exit 1
fi

if [ "$(mpe_read_appliance_env_var MPE_PEAK_METER 2>/dev/null || echo 0)" != "1" ]; then
    echo "ERROR: MPE_PEAK_METER is not 1" >&2
    exit 1
fi

# Probe xrun delta under hold load (pattern from measure-capacity-ramp.sh _xruns_delta).
_conformance_xrun_probe() {
    local voices="$1" secs="$2"
    local load_log start_xr end_xr load_pid prev_xr cur_xr i
    load_log="$(mktemp)"
    _as_user python3 "$ROOT/scripts/midi-load-hold.py" "$((secs + 5))" "$voices" >"$load_log" 2>&1 &
    load_pid=$!
    sleep 2
    if ! kill -0 "$load_pid" 2>/dev/null; then
        echo "ERROR: midi-load-hold died voices=${voices}: $(tail -5 "$load_log" 2>/dev/null || true)" >&2
        rm -f "$load_log"
        return 1
    fi
    if ! start_xr="$(mpe_meter_xruns_read)"; then
        kill "$load_pid" 2>/dev/null || true
        wait "$load_pid" 2>/dev/null || true
        rm -f "$load_log"
        echo "ERROR: meter blind at load start voices=${voices}" >&2
        return 1
    fi
    prev_xr=$start_xr
    for ((i = 1; i < secs; i++)); do
        sleep 1
        if ! cur_xr="$(mpe_meter_xruns_read)"; then
            kill "$load_pid" 2>/dev/null || true
            wait "$load_pid" 2>/dev/null || true
            rm -f "$load_log"
            echo "ERROR: meter blind mid-load t=${i}s voices=${voices}" >&2
            return 1
        fi
        if [ "$cur_xr" -lt "$prev_xr" ]; then
            kill "$load_pid" 2>/dev/null || true
            wait "$load_pid" 2>/dev/null || true
            rm -f "$load_log"
            echo "ERROR: meter counter reset (${prev_xr} -> ${cur_xr}) voices=${voices}" >&2
            return 1
        fi
        prev_xr=$cur_xr
    done
    if ! end_xr="$(mpe_meter_xruns_read)"; then
        kill "$load_pid" 2>/dev/null || true
        wait "$load_pid" 2>/dev/null || true
        rm -f "$load_log"
        echo "ERROR: meter blind at load end voices=${voices}" >&2
        return 1
    fi
    kill "$load_pid" 2>/dev/null || true
    wait "$load_pid" 2>/dev/null || true
    rm -f "$load_log"
    echo $((end_xr - start_xr))
}

# Sample jack_cpu_load median during an active hold load (same median awk as measure-latency-run).
_conformance_dsp_median_under_load() {
    local voices="$1" secs="$2"
    local load_log dsp_raw load_pid dsp_pid dsp
    load_log="$(mktemp)"
    dsp_raw="$(mktemp)"
    _as_user python3 "$ROOT/scripts/midi-load-hold.py" "$((secs + 5))" "$voices" >"$load_log" 2>&1 &
    load_pid=$!
    sleep 2
    if ! kill -0 "$load_pid" 2>/dev/null; then
        echo "ERROR: midi-load-hold died for DSP sample voices=${voices}" >&2
        rm -f "$load_log" "$dsp_raw"
        return 1
    fi
    _as_user stdbuf -oL jack_cpu_load >"$dsp_raw" 2>/dev/null &
    dsp_pid=$!
    sleep "$secs"
    kill "$dsp_pid" 2>/dev/null || true
    wait "$dsp_pid" 2>/dev/null || true
    kill "$load_pid" 2>/dev/null || true
    wait "$load_pid" 2>/dev/null || true
    if ! dsp="$(mpe_result_jack_cpu_load_median "$dsp_raw")"; then
        rm -f "$load_log" "$dsp_raw"
        echo "ERROR: no numeric DSP samples during load voices=${voices}" >&2
        return 1
    fi
    rm -f "$load_log" "$dsp_raw"
    echo "$dsp"
}

# Known-clean baseline: idle xrun delta must be 0.
start_idle="$(mpe_meter_xruns_read)"
sleep 10
end_idle="$(mpe_meter_xruns_read)"
delta_idle=$((end_idle - start_idle))
if [ "$delta_idle" -ne 0 ]; then
    echo "ERROR: idle window xrun delta=${delta_idle}, expected 0" >&2
    exit 1
fi
echo "live positive: idle xrun delta=0"

# Load positive: escalate hold voices until xrun delta > 0 (bounded).
load_voices=0
load_delta=0
IFS=',' read -r -a _voice_steps <<< "$CONFORMANCE_VOICE_STEP"
for v in "${_voice_steps[@]}"; do
    [ "$v" -le "$CONFORMANCE_MAX_VOICES" ] || continue
    delta="$(_conformance_xrun_probe "$v" "$CONFORMANCE_PROBE_SEC")" || exit 1
    echo "live probe: voices=${v} xrun_delta=${delta}"
    if [ "$delta" -gt 0 ]; then
        load_voices=$v
        load_delta=$delta
        break
    fi
done

if [ "$load_voices" -eq 0 ]; then
    # Final bounded attempt: max voices, longer window (Pi 5 may need both).
    final_delta="$(_conformance_xrun_probe "$CONFORMANCE_MAX_VOICES" "$((CONFORMANCE_PROBE_SEC * 2))")" || exit 1
    echo "live probe: voices=${CONFORMANCE_MAX_VOICES} xrun_delta=${final_delta} (extended window)"
    if [ "$final_delta" -gt 0 ]; then
        load_voices=$CONFORMANCE_MAX_VOICES
        load_delta=$final_delta
    fi
fi

if [ "$load_voices" -eq 0 ]; then
    echo "ERROR: xrun counter did not increment through voices<=${CONFORMANCE_MAX_VOICES}" >&2
    echo "ERROR: raise MPE_CONFORMANCE_MAX_VOICES or set MPE_CONFORMANCE_VOICE_STEP for this platform" >&2
    exit 1
fi
echo "live positive: load xrun delta=${load_delta} at voices=${load_voices}"

if ! mpe_meter_assert_live; then
    echo "ERROR: meter blind after load probe" >&2
    exit 1
fi

# DSP band: median of jack_cpu_load during the same voice count (not tail -1).
if ! dsp="$( _conformance_dsp_median_under_load "$load_voices" "$CONFORMANCE_PROBE_SEC")"; then
    exit 1
fi

if ! buf="$(mpe_jack_applied_period)"; then
    echo "ERROR: cannot read applied JACK buffer from jack_bufsize" >&2
    exit 1
fi
floor="$(mpe_result_dsp_plausibility_floor "$buf")" || exit 1
if awk -v d="$dsp" -v fl="$floor" 'BEGIN{exit !(d+0 < fl+0)}'; then
    echo "ERROR: live DSP median ${dsp}% below floor ${floor}% at applied buffer ${buf}" >&2
    exit 1
fi
echo "live positive: dsp_median=${dsp}% floor=${floor}% applied_buffer=${buf} voices=${load_voices}"

echo "SENTINEL measure-instrument-conformance-live-pass"
