#!/bin/bash
# Assert that a USB interface is in a state where host audio can actually reach
# its outputs. Sourced by jackd-prestart.sh, before jackd binds the device.
#
# Why this exists (2026-08-26). A Focusrite Scarlett 4i4 sat in STANDALONE mode:
# it runs as a self-contained hardware mixer and discards the host's playback
# stream. Every reading on the appliance said healthy — five units active, jackd
# on the right card, correct routing, correct levels, Sync Status Locked,
# hw_ptr advancing at exactly 48 kHz, zero xruns — and no sound came out for
# hours.
#
# That is the failure shape this project keeps meeting: an instrument that reads
# the same whether it is working or broken. Nothing in the stack could tell
# "samples reach the DAC" from "samples are discarded", because everything it
# measured was upstream of the discard.
#
# Two classes of device state break host audio unconditionally:
#
#   standalone mode   — the device ignores USB playback entirely
#   output routing    — an output fed from the hardware mixer or an analogue
#                       input carries no host audio, whatever the host sends
#
# Both are corrected here and logged loudly. Loudly matters more than the fix:
# a silent self-heal hides the cause and it recurs.
#
# Kill switches:
#   MPE_INTERFACE_GUARD=0    skip entirely
#   MPE_INTERFACE_FORCE_PCM=0  warn about output routing but do not change it

_ig_log() { echo "interface-guard: $*" >&2; }

# True when the card exposes a named control.
mpe_iface_has_control() {
    local card="${1:-}" name="${2:-}"
    [ -n "$card" ] && [ -n "$name" ] || return 1
    amixer -c "$card" controls 2>/dev/null | grep -qF "name='$name'"
}

# Enumerated control value as its ITEM TEXT, not its index. Indices are not
# stable across firmware or driver versions; item names are what the driver
# documents, so every comparison here is by name.
mpe_iface_enum_item() {
    local card="${1:-}" name="${2:-}" out idx
    out="$(amixer -c "$card" cget name="$name" 2>/dev/null)" || return 1
    idx="$(printf '%s\n' "$out" | grep -m1 ': values=' | cut -d= -f2)"
    [ -n "$idx" ] || return 1
    # cget prints the whole item table ("; Item #11 'PCM 1'"), so index -> name
    # resolves from one call. Never compare indices directly: they are not
    # stable across firmware or driver versions, item names are.
    printf '%s\n' "$out" |
        sed -n "s/^ *; Item #${idx} '\\(.*\\)'.*/\\1/p" | head -1
}

# Standalone mode cannot coexist with being a host audio interface. Always
# corrected — there is no configuration in which the appliance wants it on.
mpe_iface_assert_not_standalone() {
    local card="${1:-}" value
    mpe_iface_has_control "$card" "Standalone Switch" || return 0
    value="$(amixer -c "$card" cget name='Standalone Switch' 2>/dev/null | grep -m1 ': values=' | cut -d= -f2)"
    case "$value" in
        on|1|true)
            _ig_log "WARNING card $card is in STANDALONE mode — it discards host audio. Clearing."
            _ig_log "  A power cycle of the interface may be required for this to take effect."
            amixer -c "$card" cset name='Standalone Switch' off >/dev/null 2>&1 || {
                _ig_log "ERROR could not clear standalone on card $card"
                return 1
            }
            return 2   # changed
            ;;
    esac
    return 0
}

# Each analogue output must be sourced from a PCM channel, i.e. from the host.
# An output fed from 'Mix A' or 'Analogue 1' carries the interface's own inputs
# and no host audio at all.
mpe_iface_assert_outputs_from_pcm() {
    local card="${1:-}" n name item changed=0
    for n in 01 02 03 04; do
        name="Analogue Output $n Playback Enum"
        mpe_iface_has_control "$card" "$name" || continue
        item="$(mpe_iface_enum_item "$card" "$name")"
        case "$item" in
            PCM*) continue ;;
        esac
        _ig_log "WARNING analogue output $n is sourced from '${item:-unknown}', not the host (PCM)"
        if [ "${MPE_INTERFACE_FORCE_PCM:-1}" = "0" ]; then
            _ig_log "  left as-is (MPE_INTERFACE_FORCE_PCM=0)"
            changed=1
            continue
        fi
        # Outputs 1-4 pair to PCM 1,2,1,2: monitors and headphones carry the
        # same stereo programme, which is what a single-source instrument wants.
        case "$n" in
            01|03) want="PCM 1" ;;
            02|04) want="PCM 2" ;;
        esac
        if amixer -c "$card" sset "Analogue Output $n" "$want" >/dev/null 2>&1; then
            _ig_log "  corrected output $n -> $want"
            changed=1
        else
            _ig_log "  ERROR could not set output $n to $want"
        fi
    done
    [ "$changed" -eq 0 ] || return 2
    return 0
}

# Entry point. Never fails the caller: a guard that blocks startup would turn a
# recoverable misconfiguration into no instrument at all.
mpe_interface_guard() {
    local card="${1:-}" rc=0 r
    [ "${MPE_INTERFACE_GUARD:-1}" = "0" ] && return 0
    [ -n "$card" ] || return 0
    command -v amixer >/dev/null 2>&1 || return 0

    mpe_iface_assert_not_standalone "$card"; r=$?
    [ "$r" -eq 2 ] && rc=2
    mpe_iface_assert_outputs_from_pcm "$card"; r=$?
    [ "$r" -eq 2 ] && rc=2

    [ "$rc" -eq 2 ] && _ig_log "device state corrected on card $card — see warnings above"
    return 0
}
