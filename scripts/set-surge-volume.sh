#!/bin/bash
# Set Surge XT master volume levels

VOLUME=${1:-0.8}  # Default to 0.8 (80%), or use first argument

echo "Setting Surge volume to $VOLUME (0.0 = silent, 1.0 = default, >1.0 = boost)"

# Install python-osc if needed
if ! python3 -c "import pythonosc" 2>/dev/null; then
    echo "Installing python-osc..."
    pip3 install python-osc --break-system-packages
fi

# Send volume commands via OSC
python3 << EOF
from pythonosc import udp_client
import sys

try:
    client = udp_client.SimpleUDPClient('127.0.0.1', 53280)

    volume = float($VOLUME)

    # Set volume for both scenes
    client.send_message('/param/a/amp/volume', volume)
    client.send_message('/param/b/amp/volume', volume)

    print(f"✓ Scene A volume set to {volume}")
    print(f"✓ Scene B volume set to {volume}")
    print()
    print("Note: Values > 1.0 will boost and may clip/distort")

except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
EOF
