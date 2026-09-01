#!/bin/bash
# Persist Surge ALSA buffer size and/or sample rate; restart gadget + Surge as needed.
#
# Usage: sudo ./scripts/set-surge-audio.sh --buffer N [--sample-rate R]
#        sudo ./scripts/set-surge-audio.sh --sample-rate R [--buffer N]
#        sudo ./scripts/set-surge-audio.sh --output usb:VID:PID[:SERIAL]|auto|silent
#
# Intended for NOPASSWD in sudoers (touch UI). See docs/TOUCH_PATCH_BROWSER.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"
# shellcheck source=lib/audio-settings-pending.sh
source "$SCRIPT_DIR/lib/audio-settings-pending.sh"
# Must be sourced HERE, not further down: is_valid_buffer runs at line ~74 and
# this is what defines mpe_jack_period_is_valid. It used to be sourced at line
# 238, so validation called an undefined function, bash returned 127, and the
# script reported "invalid buffer size: 96" for every value the user picked.
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"
# shellcheck source=lib/audio-outputs.sh
source "$SCRIPT_DIR/lib/audio-outputs.sh"

BUFFER=""
SAMPLE_RATE=""
PERIODS=""
OUTPUT=""
OUTPUT_LABEL=""

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
        --output)
            OUTPUT="${2:?--output requires a value}"
            shift 2
            ;;
        --output-label)
            # Display name, stored so an ABSENT device can still be named in the
            # fall-through warning. "Scarlett 4i4 not connected" beats
            # "usb:1235:8212 not connected", which beats "no audio output".
            OUTPUT_LABEL="${2:?--output-label requires a value}"
            shift 2
            ;;
        *)
            echo "Usage: $0 --buffer N | --sample-rate R | --periods P | --output KEY" >&2
            exit 1
            ;;
    esac
done

if [ -z "$BUFFER" ] && [ -z "$SAMPLE_RATE" ] && [ -z "$PERIODS" ] && [ -z "$OUTPUT" ]; then
    echo "ERROR: specify --buffer, --sample-rate, --periods and/or --output" >&2
    exit 1
fi

is_valid_buffer() {
    # JACK server period — must match jackd / mpe jack buffer (not legacy Surge
    # ALSA sizes). Delegates to config/jack-periods.conf; this used to carry its
    # own copy of the list, which is how the UI and mpe-cli drifted away from it.
    mpe_jack_period_is_valid "$1"
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

# Argument validation runs BEFORE the environment check, and before anything is
# touched. Two reasons. Bad input should be rejected without side effects. And it
# makes this gate reachable without root, /etc/mpe, or a live graph -- which is
# why tests/test_set_surge_audio_rollback.py could only ever assert the script's
# TEXT, and why "mpe_jack_period_is_valid: command not found" shipped behind
# 1905 passing tests.
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

# An output is validated for SHAPE, never for presence. A device you can only
# select while it is plugged in is a device you cannot pre-configure, and the
# resolved rule is that a stored selection is a preference applied when the
# device appears -- not a command that must be satisfiable right now.
is_valid_output() {
    case "$1" in
        auto | silent) return 0 ;;
        usb:[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]) return 0 ;;
        # The serial is restricted to [A-Za-z0-9._-] deliberately. It reaches
        # _update_env_var and mpe_pending_reconcile, both of which build a sed
        # REPLACEMENT from it -- an `&` would expand to the whole match and a
        # `/` would end the expression, either of which corrupts /etc/mpe/mpe.env
        # and takes the appliance down on the next boot.
        usb:[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]:*[!A-Za-z0-9._-]*) return 1 ;;
        usb:[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]:?*) return 0 ;;
        *) return 1 ;;
    esac
}

if [ -n "$OUTPUT" ] && ! is_valid_output "$OUTPUT"; then
    echo "ERROR: invalid output: $OUTPUT" >&2
    echo "       expected 'auto', 'silent', or 'usb:VID:PID[:SERIAL]'." >&2
    echo "       A card index or card id is NOT an identity -- hw:0 was two" >&2
    echo "       different DACs in one boot on this appliance." >&2
    exit 1
