# Hardware Wiring Guide - Patch Browser UI

## Overview

This guide covers the wiring for the patch browser UI system with:
- 1x 128x64 I2C OLED Display (1.3 inch)
- 1x Rotary Encoder (KY-040 or similar)
- Raspberry Pi 5

**IMPORTANT:** There is already a fan connected to physical pins 1 (+5V) and 6 (GND). Avoid using these pins.

## Components Required

### 1. OLED Display (1.3" I2C)
- **Type:** SH1106 or SSD1306 controller
- **Resolution:** 128x64 pixels
- **Interface:** I2C (4 pins: VCC, GND, SCL, SDA)
- **Color:** Blue (monochrome)
- **Voltage:** 3.3V or 5V (3.3V recommended for direct Pi connection)

### 2. Rotary Encoder
- **Type:** KY-040 or equivalent
- **Pins:** 5 pins (CLK, DT, SW, +, GND)
- **Voltage:** 3.3V-5V
- **Features:** Quadrature encoder with push button

## Raspberry Pi 5 GPIO Pinout Reference

```
        3.3V [ 1] [ 2] 5V   ← FAN CONNECTED (DO NOT USE)
   I2C1 SDA [ 3] [ 4] 5V
   I2C1 SCL [ 5] [ 6] GND  ← FAN CONNECTED (DO NOT USE)
             [ 7] [ 8]
         GND [ 9] [10]
 ENC_CLK/17 [11] [12]
 ENC_DT /27 [13] [14] GND
 ENC_SW /22 [15] [16]
    3.3V    [17] [18]
            [19] [20] GND
            [21] [22]
            [23] [24]
        GND [25] [26]
            ... (additional pins not shown)
```

## Wiring Connections

### OLED Display I2C Wiring

| OLED Pin | Description | Connect To | Pi Physical Pin | Pi GPIO |
|----------|-------------|------------|-----------------|---------|
| VCC      | Power 3.3V  | 3.3V       | Pin 17          | N/A     |
| GND      | Ground      | GND        | Pin 9, 14, or 20| N/A     |
| SCL      | I2C Clock   | I2C1 SCL   | Pin 5           | GPIO 3  |
| SDA      | I2C Data    | I2C1 SDA   | Pin 3           | GPIO 2  |

**Notes:**
- Use Pin 17 for 3.3V (NOT Pin 1 - fan is using it)
- Use any available GND pin EXCEPT Pin 6 (fan is using it)
- Recommended GND: Pin 9 or Pin 14
- I2C address is typically `0x3C` (verify with `i2cdetect -y 1`)

### Rotary Encoder Wiring

| Encoder Pin | Description    | Connect To | Pi Physical Pin | Pi GPIO  |
|-------------|----------------|------------|-----------------|----------|
| CLK         | Clock signal   | GPIO 17    | Pin 11          | GPIO 17  |
| DT          | Data signal    | GPIO 27    | Pin 13          | GPIO 27  |
| SW          | Button switch  | GPIO 22    | Pin 15          | GPIO 22  |
| + (VCC)     | Power 3.3V     | **NOT CONNECTED** | -        | -        |
| GND         | Ground         | GND        | Pin 9, 14, or 20| N/A      |

**Notes:**
- **VCC pin is NOT needed** - Pi's internal pull-ups are used instead (configured in software)
- This saves a 3.3V pin for other uses (only OLED needs 3.3V power)
- GND connection is required
- `gpiozero` library automatically enables pull-up resistors on GPIO pins

## Complete Wiring Diagram (ASCII)

