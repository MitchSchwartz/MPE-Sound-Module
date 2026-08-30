#!/bin/bash
# Unload snd-aloop when nothing holds a reference (leftover from calibration loopback).
# Safe to call before Surge start or after calibration loader exits.
#
# EXCEPT when snd-aloop is this unit's IDLE SINK. On a Pi 5 running
# MPE_AUDIO_PROFILE=usb-host with no external DAC there is no other
# free-running playback device -- no headphone jack, HDMI disconnected, and the
# UAC2 gadget has no clock until the host starts capturing -- so the loopback is
# the only thing jackd can bind. Unloading it there does not tidy up a leftover,
# it removes the appliance's clock, and the refcount test does not protect
# against it: there is a window between the module being installed and jackd
# binding it in which the count is legitimately 0.
#
# scripts/install-idle-sink.sh writes /etc/modprobe.d/mpe-idle-sink.conf, so the
# presence of that file is this unit saying "snd-aloop has a job here."

# Overridable only so the tests can exercise this without a real kernel.
_MODULES_FILE="${MPE_MODULES_FILE:-/proc/modules}"
_IDLE_SINK_CONF="${MPE_IDLE_SINK_CONF:-/etc/modprobe.d/mpe-idle-sink.conf}"

# `return` because this file is sourced -- a bare `exit` here would take the
# calling script down with it.
if [ ! -r "$_MODULES_FILE" ]; then
    return 0 2>/dev/null || exit 0
fi

if [ -f "$_IDLE_SINK_CONF" ]; then
    # Idle sink -- leave it alone.
    return 0 2>/dev/null || exit 0
fi

if grep -q '^snd_aloop .* 0 ' "$_MODULES_FILE" 2>/dev/null; then
    sudo modprobe -r snd_aloop 2>/dev/null || true
fi
