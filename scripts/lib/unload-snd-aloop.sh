#!/bin/bash
# Unload snd-aloop when nothing holds a reference (leftover from calibration loopback).
# Safe to call before Surge start or after calibration loader exits.
#
# snd-aloop is NOT the idle sink -- that is snd-dummy, on its own card index, so
# this tidy-up cannot reach it. See config/modprobe.d/mpe-idle-sink.conf for why
# snd-aloop was rejected for the role (it stalls jackd's driver thread).

# Overridable only so the tests can exercise this without a real kernel.
_MODULES_FILE="${MPE_MODULES_FILE:-/proc/modules}"

# `return` because this file is sourced -- a bare `exit` here would take the
# calling script (start-surge-cli.sh) down with it.
if [ ! -r "$_MODULES_FILE" ]; then
    return 0 2>/dev/null || exit 0
fi

if grep -q '^snd_aloop .* 0 ' "$_MODULES_FILE" 2>/dev/null; then
    sudo modprobe -r snd_aloop 2>/dev/null || true
fi
