#!/usr/bin/env python3
"""Test script to monitor raw encoder events and count pulses per detent"""

import evdev
from evdev import InputDevice, ecodes
import sys

device_path = '/dev/input/event5'

try:
    device = InputDevice(device_path)
    print(f"Monitoring: {device.name} at {device_path}")
    print(f"Capabilities: {device.capabilities()}")
    print("\nRotate encoder slowly, one detent at a time.")
    print("Watch the event.value - it should be +1 (CW) or -1 (CCW)")
    print("Count how many events you see per physical click/detent.")
    print("Press Ctrl+C to stop.\n")

    event_count = 0

    for event in device.read_loop():
        if event.type == ecodes.EV_REL:
            if event.code in (ecodes.REL_X, ecodes.REL_DIAL):
                event_count += 1
                event_type = "REL_X" if event.code == ecodes.REL_X else "REL_DIAL"
                direction = "CW" if event.value > 0 else "CCW"
                print(f"Event #{event_count}: {event_type} = {event.value:+d} ({direction})")

except KeyboardInterrupt:
    print(f"\nStopped. Total events: {event_count}")
except PermissionError:
    print(f"Permission denied. Run with: sudo python3 {sys.argv[0]}")
except FileNotFoundError:
    print(f"Device not found: {device_path}")
    print("Available devices:")
    for path in evdev.list_devices():
        dev = InputDevice(path)
        print(f"  {path}: {dev.name}")
