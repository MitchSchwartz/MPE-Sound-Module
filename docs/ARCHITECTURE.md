# Pi-Surge-MPE Architecture

**Version**: 2.0 (Headless + Custom Display)
**Date**: 2025-12-26
**Status**: Design Phase

---

## Design Philosophy

**Modular, Headless-First, Purpose-Built**

This is NOT a general-purpose music workstation. It's a **dedicated MPE performance module** optimized for:
- Minimal boot time
- Low latency audio
- Reliable live performance
- Simple, focused control surface
- No GUI bloat

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     PERFORMANCE MODE                         │
│                   (Headless - No X11)                        │
└─────────────────────────────────────────────────────────────┘

┌──────────────────┐
│  Roli Seaboard   │
│   MPE Controller │
└────────┬─────────┘
         │ USB MIDI
         ▼
┌────────────────────┐         ┌──────────────────┐
│    Surge XT        │ ──ALSA─>│ Sound Blaster S3 │──> Audio Out
│   (Headless)       │         │   USB Interface  │
└────────┬───────────┘         └──────────────────┘
         │
         │ MIDI CC/PC
         │ (Preset switching)
         │
┌────────▼───────────┐
│  Preset Browser    │
│  Python App        │
│  - Read encoders   │
│  - Send MIDI       │
│  - Drive display   │
└────────┬───────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌─────────┐ ┌──────────────┐
│ 2x GPIO │ │ 1.3" Display │
│ Encoders│ │ (SPI/I2C)    │
│ - Cat   │ │ Shows:       │
│ - Patch │ │ - Category   │
└─────────┘ │ - Patch Name │
            └──────────────┘


┌─────────────────────────────────────────────────────────────┐
│                   CONFIGURATION MODE                         │
│              (VNC - Only When Needed)                        │
└─────────────────────────────────────────────────────────────┘

Windows PC ──VNC──> Pi (X11 on demand) ──> Surge XT GUI
                    - Configure presets
                    - Adjust effects
                    - MPE settings
                    - Then shutdown X11
```

---

## Component Breakdown

### 1. Audio Engine: Surge XT (Headless)

**Role**: Pure sound generation, no GUI during performance

**Configuration**:
- Runs directly with ALSA backend (no JACK overhead)
- Configured via files in `~/.config/surge-xt/`
- MPE enabled by default
- Listens for MIDI on all channels (MPE zones 2-16)
- Responds to MIDI Program Change for preset switching

**Boot Service**:
```ini
[Unit]
Description=Surge XT Synthesizer (Headless)
After=sound.target

[Service]
Type=simple
Environment="DISPLAY=:0"  # Minimal X for rendering
ExecStart=/usr/local/bin/Surge-XT --headless
Restart=on-failure

[Install]
WantedBy=default.target
```

### 2. Control Surface: Python Preset Browser

**Role**: User interface layer (encoders + display)

**Responsibilities**:
1. **Read GPIO encoders** (category, patch)
2. **Parse Surge preset library** (XML/folders on disk)
3. **Send MIDI PC/CC** to Surge for preset changes
4. **Update 1.3" display** with current selection

**File**: `~/pisurge/preset_browser.py`

**Libraries**:
- `gpiozero` - Encoder input
- `python-rtmidi` or `mido` - MIDI output
- `luma.oled` or `adafruit-circuitpython-ssd1306` - Display driver
- `Pillow` (PIL) - Display rendering

**Features**:
- Category browsing (Pads, Leads, Bass, Keys, FX, etc.)
- Patch browsing within category
- Wrap-around navigation
- Preset name display
- Fast updates (<100ms)

### 3. Display: 1.3" OLED/LCD

**Hardware Options**:
- **SSD1306 OLED** (128x64, I2C) - Monochrome, cheap, easy
- **ST7789 LCD** (240x240, SPI) - Color, higher res
- **SH1106 OLED** (128x64, I2C) - Similar to SSD1306

**Recommended**: SSD1306 (simple, reliable, low power)

**Display Layout** (128x64):
```
┌────────────────────┐
│ Category: PADS     │ <- 8px font
├────────────────────┤
│                    │
│   Warm Strings     │ <- 16px font (patch name)
│                    │
└────────────────────┘
```

**Future Enhancement** (240x240):
```
┌──────────────────────┐
│  Category: PADS      │
│  (12 / 45 presets)   │
│                      │
│   ┌──────────────┐   │
│   │              │   │
│   │ Warm Strings │   │
│   │              │   │
│   └──────────────┘   │
│                      │
│  Vol: ||||||||       │
│  Mod: |||||          │
└──────────────────────┘
```

### 4. Encoders: 2x KY-040 Rotary

**Wiring**:
```
Encoder 1 (Category):
- CLK -> GPIO 17
- DT  -> GPIO 27
- SW  -> GPIO 22 (button, future use)
- +   -> 3.3V
- GND -> GND

