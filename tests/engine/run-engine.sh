#!/usr/bin/env bash
# Headless JACK (dummy backend) + SooperLooper, launched the way the appliance
# launches it — scripts/sooperlooper/run-sooperlooper.sh:52 — so the engine
# under test has the appliance's loop count, channel count and time budget.
set -euo pipefail

RATE="${ENGINE_RATE:-48000}"
PERIOD="${ENGINE_PERIOD:-256}"
LOOPS="${ENGINE_LOOPS:-15}"
TIME_MAX="${ENGINE_TIME_MAX:-40}"
OSC_PORT="${ENGINE_OSC_PORT:-9951}"

# -r: no realtime scheduling (none available in a container, none needed).
jackd -r -d dummy -r "$RATE" -p "$PERIOD" -C 2 -P 2 &
jack_wait -w -t 15

exec sooperlooper -q -D yes -l "$LOOPS" -c 2 -t "$TIME_MAX" -p "$OSC_PORT" -j mpe-looper
