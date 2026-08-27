#!/bin/bash
# Prove that host audio actually reaches the interface. The one check on this
# appliance that measures the thing itself rather than inferring it.
#
# Why (2026-08-26): a Scarlett 4i4 in standalone mode discarded every sample the
# Pi sent, for hours, while ALL of the following read healthy — five units
# active, jackd bound to the right card, Surge connected to system:playback,
# correct output routing, correct levels, nothing muted, Sync Status Locked,
# hw_ptr advancing at exactly 48 kHz, zero xruns. Every one of those is upstream
# of where the audio was being thrown away, so none of them could see it.
#
# Method: route the interface's own capture channels to its PCM playback
# channels — an internal loopback of what the host sends — play a tone, and
# measure. Silence on PCM 1-4 means the host's audio is not arriving inside the
# device, whatever everything else claims.
#
# Usage:
#   check-audio-path.sh              # measure, restore, report
#   check-audio-path.sh --explain    # print the plan and exit, touching nothing
#
# NOTE: this makes an audible tone for a few seconds. Not for use mid-take.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

CARD="${MPE_CHECK_CARD:-}"
DURATION_S="${MPE_CHECK_DURATION_S:-3}"
EXPLAIN=false
[ "${1:-}" = "--explain" ] && EXPLAIN=true

log() { echo "check-audio-path: $*"; }

# Resolve the card jackd is bound to, so the check tests the device actually in
# use rather than whichever one happens to be card 0.
if [ -z "$CARD" ]; then
    DEVICE_FILE="${MPE_JACK_DEVICE_FILE:-$(mpe_run_dir)/jack-device}"
    if [ -r "$DEVICE_FILE" ]; then
        CARD="$(grep '^JACK_DEVICE=' "$DEVICE_FILE" 2>/dev/null | cut -d= -f2- | sed 's/^hw://;s/,.*//')"
    fi
fi
CARD="${CARD:-0}"

if [ "$EXPLAIN" = true ]; then
    echo "check-audio-path: would, on card $CARD:"
    echo "  1. save capture-source routing for PCM 01-04"
    echo "  2. point capture 1-4 at PCM 1-4 (internal loopback of host audio)"
    echo "  3. play a tone (jack_metro if jackd is up, else speaker-test)"
    echo "  4. record ${DURATION_S}s and measure per-channel peak"
    echo "  5. restore the saved routing"
    echo "check-audio-path: no action taken (--explain)"
    exit 0
fi

for c in amixer arecord python3; do
    command -v "$c" >/dev/null 2>&1 || { log "ERROR missing command: $c"; exit 2; }
done

if ! amixer -c "$CARD" controls 2>/dev/null | grep -qF "name='PCM 01 Capture Enum'"; then
    log "card $CARD has no PCM capture routing — this check needs a Scarlett-style matrix"
    log "SKIP (not a failure: the check simply does not apply to this interface)"
    exit 0
fi

WAV="$(mktemp -t audiopath-XXXXXX.wav)"
SAVED=""

# Restore routing whatever happens. Leaving an interface with its capture inputs
# rewired would be a worse bug than the one this diagnoses.
restore() {
    local pair name val
    for pair in $SAVED; do
        name="${pair%%=*}"; val="${pair##*=}"
        amixer -c "$CARD" cset name="PCM $name Capture Enum" "$val" >/dev/null 2>&1 || true
    done
    rm -f "$WAV"
    pkill -x jack_metro 2>/dev/null
    pkill -x speaker-test 2>/dev/null
}
trap restore EXIT

for n in 01 02 03 04; do
    v="$(amixer -c "$CARD" cget name="PCM $n Capture Enum" 2>/dev/null | grep -m1 ': values=' | cut -d= -f2)"
    [ -n "$v" ] && SAVED="$SAVED $n=$v"
done
log "saved capture routing:$SAVED"

# Point capture 1-4 at PCM 1-4 by ITEM NAME. Indices are not stable across
# firmware; names are.
for n in 1 2 3 4; do
    amixer -c "$CARD" sset "PCM 0$n" "PCM $n" >/dev/null 2>&1 || {
        log "ERROR could not route capture $n to PCM $n"
        exit 2
    }
done

CHANNELS="$(amixer -c "$CARD" cget name='PCM 01 Capture Enum' >/dev/null 2>&1; \
    arecord -D "hw:$CARD" --dump-hw-params -d 1 /dev/null 2>&1 | \
    grep -m1 '^CHANNELS:' | tr -dc '0-9 ' | tr ' ' '\n' | grep -v '^$' | tail -1)"
CHANNELS="${CHANNELS:-6}"

if pgrep -x jackd >/dev/null 2>&1; then
    log "jackd is running — injecting via jack_metro"
    jack_metro -b 100 </dev/null >/dev/null 2>&1 &
    sleep 2
    PORT="$(jack_lsp 2>/dev/null | grep -i metro | head -1)"
    if [ -z "$PORT" ]; then
        log "ERROR jack_metro did not register with the JACK server"
        # Overwhelmingly the cause: run under sudo. The server belongs to the
        # appliance user, and root is a different JACK client namespace, so the
        # tone silently goes nowhere and the check would report a false FAIL.
        if [ "$(id -u)" -eq 0 ]; then
            log "  Running as root. Re-run as the user that owns jackd (no sudo):"
            log "    ./scripts/check-audio-path.sh"
        else
            log "  Is jackd healthy? try: jack_lsp"
        fi
        exit 2
    fi
    for n in 1 2 3 4; do jack_connect "$PORT" "system:playback_$n" >/dev/null 2>&1; done
else
    log "jackd is not running — injecting via speaker-test"
    speaker-test -D "hw:$CARD" -c 4 -r 48000 -F S32_LE -t sine -f 440 >/dev/null 2>&1 &
    sleep 2
fi

log "recording ${DURATION_S}s on card $CARD ($CHANNELS ch)"
if ! arecord -D "hw:$CARD" -c "$CHANNELS" -f S32_LE -r 48000 -d "$DURATION_S" "$WAV" 2>/dev/null; then
    log "ERROR capture failed"
    exit 2
fi

echo
log "host audio as seen INSIDE the interface (PCM 1-4):"
if python3 "$SCRIPT_DIR/lib/wav_peaks.py" "$WAV" 1 2 3 4; then
    echo
    log "PASS — host audio reaches the interface"
    exit 0
fi
echo
log "FAIL — the interface is receiving silence from the host"
log "  Everything upstream can still look healthy. Check, in order:"
log "    1. standalone mode  (amixer -c $CARD cget name='Standalone Switch')"
log "    2. output routing   (Analogue Output NN should be sourced from PCM)"
log "    3. power-cycle the interface — a cleared standalone flag may need it"
exit 1
