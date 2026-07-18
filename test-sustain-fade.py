#!/usr/bin/env python3
"""
Test script for sustain fade functionality
Tests the SustainFadeController without needing physical pedals
"""

import time
import sys
import os

# Add scripts directory to path
scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts')
sys.path.insert(0, scripts_dir)

# Change to script directory to make relative imports work
original_dir = os.getcwd()
os.chdir(scripts_dir)

# Import the module
import importlib.util
spec = importlib.util.spec_from_file_location("pedal_to_osc", os.path.join(scripts_dir, "pedal-to-osc.py"))
pedal_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pedal_module)

# Get the classes
PedalConfig = pedal_module.PedalConfig
SustainFadeController = pedal_module.SustainFadeController

# Restore directory
os.chdir(original_dir)

class MockOSCClient:
    """Mock OSC client that prints instead of sending"""
    def __init__(self):
        self.messages = []

    def send_message(self, path, args):
        timestamp = time.time()
        self.messages.append((timestamp, path, args))
        print(f"  OSC: {path} {args} (CC value: {args[2]:.0f})")

def test_basic_fade():
    """Test basic fade-out functionality"""
    print("=" * 60)
    print("TEST 1: Basic Fade-Out")
    print("=" * 60)

    config = PedalConfig(sustain_fade_duration=1.0)  # Faster for testing
    mock_osc = MockOSCClient()
    controller = SustainFadeController(mock_osc, config)

    print("\n1. Pressing pedal (expect immediate CC 127)...")
    controller.pedal_pressed()

    print("\n2. Releasing pedal (expect fade over 1 second)...")
    start_time = time.time()
    controller.pedal_released()

    # Wait for fade to complete
    time.sleep(1.2)

    print(f"\n3. Fade completed in {time.time() - start_time:.2f} seconds")
    print(f"   Total OSC messages sent: {len(mock_osc.messages)}")

    # Show the fade sequence
    print("\n   Fade sequence:")
    for i, (ts, path, args) in enumerate(mock_osc.messages):
        if i > 0:
            delta = ts - mock_osc.messages[i-1][0]
            print(f"   {i}: CC {args[2]:.0f} (after {delta:.3f}s)")
        else:
            print(f"   {i}: CC {args[2]:.0f} (initial)")

    return True

def test_cancellation():
    """Test fade cancellation by re-pressing pedal"""
    print("\n" + "=" * 60)
    print("TEST 2: Fade Cancellation")
    print("=" * 60)

    config = PedalConfig(sustain_fade_duration=2.0)
    mock_osc = MockOSCClient()
    controller = SustainFadeController(mock_osc, config)

    print("\n1. Pressing pedal...")
    controller.pedal_pressed()

    print("\n2. Releasing pedal (starting fade)...")
    controller.pedal_released()

    print("\n3. Waiting 0.5 seconds...")
    time.sleep(0.5)

    print("\n4. Pressing pedal again (should cancel fade)...")
    msgs_before_cancel = len(mock_osc.messages)
    controller.pedal_pressed()

    print("\n5. Waiting another 2 seconds to confirm fade stopped...")
    time.sleep(2.0)

    print(f"\n6. Messages before cancel: {msgs_before_cancel}")
    print(f"   Total messages after cancel: {len(mock_osc.messages)}")
    print(f"   Last CC value sent: {mock_osc.messages[-1][2][2]:.0f}")

    if mock_osc.messages[-1][2][2] == 127:
        print("   ✓ SUCCESS: Fade was cancelled, pedal is sustaining (CC 127)")
        return True
    else:
        print("   ✗ FAIL: Expected CC 127 after cancellation")
        return False

def test_rapid_pumping():
    """Test rapid pedal press/release cycles"""
    print("\n" + "=" * 60)
    print("TEST 3: Rapid Pedal Pumping")
    print("=" * 60)

    config = PedalConfig(sustain_fade_duration=1.0)
    mock_osc = MockOSCClient()
    controller = SustainFadeController(mock_osc, config)

    print("\nRapidly pressing/releasing pedal 5 times...")
    for i in range(5):
        print(f"\n  Cycle {i+1}: Press -> Release")
        controller.pedal_pressed()
        time.sleep(0.1)
        controller.pedal_released()
        time.sleep(0.1)

    print(f"\nTotal messages sent: {len(mock_osc.messages)}")
    print("✓ SUCCESS: No crashes during rapid pumping")
    return True

def test_config_loading():
    """Test configuration file loading"""
    print("\n" + "=" * 60)
    print("TEST 4: Configuration Loading")
    print("=" * 60)

    config_path = os.path.join(
        os.path.dirname(__file__),
        'config',
        'pedal-config.json'
    )

    print(f"\n1. Loading config from: {config_path}")
    config = PedalConfig.load_from_file(config_path)

    print(f"\n2. Configuration loaded:")
    print(f"   - Fade Enabled: {config.sustain_fade_enabled}")
    print(f"   - Fade Duration: {config.sustain_fade_duration}s")
    print(f"   - Fade Curve: {config.sustain_fade_curve}")

    if config.sustain_fade_duration == 2.0 and config.sustain_fade_enabled:
        print("\n✓ SUCCESS: Config loaded correctly")
        return True
    else:
        print("\n✗ FAIL: Config values don't match expected defaults")
        return False

def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("SUSTAIN FADE CONTROLLER TEST SUITE")
    print("=" * 60)

    tests = [
        test_basic_fade,
        test_cancellation,
        test_rapid_pumping,
        test_config_loading,
    ]

    results = []
    for test in tests:
        try:
            result = test()
            results.append((test.__name__, result))
        except Exception as e:
            print(f"\n✗ ERROR in {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            results.append((test.__name__, False))

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")

    passed = sum(1 for _, r in results if r)
    total = len(results)
    print(f"\nPassed: {passed}/{total}")

    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())
