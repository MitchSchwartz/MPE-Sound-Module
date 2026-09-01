#!/bin/bash
# Crash-safe audio settings changes.
#
# THE PROBLEM. set-surge-audio.sh must write the new period/rate into
# /etc/mpe/mpe.env before it can prove the graph comes up on it -- jackd reads
# the file, so there is no way to test a value without committing it first. That
# leaves a window where the file holds a setting nothing has validated.
#
# Guarding that window with a rollback code path does not work, because the
# process frequently does not survive to reach it: the touch UI calls the script
# through `subprocess.run(..., timeout=...)`, and on timeout CPython calls
# Popen.kill() -- SIGKILL. A trap cannot catch SIGKILL, and if sudo forked a
# monitor the script is orphaned rather than killed and keeps running. Neither
# branch reaches an in-process rollback.
#
# MEASURED 2026-09-01: `--buffer 64` at 09:12:32 was killed mid-flight; mpe.env
# kept 64 -- untested, and 64x2 does not start the driver on the attached DAC --
# instead of restoring the 128 that was running. The appliance then BOOTED into
# that value, dead.
#
# THE FIX. Record the known-good values in a file on PERSISTENT storage before
# touching mpe.env, and reconcile from it when the graph next starts. Nothing is
# asked of the dying process, so nothing depends on it surviving.
#
# NOT /run: that is tmpfs and is wiped by the reboot, which is the exact event
# this has to survive. The marker lives beside mpe.env.
#
# In-flight changes are distinguished from dead ones by (boot_id, pid):
#   different boot id      -> we have rebooted since; the change never completed
#   same boot, pid gone    -> died mid-flight
#   same boot, pid alive   -> still running; leave it alone
MPE_PENDING_FILE_DEFAULT="/etc/mpe/mpe.env.pending"

mpe_pending_file() {
    printf '%s' "${MPE_AUDIO_PENDING_FILE:-$MPE_PENDING_FILE_DEFAULT}"
}

mpe_boot_id() {
    cat /proc/sys/kernel/random/boot_id 2>/dev/null || printf '%s' "unknown"
}

# mpe_pending_write <env_file> <key=value>...   -- the KNOWN-GOOD values to restore.
#
# Refuses to overwrite a marker whose writer is STILL ALIVE. Two concurrent
# settings changes would otherwise have writer B record writer A's in-flight,
# untested value as "known good", and the reconciler would then restore the very
# value it exists to undo -- the same compounding failure the caller's own
# comment claims to have solved, moved one layer down. set-surge-audio.sh also
# takes an flock, so this is defence in depth rather than the primary guard.
mpe_pending_write() {
    local env_file="${1:?env file required}"; shift
    local file tmp
    file="$(mpe_pending_file)"
    if [ "$(mpe_pending_status)" = inflight ]; then
        echo "audio-settings: a settings change is already in flight — keeping its" \
             "known-good marker rather than recording an untested value" >&2
        return 1
    fi
    tmp="${file}.tmp.$$"
    {
        printf 'boot_id=%s\n' "$(mpe_boot_id)"
        printf 'pid=%s\n' "$$"
        printf 'env_file=%s\n' "$env_file"
        printf 'written=%s\n' "$(date +%s)"
        while [ "$#" -gt 0 ]; do printf 'restore:%s\n' "$1"; shift; done
    } >"$tmp" 2>/dev/null || return 1
    chmod 0644 "$tmp" 2>/dev/null || true
    # fsync the directory so the marker is durable before mpe.env is touched --
    # a power cut between the two must not lose the marker and keep the change.
    mv -f "$tmp" "$file" 2>/dev/null || { rm -f "$tmp" 2>/dev/null || true; return 1; }
    sync 2>/dev/null || true
    return 0
}

mpe_pending_clear() {
    rm -f "$(mpe_pending_file)" 2>/dev/null || true
}

# Prints: none | inflight | stale
mpe_pending_status() {
    local file boot pid
    file="$(mpe_pending_file)"
    if [ ! -r "$file" ]; then printf 'none'; return 0; fi
    boot="$(sed -n 's/^boot_id=//p' "$file" | head -1)"
    pid="$(sed -n 's/^pid=//p' "$file" | head -1)"
    if [ "$boot" != "$(mpe_boot_id)" ]; then printf 'stale'; return 0; fi
    case "$pid" in
        ''|*[!0-9]*) printf 'stale'; return 0 ;;
    esac
    if kill -0 "$pid" 2>/dev/null; then printf 'inflight'; else printf 'stale'; fi
}

# Restore the recorded known-good values into the env file. Returns 0 if it did.
mpe_pending_reconcile() {
    local file env_file line key value tmp restored=0 failed=0
    file="$(mpe_pending_file)"
    [ -r "$file" ] || return 1
    env_file="$(sed -n 's/^env_file=//p' "$file" | head -1)"
    [ -n "$env_file" ] && [ -f "$env_file" ] || { mpe_pending_clear; return 1; }

    while IFS= read -r line; do
        case "$line" in restore:*) ;; *) continue ;; esac
        line="${line#restore:}"
        key="${line%%=*}"
        value="${line#*=}"
        [ -n "$key" ] || continue
        tmp="$(mktemp)" || continue
        if grep -q "^${key}=" "$env_file"; then
            sed "s/^${key}=.*/${key}=${value}/" "$env_file" >"$tmp"
        else
            cat "$env_file" >"$tmp"
            printf '\n%s=%s\n' "$key" "$value" >>"$tmp"
        fi
        # The log line goes INSIDE the success branch. Reporting "restored" after a
        # failed install is an in-band failure on the recovery path itself: the
        # journal would show a rollback that did not happen, on the one appliance
        # state where that message is the only evidence anyone has (Rule -1).
        if install -m 0644 "$tmp" "$env_file"; then
            restored=1
            echo "audio-settings: restored ${key}=${value} (settings change did not complete)" >&2
        else
            failed=1
            echo "audio-settings: FAILED to restore ${key}=${value} — /etc may be" \
                 "read-only or full; the appliance is still on an untested setting" >&2
        fi
        rm -f "$tmp"
    done < "$file"

    # Keep the marker when nothing could be written: clearing it would discard the
    # only record of the known-good values, and the next graph start is a free
    # retry. Clear it only once every key has actually landed.
    if [ "$failed" = 1 ]; then
        echo "audio-settings: keeping the marker for the next graph start" >&2
        return 1
    fi
    mpe_pending_clear
    sync 2>/dev/null || true
    [ "$restored" = 1 ]
}
