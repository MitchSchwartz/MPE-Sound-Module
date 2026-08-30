#!/usr/bin/env bash
# Install and load the idle sink — the free-running clock the audio graph runs
# on while the tethered host is not capturing.
#
# Idempotent, and safe to call on every deploy: it installs two config files
# and loads the module only if it is not already present.
#
# WHY. A UAC2 gadget has no clock of its own. Under the USB Audio Class spec
# the HOST enables the streaming interface, and isochronous transfers only
# occur while it is active. MEASURED on the appliance 2026-08-30 with the host
# attached but idle:
#
#     aplay -D hw:<gadget>   -> write error: Input/output error, after 1s
#     aplay -D hw:<Loopback> -> 3s of audio took 3s, nothing reading it
#
# So the graph cannot bind the gadget early and wait for the host — the device
# refuses the writes. The standard arrangement, and the one this repo already
# implements in `usb-host-session`, is to run on a free-running local device
# and bridge into the gadget when the host appears.
#
# That needs a local device. docs/USB-AUDIO-HOST.md gives the Pi 4 answer —
# "No external DAC: idle sink is Pi headphone" — and the Pi 5 has no headphone
# jack, with HDMI reading `disconnected` when no display is attached. So on a
# Pi 5 with no external DAC there was no idle sink at all: jackd refused to
# start, Surge never started, and the appliance was silent behind a misleading
# "no ALSA card matches tier '4'".
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")/.." && pwd)"

_log() { echo "install-idle-sink: $1"; }

_install() {
    local src="$1" dst="$2"
    if [ ! -f "$src" ]; then
        _log "WARN: missing $src" >&2
        return 1
    fi
    if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
        return 0
    fi
    sudo install -m 0644 "$src" "$dst"
    _log "installed $dst"
}

_install "$REPO_ROOT/config/modprobe.d/mpe-idle-sink.conf" \
         /etc/modprobe.d/mpe-idle-sink.conf || true
_install "$REPO_ROOT/config/modules-load.d/mpe-idle-sink.conf" \
         /etc/modules-load.d/mpe-idle-sink.conf || true

# Load now so the current boot gets it without a reboot. The options file above
# supplies index=7; passing them again here would be a second place for them to
# drift, so it is deliberately a bare modprobe.
if [ -d /proc/asound/Loopback ]; then
    _log "idle sink already present (card Loopback)"
    exit 0
fi

if ! sudo modprobe snd-aloop 2>/dev/null; then
    _log "WARN: could not load snd-aloop — usb-host with no external DAC will" >&2
    _log "      have no idle sink and jackd will refuse to start" >&2
    exit 0    # not fatal: a unit with a real DAC does not need this
fi

# Verify rather than assume. A modprobe that returns 0 while the card does not
# appear is exactly the reading-that-is-the-same-either-way this project keeps
# paying for.
for _ in 1 2 3 4 5; do
    [ -d /proc/asound/Loopback ] && break
    sleep 0.2
done
if [ -d /proc/asound/Loopback ]; then
    _log "idle sink loaded (card Loopback)"
else
    _log "WARN: modprobe succeeded but no Loopback card appeared" >&2
fi
