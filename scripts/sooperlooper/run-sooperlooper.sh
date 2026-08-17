#!/usr/bin/env bash
# ExecStart for mpe-sooperlooper.service — the engine, in the foreground.
#
# restart-sooperlooper.sh stays the hand-operated remedy: it detects an orphan,
# rewires, and returns over SSH. This is the supervised path, and it deliberately
# does NOT setsid/nohup/background — systemd owns the process lifetime. A unit whose
# ExecStart backgrounds its real work reports "started" the instant the shell exits,
# so Restart= watches the wrapper rather than the engine.
#
# Why this exists at all: on 2026-08-17 the engine died at 16:15 and nothing noticed
# for six hours. The APC bench kept re-registering OSC into a void and the grid held
# stale state from a dead engine, which read as "looper controls broken". Nothing was
# broken — nothing was supervising.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"

SOOP_BIN="${MPE_SOOPERLOOPER_BIN:-${HOME}/src/sooperlooper-1.7.9/src/sooperlooper}"
OSC_PORT="${MPE_SL_OSC_PORT:-9951}"
LOOPS="${MPE_SL_LOOPS:-16}"
TIME_MAX="${MPE_SL_TIME_MAX:-40}"
JACK_CLIENT="${MPE_SL_JACK_CLIENT:-mpe-looper}"

if [ ! -x "$SOOP_BIN" ]; then
    echo "run-sooperlooper: engine binary not found or not executable: $SOOP_BIN" >&2
    echo "  Set MPE_SOOPERLOOPER_BIN in /etc/mpe/mpe.env if it lives elsewhere." >&2
    exit 1
fi

# jackd owns the device; an engine that starts without it comes up off the bus and
# has to be rescued. Ordering in the unit is not enough — After= only sequences unit
# activation, and jackd is "active" before it is accepting clients.
# shellcheck source=../lib/audio-engine.sh
source "$SCRIPT_DIR/../lib/audio-engine.sh"
if ! mpe_wait_for_jack_server "${MPE_SL_JACK_WAIT_S:-30}"; then
    echo "run-sooperlooper: JACK server not accepting clients — refusing to start" >&2
    exit 1
fi

echo "run-sooperlooper: ${LOOPS} loops, -t ${TIME_MAX}, OSC ${OSC_PORT}, client ${JACK_CLIENT}"
exec "$SOOP_BIN" -q -D yes -l "$LOOPS" -c 2 -t "$TIME_MAX" \
    -p "$OSC_PORT" -j "$JACK_CLIENT"
