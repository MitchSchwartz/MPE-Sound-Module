#!/bin/bash
# Physical audio OUTPUT identity and explicit selection.
#
# Written 2026-09-01 for Documents/specs/audio-output-selection-spec.md, after a
# day lost to three bugs that were one bug: the output device was chosen
# IMPLICITLY, by whichever heuristic was nearest to hand.
#
#   grep -i "JACK" | head -1   selected a DAC named "...Headphone JACK A"
#   hw:N as an identity        selected whatever card held index N this boot
#   first playable card wins   selected snd-dummy, inaudible by construction
#
# So: a selection keys on the USB device's own identity, never on a card index,
# never on a card id, never on the product string. Measured on the appliance --
# the card ids are `USB` (Scarlett 4i4), `A` (Apple dongle) and `KA1` (FiiO).
# None is descriptive, none is unique, and two identical DACs collide outright.
#
# Selection values (MPE_AUDIO_OUTPUT):
#   auto              tier detection, the default and the fallback
#   silent            deliberately bind the idle sink
#   usb:VID:PID       a device model
#   usb:VID:PID:SER   a specific unit, where the device exposes a real serial

MPE_AUDIO_OUTPUT_AUTO="auto"
MPE_AUDIO_OUTPUT_SILENT="silent"

# /sys/class/sound/cardN/device points at the USB *interface* (…:1.0). The
# device that carries idVendor/idProduct/serial/speed is its parent. Measured on
# the appliance 2026-09-01; do not "simplify" this to the interface directory.
mpe_output_usb_dir() {
    local idx="${1:?card index required}"
    local sysfs="${MPE_SYSFS_SOUND:-/sys/class/sound}"
    local link
    link="$(readlink -f "$sysfs/card$idx/device" 2>/dev/null)" || return 1
    [ -n "$link" ] || return 1
    if [ -r "$link/../idVendor" ]; then
        (cd "$link/.." && pwd)
        return 0
    fi
    [ -r "$link/idVendor" ] || return 1
    printf '%s' "$link"
}

mpe_output_attr() {
    local dir="${1:-}" name="${2:-}" value
    [ -n "$dir" ] && [ -n "$name" ] || return 1
    value="$(cat "$dir/$name" 2>/dev/null)" || return 1
    printf '%s' "$value"
}

# A serial that does not distinguish anything is not a serial. The FiiO KA1
# reports the literal string "0" (measured 2026-09-01) -- keying on it would
# promise a per-unit match this device cannot deliver, so two KA1s would both
# claim usb:2972:0051:0 and the ambiguity would be invisible. Empty, "0" and
# all-zero strings are treated as ABSENT, which downgrades the key to the model
# form, which is a case the ambiguity rules already handle honestly.
mpe_output_serial_is_meaningful() {
    local serial="${1:-}"
    [ -n "$serial" ] || return 1
    case "$serial" in
        *[!0]*) return 0 ;;
        *) return 1 ;;
    esac
}

mpe_output_key() {
    local vid="${1:-}" pid="${2:-}" serial="${3:-}"
    [ -n "$vid" ] && [ -n "$pid" ] || return 1
    if mpe_output_serial_is_meaningful "$serial"; then
        printf 'usb:%s:%s:%s' "$vid" "$pid" "$serial"
    else
        printf 'usb:%s:%s' "$vid" "$pid"
    fi
}

# A card can be "physical" and still have nothing to play out of. The LUMI Keys
# BLOCK and the APC mini both enumerate as USB-Audio with NO playback PCM at
# all; handing one to jackd kills the server and the appliance goes silent
# (measured 2026-08-28 on the APC, confirmed 2026-09-01 on the LUMI). A name
# blocklist cannot see this. The pcm nodes can.
mpe_output_card_can_play() {
    local idx="${1:?card index required}"
    local root="${MPE_ASOUND_ROOT:-/proc/asound}"
    # No proc tree to consult (hermetic tests): do not veto on missing evidence.
    [ -d "$root/card$idx" ] || return 0
    ls "$root/card$idx" 2>/dev/null | grep -qE '^pcm[0-9]+p$'
}

# One record per SELECTABLE output:
#   index|card_id|key|speed|product
#
# Selectable means: not virtual (shared predicate, never a local regex) and has
# a playback PCM. A device with no key -- no USB identity -- is still listed,
# with an empty key field, because it can be displayed and bound automatically
# even though it cannot be the target of a stored selection.
mpe_output_records() {
    local cards_file="${MPE_ASOUND_CARDS:-/proc/asound/cards}"
    local idx id dir vid pid serial speed product key
    [ -r "$cards_file" ] || return 1
    while IFS='|' read -r idx id; do
        [ -n "$idx" ] || continue
        mpe_card_is_virtual "$id" && continue
        mpe_output_card_can_play "$idx" || continue
        dir="$(mpe_output_usb_dir "$idx" 2>/dev/null)" || dir=""
        vid="$(mpe_output_attr "$dir" idVendor 2>/dev/null)" || vid=""
        pid="$(mpe_output_attr "$dir" idProduct 2>/dev/null)" || pid=""
        serial="$(mpe_output_attr "$dir" serial 2>/dev/null)" || serial=""
        speed="$(mpe_output_attr "$dir" speed 2>/dev/null)" || speed=""
        product="$(mpe_output_attr "$dir" product 2>/dev/null)" || product=""
        [ -n "$product" ] || product="$id"
        key="$(mpe_output_key "$vid" "$pid" "$serial" 2>/dev/null)" || key=""
        printf '%s|%s|%s|%s|%s\n' "$idx" "$id" "$key" "$speed" "$product"
    done < <(sed -n 's/^[[:space:]]*\([0-9]\+\)[[:space:]]*\[\([^]]*\)\].*/\1|\2/p' \
                 "$cards_file" 2>/dev/null | sed 's/[[:space:]]*$//')
}

