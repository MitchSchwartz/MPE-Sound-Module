#!/bin/bash
# Phase 2 — the DAC leg, via a physical loopback.
#
# jackd runs with no -I/-O and both default to 0, so JACK declares the KA1's USB
# transfer and conversion latency as ZERO. That is not a measurement, it is an
# unset parameter. This measures it: KA1 analogue out -> Scarlett 4i4 line in,
# with jack_iodelay reporting the round trip that JACK cannot see.
#
# The appliance's own graph is playback-only on the KA1, so this needs a
# TEMPORARY duplex server (capture on the Scarlett, playback on the KA1) and
# therefore stops the services Mitch gigs with. RESTORING THEM IS THE FIRST
# THING WRITTEN AND RUNS FROM A TRAP, because a script that measures perfectly
# and leaves the instrument dead has failed.
#
# Two unsynchronised USB clocks drift against each other. A generous period is
# used because stability matters more than latency here, and the reading is
# watched for drift rather than sampled once and trusted.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"

PLAYBACK_DEV="${PLAYBACK_DEV:-hw:0}"     # KA1 — the DAC we actually gig through
CAPTURE_CARD="${CAPTURE_CARD:-5}"        # Scarlett 4i4
RATE="${RATE:-48000}"
PERIOD="${PERIOD:-256}"
NPERIODS="${NPERIODS:-3}"
SETTLE_S="${SETTLE_S:-12}"
SERVICES="${SERVICES:-mpe-looper surge-xt-cli mpe-jackd}"

TMP_JACK_PID=""

_restore() {
    echo
    echo "=== restoring the appliance ==="
    if [ -n "$TMP_JACK_PID" ]; then
        kill -TERM "$TMP_JACK_PID" 2>/dev/null || true
        sleep 2
        kill -KILL "$TMP_JACK_PID" 2>/dev/null || true
    fi
    pkill -f jack_iodelay 2>/dev/null || true
    # Start in reverse dependency order: the graph before its clients.
    for s in mpe-jackd surge-xt-cli mpe-looper; do
        case " $SERVICES " in *" $s "*) sudo systemctl start "$s" 2>&1 | tail -1 ;; esac
    done
    sleep 3
    echo "SENTINEL phase2-restored services=$(echo "$SERVICES" | tr ' ' ',')"
    systemctl is-active mpe-jackd surge-xt-cli 2>/dev/null | tr '\n' ' '
    echo
}
trap _restore EXIT

echo "=== stopping the appliance graph ==="
for s in $SERVICES; do sudo systemctl stop "$s" 2>&1 | tail -1; done
sleep 2
pkill -x jackd 2>/dev/null || true
sleep 1

echo "=== temporary duplex server: capture hw:${CAPTURE_CARD} / playback ${PLAYBACK_DEV} ==="
jackd -R -P70 -d alsa -C "hw:${CAPTURE_CARD}" -P "$PLAYBACK_DEV" \
    -r "$RATE" -p "$PERIOD" -n "$NPERIODS" > /tmp/phase2-jackd.log 2>&1 &
TMP_JACK_PID=$!

# A server that is "up" but whose driver never started is this project's oldest
# trap. Require the driver's own ports, not the process.
for _ in $(seq 1 20); do
    if jack_lsp 2>/dev/null | grep -q '^system:capture_'; then break; fi
    sleep 1
done
if ! jack_lsp 2>/dev/null | grep -q '^system:capture_'; then
    echo "SENTINEL phase2-aborted stage=driver reason=no-capture-ports" >&2
    echo "--- jackd log ---" >&2; tail -20 /tmp/phase2-jackd.log >&2
    exit 1
fi

echo "--- ports ---"
jack_lsp | grep '^system:' | tr '\n' ' '; echo

# Which capture channel is the cable actually in? Mitch patched the rear input
# marked 1, which is input 3 on a 4i4, but ASKING THE HARDWARE beats trusting
# the panel legend. Try each and keep the one that converges.
CHANNELS="${CHANNELS:-3 4 1 2}"
for CH in $CHANNELS; do
    CAP="system:capture_${CH}"
    jack_lsp | grep -qx "$CAP" || { echo "  $CAP absent, skipping"; continue; }
    echo
    echo "=== jack_iodelay on ${CAP} (${SETTLE_S}s) ==="
    jack_iodelay > /tmp/phase2-iodelay.log 2>&1 &
    IOD=$!
    sleep 2
    jack_connect jack_delay:out system:playback_1 2>/dev/null
    jack_connect "$CAP" jack_delay:in 2>/dev/null
    sleep "$SETTLE_S"
    kill -TERM "$IOD" 2>/dev/null || true
    wait "$IOD" 2>/dev/null || true

    tail -4 /tmp/phase2-iodelay.log
    if grep -q "frames" /tmp/phase2-iodelay.log && \
       ! grep -qi "signal" /tmp/phase2-iodelay.log; then
        echo "SENTINEL phase2-measured channel=${CH}"
        echo "--- full trace (watch for drift, two unsynced clocks) ---"
        tail -12 /tmp/phase2-iodelay.log
        exit 0
    fi
    echo "  no convergence on ${CAP}"
done

echo "SENTINEL phase2-aborted stage=iodelay reason=no-channel-converged" >&2
exit 1
