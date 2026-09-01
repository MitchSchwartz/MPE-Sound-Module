#!/bin/bash
# Persist Surge ALSA buffer size and/or sample rate; restart gadget + Surge as needed.
#
# Usage: sudo ./scripts/set-surge-audio.sh --buffer N [--sample-rate R]
#        sudo ./scripts/set-surge-audio.sh --sample-rate R [--buffer N]
#
# Intended for NOPASSWD in sudoers (touch UI). See docs/TOUCH_PATCH_BROWSER.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"
# shellcheck source=lib/audio-settings-pending.sh
source "$SCRIPT_DIR/lib/audio-settings-pending.sh"

BUFFER=""
SAMPLE_RATE=""
PERIODS=""

while [ $# -gt 0 ]; do
    case "$1" in
        --buffer)
            BUFFER="${2:?--buffer requires a value}"
            shift 2
            ;;
        --sample-rate)
            SAMPLE_RATE="${2:?--sample-rate requires a value}"
            shift 2
            ;;
        --periods)
            PERIODS="${2:?--periods requires a value}"
            shift 2
            ;;
        *)
            echo "Usage: $0 --buffer N | --sample-rate R | --periods P (at least one required)" >&2
            exit 1
            ;;
    esac
done

if [ -z "$BUFFER" ] && [ -z "$SAMPLE_RATE" ] && [ -z "$PERIODS" ]; then
    echo "ERROR: specify --buffer, --sample-rate, and/or --periods" >&2
    exit 1
fi

is_valid_buffer() {
    # JACK server period — must match jackd / mpe jack buffer (not legacy Surge ALSA sizes).
    case "$1" in
        32 | 64 | 96 | 128 | 192 | 256 | 512 | 1024) return 0 ;;
        *) return 1 ;;
    esac
}

is_valid_sample_rate() {
    case "$1" in
        44100 | 48000) return 0 ;;
        *) return 1 ;;
    esac
}

is_valid_periods() {
    case "$1" in
        2 | 3 | 4 | 6 | 8) return 0 ;;
        *) return 1 ;;
    esac
}

ENV_FILE="/etc/mpe/mpe.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found — run configure-pi-paths.sh first" >&2
    exit 1
fi

if [ -n "$BUFFER" ] && ! is_valid_buffer "$BUFFER"; then
    echo "ERROR: invalid buffer size: $BUFFER" >&2
    exit 1
fi

if [ -n "$SAMPLE_RATE" ] && ! is_valid_sample_rate "$SAMPLE_RATE"; then
    echo "ERROR: invalid sample rate: $SAMPLE_RATE" >&2
    exit 1
fi

if [ -n "$PERIODS" ] && ! is_valid_periods "$PERIODS"; then
    echo "ERROR: invalid periods: $PERIODS (allowed: 2, 3, 4, 6, 8)" >&2
    exit 1
fi

# Serialise settings changes. Two concurrent runs each read _prev_* from the same
# file, so the second adopts the first's in-flight, UNTESTED value as its
# known-good baseline — and after that no rollback anywhere can find the setting
# that actually worked. flock releases on process death, including SIGKILL, so a
# killed run cannot wedge the lock.
_LOCK_DIR="/run/mpe"
mkdir -p "$_LOCK_DIR" 2>/dev/null || _LOCK_DIR="${TMPDIR:-/tmp}"
# NO redirection other than the fd itself. `exec 9>FILE 2>/dev/null` sends this
# script's stderr to /dev/null for the REST OF THE RUN -- with no command, every
# redirection on an exec applies to the shell permanently. That swallowed the
# rollback diagnostics and made a failing settings change look silent: exit 1,
# nothing on stdout or stderr. Open the file first so a real failure is
# reportable; an exec redirection error is fatal and cannot be trapped.
if : > "$_LOCK_DIR/set-surge-audio.lock" 2>/dev/null; then
    exec 9>"$_LOCK_DIR/set-surge-audio.lock"
else
    echo "WARNING: cannot create $_LOCK_DIR/set-surge-audio.lock — proceeding" >&2
    echo "         without serialisation." >&2
