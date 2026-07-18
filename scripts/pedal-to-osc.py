#!/usr/bin/env python3
"""
USB Foot Pedal to OSC Bridge
Converts 3-pedal USB footswitch to Surge XT OSC commands
"""

import evdev
import time
import sys
import threading
import json
import os
from dataclasses import dataclass, asdict

# OSC Configuration
OSC_HOST = '127.0.0.1'
OSC_PORT = 53280

# Pedal to Surge parameter mapping
# Supports both MIDI CC (via 'cc' key) and direct OSC params (via 'osc_path' key)
PEDAL_MAPPING = {
    30: {'name': 'Pedal 1 (Left)', 'multi': [
        {'cc': 91},  # Reverb
        {'osc_path': '/param/a/aeg/attack', 'value_on': 0.50, 'value_off': 0.0},  # Scene A attack
        {'osc_path': '/param/b/aeg/attack', 'value_on': 0.50, 'value_off': 0.0}   # Scene B attack
    ], 'desc': 'Reverb + Soft Attack'},
    48: {'name': 'Pedal 2 (Middle)', 'cc': 93, 'desc': 'Chorus Depth (CC 93)'},
    46: {'name': 'Pedal 3 (Right)', 'cc': 64, 'desc': 'Sustain'},
}

@dataclass
class PedalConfig:
    """Configuration for pedal behavior"""
    sustain_fade_enabled: bool = True
    sustain_fade_duration: float = 2.0  # seconds
    sustain_fade_curve: str = "linear"  # "linear" or "exponential"

    @classmethod
    def load_from_file(cls, config_path: str) -> 'PedalConfig':
        """Load config from JSON file, falling back to defaults"""
        config = cls()  # Start with defaults

        try:
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    user_config = json.load(f)
                    # Update defaults with user values
                    for key, value in user_config.items():
                        if hasattr(config, key) and not key.startswith('_'):
                            setattr(config, key, value)
        except Exception as e:
            print(f"   Warning: Could not load config from {config_path}: {e}")
            print(f"   Using default configuration")

        return config

    def save_to_file(self, config_path: str):
        """Save current config to JSON file"""
        try:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, 'w') as f:
                # Filter out private attributes
                config_dict = {k: v for k, v in asdict(self).items() if not k.startswith('_')}
                json.dump(config_dict, f, indent=2)
        except Exception as e:
            print(f"   Warning: Could not save config to {config_path}: {e}")


class SustainFadeController:
    """Manages sustain pedal fade-out behavior"""

    def __init__(self, osc_client, config: PedalConfig):
        self.osc_client = osc_client
        self.config = config
        self.fade_lock = threading.Lock()
        self.current_fade_thread = None
        self.cancel_fade_event = threading.Event()

    def get_fade_steps(self) -> list:
        """Generate CC values for fade curve based on config"""
        if self.config.sustain_fade_curve == "exponential":
            return [127, 90, 64, 45, 32, 22, 16, 11, 8, 5, 3, 2, 1, 0]
        else:  # faster initial decay, focus on smooth tail
            # Drop quickly at first, then spend time on gentle fade-out
            return [127, 85, 58, 38, 25, 16, 10, 6, 3, 1, 0]

    def pedal_pressed(self):
        """Handle pedal press - cancel any fade and send immediate 127"""
        with self.fade_lock:
            if self.current_fade_thread and self.current_fade_thread.is_alive():
                self.cancel_fade_event.set()
            self._send_cc_safe(127)

    def pedal_released(self):
        """Handle pedal release - start fade-out"""
        with self.fade_lock:
            # Cancel any previous fade
            if self.current_fade_thread and self.current_fade_thread.is_alive():
                self.cancel_fade_event.set()

            # Clear event for new fade
            self.cancel_fade_event.clear()

            # Start new fade thread
            self.current_fade_thread = threading.Thread(
                target=self._fade_worker,
                daemon=True,
                name="SustainFade"
            )
            self.current_fade_thread.start()

    def _fade_worker(self):
        """Background thread worker for fade-out"""
        steps = self.get_fade_steps()
        step_duration = self.config.sustain_fade_duration / (len(steps) - 1)

        # Skip first value (127) since we start from current state
        for cc_value in steps[1:]:
            if self.cancel_fade_event.is_set():
                return  # Fade cancelled

            self._send_cc_safe(cc_value)

            # Don't sleep after last step
            if cc_value > 0 and not self.cancel_fade_event.is_set():
                time.sleep(step_duration)

    def _send_cc_safe(self, value: int):
        """Send CC message with error handling"""
        try:
            self.osc_client.send_message(
                '/cc',
                [0.0, 64.0, float(value)]
            )
        except Exception as e:
            print(f"  ✗ Failed to send sustain CC {value}: {e}")