Encoder 2 (Patch):
- CLK -> GPIO 23
- DT  -> GPIO 24
- SW  -> GPIO 25 (button, future use)
- +   -> 3.3V
- GND -> GND
```

**Future Expansion** (Volume, Mod Wheel, etc.):
- Encoder 3-5 can control MIDI CC values
- Map to Surge parameters via MIDI learn

### 5. Configuration GUI: VNC (On-Demand)

**Purpose**: Deep Surge configuration when needed

**Setup**:
- Install `x11vnc` (lightweight)
- Only start when needed: `x11vnc -display :0`
- Connect from Windows via TightVNC/RealVNC
- Configure Surge: effects, modulation, fine-tuning
- Stop VNC when done

**NOT running during performance** - zero overhead

---

## Software Stack

### Operating System
- **Raspberry Pi OS Lite (64-bit)** - Minimal base
- **No desktop environment** - Headless
- **Minimal services** - Optimized boot

### Audio
- **ALSA Direct** - Surge → Sound Blaster S3
- No JACK (eliminates complexity, lower latency)
- Sample rate: 48kHz
- Buffer: 512 samples (~10.7ms latency)

### MIDI
- **ALSA MIDI** for Roli input
- **RTMIDI** for Python → Surge communication

### Display
- **Luma.OLED** library for SSD1306
- **Pillow (PIL)** for text/graphics rendering

### Boot Services
```
systemd user services:
- surge.service      # Surge XT headless
- preset-browser.service  # Encoder + display control
```

---

## Preset Management

### Surge Preset Structure

Surge presets live in: `~/.local/share/surge-xt/patches/`

**Organization**:
```
patches/
├── 01_Pads/
│   ├── 001_Warm_Strings.fxp
│   ├── 002_Soft_Pad.fxp
│   └── ...
├── 02_Leads/
│   ├── 001_Bright_Lead.fxp
│   └── ...
├── 03_Bass/
├── 04_Keys/
└── 05_FX/
```

**Preset Browser Logic**:
1. Scan `patches/` directory on startup
2. Build in-memory index: `{category: [preset_files]}`
3. Encoder 1 navigates categories
4. Encoder 2 navigates presets within category
5. On selection: Send MIDI Program Change to Surge

**MIDI Mapping**:
- Bank Select MSB (CC#0) = Category index
- Program Change (PC) = Preset index within category
- Alternative: OSC if Surge supports it

---

## Boot Sequence

**Target**: < 20 seconds to audio-ready

1. **Kernel boot** (~5s)
2. **systemd init** (~3s)
3. **Sound device ready** (~2s)
4. **Surge XT launch** (~5s)
   - Load default preset
   - Initialize audio engine
5. **Preset browser launch** (~2s)
   - Scan preset library
   - Initialize display
   - Start encoder polling
6. **Ready for input** (~17s total)

**Optimizations**:
- Disable unused services (bluetooth, wifi if wired)
- Parallel service startup
- Preload default preset
- Fast SD card (UHS-I/U3)

---

## Performance Targets

| Metric | Target | Stretch Goal |
|--------|--------|--------------|
| Boot time | < 20s | < 15s |
| Audio latency | < 15ms | < 10ms |
| CPU usage (idle) | < 30% | < 20% |
| CPU usage (playing) | < 70% | < 60% |
| Preset change time | < 500ms | < 200ms |
| Display update | < 100ms | < 50ms |
| Encoder responsiveness | No lag | Instant |

---

## Configuration Files

### `~/.config/surge-xt/SurgeXT.conf`
```ini
[audio]
backend=ALSA
device=hw:S3  # Sound Blaster S3
samplerate=48000
buffersize=512

[mpe]
enabled=1
pitchbend=48
mode=0  # Lower zone