```
┌─────────────────────────────────────────────────────────────┐
│                    Raspberry Pi 5 GPIO                      │
├──────────┬──────────┬──────────────────────────────────────┤
│ Physical │ GPIO     │ Connection                            │
├──────────┼──────────┼──────────────────────────────────────┤
│ Pin 1    │ 3.3V     │ *** FAN ONLY (DO NOT USE) ***        │
│ Pin 3    │ GPIO 2   │ → OLED SDA                           │
│ Pin 5    │ GPIO 3   │ → OLED SCL                           │
│ Pin 6    │ GND      │ *** FAN ONLY (DO NOT USE) ***        │
│ Pin 9    │ GND      │ → OLED GND + Encoder GND (shared)    │
│ Pin 11   │ GPIO 17  │ → Encoder CLK                        │
│ Pin 13   │ GPIO 27  │ → Encoder DT                         │
│ Pin 15   │ GPIO 22  │ → Encoder SW                         │
│ Pin 17   │ 3.3V     │ → OLED VCC ONLY                      │
└──────────┴──────────┴──────────────────────────────────────┘

┌──────────────────┐                    ┌────────────────────┐
│  OLED Display    │                    │  Rotary Encoder    │
│  (128x64 I2C)    │                    │  (KY-040)          │
├──────────────────┤                    ├────────────────────┤
│ VCC → Pin 17     │                    │ CLK → Pin 11       │
│ GND → Pin 9      │◄───── shared ─────►│ DT  → Pin 13       │
│ SCL → Pin 5      │       (GND)        │ SW  → Pin 15       │
│ SDA → Pin 3      │                    │ +   → NOT CONNECTED│
└──────────────────┘                    │ GND → Pin 9        │
                                        └────────────────────┘

┌──────────────────┐
│  Fan (existing)  │
├──────────────────┤
│ +5V → Pin 1      │
│ GND → Pin 6      │
└──────────────────┘

POWER USAGE:
  Pin 1 (3.3V): Fan only
  Pin 17 (3.3V): OLED only
  Encoder: Uses NO power pin (internal pull-ups)
```

## Physical Pin Layout Summary

```
     ┌─────────────────────────────┐
     │ [1]  3.3V ════════ FAN ONLY │ (Do not use)
     │ [2]  5V                     │
     │ [3]  GPIO 2 ═══════ OLED SDA│
     │ [4]  5V                     │
     │ [5]  GPIO 3 ═══════ OLED SCL│
     │ [6]  GND ══════════ FAN ONLY│ (Do not use)
     │ [7]  GPIO 4                 │
     │ [8]  GPIO 14                │
     │ [9]  GND ══════════ SHARED  │─── OLED + Encoder GND
     │ [10] GPIO 15                │
     │ [11] GPIO 17 ══════ ENC CLK │
     │ [12] GPIO 18                │
     │ [13] GPIO 27 ══════ ENC DT  │
     │ [14] GND (alternate)        │
     │ [15] GPIO 22 ══════ ENC SW  │
     │ [16] GPIO 23                │
     │ [17] 3.3V ═════════ OLED VCC│ (OLED only, encoder needs no power)
     │ [18] GPIO 24                │
     │ ...                         │
     └─────────────────────────────┘
```

## Verification Steps

### 1. Check I2C Connection

After wiring the OLED, verify I2C is detected:

```bash
# Enable I2C if not already enabled
sudo raspi-config
# Navigate to: Interface Options → I2C → Enable

# Scan for I2C devices
sudo i2cdetect -y 1
```

Expected output:
```
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
00:          -- -- -- -- -- -- -- -- -- -- -- -- --
10: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
20: -- -- -- -- -- -- -- -- -- -- -- -- 3c -- -- --
30: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
...
```

The `3c` confirms the OLED is detected at address 0x3C.

### 2. Test Encoder GPIO

Test encoder pins with Python:

```bash
python3 << EOF
from gpiozero import RotaryEncoder, Button
import time

# Test encoder
encoder = RotaryEncoder(17, 27, bounce_time=0.002)
button = Button(22, pull_up=True)

print("Rotate encoder and press button. Ctrl+C to exit.")

encoder.when_rotated_clockwise = lambda: print("CW")
encoder.when_rotated_counter_clockwise = lambda: print("CCW")
button.when_pressed = lambda: print("BUTTON")

while True:
    time.sleep(0.1)
EOF
```

