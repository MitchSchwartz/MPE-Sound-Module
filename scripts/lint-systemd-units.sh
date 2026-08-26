#!/usr/bin/env bash
# Offline guards for shipped systemd unit invariants (formerly scattered unittest grep).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="${ROOT}/config"

fail() { echo "lint-systemd-units: FAIL: $*" >&2; exit 1; }
ok() { echo "lint-systemd-units: OK: $*"; }

require_in() {
    local file="$1" needle="$2" msg="$3"
    grep -qF "$needle" "$file" || fail "$msg (missing '$needle' in $(basename "$file"))"
}

require_not_in() {
    local file="$1" needle="$2" msg="$3"
    if grep -qF "$needle" "$file"; then
        fail "$msg (found '$needle' in $(basename "$file"))"
    fi
}

unit_section_line() {
    local file="$1" section="$2" key="$3"
    awk -v sec="[$section]" -v k="$key=" '
        $0 == sec { in_sec=1; next }
        /^\[/ { in_sec=0 }
        in_sec && index($0, k) == 1 { print; exit }
    ' "$file"
}

for unit in surge-xt-cli surge-watchdog mpe-jackd; do
    f="${CONFIG}/${unit}.service"
    [ -f "$f" ] || fail "missing config/${unit}.service"
    require_in "$f" "RuntimeDirectory=mpe" "${unit}: RuntimeDirectory"
    require_in "$f" "RuntimeDirectoryPreserve=yes" "${unit}: RuntimeDirectoryPreserve"
done

surge="${CONFIG}/surge-xt-cli.service"
watchdog="${CONFIG}/surge-watchdog.service"
jackd="${CONFIG}/mpe-jackd.service"

grep -qE '^Wants=.*surge-watchdog\.service' "$surge" || fail "surge unit wants watchdog (missing surge-watchdog.service in Wants=)"
require_not_in "$watchdog" "BindsTo=surge-xt-cli.service" "watchdog must not bind to surge"
require_in "$watchdog" "After=surge-xt-cli.service" "watchdog starts after surge"
require_in "$watchdog" "Restart=always" "watchdog supervised"

surge_unit_line="$(unit_section_line "$surge" Unit StartLimitBurst)"
surge_service_line="$(unit_section_line "$surge" Service StartLimitBurst)"
[ -n "$surge_unit_line" ] || fail "StartLimitBurst must live under [Unit] in surge-xt-cli.service"
[ -z "$surge_service_line" ] || fail "StartLimitBurst must not live under [Service] in surge-xt-cli.service"
require_in "$surge" "StartLimitIntervalSec=300" "surge StartLimitIntervalSec"

require_in "$jackd" "Restart=always" "jackd supervised"
require_in "$jackd" "StartLimitIntervalSec=0" "jackd start limit disabled for replug"
require_in "$jackd" "JACK_NO_AUDIO_RESERVATION=1" "jackd ALSA reservation off"
require_not_in "$jackd" "ExecCondition=" "jackd must not gate on engine condition"

cond_script="${ROOT}/scripts/jackd-engine-condition.sh"
[ ! -f "$cond_script" ] || fail "retired jackd-engine-condition.sh must stay deleted"

ok "unit invariants"
