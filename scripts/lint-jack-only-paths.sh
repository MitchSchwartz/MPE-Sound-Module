#!/usr/bin/env bash
# JACK-only audio path — banned ALSA/engine-selection tokens must not return.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

fail() { echo "lint-jack-only-paths: FAIL: $*" >&2; exit 1; }
ok() { echo "lint-jack-only-paths: OK: $*"; }

ban_in_file() {
    local file="$1"
    shift
    local token
    for token in "$@"; do
        if grep -qF -- "$token" "$file"; then
            fail "$(basename "$file") still references '$token'"
        fi
    done
}

require_in_file() {
    local file="$1" token="$2"
    grep -qF "$token" "$file" || fail "$(basename "$file") missing required '$token'"
}

start_surge="${ROOT}/scripts/start-surge-cli.sh"
audio_engine="${ROOT}/scripts/lib/audio-engine.sh"
watchdog="${ROOT}/scripts/surge-watchdog.sh"
guard="${ROOT}/scripts/lib/engine-guard.sh"
set_surge="${ROOT}/scripts/set-surge-audio.sh"

for f in "$start_surge" "$audio_engine" "$watchdog" "$guard" "$set_surge"; do
    [ -f "$f" ] || fail "missing $(basename "$f")"
done

ban_in_file "$start_surge" \
    select_alsa_device \
    ALSA_FAIL_REASON \
    ENGINE-FALLBACK \
    MPE_AUDIO_ENGINE \
    mpe_release_audio_device_for_alsa \
    '--buffer-size='

if grep -qF '${MPE_AUDIO_ENGINE' "$audio_engine"; then
    fail "audio-engine.sh still reads \$MPE_AUDIO_ENGINE"
fi
ban_in_file "$audio_engine" \
    'mpe_audio_engine()' \
    'mpe_engine_is_jack()' \
    mpe_release_audio_device_for_alsa \
    mpe_surge_active_engine \
    mpe_jackd_unit_masked \
    mpe_jackd_unit_seeking_start \
    MPE_AUDIO_GRAPH_ACTION

ban_in_file "$watchdog" active=alsa mpe_surge_active_engine release-alsa-for-jackd degraded
require_in_file "$watchdog" 'now=$EPOCHSECONDS'
if grep -qF 'now=$(date +%s)' "$watchdog"; then
    fail "surge-watchdog.sh must use EPOCHSECONDS not date +%s"
fi
require_in_file "$watchdog" 'systemctl is-active --quiet "$SURGE_SERVICE"'
require_in_file "$watchdog" '[ "$state" = ok ] && [ "$active" = jack ]'
require_in_file "$watchdog" JACK_PROBE_INTERVAL_S
require_in_file "$watchdog" '_last_jack_probe=$EPOCHSECONDS'
require_in_file "$watchdog" '_reconcile_looper_units_if_needed'
require_in_file "$watchdog" LOOPER_RECONCILE_INTERVAL_S

ban_in_file "$guard" MPE_AUDIO_ENGINE 'engine set alsa'
ban_in_file "$set_surge" mpe_audio_engine
require_in_file "$set_surge" mpe_promote_surge_planned
if grep -qF 'systemctl restart surge-xt-cli.service' "$set_surge"; then
    fail "set-surge-audio.sh must use graph restart not bare surge restart"
fi
require_in_file "$set_surge" '_prev_buffer='
require_in_file "$set_surge" rollback-after-failed-settings

bash -n "$set_surge" || fail "set-surge-audio.sh fails bash -n"

if grep -qF 'detect-audio-device.sh"' "$start_surge"; then
    fail "start-surge-cli.sh must not invoke detect-audio-device.sh"
fi

require_in_file "$audio_engine" _mpe_jack_lsp_bin
require_in_file "$audio_engine" '"$_MPE_JACK_LSP_BIN"'
if grep -qF 'timeout "$timeout_s" jack_lsp "$@"' "$audio_engine"; then
    fail "audio-engine.sh must call resolved jack_lsp path"
fi

ok "jack-only path invariants"
