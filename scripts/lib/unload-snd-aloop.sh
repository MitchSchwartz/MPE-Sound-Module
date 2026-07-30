#!/bin/bash
# Unload snd-aloop when nothing holds a reference (leftover from calibration loopback).
# Safe to call before Surge start or after calibration loader exits.

if [ ! -r /proc/modules ]; then
    exit 0
fi

if grep -q '^snd_aloop .* 0 ' /proc/modules 2>/dev/null; then
    sudo modprobe -r snd_aloop 2>/dev/null || true
fi
