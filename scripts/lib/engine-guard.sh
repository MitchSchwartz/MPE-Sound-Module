#!/bin/bash
# Looper guard (spec D5) — one message, one decision, many call sites.
#
# The looper captures snd-aloop with arecord and plays to the DAC with aplay.
# Under JACK, Surge is a graph client and there is no loopback stream to capture,
# so looping cannot work until the Phase 2 callback client ships. There is no
# alternate engine to switch to anymore (ALSA removed entirely, 2026-08-12) —
# the guard is unconditional whenever the looper is asked for, not a decision
# keyed on which engine is configured. MPE_LOOPER_ENABLED is read in nine
# files; a guard per call site would rot, so the decision lives here.
#
# The looper itself lives on yolo/looper-phase0, not on dev, so its authoritative
# guard — main() of scripts/mpe-looper.py, which every start path crosses — must be
# added on that branch. This helper and patch_browser/audio_engine.py carry the
# policy (message, blocked test, exit-code split) so both branches share one answer.

# shellcheck source=audio-engine.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/audio-engine.sh"

MPE_LOOPER_GUARD_MESSAGE="looper is unavailable until the JACK callback client ships (spec Phase 2) — there is no ALSA route to run it through."

# True when the looper must be refused: asked for at all (JACK is the only
# engine, and JACK cannot run the looper until Phase 2).
mpe_looper_engine_blocked() {
    [ "${MPE_LOOPER_ENABLED:-0}" = "1" ]
}

# Refuse loudly and non-zero — for interactive callers, where a human reads the
# result. The systemd path needs exit 0 instead (Restart=on-failure would storm);
# that split is looper_guard_exit_code() in patch_browser/audio_engine.py, applied
# by mpe-looper.py main() when yolo/looper-phase0 merges (keep for phase0 merge).
mpe_guard_looper_engine() {
    local context="${1:-looper}"
    if ! mpe_looper_engine_blocked; then
        return 0
    fi
    echo "LOOPER-GUARDED: ${context} — ${MPE_LOOPER_GUARD_MESSAGE}" >&2
    return 1
}
