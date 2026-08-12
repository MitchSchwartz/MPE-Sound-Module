#!/bin/bash
# ExecCondition for mpe-jackd.service — is the graph server wanted at all?
#
# MPE_AUDIO_ENGINE=alsa is a full regression path: no jackd process may be
# present (spec criterion 3). ConditionEnvironment= cannot express this because
# it reads the manager environment, not the unit's EnvironmentFile.
#
# ExecCondition (not ExecStartPre) is deliberate: a 1-254 exit marks the unit
# skipped rather than failed, so Restart=always does not turn a deliberate ALSA
# appliance into a restart loop.
#
# Exit 0 = start jackd. Exit 1 = skip (engine is alsa).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

if mpe_engine_is_jack; then
    echo "mpe-jackd: MPE_AUDIO_ENGINE=$(mpe_audio_engine) — starting graph server"
    exit 0
fi

echo "mpe-jackd: MPE_AUDIO_ENGINE=$(mpe_audio_engine) — graph server not wanted, skipping"
exit 1