mpe_output_selection() {
    local raw="${MPE_AUDIO_OUTPUT:-}"
    raw="$(printf '%s' "$raw" | tr -d '[:space:]')"
    case "$raw" in
        "" | auto | AUTO | Auto) printf '%s' "$MPE_AUDIO_OUTPUT_AUTO" ;;
        silent | SILENT | Silent) printf '%s' "$MPE_AUDIO_OUTPUT_SILENT" ;;
        *) printf '%s' "$raw" ;;
    esac
}

mpe_output_selection_is_explicit() {
    case "$(mpe_output_selection)" in
        "$MPE_AUDIO_OUTPUT_AUTO" | "$MPE_AUDIO_OUTPUT_SILENT") return 1 ;;
        *) return 0 ;;
    esac
}

# Every record matching a key. Emits ALL matches, never head -1: two devices
# matching one key is a case to SURFACE, not to guess at (spec section 4). A
# model-form key matches a device whose own key carries a serial, so
# usb:VID:PID still selects the one unit of that model you have plugged in.
mpe_output_find() {
    local want="${1:-}" record key found=0
    [ -n "$want" ] || return 1
    while IFS= read -r record; do
        [ -n "$record" ] || continue
        key="$(printf '%s' "$record" | cut -d'|' -f3)"
        [ -n "$key" ] || continue
        if [ "$key" = "$want" ]; then
            printf '%s\n' "$record"
            found=1
        elif [ "${key#"$want":}" != "$key" ]; then
            printf '%s\n' "$record"
            found=1
        fi
    done < <(mpe_output_records)
    # Non-zero when NOTHING matched. The caller's whole job is to branch here --
    # "chosen device absent" is the case the spec says must fall through to
    # Automatic and say so by name, and it cannot do that if absence looks like
    # success.
    [ "$found" -eq 1 ]
}

# 12 = full speed, 480 = high speed. On the appliance this is the single
# strongest predictor of the smallest period a device can run: the Apple dongle
# and the FiiO KA1 both enumerate at 12 and neither starts a driver at 64, while
# the Scarlett 4i4 enumerates at 480 and runs 64 and 32 clean (measured
# 2026-09-01). It is otherwise invisible to the user.
mpe_output_speed_label() {
    case "${1:-}" in
        480 | 480M) printf 'high speed' ;;
        12 | 12M) printf 'full speed' ;;
        5000 | 10000) printf 'SuperSpeed' ;;
        "") printf 'unknown speed' ;;
        *) printf '%s Mbps' "$1" ;;
    esac
}

# The output LABEL is the only free-form value /etc/mpe/mpe.env ever holds, and
# it is vendor-controlled -- the product string is whatever the DAC says it is,
# and the spec is explicit that a vendor can put any word in it (that is how
# "Headphone JACK A" started a day of bisection).
#
# MEASURED 2026-09-01: selecting the FiiO wrote
#
#     MPE_AUDIO_OUTPUT_LABEL=FiiO KA1
#
# and mpe.env is SOURCED by bash, where that reads "set the var to FiiO, then
# run the command KA1" -- so every script sourcing the file reported
# "KA1: command not found" from line 52.
#
# Strip to a conservative set:
#   `/` and `&` -- the value reaches a sed REPLACEMENT in _update_env_var and in
#     mpe_pending_reconcile; `&` expands to the whole match and `/` ends the
#     expression, either of which corrupts the env file the appliance boots from.
#   `"`, `$`, backslash, backtick -- the value is re-read by `source`, where
#     they would break out of the quoting or run a command.
#
# This lives in the library, not in set-surge-audio.sh, because a function
# inside a top-to-bottom script cannot be sourced by a test -- and "the test
# could only assert the script's TEXT" is precisely how the buffer validator
# shipped undefined behind 1905 passing tests.
mpe_output_label_sanitize() {
    printf '%s' "${1:-}" \
        | tr -d '\n\r' \
        | sed -e 's/[^A-Za-z0-9 ._+()-]//g' -e 's/^ *//' -e 's/ *$//'
}

# The env-file LINE for a label, quoted so a value with spaces survives being
# sourced. Every reader copes: bash `source` strips the quotes, systemd
# EnvironmentFile= strips them, and the Python readers already .strip("\"'").
mpe_output_label_env_value() {
    printf '"%s"' "$(mpe_output_label_sanitize "${1:-}")"
}
