#!/bin/bash
# Resolve the appliance login user (the account that owns ~/MPE-Module on the Pi).
#
# There is no built-in default: the user is per-device and this repo is public.
# Order: MPE_PI_USER from the environment, then MPE_PI_USER= in /etc/mpe/mpe.env
# (configure-pi-paths.sh --local writes it). MPE_APPLIANCE_ENV_FILE overrides the
# file path for tests.
mpe_appliance_user() {
    local u="${MPE_PI_USER:-}"
    local env_file="${MPE_APPLIANCE_ENV_FILE:-/etc/mpe/mpe.env}"
    if [ -z "$u" ] && [ -f "$env_file" ]; then
        u="$(grep -E '^MPE_PI_USER=' "$env_file" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\"'" || true)"
    fi
    if [ -z "$u" ]; then
        echo "ERROR: MPE_PI_USER is not set. Export it, or set MPE_PI_USER= in $env_file" >&2
        echo "       (scripts/configure-pi-paths.sh --local writes it on the appliance)." >&2
        return 1
    fi
    printf '%s\n' "$u"
}
