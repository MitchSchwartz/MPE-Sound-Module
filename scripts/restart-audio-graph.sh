#!/bin/bash
# Restart the JACK graph server (spec D2) — mpe-jackd.service. jackd binds one
# device at start; Surge is reconciled onto the new server by
# surge-watchdog.sh, not restarted directly here.
#
# Entry point for callers that cannot source shell libraries — notably
# config/99-usb-audio.rules, where a DAC unplug/replug must restart the graph
# rather than only Surge (criterion 15). Resets supervisor budget and sets
# state=recovering so supervisor-exhausted cannot survive a good replug. udev kills
# long-running RUN commands, so the restart is issued with --no-block.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

UNIT="$(mpe_audio_graph_unit)"
EXPLAIN=false
_args=()
for _a in "$@"; do
    case "$_a" in
        --explain) EXPLAIN=true ;;
        *) _args+=("$_a") ;;
    esac
done
set -- ${_args+"${_args[@]}"}

CARD_ID="${1:-${SOUND_CARD_ID:-}}"
PCM_NODE="${2:-}"

# Resolve the card id without trusting the udev database.
#
# Measured 2026-08-26: SOUND_CARD_ID was absent from the udev db for every card
# on this appliance, so `%E{SOUND_CARD_ID}` expanded to empty on `remove` and the
# name denylist below was never reached — HDMI and UAC2 teardowns restarted the
# production graph unchallenged. The failure was invisible because the guard is
# `[ -n "$CARD_ID" ]`: an unresolved id reads exactly like "no id to check".
#
# Resolution order: udev env -> sysfs by card number -> add-time cache. The cache
# exists because on `remove` the card is already unlinked from sysfs.
CARD_ID_CACHE_DIR="/run/mpe/pcm-card-id"
if [ -n "$PCM_NODE" ]; then
    _card_num="${PCM_NODE#pcmC}"
    _card_num="${_card_num%%D*}"
    if [ -z "$CARD_ID" ] && [ -r "/sys/class/sound/card${_card_num}/id" ]; then
        CARD_ID="$(cat "/sys/class/sound/card${_card_num}/id" 2>/dev/null || true)"
    fi
    if [ -n "$CARD_ID" ]; then
        mkdir -p "$CARD_ID_CACHE_DIR" 2>/dev/null || true
        printf '%s\n' "$CARD_ID" > "$CARD_ID_CACHE_DIR/$PCM_NODE" 2>/dev/null || true
    elif [ -r "$CARD_ID_CACHE_DIR/$PCM_NODE" ]; then
        CARD_ID="$(cat "$CARD_ID_CACHE_DIR/$PCM_NODE" 2>/dev/null || true)"
    fi
fi

if [ -z "$CARD_ID" ]; then
    echo "restart-audio-graph: WARNING card id unresolved (pcm=${PCM_NODE:-none}) — denylist not applied" >&2
fi

if [ -n "$CARD_ID" ] && mpe_should_skip_graph_restart_for_card "$CARD_ID"; then
    echo "restart-audio-graph: skipped denylisted card (card=$CARD_ID)"
    exit 0
fi

# Relevance — restart only when the changed card actually affects jackd's binding.
#
# Compare by card ID, never by hw:N. ALSA frees and reuses card indices as devices
# come and go, so `hw:0` before an event and `hw:0` after it are not necessarily
# the same hardware. IDs are stable for the lifetime of a live card.
#
# One comparison covers both cases worth restarting for:
#   bound card removed        -> detection picks something else -> differs -> restart
#   higher-tier card appears  -> detection prefers it           -> differs -> restart
#   unrelated card add/remove -> detection unchanged            -> same    -> skip
#
# Relevance is an OPTIMISATION, never a gate: every ambiguous case restarts and
# warns. A spurious restart is audible and self-correcting; a missed one is
# silent and leaves the instrument on the wrong device with no further event
# coming. Those outcomes are not symmetric, so this errs loudly on purpose.
mpe_bound_card_id() {
    local args idx
    args="$(ps -o args= -C jackd 2>/dev/null | head -1)"
    [ -n "$args" ] || return 1
    idx="$(printf '%s\n' "$args" | grep -oE -- '-P +hw:[0-9]+' | grep -oE '[0-9]+$' | head -1)"
    [ -n "$idx" ] || return 1
    [ -r "/sys/class/sound/card${idx}/id" ] || return 1
    cat "/sys/class/sound/card${idx}/id" 2>/dev/null
}

