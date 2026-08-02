#!/bin/bash
# Flag file: start-surge-cli.sh skips wait-for-usb-midi when set (profile toggle restart).

MPE_PROFILE_SWITCH_FLAG="${MPE_PROFILE_SWITCH_FLAG:-/run/mpe-profile-switch-restart}"

profile_switch_flag_set() {
    [ -f "$MPE_PROFILE_SWITCH_FLAG" ]
}

profile_switch_flag_mark() {
    if [ "$(id -u)" -eq 0 ]; then
        : >"$MPE_PROFILE_SWITCH_FLAG"
        chmod 0644 "$MPE_PROFILE_SWITCH_FLAG" 2>/dev/null || true
    else
        sudo sh -c ": >'$MPE_PROFILE_SWITCH_FLAG' && chmod 0644 '$MPE_PROFILE_SWITCH_FLAG'" 2>/dev/null || true
    fi
}

profile_switch_flag_clear() {
    rm -f "$MPE_PROFILE_SWITCH_FLAG" 2>/dev/null || sudo rm -f "$MPE_PROFILE_SWITCH_FLAG" 2>/dev/null || true
}