fi

ENV_FILE="/etc/mpe/mpe.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found — run configure-pi-paths.sh first" >&2
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
_prev_output="${MPE_AUDIO_OUTPUT:-auto}"
_prev_output_label="${MPE_AUDIO_OUTPUT_LABEL:-}"

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
    "MPE_AUDIO_OUTPUT=$_prev_output" \
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
    if [ -n "${OUTPUT:-}" ]; then
        _update_env_var MPE_AUDIO_OUTPUT "$_prev_output" || true
        _update_env_var MPE_AUDIO_OUTPUT_LABEL "$_prev_output_label" || true
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

if [ -n "$OUTPUT" ]; then
    _env_dirty=true
    _update_env_var MPE_AUDIO_OUTPUT "$OUTPUT"
    export MPE_AUDIO_OUTPUT="$OUTPUT"
    # The label is written even when empty, so a stale name from a previous
    # selection can never be attached to a new device.
    _update_env_var MPE_AUDIO_OUTPUT_LABEL "$OUTPUT_LABEL"
    export MPE_AUDIO_OUTPUT_LABEL="$OUTPUT_LABEL"
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
# audio-engine.sh is sourced at the top (see there) — it must precede validation.
profile_switch_flag_mark

# JACK is the only engine (spec amended 2026-08-13) — always the graph-restart
# path below; there is no ALSA-direct branch left to fall through to.
if ! mpe_promote_surge_planned "settings-change"; then
    _rollback=false
    if [ -n "$BUFFER" ] || [ -n "$PERIODS" ] || [ -n "$OUTPUT" ] || [ "$_rate_changed" = true ]; then
        _rollback=true
    fi
    if [ "$_rollback" = true ]; then
        echo "ERROR: audio graph change failed — restoring ${_prev_buffer}×${_prev_periods}" >&2
        [ -n "$BUFFER" ] && _update_env_var MPE_JACK_BUFFER "$_prev_buffer"
        [ -n "$PERIODS" ] && _update_env_var MPE_JACK_PERIODS "$_prev_periods"
        if [ -n "$OUTPUT" ]; then
            _update_env_var MPE_AUDIO_OUTPUT "$_prev_output"
            _update_env_var MPE_AUDIO_OUTPUT_LABEL "$_prev_output_label"
        fi
        if [ "$_rate_changed" = true ]; then
            _update_env_var MPE_SURGE_SAMPLE_RATE "$_old_rate"
        fi
        _env_committed=true   # the file is already where this path wants it
        mpe_source_appliance_env
        # The marker is cleared ONLY once the rollback is PROVEN. It used to be
        # cleared here, before the promote below -- so when the rollback also
        # failed, the script deleted the one mechanism that could still recover
        # the appliance (reconcile-audio-settings.sh, mpe-jackd ExecStartPre) at
        # exactly the moment it was needed, and the box stayed silent across
        # reboots. Observed 2026-09-01 as "graph failed and rollback failed".
        if mpe_promote_surge_planned "rollback-after-failed-settings"; then
            mpe_pending_clear
            echo "ERROR: requested settings not supported — reverted to ${_prev_buffer}×${_prev_periods}" >&2
        else
            echo "ERROR: graph failed AND rollback failed. The known-good settings" >&2
            echo "       ${_prev_buffer}×${_prev_periods} are in $ENV_FILE and the recovery marker is" >&2
            echo "       DELIBERATELY LEFT IN PLACE — the next graph start will reconcile it." >&2
            echo "       This is not a settings problem: the graph would not come up on" >&2
            echo "       values that were working. Check the DAC is connected, then:" >&2
            echo "       journalctl -u mpe-jackd -u surge-xt-cli" >&2
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
[ -n "$OUTPUT" ] && echo -n " output=$OUTPUT"
echo