mpe_graph_restart_is_relevant() {
    local reason_var="${1:-}"
    local bound desired

    if [ "${MPE_GRAPH_RESTART_RELEVANCE:-1}" = "0" ]; then
        printf -v "$reason_var" '%s' "relevance check disabled (MPE_GRAPH_RESTART_RELEVANCE=0)"
        return 0
    fi

    if ! bound="$(mpe_bound_card_id)" || [ -z "$bound" ]; then
        printf -v "$reason_var" '%s' "jackd binding unresolved — restarting (fail loud)"
        return 0
    fi

    # Race guard: on remove, /proc/asound/cards may still list the departing card,
    # so detection would still choose it and we would skip the restart we need.
    # Decide this case from the event itself, without consulting detection.
    if [ "${ACTION:-}" = "remove" ] && [ -n "$CARD_ID" ] && [ "$CARD_ID" = "$bound" ]; then
        printf -v "$reason_var" '%s' "bound card '$bound' removed"
        return 0
    fi

    local det
    if ! det="$("$SCRIPT_DIR/detect-jack-device.sh" 2>/dev/null)"; then
        printf -v "$reason_var" '%s' "tier detection failed — restarting (fail loud)"
        return 0
    fi
    desired="$(printf '%s\n' "$det" | sed -n 's/^JACK_CARD_ID=//p' | head -1)"
    if [ -z "$desired" ]; then
        printf -v "$reason_var" '%s' "tier detection returned no card id — restarting (fail loud)"
        return 0
    fi

    if [ "$desired" != "$bound" ]; then
        printf -v "$reason_var" '%s' "binding should change: bound='$bound' desired='$desired'"
        return 0
    fi

    printf -v "$reason_var" '%s' "not relevant: jackd already on '$bound' and tier detection still prefers it"
    return 1
}

RELEVANCE_REASON=""
if mpe_graph_restart_is_relevant RELEVANCE_REASON; then
    case "$RELEVANCE_REASON" in
        *"fail loud"*) echo "restart-audio-graph: WARNING $RELEVANCE_REASON" >&2 ;;
    esac
    if [ "$EXPLAIN" = true ]; then
        echo "restart-audio-graph: WOULD RESTART — $RELEVANCE_REASON (card=${CARD_ID:-unknown} action=${ACTION:-unknown})"
        exit 0
    fi
    echo "restart-audio-graph: relevant — $RELEVANCE_REASON"
else
    echo "restart-audio-graph: skipped — $RELEVANCE_REASON (card=${CARD_ID:-unknown} action=${ACTION:-unknown})"
    exit 0
fi

# Debounce — coalesce a burst of IDENTICAL events only.
#
# A card with several playback PCMs fires one udev event per node, so a single
# plug can restart the graph repeatedly. But the window must never swallow a
# DIFFERENT event: unplug -> replug inside the window would drop the `add` and
# leave jackd bound to a device that is gone, with no further event coming and
# no sound. That failure is silent, which is the one outcome worth engineering
# against here.
#
# So the guard keys on (card, action) and suppresses only exact repeats. Any
# change of card or of action falls straight through and restarts.
DEBOUNCE_S="${MPE_GRAPH_RESTART_DEBOUNCE_S:-3}"
STAMP_DIR="/run/mpe"
STAMP="$STAMP_DIR/audio-graph-restart.stamp"
THIS_KEY="${CARD_ID:-unknown}:${ACTION:-unknown}"

mkdir -p "$STAMP_DIR" 2>/dev/null || true
if [ -r "$STAMP" ]; then
    read -r _last _key < "$STAMP" 2>/dev/null || { _last=0; _key=""; }
    case "${_last:-}" in
        ''|*[!0-9]*) _last=0 ;;
    esac
    if [ "${_key:-}" = "$THIS_KEY" ]; then
        _elapsed=$(( $(date +%s) - _last ))
        if [ "$_elapsed" -lt "$DEBOUNCE_S" ]; then
            echo "restart-audio-graph: debounced repeat ($THIS_KEY, ${_elapsed}s ago, window ${DEBOUNCE_S}s)"
            exit 0
        fi
    fi
fi
echo "$(date +%s) $THIS_KEY" > "$STAMP" 2>/dev/null || true

if [ "$EXPLAIN" = true ]; then
    echo "restart-audio-graph: WOULD RESTART $UNIT (explain mode — no action taken)"
    exit 0
fi

if mpe_restart_audio_graph; then
    echo "restart-audio-graph: restarted $UNIT"
    # shellcheck source=lib/dac-volume.sh
    source "$SCRIPT_DIR/lib/dac-volume.sh"
    mpe_apply_dac_volume || true
else
    echo "restart-audio-graph: FAILED to restart $UNIT" >&2
    exit 1
fi
