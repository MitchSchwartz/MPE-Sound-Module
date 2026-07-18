#!/usr/bin/env python3
"""
Pi-Surge-MPE Encoder Controller

Maps 5 rotary encoders (KY-040) to MIDI CC messages for controlling Surge XT.

Encoder mapping:
1. Category Navigation - MIDI CC 20 (increment/decrement category)
2. Patch Navigation    - MIDI CC 21 (increment/decrement patch within category)
3. Volume              - MIDI CC 7 (master volume)
4. Spare 1             - MIDI CC 1 (mod wheel / assignable)
5. Spare 2             - MIDI CC 74 (filter cutoff / assignable)

Each encoder has:
- CLK/DT pins for rotation detection
- SW pin for button press (not used in v1)
"""

import time
import signal
import sys
from gpiozero import RotaryEncoder, Button
import rtmidi

# GPIO Pin Configuration
# Format: (CLK, DT, SW)
ENCODER_PINS = {
    'category': (17, 27, 22),
    'patch': (23, 24, 25),
    'volume': (5, 6, 13),
    'spare1': (19, 26, 16),
    'spare2': (20, 21, 12),
}

# MIDI CC Mapping
MIDI_CC = {
    'category': 20,  # Category navigation
    'patch': 21,     # Patch navigation
    'volume': 7,     # Standard MIDI volume
    'spare1': 1,     # Mod wheel
    'spare2': 74,    # Filter cutoff (standard)
}

# Encoder value ranges
# Category/patch use relative increments (64=center, <64=down, >64=up)
# Volume/spare use absolute values (0-127)
VALUE_RANGES = {
    'category': {'min': 0, 'max': 127, 'default': 64, 'mode': 'relative'},
    'patch': {'min': 0, 'max': 127, 'default': 64, 'mode': 'relative'},
    'volume': {'min': 0, 'max': 127, 'default': 100, 'mode': 'absolute'},
    'spare1': {'min': 0, 'max': 127, 'default': 64, 'mode': 'absolute'},
    'spare2': {'min': 0, 'max': 127, 'default': 64, 'mode': 'absolute'},
}

# MIDI Configuration
MIDI_CHANNEL = 0  # Channel 1 (0-indexed)


class EncoderController:
    def __init__(self):
        self.encoders = {}
        self.buttons = {}
        self.values = {}
        self.midi_out = None
        self.running = True

        # Initialize MIDI output
        self._init_midi()

        # Initialize encoders
        self._init_encoders()

        print("Encoder Controller initialized")
        print("Press Ctrl+C to exit\n")

    def _init_midi(self):
        """Initialize MIDI output port"""
        self.midi_out = rtmidi.MidiOut()

        # List available MIDI ports
        available_ports = self.midi_out.get_ports()
        print(f"Available MIDI ports: {available_ports}")

        # Try to find Surge XT or use first available port
        surge_port = None
        for i, port in enumerate(available_ports):
            if 'Surge' in port or 'MIDI' in port:
                surge_port = i
                break

        if surge_port is not None:
            self.midi_out.open_port(surge_port)
            print(f"Connected to MIDI port: {available_ports[surge_port]}\n")
        elif len(available_ports) > 0:
            self.midi_out.open_port(0)
            print(f"Connected to MIDI port: {available_ports[0]}\n")
        else:
            # Open virtual port if no ports available
            self.midi_out.open_virtual_port("PiSurge Encoders")
            print("Opened virtual MIDI port: PiSurge Encoders\n")

    def _init_encoders(self):
        """Initialize rotary encoders and buttons"""
        for name, pins in ENCODER_PINS.items():
            clk_pin, dt_pin, sw_pin = pins

            # Create rotary encoder
            encoder = RotaryEncoder(clk_pin, dt_pin, bounce_time=0.002)
            encoder.when_rotated_clockwise = lambda e=name: self._on_rotate_cw(e)
            encoder.when_rotated_counter_clockwise = lambda e=name: self._on_rotate_ccw(e)

            self.encoders[name] = encoder

            # Create button (for future use)
            button = Button(sw_pin, pull_up=True, bounce_time=0.05)
            button.when_pressed = lambda e=name: self._on_button_press(e)

            self.buttons[name] = button

            # Initialize value
            self.values[name] = VALUE_RANGES[name]['default']

            print(f"Encoder '{name}' initialized on GPIO {clk_pin}/{dt_pin}/{sw_pin}")

    def _on_rotate_cw(self, encoder_name):
        """Handle clockwise rotation"""
        config = VALUE_RANGES[encoder_name]

        if config['mode'] == 'relative':
            # For category/patch navigation, send single increment
            value = 65  # Just above center (64) = increment by 1
            self.values[encoder_name] = value
        else:
            # For absolute controls, increment value
            self.values[encoder_name] = min(
                config['max'],
                self.values[encoder_name] + 1
            )
            value = self.values[encoder_name]

        self._send_midi_cc(encoder_name, value)
        print(f"{encoder_name:10s} CW  -> CC {MIDI_CC[encoder_name]:3d} = {value:3d}")

    def _on_rotate_ccw(self, encoder_name):
        """Handle counter-clockwise rotation"""
        config = VALUE_RANGES[encoder_name]

        if config['mode'] == 'relative':
            # For category/patch navigation, send single decrement
            value = 63  # Just below center (64) = decrement by 1
            self.values[encoder_name] = value
        else:
            # For absolute controls, decrement value
            self.values[encoder_name] = max(
                config['min'],
                self.values[encoder_name] - 1
            )
            value = self.values[encoder_name]

        self._send_midi_cc(encoder_name, value)
        print(f"{encoder_name:10s} CCW -> CC {MIDI_CC[encoder_name]:3d} = {value:3d}")

    def _on_button_press(self, encoder_name):
        """Handle button press (future use)"""
        print(f"{encoder_name:10s} BUTTON PRESSED")
        # Future: Could reset to default, toggle mute, etc.

    def _send_midi_cc(self, encoder_name, value):
        """Send MIDI CC message"""
        cc_number = MIDI_CC[encoder_name]

        # MIDI CC message: [status_byte, cc_number, value]
        # Status byte: 0xB0 + channel (0xB0 = CC on channel 1)
        status_byte = 0xB0 + MIDI_CHANNEL
        message = [status_byte, cc_number, value]

        self.midi_out.send_message(message)

    def run(self):
        """Main loop"""
        try:
            while self.running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            self.cleanup()

    def cleanup(self):
        """Clean up resources"""
        print("Cleaning up...")

        # Close encoders
        for encoder in self.encoders.values():
            encoder.close()

        # Close buttons
        for button in self.buttons.values():
            button.close()

        # Close MIDI
        if self.midi_out:
            del self.midi_out

        print("Cleanup complete")


def signal_handler(sig, frame):
    """Handle SIGTERM for systemd service"""
    print("\nReceived shutdown signal")
    sys.exit(0)


if __name__ == '__main__':
    # Register signal handler for clean shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    print("=== Pi-Surge-MPE Encoder Controller ===\n")

    controller = EncoderController()
    controller.run()
