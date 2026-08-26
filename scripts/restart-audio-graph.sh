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
    echo "restart-audio-graph: skipped (card=$CARD_ID)"
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

if mpe_restart_audio_graph; then
    echo "restart-audio-graph: restarted $UNIT"
    # shellcheck source=lib/dac-volume.sh
    source "$SCRIPT_DIR/lib/dac-volume.sh"
    mpe_apply_dac_volume || true
else
    echo "restart-audio-graph: FAILED to restart $UNIT" >&2
    exit 1
fi
