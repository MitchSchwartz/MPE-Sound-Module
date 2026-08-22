#!/bin/bash
# Pi-only live instrument positive controls (Part 2a). Invoked from instrument-conformance --live.
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

# Known-clean baseline: 10 s idle, xrun delta should be 0.
start_idle="$(mpe_meter_xruns_read)"
sleep 10
end_idle="$(mpe_meter_xruns_read)"
delta_idle=$((end_idle - start_idle))
if [ "$delta_idle" -ne 0 ]; then
    echo "ERROR: idle window xrun delta=${delta_idle}, expected 0" >&2
    exit 1
fi
echo "live positive: idle xrun delta=0"

# Load positive: midi-load during window; sample DSP before killing load.
stamp="$(date +%s)"
load_log="/tmp/conformance-midi-load-${stamp}.log"
_as_user python3 "$ROOT/scripts/midi-load.py" 20 >"$load_log" 2>&1 &
load_pid=$!
sleep 2
if ! kill -0 "$load_pid" 2>/dev/null; then
    echo "ERROR: midi-load died within 2s: $(tail -5 "$load_log" 2>/dev/null || true)" >&2
    exit 1
fi
start_load="$(mpe_meter_xruns_read)"
dsp_raw="$(mktemp)"
_as_user stdbuf -oL jack_cpu_load >"$dsp_raw" 2>/dev/null &
dsp_pid=$!
sleep 3
kill "$dsp_pid" 2>/dev/null || true
wait "$dsp_pid" 2>/dev/null || true
dsp="$(grep -oE '[0-9]+\.[0-9]+' "$dsp_raw" | tail -1 || true)"
rm -f "$dsp_raw"
sleep 3
end_load="$(mpe_meter_xruns_read)"
kill "$load_pid" 2>/dev/null || true
wait "$load_pid" 2>/dev/null || true

delta_load=$((end_load - start_load))
if [ "$delta_load" -le 0 ]; then
    echo "ERROR: load window xrun delta=${delta_load}, expected > 0" >&2
    exit 1
fi

if ! mpe_meter_assert_live; then
    echo "ERROR: meter blind after load window" >&2
    exit 1
fi
echo "live positive: load xrun delta=${delta_load}"

if [ -z "$dsp" ] || [ "$dsp" = "?" ]; then
    echo "ERROR: jack_cpu_load returned no numeric DSP during load window" >&2
    exit 1
fi

if ! buf="$(mpe_jack_applied_period)"; then
    echo "ERROR: cannot read applied JACK buffer from jack_bufsize" >&2
    exit 1
fi
floor="$(mpe_result_dsp_plausibility_floor "$buf")"
if awk -v d="$dsp" -v fl="$floor" 'BEGIN{exit !(d+0 < fl+0)}'; then
    echo "ERROR: live DSP ${dsp}% below floor ${floor}% at applied buffer ${buf}" >&2
    exit 1
fi
echo "live positive: dsp=${dsp}% floor=${floor}% applied_buffer=${buf}"

echo "SENTINEL measure-instrument-conformance-live-pass"
