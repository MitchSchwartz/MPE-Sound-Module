# Surge XT CLI — Headless Setup

Build and run **Surge XT CLI** on a Raspberry Pi: headless MPE synth, auto MIDI connect, systemd on boot.

## What you get

- Auto MIDI connection — devices you plug in connect without manual setup
- MPE always enabled (48 semitone pitch bend range)
- Auto-starts on boot via systemd
- Direct ALSA to your USB audio interface (no GUI / X11)

## Verify services

```bash
ssh $PI_USER@$PI_HOST 'systemctl status surge-xt-cli'
ssh $PI_USER@$PI_HOST 'tail -f ~/surge-cli.log'
ssh $PI_USER@$PI_HOST 'sudo systemctl restart surge-xt-cli'
ssh $PI_USER@$PI_HOST 'sudo systemctl stop surge-xt-cli'
```

## Boot sequence

On power-on, the Pi will:

1. Boot Raspberry Pi OS Lite
2. Initialize audio
3. Start `surge-xt-cli.service`
4. Wait for MIDI devices (controller auto-connects when plugged in)

**No keyboard, mouse, monitor, or VNC needed for performance.**

---

# Patch Switching Methods

Surge XT CLI supports THREE ways to switch patches during performance:

## Method 1: MIDI Program Change (Simplest)

Send MIDI Program Change (PC) messages from your controller or a script.

**How it works:**
- Surge has numbered patches (0-127 in each bank)
- Send PC 0-127 to switch patches within current category
- Works great with MIDI foot controllers or custom scripts

**Example - Switch using Python:**
```python
import mido

# Open MIDI output to Surge
port = mido.open_output('Surge XT CLI')

# Switch to patch 5
port.send(mido.Message('program_change', program=5))
```

## Method 2: OSC (Open Sound Control)

Surge XT CLI has full OSC support for remote control.

**Start with OSC enabled:**
```bash
surge-xt-cli \
  --all-midi-inputs \
  --mpe-enable \
  --osc-in-port=8000 \
  --osc-out-port=9000
```

**OSC Commands:**
```
/patch/load <category> <patch>    # Load specific patch
/param/set <id> <value>           # Change parameter
/mpe/enable 1                     # Enable MPE
```

**Example - Control from Python:**
```python
from pythonosc import udp_client

client = udp_client.SimpleUDPClient("localhost", 8000)
client.send_message("/patch/load", ["Bass", "Acid Bass"])
```

## Method 3: Restart with Different `--init-patch`

Change the startup script to load a different patch on boot.

**Edit your Surge start script** (from `scripts/start-surge-cli.sh` after `configure-pi-paths.sh`):

```bash
INIT_PATCH="$MPE_SURGE_ROOT/resources/data/patches_3rdparty/Exquis MPE/Keys/Example.fxp"
```

Then restart:
```bash
sudo systemctl restart surge-xt-cli
```

---

# Building a Custom Preset Browser UI

## Architecture

```
┌─────────────────────────┐
│  Python Preset Browser  │
│  (Your Custom App)      │
│                         │
│  - Rotary encoder input │
│  - 1.3" OLED display    │
│  - Preset list UI       │
└───────────┬─────────────┘
            │
            │ MIDI Program Change
            │ or OSC Commands
            ▼
┌─────────────────────────┐
│  Surge XT CLI           │
│  (Background Service)   │
│  - Receives MIDI/OSC    │
│  - Switches patches     │
│  - Outputs audio        │
└─────────────────────────┘
```

## Hardware Needed

1. **Display**: 1.3" OLED (SSD1306 or SH1106, I2C)
   - Connect to Pi GPIO I2C pins
   - Resolution: 128x64 pixels
   - Cost: ~$5-10

2. **Rotary Encoders**: 2x KY-040 or similar
   - Encoder 1: Category selection (Bass, Keys, Pads, etc.)
   - Encoder 2: Patch selection within category
   - Connect to GPIO pins with pull-up resistors

## Software Stack

**Python Libraries:**
```bash
pip3 install luma.oled pillow python-rtmidi RPi.GPIO
```

**UI Framework:**
- `luma.oled` - OLED display control
- `RPi.GPIO` - Rotary encoder reading
- `mido` or `python-osc` - Communication with Surge

## Implementation Outline

### 1. Preset Scanner
```python
import os
import glob

def scan_surge_presets():
    """Scan Surge patch directories and build a category/patch tree"""
    base_dirs = [
        "$MPE_SURGE_ROOT/resources/data/patches_factory",
        "$MPE_SURGE_ROOT/resources/data/patches_3rdparty"
    ]

    presets = {}
    for base_dir in base_dirs:
        for category_dir in glob.glob(f"{base_dir}/*"):
            category = os.path.basename(category_dir)
            if category not in presets:
                presets[category] = []

            for patch_file in glob.glob(f"{category_dir}/*.fxp"):
                patch_name = os.path.basename(patch_file).replace('.fxp', '')
                presets[category].append({
                    'name': patch_name,
                    'path': patch_file
                })

    return presets
```

### 2. Rotary Encoder Handler
```python
import RPi.GPIO as GPIO

class RotaryEncoder:
    def __init__(self, pin_a, pin_b, callback):
        self.pin_a = pin_a
        self.pin_b = pin_b
        self.callback = callback
        self.last_state = None

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(pin_a, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(pin_b, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        GPIO.add_event_detect(pin_a, GPIO.BOTH, callback=self._edge_callback)
        GPIO.add_event_detect(pin_b, GPIO.BOTH, callback=self._edge_callback)

    def _edge_callback(self, channel):
        a_state = GPIO.input(self.pin_a)
        b_state = GPIO.input(self.pin_b)

        if (a_state, b_state) == (1, 0):
            self.callback(1)  # Clockwise
        elif (a_state, b_state) == (0, 1):
            self.callback(-1)  # Counter-clockwise
```

