#!/usr/bin/env bash
# Stop/wait/start mpe-looper-session — avoids the APC MIDI subscription race.
#
# systemctl restart can SIGKILL the previous instance and start the replacement
# in the same second. rtmidi open_port() succeeds either way; ALSA may show no
# reader on the APC, and the startup banner still prints — dead pads, no error.
set -euo pipefail

UNIT=mpe-looper-session.service
WAIT_S="${MPE_LOOPER_SESSION_STOP_WAIT_S:-20}"
SETTLE_S="${MPE_LOOPER_SESSION_ALSA_SETTLE_S:-4}"

if ! systemctl cat "$UNIT" >/dev/null 2>&1; then
    echo "restart-looper-session: no $UNIT on this host — skipping" >&2
    exit 0
fi

echo "restart-looper-session: stopping $UNIT"
sudo systemctl stop "$UNIT" || true

for i in $(seq 1 "$WAIT_S"); do
    if ! pgrep -f 'looper-session.py' >/dev/null 2>&1; then
        break
    fi
    echo "restart-looper-session: waiting for looper-session.py to exit ($i/${WAIT_S})"
    sleep 1
done

if pgrep -f 'looper-session.py' >/dev/null 2>&1; then
    echo "restart-looper-session: WARN — sending SIGKILL to looper-session.py" >&2
    sudo pkill -9 -f 'looper-session.py' || true
    sleep 2
fi

echo "restart-looper-session: ALSA settle ${SETTLE_S}s"
sleep "$SETTLE_S"

echo "restart-looper-session: starting $UNIT"
sudo systemctl start "$UNIT"
sleep 6

if ! systemctl is-active --quiet "$UNIT"; then
    echo "restart-looper-session: FAIL — $UNIT not active" >&2
    systemctl status "$UNIT" --no-pager -l || true
    exit 1
fi

if [ -r "${MPE_SEQ_CLIENTS:-/proc/asound/seq/clients}" ]; then
    # Ask whether THE NEW SESSION holds the APC, not whether anything does.
    # This was an awk line crediting any reader on any APC port, and on
    # 2026-09-13 it printed PASS for mpe-pressure-remap on the Notes port.
    # The bench names its ALSA clients with its pid; systemd knows the pid.
    # The parser lives in midi_subscription.py so there is one of it.
    bench_pid="$(systemctl show -p MainPID --value "$UNIT" 2>/dev/null || echo 0)"
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ "${bench_pid:-0}" != 0 ] && python3 "$here/sooperlooper/midi_subscription.py" \
        --pid "$bench_pid" --device APC; then
        echo "restart-looper-session: PASS — session pid $bench_pid holds the APC"
    else
        echo "restart-looper-session: WARN — session pid ${bench_pid:-?} holds no APC port; check journal" >&2
        journalctl -u "$UNIT" -n 15 --no-pager || true
        exit 1
    fi
fi

echo "restart-looper-session: PASS — $(systemctl is-active "$UNIT")"
