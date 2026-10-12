#!/bin/bash
# Measurement edits to /etc/mpe/mpe.env: only when they change something, and
# always undone, byte for byte, by a trap.
#
# Before 2026-10-12 each measure-* script carried its own _set_env_var. It wrote
# whether or not the value differed, restarted jackd and Surge after it, and
# most copies never restored anything: measure-capacity-ramp.sh left the
# governor off and the ceiling at 64 on the appliance for good.
#
#   mpe_env_arm [HOOK]     install the EXIT/INT/TERM/HUP trap. HOOK (a function)
#                          runs first, then mpe_env_restore. Call it before any
#                          edit; mpe_env_set refuses to write without it.
#   mpe_env_set KEY VALUE  write KEY=VALUE only if the file's value differs.
#                          Sets MPE_ENV_LAST_CHANGED to 1 or 0. The first write
#                          snapshots the file; restore puts that snapshot back.
#   mpe_env_get KEY        the file's current value (last assignment), or empty.
#   mpe_env_snapshot       take the snapshot now (before a tool that edits the
#                          file itself, such as set-surge-audio.sh). Once only.
#   mpe_env_on_restore CMD [ARG...]
#                          run CMD after the file is restored (once, in order of
#                          registration), e.g. to restart a unit that was
#                          restarted on the edited config.
#   mpe_unit_stop_for_measure UNIT
#                          stop UNIT if it is active, and start it again on
#                          restore. A unit that was not running stays stopped.
#   mpe_env_restore        restore the snapshot (verified with cmp), then run
#                          the registered functions. Idempotent.
#
# MPE_MEASURE_ENV_FILE overrides the target (tests point it at a temp file).

MPE_MEASURE_ENV_FILE="${MPE_MEASURE_ENV_FILE:-/etc/mpe/mpe.env}"
MPE_ENV_LAST_CHANGED=0
_MPE_ENV_ARMED=0
_MPE_ENV_SNAP_DIR=""
_MPE_ENV_SNAP_EXISTED=0
_MPE_ENV_HOOK=""
_MPE_ENV_RESTORE_FNS=()

mpe_env_get() {
    local key="$1"
    [ -f "$MPE_MEASURE_ENV_FILE" ] || return 0
    sed -n "s|^${key}=||p" "$MPE_MEASURE_ENV_FILE" | tail -1
}

mpe_env_arm() {
    _MPE_ENV_HOOK="${1:-}"
    _MPE_ENV_ARMED=1
    trap '_mpe_env_on_exit' EXIT
    trap '_mpe_env_on_signal 130' INT
    trap '_mpe_env_on_signal 143' TERM
    trap '_mpe_env_on_signal 129' HUP
}

_mpe_env_status() {
    return "$1"
}

_mpe_env_on_signal() {
    exit "$1"
}

_mpe_env_on_exit() {
    local rc=$?
    trap - EXIT INT TERM HUP
    # Exiting anyway: a failing step in the hook must not skip the restore.
    set +e
    if [ -n "$_MPE_ENV_HOOK" ]; then
        # The hook sees the script's exit status in $?, as a plain trap would.
        _mpe_env_status "$rc"
        "$_MPE_ENV_HOOK" || true
    fi
    mpe_env_restore || rc=1
    exit "$rc"
}

mpe_env_snapshot() {
    if [ "$_MPE_ENV_ARMED" != 1 ]; then
        echo "ERROR: mpe_env_snapshot before mpe_env_arm — no restore trap" >&2
        return 1
    fi
    [ -n "$_MPE_ENV_SNAP_DIR" ] && return 0
    _MPE_ENV_SNAP_DIR="$(mktemp -d)" || return 1
    if [ -e "$MPE_MEASURE_ENV_FILE" ]; then
        _MPE_ENV_SNAP_EXISTED=1
        cp -p "$MPE_MEASURE_ENV_FILE" "$_MPE_ENV_SNAP_DIR/env" || return 1
    else
        _MPE_ENV_SNAP_EXISTED=0
    fi
}

mpe_env_set() {
    local key="$1" value="$2" tmp
    MPE_ENV_LAST_CHANGED=0
    if [ "$_MPE_ENV_ARMED" != 1 ]; then
        echo "ERROR: mpe_env_set ${key} before mpe_env_arm — no restore trap" >&2
        return 1
    fi
    if [ -f "$MPE_MEASURE_ENV_FILE" ] && grep -q "^${key}=" "$MPE_MEASURE_ENV_FILE" \
        && [ "$(mpe_env_get "$key")" = "$value" ]; then
        return 0
    fi
    mpe_env_snapshot || {
        echo "ERROR: could not snapshot ${MPE_MEASURE_ENV_FILE}; not editing it" >&2
        return 1
    }
    tmp="$(mktemp)"
    if grep -q "^${key}=" "$MPE_MEASURE_ENV_FILE" 2>/dev/null; then
        sed "s|^${key}=.*|${key}=${value}|" "$MPE_MEASURE_ENV_FILE" >"$tmp"
    else
        cat "$MPE_MEASURE_ENV_FILE" >"$tmp" 2>/dev/null || true
        printf '\n%s=%s\n' "$key" "$value" >>"$tmp"
    fi
    cat "$tmp" >"$MPE_MEASURE_ENV_FILE"
    rm -f "$tmp"
    MPE_ENV_LAST_CHANGED=1
    echo "mpe-env: ${key}=${value} (restored on exit)" >&2
}

mpe_env_on_restore() {
    local cmd f
    cmd="$(printf '%q ' "$@")"
    for f in "${_MPE_ENV_RESTORE_FNS[@]}"; do
        [ "$f" = "$cmd" ] && return 0
    done
    _MPE_ENV_RESTORE_FNS+=("$cmd")
}

mpe_unit_stop_for_measure() {
    local unit="$1"
    systemctl is-active --quiet "$unit" 2>/dev/null || return 0
    mpe_env_on_restore systemctl start "$unit"
    systemctl stop "$unit"
}

mpe_env_restore() {
    local rc=0 fn
    if [ -n "$_MPE_ENV_SNAP_DIR" ]; then
        if [ "$_MPE_ENV_SNAP_EXISTED" = 1 ]; then
            if ! cmp -s "$_MPE_ENV_SNAP_DIR/env" "$MPE_MEASURE_ENV_FILE"; then
                cat "$_MPE_ENV_SNAP_DIR/env" >"$MPE_MEASURE_ENV_FILE" || rc=1
            fi
            if cmp -s "$_MPE_ENV_SNAP_DIR/env" "$MPE_MEASURE_ENV_FILE"; then
                echo "mpe-env: ${MPE_MEASURE_ENV_FILE} restored (byte-identical)" >&2
            else
                echo "ERROR: ${MPE_MEASURE_ENV_FILE} differs from its snapshot after restore;" \
                    "snapshot kept at ${_MPE_ENV_SNAP_DIR}/env" >&2
                return 1
            fi
        else
            rm -f "$MPE_MEASURE_ENV_FILE" || rc=1
        fi
        rm -rf "$_MPE_ENV_SNAP_DIR"
        _MPE_ENV_SNAP_DIR=""
    fi
    for fn in "${_MPE_ENV_RESTORE_FNS[@]}"; do
        eval "$fn" || rc=1
    done
    _MPE_ENV_RESTORE_FNS=()
    return "$rc"
}
