#!/bin/bash
# MIDI Device Diagnostics - Shows current MIDI connection state

echo "=== USB MIDI Devices ==="
lsusb | grep -i "midi\|roli\|seaboard" || echo "No USB MIDI devices found"

echo ""
echo "=== ALSA MIDI Ports ==="
amidi -l 2>/dev/null || echo "amidi not available"

echo ""
echo "=== Roli Seaboard BLOCK Detection ==="
if lsusb | grep -q "2af4:0700"; then
    echo "✓ Roli Seaboard BLOCK detected (USB 2af4:0700)"
else
    echo "✗ Roli NOT detected"
fi

echo ""
echo "=== Surge XT CLI MIDI State ==="
if pgrep -f surge-xt-cli > /dev/null; then
    echo "✓ Surge XT CLI is running"
    echo ""
    echo "Recent Surge MIDI log entries:"
    grep -i "midi\|opened.*input" ~/surge-cli.log 2>/dev/null | tail -5 || echo "  No MIDI entries in log"
else
    echo "✗ Surge XT CLI is NOT running"
fi
