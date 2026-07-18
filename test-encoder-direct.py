#!/usr/bin/env python3
"""Test encoder and button directly"""
import sys
import time

print("Testing encoder via evdev...")
try:
    import evdev
    from evdev import InputDevice, categorize, ecodes
    
    device = InputDevice("/dev/input/by-path/platform-rotary@11-event")
    print(f"Device: {device.name}")
    print("Rotate the encoder now (will show 10 events then exit)...")
    
    count = 0
    for event in device.read_loop():
        if event.type == ecodes.EV_REL:
            print(f"Event {count}: type={event.type}, code={event.code}, value={event.value}")
            count += 1
            if count >= 10:
                break
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)

print("\nTesting button via gpiozero...")
try:
    from gpiozero import Button
    
    button = Button(22, pull_up=True)
    print("Press the button now (waiting 10 seconds)...")
    
    if button.wait_for_press(timeout=10):
        print("Button pressed!")
    else:
        print("No button press detected in 10 seconds")
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)

