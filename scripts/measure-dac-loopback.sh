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
SERVICES="${SERVICES:-mpe-peak-meter mpe-sooperlooper surge-xt-cli mpe-jackd}"

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
    pkill -x jackd 2>/dev/null || true
    sleep 2

    # The first version of this restore started all three units three seconds
    # apart and reported success. Surge came back INACTIVE and the looper failed
    # outright, because a client cannot start before the graph it attaches to is
    # accepting -- the same ordering truth the startup path already knows. Wait
    # on the PORTS, and verify, because "I started it" is not "it is running".
    # This restore runs as root, and root's bare jack_lsp CANNOT SEE the graph --
    # it must drop to the graph owner, which mpe_jack_lsp does and a bare call
    # does not. The first version used bare jack_lsp here and reported
    # surge_port=0 for a Surge that was running perfectly: the verification
    # instrument was blind and published its blindness as a fact. A false alarm
    # that the instrument is dead is nearly as expensive as missing a real one.
    # shellcheck source=lib/audio-engine.sh
    source "$SCRIPT_DIR/lib/audio-engine.sh" 2>/dev/null || true

    sudo systemctl start mpe-jackd 2>&1 | tail -1
    for _ in $(seq 1 30); do
        mpe_jack_lsp 2>/dev/null | grep -q '^system:playback_' && break
        sleep 1
    done
    sudo systemctl start surge-xt-cli 2>&1 | tail -1
    for _ in $(seq 1 30); do
        mpe_jack_lsp 2>/dev/null | grep -q '^Surge XT:out_1$' && break
        sleep 1
    done
    case " $SERVICES " in
        *" mpe-sooperlooper "*) sudo systemctl start mpe-sooperlooper 2>&1 | tail -1 ;;
    esac
    # The meter is PartOf=mpe-jackd. A RESTART of jackd would bring it back by
    # itself, but this script STOPS the graph and pkills jackd -- PartOf
    # propagates the stop and starts nothing. That is what left Mitch's output
    # meter dead after Phase 2, reading zero and looking like silence.
    if systemctl is-enabled --quiet mpe-peak-meter.service 2>/dev/null; then
        sudo systemctl start mpe-peak-meter.service 2>&1 | tail -1
    fi
    sleep 2

    local st
    st="$(systemctl is-active mpe-jackd surge-xt-cli 2>/dev/null | tr '\n' ' ')"
    echo "SENTINEL phase2-restored state=\"${st}\" surge_port=$(mpe_jack_lsp 2>/dev/null | grep -c '^Surge XT:out_1$')"
    case "$st" in
        *inactive*|*failed*)
            echo "WARNING: THE APPLIANCE DID NOT COME BACK -- ${st}" >&2
            echo "         run: sudo systemctl restart mpe-jackd surge-xt-cli" >&2
            ;;
    esac
}
trap _restore EXIT

echo "=== stopping the appliance graph ==="
for s in $SERVICES; do sudo systemctl stop "$s" 2>&1 | tail -1; done
sleep 2
pkill -x jackd 2>/dev/null || true
sleep 1

echo "=== temporary duplex server: capture hw:${CAPTURE_CARD} / playback ${PLAYBACK_DEV} ==="
# Without a session bus jackd cannot reserve the device over dbus and dies
# with "cannot be acquired" -- which reads like the Scarlett is missing when
# it is in fact present and free.
JACK_NO_AUDIO_RESERVATION=1 \
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
# The headphone jack on a 4i4 mirrors a different output pair depending on the
# generation, so the playback port is swept too. Guessing it wrong is
# indistinguishable from an unplugged cable, which is precisely the confusion
# that cost the first two runs of this script.
CHANNELS="${CHANNELS:-3 4 1 2}"
PLAYBACK_PORTS="${PLAYBACK_PORTS:-1 3 2 4}"
for CH in $CHANNELS; do
  for PB in $PLAYBACK_PORTS; do
    CAP="system:capture_${CH}"
    PBP="system:playback_${PB}"
    jack_lsp | grep -qx "$CAP" || { echo "  $CAP absent, skipping"; continue; }
    jack_lsp | grep -qx "$PBP" || { echo "  $PBP absent, skipping"; continue; }
    echo
    echo "=== jack_iodelay ${PBP} -> ${CAP} (${SETTLE_S}s) ==="
    # jack_iodelay redraws ONE line with \r and block-buffers when stdout is not
    # a terminal, so a plain redirect captured 0 bytes and every channel looked
    # like "no signal" when the real fault was that nothing was ever flushed.
    # Give it a pty so it behaves as it does interactively.
    script -qec "jack_iodelay" /dev/null > /tmp/phase2-iodelay.raw 2>&1 &
    IOD=$!
    sleep 2
    # The client name differs between builds (jack_delay / jack_iodelay). Ask
    # the graph what actually registered instead of guessing and then silently
    # measuring nothing.
    IOD_OUT="$(jack_lsp 2>/dev/null | grep -iE '^jack_(io)?delay:.*out' | head -1)"
    IOD_IN="$(jack_lsp 2>/dev/null | grep -iE '^jack_(io)?delay:.*in' | head -1)"
    if [ -z "$IOD_OUT" ] || [ -z "$IOD_IN" ]; then
        echo "  jack_iodelay registered no ports — cannot connect" >&2
        jack_lsp 2>/dev/null | grep -iv '^system:' | tr '\n' ' ' >&2; echo >&2
        kill -TERM "$IOD" 2>/dev/null || true
        continue
    fi
    jack_connect "$IOD_OUT" "$PBP" 2>/dev/null
    jack_connect "$CAP" "$IOD_IN" 2>/dev/null
    sleep "$SETTLE_S"
    kill -TERM "$IOD" 2>/dev/null || true
    wait "$IOD" 2>/dev/null || true

    tr '\r' '\n' < /tmp/phase2-iodelay.raw | grep -vE '^\s*$' > /tmp/phase2-iodelay.log
    echo "  --- last lines ---"
    tail -4 /tmp/phase2-iodelay.log | sed 's/^/  /'
    if grep -q "extra loopback latency" /tmp/phase2-iodelay.log; then
        echo "SENTINEL phase2-measured capture=${CH} playback=${PB}"
        echo "--- convergence trace (watch it WALK: two unsynced USB clocks) ---"
        grep "extra loopback latency" /tmp/phase2-iodelay.log | tail -10
        exit 0
    fi
    echo "  no convergence ${PBP} -> ${CAP}"
  done
done

echo "SENTINEL phase2-aborted stage=iodelay reason=no-channel-converged" >&2
exit 1
