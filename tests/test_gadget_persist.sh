#!/usr/bin/env bash
# shellcheck disable=SC1091
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=../scripts/lib/gadget-persist.sh
source "$ROOT/scripts/lib/gadget-persist.sh"

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_persist() {
    local env_val="$1"
    local expect="$2"
    MPE_USB_GADGET_PERSIST="$env_val"
    if mpe_gadget_persist_enabled; then
        got=1
    else
        got=0
    fi
    [ "$got" = "$expect" ] || fail "persist($env_val) expected $expect got $got"
}

assert_bind() {
    local profile="$1"
    local persist="$2"
    local expect="$3"
    MPE_AUDIO_PROFILE="$profile"
    MPE_USB_GADGET_PERSIST="$persist"
    if mpe_gadget_should_bind; then
        got=1
    else
        got=0
    fi
    [ "$got" = "$expect" ] || fail "bind(profile=$profile persist=$persist) expected $expect got $got"
}

assert_persist 1 1
assert_persist yes 1
assert_persist 0 0
assert_persist off 0
unset MPE_USB_GADGET_PERSIST
assert_persist "" 1

assert_bind usb-host 0 1
assert_bind usb-host 1 1
assert_bind usb-host-session 0 1
assert_bind usb-host-session 1 1
assert_bind standalone 1 1
assert_bind standalone 0 0

echo "OK test_gadget_persist.sh"