### 3. Test OLED Display

```bash
python3 << EOF
from luma.core.interface.serial import i2c
from luma.oled.device import sh1106
from luma.core.render import canvas

serial = i2c(port=1, address=0x3C)
device = sh1106(serial)

with canvas(device) as draw:
    draw.text((10, 20), "Hello, Pi-Surge!", fill="white")

import time
time.sleep(5)
EOF
```

## Troubleshooting

### OLED Not Detected
- Check I2C is enabled: `sudo raspi-config`
- Verify wiring: SDA to Pin 3, SCL to Pin 5
- Check power: 3.3V to VCC, GND to GND
- Try alternate I2C address: `0x3D` instead of `0x3C`
- Some modules require 5V power (check module specs)

### Encoder Not Responding
- Verify GPIO pins: CLK=17, DT=27, SW=22
- Check power connections
- Try increasing debounce time
- Some encoders need pull-up resistors (most modules have them built-in)

### Display Shows Garbage
- Wrong driver: Try `ssd1306` instead of `sh1106` or vice versa
- Check if display needs SPI instead of I2C
- Verify I2C speed (try adding `i2c_baudrate=400000` to `/boot/config.txt`)

### Shared Power Issues
- If both devices don't work, check 3.3V power supply capacity
- Pi 5 can supply ~500mA on 3.3V rail (more than enough for OLED + encoder)
- Ensure good ground connection

## Bill of Materials (BOM)

| Item | Quantity | Specs | Approx Cost |
|------|----------|-------|-------------|
| OLED Display | 1 | 1.3" 128x64 I2C SH1106/SSD1306 | $5-8 |
| Rotary Encoder | 1 | KY-040 or equivalent | $2-5 |
| Jumper Wires | 10 | Female-to-female or male-to-female | $2-5 |
| Breadboard (optional) | 1 | For prototyping | $3-5 |
| **Total** | | | **~$12-23** |

## Software Dependencies

Install required Python packages:

```bash
# Install system dependencies
sudo apt-get update
sudo apt-get install -y python3-pip python3-pil i2c-tools

# Install Python libraries
pip3 install luma.oled gpiozero RPi.GPIO pillow
```

## Pin Reservation Summary

**Used Pins:**
- Pin 1 (3.3V) - Fan power
- Pin 3 (GPIO 2) - OLED SDA
- Pin 5 (GPIO 3) - OLED SCL
- Pin 6 (GND) - Fan ground
- Pin 9 (GND) - OLED + Encoder ground (shared)
- Pin 11 (GPIO 17) - Encoder CLK
- Pin 13 (GPIO 27) - Encoder DT
- Pin 15 (GPIO 22) - Encoder SW
- Pin 17 (3.3V) - OLED + Encoder power (shared)

**Available for Future Expansion:**
- GPIOs: 4, 7, 8-11, 13-16, 18-21, 23-27
- Power: Pin 2, 4 (5V); Pin 1 (if fan removed)
- Ground: Pins 14, 20, 25, 30, 34, 39

## Safety Notes

1. **Never short 3.3V to GND** - can damage the Pi
2. **Don't connect 5V to GPIO pins** - GPIOs are 3.3V tolerant only
3. **Fan pins are reserved** - Do not disconnect or use pins 1 and 6
4. **ESD precautions** - Use anti-static wrist strap when working with Pi
5. **Power off before wiring** - Always power down Pi before connecting/disconnecting

## Next Steps

After wiring is complete:
1. Test I2C communication with `i2cdetect`
2. Test encoder with simple GPIO script
3. Run `patch_browser_ui.py` to test full system
4. Configure systemd service for auto-start

See [PATCH_BROWSER_SETUP.md](PATCH_BROWSER_SETUP.md) for software setup and configuration.
