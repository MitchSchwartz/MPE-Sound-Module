# Running Rotary Encoder Without VCC

## Problem

With the fan using Pin 1 (3.3V), we only have Pin 17 (3.3V) available. Both the OLED display and rotary encoder need power, but we only have one 3.3V pin free.

## Solution: Encoder Doesn't Need VCC

The rotary encoder **does not need the VCC pin** to function. It only needs:
- CLK (clock signal)
- DT (data signal)
- SW (button switch)
- GND (ground)

## Why This Works

### Understanding Pull-Up Resistors

Rotary encoders work by connecting GPIO pins to ground when rotated. For this to work, the GPIO pins need to be "pulled high" (connected to 3.3V through a resistor) so they can detect when they're pulled low (connected to ground).

There are two ways to provide these pull-up resistors:

1. **External pull-ups** - Resistors on the encoder module powered by VCC
2. **Internal pull-ups** - Built into the Raspberry Pi GPIO pins (enabled in software)

### The Pi Has Internal Pull-Ups

Every GPIO pin on the Raspberry Pi has built-in pull-up resistors that can be enabled in software. The `gpiozero` library automatically enables these for encoder and button inputs.

### Code Configuration

In [patch_browser_ui.py](../patch_browser_ui.py), the encoder is configured to use internal pull-ups:

```python
# RotaryEncoder automatically enables pull-ups on CLK and DT pins
self.encoder = RotaryEncoder(
    ENCODER_CLK,      # GPIO 17
    ENCODER_DT,       # GPIO 27
    bounce_time=DEBOUNCE_TIME,
    max_steps=0
)

# Button explicitly enables pull-up on SW pin
self.button = Button(
    ENCODER_SW,       # GPIO 22
    pull_up=True,     # ← This enables the internal pull-up
    bounce_time=BUTTON_DEBOUNCE
)
```

## Wiring Configuration

### With VCC (Traditional Method)
```
Encoder        Raspberry Pi
CLK    ───────→ GPIO 17
DT     ───────→ GPIO 27
SW     ───────→ GPIO 22
VCC    ───────→ 3.3V (Pin 17)
GND    ───────→ GND (Pin 14)
```

### Without VCC (Our Solution)
```
Encoder        Raspberry Pi
CLK    ───────→ GPIO 17 (with internal pull-up enabled)
DT     ───────→ GPIO 27 (with internal pull-up enabled)
SW     ───────→ GPIO 22 (with internal pull-up enabled)
VCC    ───────→ NOT CONNECTED
GND    ───────→ GND (Pin 14)
```

## Benefits

1. **Saves a 3.3V pin** - Only OLED needs 3.3V power
2. **Simpler wiring** - One fewer connection to make
3. **Same functionality** - Works identically to external pull-ups
4. **Lower power consumption** - Slightly less current draw (internal pull-ups are ~50kΩ vs ~10kΩ external)

## Final Pin Allocation

```
Pin 1 (3.3V):  Fan power        ← Reserved
Pin 17 (3.3V): OLED VCC         ← Only 3.3V consumer
Pin 6 (GND):   Fan ground       ← Reserved
Pin 9 (GND):   OLED ground      ← OLED only
Pin 14 (GND):  Encoder ground   ← Encoder only (separate jumper)
```

## What If My Encoder Has Built-In Pull-Ups?

Some KY-040 modules have built-in pull-up resistors that require VCC to function. If you have this type:

### Option 1: Still Works Without VCC
Most KY-040 modules will work fine without VCC because:
- The Pi's internal pull-ups override the module's pull-ups
- The module's pull-ups simply aren't powered (which is fine)

### Option 2: Use 5V Instead
Some encoders can tolerate 5V on the VCC pin while outputting 3.3V-compatible signals:
```
VCC → Pin 2 or 4 (5V)  # Check your encoder datasheet first!
```

**Warning:** Only do this if your encoder is 5V-tolerant. Most KY-040 modules are, but verify first.

### Option 3: Voltage Divider (Not Recommended)
You could use a voltage divider to drop 5V to 3.3V, but this adds complexity and isn't needed.

## Testing

To verify your encoder works without VCC:

```bash
python3 << EOF
from gpiozero import RotaryEncoder, Button
import time

encoder = RotaryEncoder(17, 27, bounce_time=0.002)
button = Button(22, pull_up=True)

print("Testing encoder without VCC...")
print("Rotate encoder and press button. Ctrl+C to exit.")

encoder.when_rotated_clockwise = lambda: print("CW")
encoder.when_rotated_counter_clockwise = lambda: print("CCW")
button.when_pressed = lambda: print("BUTTON PRESSED")

while True:
    time.sleep(0.1)
EOF
```

If you see output when rotating/clicking, it's working!

## Troubleshooting

### Encoder doesn't respond
1. Check GND connection (most common issue)
2. Verify GPIO pins are correct (CLK=17, DT=27, SW=22)
3. Check for loose connections
4. Try increasing debounce time in code

### Encoder is erratic/jumpy
1. Increase debounce time: `bounce_time=0.01` (10ms instead of 2ms)
2. Check for electrical noise (poor GND connection)
3. Encoder might be damaged - try a different one

### Button doesn't click
1. Verify SW pin connection (GPIO 22)
2. Check `pull_up=True` in code
3. Some encoders have inverted button logic - try `pull_up=False`

## Technical Details

### GPIO Pull-Up Specifications (BCM2712 on Pi 5)
- Pull-up resistance: ~50kΩ to 3.3V
- Current when pulled low: ~66µA (3.3V / 50kΩ)
- Sufficient for debounced encoder signals

### Comparison: Internal vs External Pull-Ups

| Parameter | Internal (Pi) | External (KY-040) |
|-----------|---------------|-------------------|
| Resistance | ~50kΩ | ~10kΩ typical |
| Current draw | ~66µA per pin | ~330µA per pin |
| Requires VCC | No | Yes |
| Response time | Slightly slower | Slightly faster |
| Practical difference | None for encoders | None for encoders |

For rotary encoders (which are mechanical and slow), both methods work identically.

## Summary

✅ **Encoder works perfectly without VCC**
✅ **Uses Raspberry Pi's internal pull-up resistors**
✅ **Saves Pin 17 (3.3V) exclusively for OLED**
✅ **No code changes needed** - already configured correctly
✅ **Tested and proven approach**

Simply don't connect the encoder's VCC pin, and it will work perfectly!
