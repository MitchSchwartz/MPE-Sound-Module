#!/bin/bash
# Looper × engine guard (spec D5) — one message, one decision, many call sites.
#
# The looper captures snd-aloop with arecord and plays to the DAC with aplay.
# Under JACK, Surge is a graph client and there is no loopback stream to capture,
# so looping cannot work until the Phase 2 callback client ships. MPE_LOOPER_ENABLED
# is read in nine files; a guard per call site would rot, so the decision lives here.
#
# The authoritative guard is in main() of scripts/mpe-looper.py — every path
# (mpe-looper.service, `mpe restart looper`, a bare `python3 scripts/mpe-looper.py`)
# crosses it. This shell helper exists for earlier, friendlier refusals.

# shellcheck source=audio-engine.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/audio-engine.sh"

MPE_LOOPER_GUARD_MESSAGE="looper requires MPE_AUDIO_ENGINE=alsa until the JACK callback client ships (spec Phase 2)."

# True when the looper must be refused: asked for, and the engine is JACK.
mpe_looper_engine_blocked() {
    [ "${MPE_LOOPER_ENABLED:-0}" = "1" ] && mpe_engine_is_jack
}

# Refuse loudly and non-zero — for interactive callers, where a human reads the
# result. The systemd path needs exit 0 instead (Restart=on-failure would storm),
# which is why that decision lives in mpe-looper.py rather than here.
mpe_guard_looper_engine() {
    local context="${1:-looper}"
    if ! mpe_looper_engine_blocked; then
        return 0
    fi
    echo "LOOPER-GUARDED: ${context} — ${MPE_LOOPER_GUARD_MESSAGE}" >&2
    echo "  Current engine: $(mpe_audio_engine). Switch with: sudo mpe engine set alsa" >&2
    return 1
}
