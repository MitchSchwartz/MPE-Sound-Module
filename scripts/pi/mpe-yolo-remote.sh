#!/usr/bin/env bash
# Forced command for the `mpe-yolo` SSH identity on the appliance.
#
# Installed at /usr/local/sbin/mpe-yolo-remote.sh, root-owned 0755, NOT writable
# by mpe-yolo. Invoked only via:
#
#   authorized_keys:
#     restrict,command="/usr/local/sbin/mpe-yolo-remote.sh",from="<racknerd tailnet ip>" ssh-ed25519 ...
#
# THIS FILE IS THE COMPLETE DEFINITION OF WHAT THE REMOTE AGENT CAN DO TO THE
# APPLIANCE. Everything on the Racknerd side is mistake-prevention the agent can
# edit; see docs/racknerd-pi-access-spec.md §Layer weight. Review changes here
# with that in mind — a token that is too broad is a real hole.
#
# Design rules, each of which exists because of a specific failure mode:
#   - stdin is discarded immediately, so `ssh ... "bash -s" <<EOF` cannot smuggle
#     a payload past the token match.
#   - Tokens are matched as FULL STRINGS. No prefix matching, no argument
#     parsing, no eval, no user input interpolated into a command.
#   - Commands are fixed literals here. They deliberately do NOT call scripts
#     from the repo checkout: the agent can write to that checkout, so calling
#     into it would let the agent choose the code this wrapper runs.
#   - Unknown tokens are logged verbatim and rejected. The log is how you learn
#     what the agent actually wanted.
#
# Read-only token set. Deploy/test tokens are deliberately absent until the read
# path has run for a while — widening is one line, narrowing after the agent
# depends on it is a negotiation.

set -uo pipefail

# Discard stdin before anything else can read it.
exec </dev/null

LOGFILE=/var/log/mpe-yolo-remote.log
LOG_LINES_DEFAULT=100
LOG_LINES_MAX=400
PERF_MARKER=/etc/mpe/performance-mode

CLIENT="${SSH_CLIENT%% *}"
CMD="${SSH_ORIGINAL_COMMAND:-}"

# Never let logging break a call, and never let it leak noise to the client:
# the redirection itself can fail (permissions), so the whole block is guarded.
# The log file is append-only (chattr +a) and group-writable by mpe-yolo, so the
# agent can add entries but cannot rewrite or truncate its own history.
log() {
    { printf '%s [%s] %s\n' "$(date -Is)" "${CLIENT:-unknown}" "$*" >>"$LOGFILE"; } 2>/dev/null || true
}

reject() {
    log "REJECT $1"
    echo "mpe-yolo-remote: rejected" >&2
    exit 2
}

# An empty SSH_ORIGINAL_COMMAND means someone asked for an interactive shell.
if [ -z "$CMD" ]; then
    reject "empty command (interactive shell attempt)"
fi

# Performance-mode interlock. One Pi, one sound card: while Mitch is playing,
# the agent does not get to touch the instrument. Read-only tokens stay allowed
# because they are cheap and non-mutating; anything heavier is refused here.
perf_mode_active() { [ -f "$PERF_MARKER" ]; }

emit() { log "OK $CMD"; }

unit_list=(mpe-jackd surge-xt-cli sl-watchdog surge-watchdog surge-poly-governor
           mpe-cpu-governor mpe-audio-profile-sync mpe-pressure-remap midi-clock-in)

journal_for() {
    local unit="$1" n="$2"
    case "$n" in
        ''|*[!0-9]*) n="$LOG_LINES_DEFAULT" ;;
    esac
    [ "$n" -gt "$LOG_LINES_MAX" ] && n="$LOG_LINES_MAX"
    journalctl -u "$unit" -n "$n" --no-pager 2>&1
}

case "$CMD" in

    ping)
        emit
        echo "pong $(hostname) up $(uptime -p 2>/dev/null)"
        ;;

    version)
        emit
        echo "mpe-yolo-remote 1.0.0"
        ;;

    status)
        emit
        for u in "${unit_list[@]}"; do
            printf '%-26s %s\n' "$u" "$(systemctl is-active "$u.service" 2>/dev/null)"
        done
        perf_mode_active && echo "performance-mode: ACTIVE" || echo "performance-mode: off"
        ;;

    sysinfo)
        emit
        echo "host:    $(hostname)"
        echo "kernel:  $(uname -srm)"
        echo "model:   $(tr -d '\0' </proc/device-tree/model 2>/dev/null)"
        echo "uptime:  $(uptime -p 2>/dev/null)"
        echo "load:    $(cut -d' ' -f1-3 /proc/loadavg)"
        echo "throttle:$(vcgencmd get_throttled 2>/dev/null || echo ' n/a')"
        echo "temp:    $(vcgencmd measure_temp 2>/dev/null || echo 'n/a')"
        echo "mem:     $(free -h | awk '/^Mem:/{print $3" / "$2}')"
        echo "disk:    $(df -h / | awk 'NR==2{print $3" / "$2" ("$5")"}')"
        ;;

    jack-status)
        emit
        for f in /run/mpe/jack.state /run/mpe/engine.state /run/mpe/surge.state; do
            [ -r "$f" ] && { echo "--- $f"; cat "$f"; }
        done
        # A status token reports "not running"; it does not fail because a
        # service is down. Exit code means "the token was accepted".
        pgrep -a jackd | head -2 || echo "jackd not running"
        ;;

    osc-check)
        emit
        ss -lunp 2>/dev/null | grep -E '5327[0-9]|5328[0-9]' || echo "no OSC ports listening"
        pgrep -a surge-xt-cli | head -1 || echo "surge-xt-cli not running"
        ;;

    diagnose)
        emit
        echo "=== units ==="
        for u in "${unit_list[@]}"; do
            printf '%-26s %s\n' "$u" "$(systemctl is-active "$u.service" 2>/dev/null)"
        done
        echo "=== failed units ==="
        systemctl --failed --no-pager --no-legend 2>/dev/null | head -10
        echo "=== jack ==="
        pgrep -a jackd | head -1 || echo "jackd not running"
        [ -r /run/mpe/jack.state ] && cat /run/mpe/jack.state
        echo "=== throttle ==="
        vcgencmd get_throttled 2>/dev/null || echo "n/a"
        echo "=== recent errors ==="
        journalctl -p err -n 20 --no-pager 2>/dev/null | tail -20
        ;;

    logs-surge)      emit; journal_for surge-xt-cli        "$LOG_LINES_DEFAULT" ;;
    logs-jackd)      emit; journal_for mpe-jackd           "$LOG_LINES_DEFAULT" ;;
    logs-looper)     emit; journal_for sl-watchdog         "$LOG_LINES_DEFAULT" ;;
    logs-watchdog)   emit; journal_for surge-watchdog      "$LOG_LINES_DEFAULT" ;;
    logs-governor)   emit; journal_for surge-poly-governor "$LOG_LINES_DEFAULT" ;;
    logs-midiclock)  emit; journal_for midi-clock-in       "$LOG_LINES_DEFAULT" ;;

    help|tokens)
        emit
        cat <<'TOKENS'
mpe-yolo-remote tokens (read-only):
  ping version status sysinfo diagnose jack-status osc-check
  logs-surge logs-jackd logs-looper logs-watchdog logs-governor logs-midiclock
  help
Deploy and test tokens are deliberately not implemented yet.
TOKENS
        ;;

    *)
        reject "unknown token: $CMD"
        ;;
esac