[midi]
input=Seaboard BLOCK
channel=omni
```

### `~/pisurge/preset_browser.py`
```python
#!/usr/bin/env python3
"""
Pi-Surge-MPE Preset Browser
Headless control interface for Surge XT
"""

import time
from gpiozero import RotaryEncoder
from mido import Message, open_output
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306
from PIL import Image, ImageDraw, ImageFont

# Hardware setup
category_encoder = RotaryEncoder(17, 27, bounce_time=0.01)
patch_encoder = RotaryEncoder(23, 24, bounce_time=0.01)
display = ssd1306(i2c(port=1, address=0x3C))
midi_out = open_output('Surge XT')

# Preset library (loaded from disk)
presets = {
    "Pads": ["Warm Strings", "Soft Pad", ...],
    "Leads": ["Bright Lead", ...],
    ...
}

current_category = 0
current_patch = 0

def update_display():
    """Render current selection to OLED"""
    # ... PIL drawing code ...

def on_category_change():
    """Category encoder rotated"""
    # ... update category index, wrap around ...
    update_display()

def on_patch_change():
    """Patch encoder rotated"""
    # ... update patch index ...
    # Send MIDI Program Change to Surge
    midi_out.send(Message('program_change', program=current_patch))
    update_display()

# Main loop
category_encoder.when_rotated = on_category_change
patch_encoder.when_rotated = on_patch_change
update_display()

while True:
    time.sleep(0.1)
```

---

## VNC Configuration Mode

**When to use**:
- Initial Surge setup (MPE, audio routing)
- Preset creation/editing
- Effect parameter tuning
- Modulation routing

**Setup**:
```bash
# Install x11vnc
sudo apt install x11vnc

# Create password (one-time)
x11vnc -storepasswd ~/surge-vnc.pass

# Start X11 (minimal, no window manager needed)
startx -- :0 &

# Start VNC server
x11vnc -display :0 -passwd ~/surge-vnc.pass -forever -shared
```

**Connect from Windows**:
- Install TightVNC Viewer or RealVNC
- Connect to `surge.local:5900`
- Launch Surge XT
- Configure as needed
- Close VNC, stop X11

**Shutdown after config**:
```bash
killall Surge-XT
killall Xorg
```

---

## Data Flow Examples

### Example 1: Patch Change via Encoder

```
User rotates Patch encoder →
  RotaryEncoder (gpiozero) detects rotation →
    preset_browser.py increments patch index →
      Updates display: "Bright Lead" →
      Sends MIDI: Program Change #5 →
        Surge XT receives PC #5 →
          Loads preset "Bright Lead" →
            Audio changes (< 500ms)
```

### Example 2: MPE Performance

```
User plays Roli Seaboard →
  USB MIDI →
    ALSA MIDI Router →
      Surge XT receives MPE (Ch 2-16) →
        Pitch, Pressure, Timbre per note →
          Audio synthesis →
            ALSA →
              Sound Blaster S3 →
                Audio Output
```

### Example 3: VNC Configuration

```
User starts VNC session from Windows →
  x11vnc server on Pi :5900 →
    Windows VNC client shows Pi desktop →
      User launches Surge XT GUI →
        Configures effects chain →
        Saves preset →
      User closes Surge, disconnects VNC →
    New preset now available in preset_browser