fi
if [ -e /dev/fd/9 ] && command -v flock >/dev/null 2>&1; then
    if ! flock -n 9; then
        echo "ERROR: another audio settings change is already running — refusing to" >&2
        echo "       start a second one (it would adopt an untested value as 'previous')." >&2
        exit 1
    fi
fi

mpe_source_appliance_env
_old_rate="${MPE_SURGE_SAMPLE_RATE:-48000}"
_prev_buffer="${MPE_JACK_BUFFER:-256}"
_prev_periods="${MPE_JACK_PERIODS:-3}"

# The env file is written before the graph is proven, so between that write and
# the validation below the file holds a value nothing has tested. The rollback
# further down restores it — but only if this process lives long enough to run.
# It frequently does not: the touch UI calls us through `subprocess.run(...,
# timeout=AUDIO_SWITCH_TIMEOUT_S)`, which KILLS the child on timeout, and a
# buffer change is slowest exactly when the graph is already struggling. A kill
# there left the untested value in /etc/mpe/mpe.env permanently, with no
# rollback and no log line — the appliance then booted into that setting.
#
# Worse, it compounds: `_prev_buffer` is read from the same file, so once a
# killed run has left a bad value behind, the NEXT run adopts it as "previous".
# Two failed attempts and the last known-good setting is gone.
#
# MEASURED 2026-09-01: `--buffer 64` at 09:12:32 was killed mid-flight; the file
# kept 64 (untested, and 64x2 does not start on this DAC) instead of restoring
# the 128 that was running. The rig booted dead.
#
# The restore is therefore NOT a trap and NOT a code path — both depend on this
# process still being alive. It is a marker on persistent storage, written before
# the mutation and reconciled by mpe-jackd's ExecStartPre on the next graph
# start. `_env_committed` is set only once the graph is proven, or once the
# explicit rollback below has already put the file back the way it wants it.
_env_dirty=false
_env_committed=false

# The trap below is the FAST path — it makes an ordinary Ctrl+C or SIGTERM tidy
# up immediately. It is explicitly NOT the guarantee: SIGKILL (which is what
# `subprocess.run(timeout=)` sends) cannot be trapped, and an orphaned run after
# sudo's monitor is killed never reaches it either. The guarantee is the marker
# written here, on persistent storage, and reconciled by mpe-jackd's
# ExecStartPre. See lib/audio-settings-pending.sh.
mpe_pending_write "$ENV_FILE" \
    "MPE_JACK_BUFFER=$_prev_buffer" \
    "MPE_JACK_PERIODS=$_prev_periods" \
    "MPE_SURGE_SAMPLE_RATE=$_old_rate" \
    || echo "WARNING: could not write the pending-settings marker — a kill mid-change will not self-heal" >&2

_restore_env_on_death() {
    local rc=$?
    if [ "$_env_committed" = true ] || [ "$_env_dirty" = false ]; then
        return 0
    fi
    echo "set-surge-audio: interrupted — restoring ${_prev_buffer}x${_prev_periods}" >&2
    # `if` blocks, not `&&` chains: under `set -e` a false test in an && chain
    # aborts the trap, and a half-restored env file is the bug this exists to
    # prevent.
    if [ -n "$BUFFER" ]; then
        _update_env_var MPE_JACK_BUFFER "$_prev_buffer" || true
    fi
    if [ -n "$PERIODS" ]; then
        _update_env_var MPE_JACK_PERIODS "$_prev_periods" || true
    fi
    if [ "${_rate_changed:-false}" = true ]; then
        _update_env_var MPE_SURGE_SAMPLE_RATE "$_old_rate" || true
    fi
    mpe_pending_clear
    return $rc
}
trap _restore_env_on_death EXIT INT TERM HUP

_update_env_var() {
    local key="$1"
    local value="$2"
    local tmp
    tmp="$(mktemp)"
    if grep -q "^${key}=" "$ENV_FILE"; then
        sed "s/^${key}=.*/${key}=${value}/" "$ENV_FILE" >"$tmp"
    else
        cat "$ENV_FILE" >"$tmp"
        printf '\n%s=%s\n' "$key" "$value" >>"$tmp"
    fi
    install -m 0644 "$tmp" "$ENV_FILE"
    rm -f "$tmp"
}