def find_footswitch():
    """Find the PCsensor FootSwitch keyboard device"""
    # Try stable by-id path first (survives reboots/reconnects)
    stable_path = '/dev/input/by-id/usb-PCsensor_FootSwitch-event-kbd'
    try:
        device = evdev.InputDevice(stable_path)
        return device
    except:
        pass

    # Fall back to searching all devices
    devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
    for device in devices:
        if 'FootSwitch' in device.name or 'footswitch' in device.name.lower():
            # Make sure it has keyboard capabilities
            caps = device.capabilities()
            if evdev.ecodes.EV_KEY in caps:
                return device

    return None

def main():
    print("=== USB Foot Pedal to Surge OSC Bridge ===\n")

    # Find foot pedal with retry logic
    print("1. Looking for foot pedal...")
    pedal = None
    retry_count = 0
    max_retries = 30  # Wait up to 30 seconds

    while not pedal and retry_count < max_retries:
        pedal = find_footswitch()
        if not pedal:
            if retry_count == 0:
                print("   Foot pedal not found, waiting for USB connection...")
            retry_count += 1
            time.sleep(1)

    if not pedal:
        print("ERROR: Foot pedal not found after 30 seconds!")
        print("\nAvailable input devices:")
        devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
        for device in devices:
            print(f"  - {device.name}")
        return 1

    print(f"   ✓ Found: {pedal.name}")
    print(f"   ✓ Path: {pedal.path}")

    # Setup OSC client
    print("\n2. Setting up OSC connection to Surge XT...")
    try:
        from pythonosc import udp_client
        osc_client = udp_client.SimpleUDPClient(OSC_HOST, OSC_PORT)
        print(f"   ✓ OSC client ready: {OSC_HOST}:{OSC_PORT}")
    except ImportError:
        print("   ✗ ERROR: python-osc not installed!")
        print("   Install with: pip3 install python-osc")
        return 1
    except Exception as e:
        print(f"   ✗ ERROR: {e}")
        return 1

    # Load pedal configuration
    config_path = os.path.join(
        os.path.dirname(__file__),
        '..',
        'config',
        'pedal-config.json'
    )
    pedal_config = PedalConfig.load_from_file(config_path)

    # Create sustain fade controller
    sustain_controller = SustainFadeController(osc_client, pedal_config)

    print("\n3. Pedal Configuration:")
    for key_code, mapping in PEDAL_MAPPING.items():
        if 'multi' in mapping:
            # Handle multi-command pedals
            commands = []
            for cmd in mapping['multi']:
                if 'cc' in cmd:
                    commands.append(f"CC {cmd['cc']}")
                elif 'osc_path' in cmd:
                    commands.append(f"OSC {cmd['osc_path']}")
            cmd_str = ' + '.join(commands)
            print(f"   - {mapping['name']}: {cmd_str} ({mapping['desc']})")
        elif 'cc' in mapping:
            cc_val = mapping['cc']
            if isinstance(cc_val, list):
                cc_str = ', '.join([f"CC {cc}" for cc in cc_val])
                print(f"   - {mapping['name']}: {cc_str} ({mapping['desc']})")
            else:
                print(f"   - {mapping['name']}: CC {cc_val} ({mapping['desc']})")
        else:
            print(f"   - {mapping['name']}: {mapping['osc_path']} ({mapping['desc']})")
    print(f"   - Values: 0 (released), 1.0 (pressed)")

    # Show sustain fade settings
    if pedal_config.sustain_fade_enabled:
        print(f"\n4. Sustain Fade Settings:")
        print(f"   - Fade Duration: {pedal_config.sustain_fade_duration}s")
        print(f"   - Fade Curve: {pedal_config.sustain_fade_curve}")
        print(f"   - Status: ENABLED (smooth release)")
    else:
        print(f"\n4. Sustain Fade: DISABLED (immediate release)")

    print("\n" + "="*60)
    print("PEDAL BRIDGE ACTIVE - Press pedals to control Surge")
    print("Press Ctrl+C to stop")
    print("="*60 + "\n")

    # Track pedal states to avoid duplicate messages
    pedal_states = {}

    try:
        # Grab exclusive access to prevent pedal from typing
        pedal.grab()

        # Read pedal events
        for event in pedal.read_loop():
            # Only process key events
            if event.type == evdev.ecodes.EV_KEY:
                key_code = event.code

                # Check if this is one of our mapped pedals
                if key_code in PEDAL_MAPPING:
                    mapping = PEDAL_MAPPING[key_code]

                    # Get state (1 = pressed, 0 = released, 2 = held)
                    if event.value == 1:
                        value = 127  # MIDI CC value for ON
                        state = "PRESSED"
                    elif event.value == 0:
                        value = 0  # MIDI CC value for OFF
                        state = "RELEASED"
                    else:
                        continue  # Ignore held state

                    # Only send if state changed
                    if pedal_states.get(key_code) != value:
                        pedal_states[key_code] = value

                        try:
                            # Special handling for sustain pedal (CC 64) with fade
                            if 'cc' in mapping and mapping['cc'] == 64:
                                if event.value == 1:  # Pressed
                                    sustain_controller.pedal_pressed()
                                    print(f"[{mapping['name']} {state}]")
                                    print(f"  Sustain: IMMEDIATE ON (CC 127)")
                                    print()
                                elif event.value == 0:  # Released
                                    if pedal_config.sustain_fade_enabled:
                                        sustain_controller.pedal_released()
                                        print(f"[{mapping['name']} {state}]")
                                        print(f"  Sustain: FADING OUT over {pedal_config.sustain_fade_duration}s")
                                        print()
                                    else:
                                        # Immediate release if fade disabled
                                        osc_client.send_message('/cc', [0.0, 64.0, 0.0])
                                        print(f"[{mapping['name']} {state}]")
                                        print(f"  Sustain: IMMEDIATE OFF")
                                        print()
                            # Handle multi-command pedals
                            elif 'multi' in mapping:
                                print(f"[{mapping['name']} {state}]")
                                for cmd in mapping['multi']:
                                    if 'cc' in cmd:
                                        # Send MIDI CC
                                        channel = 0.0
                                        value_float = float(value)
                                        osc_client.send_message('/cc', [channel, float(cmd['cc']), value_float])
                                        print(f"  OSC: /cc {channel} {cmd['cc']} {value}")
                                    elif 'osc_path' in cmd:
                                        # Send OSC parameter with custom on/off values
                                        osc_path = cmd['osc_path']
                                        if event.value == 1:  # Pressed
                                            osc_value = cmd.get('value_on', 1.0)
                                        else:  # Released
                                            osc_value = cmd.get('value_off', 0.0)
                                        osc_client.send_message(osc_path, osc_value)
                                        print(f"  OSC: {osc_path} {osc_value:.2f}")
                                print(f"  Effect: {mapping['desc']} {'ON' if value == 127 else 'OFF'}")
                                print()
                            # Check if this is a MIDI CC or direct OSC parameter
                            elif 'cc' in mapping:
                                # Send OSC command as MIDI CC
                                # Format: /cc <channel> <cc_number> <value>
                                # Channel is 0-indexed (0 = channel 1)
                                # All values MUST be floats per OSC spec
                                channel = 0.0  # MIDI channel 1 (0-indexed)
                                value_float = float(value)

                                # Handle both single CC and multiple CCs (list)
                                cc_list = mapping['cc'] if isinstance(mapping['cc'], list) else [mapping['cc']]

                                for cc_number in cc_list:
                                    osc_client.send_message('/cc', [channel, float(cc_number), value_float])

                                # Display feedback
                                print(f"[{mapping['name']} {state}]")
                                if len(cc_list) > 1:
                                    cc_str = ', '.join([f"CC {cc}" for cc in cc_list])
                                    print(f"  OSC: {cc_str} = {value}")
                                else:
                                    print(f"  OSC: /cc {channel} {cc_list[0]} {value}")
                                print(f"  Effect: {mapping['desc']} {'ON' if value == 127 else 'OFF'}")
                                print()
                            else:
                                # Send direct OSC parameter command
                                # Format: /param/path <value>
                                # Value range: 0.0 to 1.0 for parameters
                                osc_path = mapping['osc_path']
                                value_normalized = float(value) / 127.0  # Normalize to 0.0-1.0
                                osc_client.send_message(osc_path, value_normalized)

                                # Display feedback
                                print(f"[{mapping['name']} {state}]")
                                print(f"  OSC: {osc_path} {value_normalized:.2f}")
                                print(f"  Effect: {mapping['desc']} {'ON' if value == 127 else 'OFF'}")
                                print()

                        except Exception as e:
                            print(f"  ✗ Failed to send OSC: {e}")

    except KeyboardInterrupt:
        print("\n\n=== Bridge Stopped ===")
        print("Foot pedal bridge has been stopped.")

    finally:
        pedal.close()

    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