```

---

## Directory Structure

```
/home/mitch/
├── pisurge/
│   ├── preset_browser.py       # Main control app
│   ├── display_renderer.py     # Display utilities
│   ├── preset_scanner.py       # Preset library parser
│   └── install.sh              # System setup
│
├── .config/
│   ├── surge-xt/
│   │   └── SurgeXT.conf        # Surge config
│   └── systemd/user/
│       ├── surge.service       # Surge auto-start
│       └── preset-browser.service
│
├── .local/share/surge-xt/
│   └── patches/                # Preset library
│       ├── 01_Pads/
│       ├── 02_Leads/
│       └── ...
│
└── surge-vnc.pass              # VNC password
```

---

## Hardware BOM (Updated)

| Component | Quantity | Purpose | Notes |
|-----------|----------|---------|-------|
| Raspberry Pi 4/5 | 1 | Main compute | 4GB+ RAM |
| Sound Blaster S3 | 1 | Audio output | USB, low latency |
| Roli Seaboard BLOCK | 1 | MPE input | USB MIDI |
| KY-040 Encoder | 2 | Category/Patch | Can expand to 5 |
| 1.3" OLED (SSD1306) | 1 | Display | I2C, 128x64 |
| MicroSD Card | 1 | Storage | 32GB+, UHS-I |
| USB-C Power | 1 | Power | 15W official |
| Jumper Wires | ~10 | GPIO wiring | Female-female |

**Total Cost**: ~$150 (excluding Roli)

---

## Future Enhancements

### Phase 1 (Post-MVP)
- **3 more encoders**: Volume, Mod Wheel, Filter Cutoff
- **Encoder push buttons**: Quick preset favorites
- **LED ring encoders**: Visual feedback

### Phase 2
- **Larger color display** (240x240 ST7789)
  - Waveform visualization
  - Preset thumbnails
  - CPU/temp monitoring
- **WiFi web UI**: Remote preset management from tablet
- **Preset tagging**: Favorites, setlists, genres

### Phase 3
- **Multi-synth layering**: Run 2+ Surge instances
- **Recording**: Capture performances to disk
- **Snapshot system**: Save/recall complete states
- **MIDI learn**: Map encoders to any Surge param

---

## Advantages Over Original Design

### ✅ Modular
- Audio engine (Surge) decoupled from UI (preset browser)
- Can swap components independently
- Easy to test/debug in isolation

### ✅ Lightweight
- No X11 during performance (zero overhead)
- No JACK (simpler, lower latency)
- Minimal Python app vs full desktop

### ✅ Custom Display
- Exactly what you need, nothing more
- Tailored font sizes, layout
- Future: animations, visualizations

### ✅ Maintainable
- Clear separation of concerns
- Config via files (versionable)
- VNC only when needed

### ✅ Extensible
- Add more encoders easily
- Swap display modules
- Add features to Python app (no Surge recompile)

---

## Disadvantages (Trade-offs)

### ⚠️ More Custom Code
- Have to write preset browser ourselves
- More Python to maintain
- Can't rely on Surge's built-in browser

**Mitigation**: Keep code simple, well-documented

### ⚠️ VNC Required for Deep Config
- Can't tweak on device directly
- Need Windows PC for detailed changes

**Mitigation**: Most changes rare; VNC works fine

### ⚠️ Display Limited
- 128x64 OLED is small
- Can't show complex Surge UI

**Mitigation**: Only need preset name; upgrade to 240x240 later

---

## Success Criteria

### Milestone 1: Headless Audio (CURRENT)
- [ ] Surge XT runs headless with ALSA
- [ ] Roli MPE input works (all axes)
- [ ] Sound output via S3 (no dropouts)
- [ ] Boot time < 20s
- [ ] CPU < 70% during playing

### Milestone 2: Preset Browser
- [ ] Python app scans preset library
- [ ] Encoders navigate categories/patches
- [ ] MIDI PC switches Surge presets
- [ ] Preset changes < 500ms

### Milestone 3: Display
- [ ] 1.3" OLED wired and working
- [ ] Shows category + patch name
- [ ] Updates < 100ms on encoder turn

### Milestone 4: VNC Config
- [ ] VNC server installed
- [ ] Can launch Surge GUI remotely
- [ ] Configure presets, save, test
- [ ] Shutdown cleanly

### v1.0 Complete
- All milestones done
- Reliable for 2-hour live set
- Boot unattended
- Zero crashes

---

## Implementation Roadmap

**Next Steps (Week 1)**:
1. Disable LightDM auto-start ✅
2. Test Surge XT headless with ALSA
3. Verify Roli MPE input
4. Benchmark performance

**Next Steps (Week 2)**:
1. Order 1.3" OLED display
2. Order 2x KY-040 encoders
3. Write preset_scanner.py
4. Write display_renderer.py

**Next Steps (Week 3)**:
1. Wire encoders + display
2. Write preset_browser.py
3. Test end-to-end workflow
4. Measure performance

**Next Steps (Week 4)**:
1. Set up VNC for config
2. Create/organize preset library
3. Boot optimization
4. Documentation updates

---

## Conclusion

This architecture achieves the project goals:

✅ **Fast boot** - Headless, minimal services
✅ **Low latency** - Direct ALSA, no overhead
✅ **Modular** - Decoupled components
✅ **Custom UI** - Exactly what you need
✅ **Maintainable** - Simple, clear design
✅ **Extensible** - Easy to add features

**Status**: Ready to implement! 🚀
