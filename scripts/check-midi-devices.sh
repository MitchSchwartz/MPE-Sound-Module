#!/bin/bash
# MIDI Device Diagnostics - Shows current MIDI connection state

echo "=== USB MIDI Devices ==="
lsusb | grep -i "midi\|roli\|seaboard\|lumi" || echo "No USB MIDI devices found"

echo ""
echo "=== ALSA MIDI Ports ==="
amidi -l 2>/dev/null || echo "amidi not available"

echo ""
echo "=== ROLI Controller Detection ==="
if lsusb | grep -q "2af4:0700"; then
    echo "✓ Roli Seaboard BLOCK (USB 2af4:0700)"
elif lsusb | grep -q "2af4:0e00"; then
    echo "✓ Roli LUMI Keys BLOCK (USB 2af4:0e00)"
elif lsusb | grep -qi "2af4:"; then
    echo "✓ ROLI device detected: $(lsusb | grep -i 2af4)"
else
    echo "✗ ROLI controller NOT detected on USB"
fi

echo ""
echo "=== Pressure remapper MIDI chain ==="
if systemctl is-active --quiet mpe-pressure-remap.service; then
    echo "✓ mpe-pressure-remap.service is running"
else
    echo "✗ mpe-pressure-remap.service is NOT running"
fi
if aconnect -l 2>/dev/null | grep -A2 "RtMidiIn Client" | grep -q "Connected From"; then
    echo "✓ RtMidiIn wired to controller (aconnect)"
    aconnect -l 2>/dev/null | grep -E "LUMI|Seaboard|ROLI|RtMidiIn" | head -6
else
    echo "✗ RtMidiIn NOT connected — power-cycle stale port? Try:"
    echo "    sudo systemctl restart mpe-pressure-remap.service"
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