if [ -n "$BUFFER" ]; then
    # --buffer is the JACK graph period. Write it here, before the re-source below,
    # or mpe_source_appliance_env sees a half-applied file and warns about drift this
    # script created. MPE_SURGE_BUFFER_SIZE is NOT touched — it is not the period.
    _env_dirty=true
    _update_env_var MPE_JACK_BUFFER "$BUFFER"
    export MPE_JACK_BUFFER="$BUFFER"
fi

if [ -n "$SAMPLE_RATE" ]; then
    _env_dirty=true
    _update_env_var MPE_SURGE_SAMPLE_RATE "$SAMPLE_RATE"
    export MPE_SURGE_SAMPLE_RATE="$SAMPLE_RATE"
fi

if [ -n "$PERIODS" ]; then
    _env_dirty=true
    _update_env_var MPE_JACK_PERIODS "$PERIODS"
    export MPE_JACK_PERIODS="$PERIODS"
fi

mpe_source_appliance_env

_rate_changed=false
if [ -n "$SAMPLE_RATE" ] && [ "$SAMPLE_RATE" != "$_old_rate" ]; then
    _rate_changed=true
fi

if [ "$_rate_changed" = true ]; then
    if systemctl is-enabled --quiet usb-audio-gadget.service 2>/dev/null; then
        systemctl restart usb-audio-gadget.service
        if [ "${MPE_AUDIO_PROFILE:-standalone}" = "usb-host" ]; then
            # shellcheck source=lib/wait-for-uac2-gadget.sh
            source "$SCRIPT_DIR/lib/wait-for-uac2-gadget.sh"
            wait_for_uac2_gadget 8 || true
        fi
    fi
fi

# shellcheck source=lib/profile-switch-flag.sh
source "$SCRIPT_DIR/lib/profile-switch-flag.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"
profile_switch_flag_mark

# JACK is the only engine (spec amended 2026-08-13) — always the graph-restart
# path below; there is no ALSA-direct branch left to fall through to.
if ! mpe_promote_surge_planned "settings-change"; then
    _rollback=false
    if [ -n "$BUFFER" ] || [ -n "$PERIODS" ] || [ "$_rate_changed" = true ]; then
        _rollback=true
    fi
    if [ "$_rollback" = true ]; then
        echo "ERROR: audio graph change failed — restoring ${_prev_buffer}×${_prev_periods}" >&2
        [ -n "$BUFFER" ] && _update_env_var MPE_JACK_BUFFER "$_prev_buffer"
        [ -n "$PERIODS" ] && _update_env_var MPE_JACK_PERIODS "$_prev_periods"
        if [ "$_rate_changed" = true ]; then
            _update_env_var MPE_SURGE_SAMPLE_RATE "$_old_rate"
        fi
        _env_committed=true   # the file is already where this path wants it
        mpe_pending_clear
        mpe_source_appliance_env
        if mpe_promote_surge_planned "rollback-after-failed-settings"; then
            echo "ERROR: requested settings not supported — reverted to ${_prev_buffer}×${_prev_periods}" >&2
        else
            echo "ERROR: graph failed and rollback failed — check journalctl -u mpe-jackd -u surge-xt-cli" >&2
        fi
    else
        echo "ERROR: audio graph change failed — check journalctl -u mpe-jackd -u surge-xt-cli" >&2
    fi
    exit 1
fi

# Proven: the graph came up on the new settings, so the file may keep them, and
# the crash marker is no longer needed.
_env_committed=true
mpe_pending_clear

echo -n "Applied"
[ -n "$BUFFER" ] && echo -n " buffer=$BUFFER"
[ -n "$PERIODS" ] && echo -n " periods=$PERIODS"
[ -n "$SAMPLE_RATE" ] && echo -n " sample_rate=$SAMPLE_RATE"
echo
