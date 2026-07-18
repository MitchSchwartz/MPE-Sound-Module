#!/bin/bash
# Diagnostic script to check current Pi state

echo "======================================"
echo "PI-SURGE MPE - SYSTEM DIAGNOSTICS"
echo "======================================"
echo ""

echo "--- 1. MIDI DEVICES ---"
aconnect -l 2>/dev/null || echo "ALSA MIDI not available, trying amidi..."
amidi -l 2>/dev/null || echo "No MIDI devices found"
echo ""

echo "--- 2. USB DEVICES (Roli detection) ---"
lsusb | grep -i roli || echo "Roli not found in USB devices"
lsusb | grep -i "ROLI" || lsusb | grep -i "Seaboard"
echo ""

echo "--- 2b. UDEV RULES STATUS ---"
echo "Roli auto-restart rule:"
if [ -f /etc/udev/rules.d/99-roli-seaboard.rules ]; then
    echo "  ✓ Installed"
    cat /etc/udev/rules.d/99-roli-seaboard.rules
else
    echo "  ✗ NOT INSTALLED (this causes connection issues!)"
fi
echo ""

echo "USB audio auto-restart rule:"
if [ -f /etc/udev/rules.d/99-usb-audio.rules ]; then
    echo "  ✓ Installed"
else
    echo "  ✗ Not installed"
fi
echo ""

echo "Recent udev events for Roli:"
journalctl -b -g "2af4:0700|Roli|Seaboard|surge-xt-cli.*restart" --no-pager 2>/dev/null | tail -10 || echo "  No events found"
echo ""

echo "--- 3. AUDIO SETUP ---"
echo "ALSA devices:"
aplay -l
echo ""
echo "Is JACK running?"
pgrep -l jackd || echo "JACK not running"
echo ""

echo "--- 4. X11 STATUS ---"
echo "X server running?"
pgrep -l Xorg || echo "No Xorg process"
echo ""
echo "Display variable: $DISPLAY"
echo ""

echo "--- 5. INSTALLED DESKTOP ENVIRONMENTS ---"
dpkg -l | grep -E 'desktop|openbox|lxde|xfce' | awk '{print $2, $3}'
echo ""

echo "--- 6. SURGE XT BUILD ---"
echo "Surge binary location:"
find /home -name "Surge XT" 2>/dev/null | head -5
echo ""

echo "--- 7. INPUT DEVICE PERMISSIONS ---"
ls -l /dev/input/event* 2>/dev/null | head -5
echo ""
echo "Current user groups:"
groups
echo ""

echo "--- 8. SYSTEMD-LOGIND STATUS ---"
systemctl status systemd-logind --no-pager | head -10
echo ""

echo "--- 9. CURRENT SEAT INFO ---"
loginctl seat-status seat0 2>/dev/null | head -15 || echo "No seat info available"
echo ""

echo "======================================"
echo "DIAGNOSTIC COMPLETE"
echo "======================================"
