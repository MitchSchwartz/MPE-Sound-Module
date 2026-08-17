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
# STALE PREMISE — read before trusting the paragraph above (noted 2026-08-17).
# It describes the v0 looper (snd-aloop capture, scripts/mpe-looper.py), which was
# stripped on 2026-08-12 by 8e6759b. `yolo/looper-phase0`, where its authoritative
# main() guard was supposed to live, does not exist on origin. Meanwhile the thing
# this guard says is impossible — a JACK callback client running loops — is what
# the appliance actually does now, via SooperLooper (16 loops, client `mpe-looper`).
#
# The guard is left ACTIVE and unchanged on purpose: it is unit-tested, it is read
# by audio-engine.sh's engine-state label and by detect-jack-device.sh, and nobody
# has decided what MPE_LOOPER_ENABLED should mean in a sooperlooper world. Deciding
# that is a product call, not a cleanup. Until then, note that setting
# MPE_LOOPER_ENABLED=1 refuses a looper that demonstrably works.

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
# that split is looper_guard_exit_code() in patch_browser/audio_engine.py. It has
# no systemd caller today — mpe-looper.service was deleted 2026-08-17 — so the
# exit-code split is currently policy without a consumer.
mpe_guard_looper_engine() {
    local context="${1:-looper}"
    if ! mpe_looper_engine_blocked; then
        return 0
    fi
    echo "LOOPER-GUARDED: ${context} — ${MPE_LOOPER_GUARD_MESSAGE}" >&2
    return 1
}
