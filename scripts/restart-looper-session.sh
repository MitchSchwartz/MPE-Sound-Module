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

if [ -r /proc/asound/seq/clients ]; then
    # Match any APC model by name, case-insensitively. Hardcoding the mk1
    # string "APC MINI" made this warn on a connected mk2 ("APC mini mk2") —
    # a false negative in the one check that exists to be trustworthy. A
    # verification that cries wolf gets ignored, which is the failure it was
    # built to prevent.
    if awk 'BEGIN{IGNORECASE=1} /^Client .*"[^"]*APC[^"]*"/{f=1;next} /^Client /{f=0} f && /Connecting To:/{ok=1} END{exit !ok}' \
        /proc/asound/seq/clients; then
        echo "restart-looper-session: PASS — APC has ALSA reader"
    else
        echo "restart-looper-session: WARN — APC has no ALSA reader yet; check journal" >&2
        journalctl -u "$UNIT" -n 15 --no-pager || true
        exit 1
    fi
fi

echo "restart-looper-session: PASS — $(systemctl is-active "$UNIT")"