### 3. OLED Display Manager
```python
from luma.core.interface.serial import i2c
from luma.oled.device import sh1106
from PIL import Image, ImageDraw, ImageFont

class PresetDisplay:
    def __init__(self):
        serial = i2c(port=1, address=0x3C)
        self.device = sh1106(serial)
        self.font = ImageFont.load_default()

    def show_preset(self, category, patch, index, total):
        image = Image.new('1', (self.device.width, self.device.height))
        draw = ImageDraw.Draw(image)

        # Category name at top
        draw.text((0, 0), f"Category: {category}", font=self.font, fill=255)

        # Patch name in middle (large)
        draw.text((0, 20), patch, font=self.font, fill=255)

        # Index at bottom
        draw.text((0, 50), f"{index + 1}/{total}", font=self.font, fill=255)

        self.device.display(image)
```

### 4. Patch Switcher (MIDI)
```python
import mido

class PatchSwitcher:
    def __init__(self):
        # Find Surge MIDI port
        self.port = None
        for name in mido.get_output_names():
            if 'Surge' in name:
                self.port = mido.open_output(name)
                break

    def load_patch(self, patch_path):
        """
        NOTE: MIDI Program Change only works for numbered patches.
        For arbitrary .fxp files, you'll need to use OSC instead.
        """
        # This is a simplified example
        # Real implementation would need OSC or preset management
        pass
```

### 5. Main Application Loop
```python
def main():
    # Initialize hardware
    display = PresetDisplay()
    presets = scan_surge_presets()
    switcher = PatchSwitcher()

    # State
    categories = list(presets.keys())
    category_index = 0
    patch_index = 0

    def on_category_change(direction):
        nonlocal category_index
        category_index = (category_index + direction) % len(categories)
        update_display()

    def on_patch_change(direction):
        nonlocal patch_index
        category = categories[category_index]
        patches = presets[category]
        patch_index = (patch_index + direction) % len(patches)

        # Load the new patch
        patch_path = patches[patch_index]['path']
        switcher.load_patch(patch_path)
        update_display()

    def update_display():
        category = categories[category_index]
        patches = presets[category]
        patch = patches[patch_index]['name']
        display.show_preset(category, patch, patch_index, len(patches))

    # Setup encoders
    encoder1 = RotaryEncoder(17, 18, on_category_change)  # GPIO pins 17,18
    encoder2 = RotaryEncoder(22, 23, on_patch_change)     # GPIO pins 22,23

    # Initial display
    update_display()

    # Keep running
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        GPIO.cleanup()

if __name__ == '__main__':
    main()
```

## GPIO Pin Layout (Example)

```
Rotary Encoder 1 (Category):
  CLK → GPIO 17
  DT  → GPIO 18
  GND → GND
  VCC → 3.3V

Rotary Encoder 2 (Patch):
  CLK → GPIO 22
  DT  → GPIO 23
  GND → GND
  VCC → 3.3V

OLED Display (I2C):
  SDA → GPIO 2 (I2C SDA)
  SCL → GPIO 3 (I2C SCL)
  VCC → 3.3V
  GND → GND
```

## Important Notes for Preset Browser

### Patch Loading Limitation

**MIDI Program Change** only works with Surge's internal preset numbering system. To load arbitrary `.fxp` files by path, you have **two options**:

#### Option A: Use OSC (Recommended)
```python
from pythonosc import udp_client

client = udp_client.SimpleUDPClient("localhost", 8000)
client.send_message("/patch/load/file", ["/path/to/patch.fxp"])
```

You'll need to start Surge CLI with OSC enabled:
```bash
surge-xt-cli --all-midi-inputs --mpe-enable --osc-in-port=8000
```

#### Option B: Restart Surge with new `--init-patch`

Less elegant but works:
```python
import subprocess

def load_patch_via_restart(patch_path):
    subprocess.run(['sudo', 'systemctl', 'stop', 'surge-xt-cli'])

    # Update startup script
    script = f'''#!/bin/bash
SURGE_CLI="$HOME/surge/build/surge_xt_products/surge-xt-cli"
INIT_PATCH="{patch_path}"
AUDIO_DEVICE="0.22"

"$SURGE_CLI" --all-midi-inputs --mpe-enable --mpe-pitch-bend-range=48 \\
  --init-patch="$INIT_PATCH" --audio-interface="$AUDIO_DEVICE" --no-stdin \\
  >> "$HOME/surge-cli.log" 2>&1 &
'''

    with open(os.path.expanduser('~/MPE-Module/scripts/start-surge-cli.sh'), 'w') as f:
        f.write(script)

    subprocess.run(['sudo', 'systemctl', 'start', 'surge-xt-cli'])
```

**This causes a brief audio interruption but guarantees the patch loads.**

---

## Next Steps

1. **Test with Roli**: Plug in your Roli and verify MIDI auto-connects
2. **Test different patches**: Try switching patches via the startup script
3. **Order hardware**: Get your 1.3" OLED and rotary encoders
4. **Build the UI**: Start with the preset scanner and display code

---

## Files Created

**On the Pi (after configure-pi-paths.sh):**
- `$MPE_MODULE_REPO/scripts/start-surge-cli.sh` — Surge startup script
- `/etc/systemd/system/surge-xt-cli.service` — Auto-start service
- `$MPE_SURGE_LOG` — Runtime log (default: `~/surge-cli.log`)

---

## Questions?

When you return, test it by:
1. Rebooting the Pi
2. Plugging in the Roli
3. Playing - it should just work!

The system is now fully hands-off. No VNC, no GUI, no manual MIDI connection needed.
