#!/bin/bash
# Test if Surge XT is receiving sustain pedal messages

echo "=== Surge XT Sustain Pedal Test ==="
echo ""

# Check if Surge is running
if ! pgrep -f surge-xt-cli > /dev/null; then
    echo "ERROR: Surge XT CLI is not running!"
    echo "Start it with: sudo systemctl start surge-xt-cli"
    exit 1
fi

echo "✓ Surge XT CLI is running"
echo ""

# Check Surge log for MIDI CC messages
echo "Recent Surge MIDI activity (last 20 lines):"
echo "---"
if [ -f ~/surge-cli.log ]; then
    tail -20 ~/surge-cli.log | grep -i "midi\|control\|cc\|sustain" || echo "No MIDI CC activity in log"
else
    echo "No Surge log file found at ~/surge-cli.log"
fi

echo ""
echo "---"
echo ""
echo "To test sustain:"
echo "1. Make sure pedal bridge is running: sudo python3 ~/MPE-Module/scripts/pedal-to-midi.py"
echo "2. Play some notes on your Seaboard"
echo "3. Press and hold Pedal 1 (should sustain the notes)"
echo "4. Release pedal (notes should stop)"
echo ""
echo "If sustain doesn't work, Surge may need CC 64 to be mapped."
echo "You can check MIDI Learn settings via VNC on the Surge GUI"
