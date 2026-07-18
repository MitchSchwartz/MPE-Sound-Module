# Hardware Setup Guide

## Bill of Materials

### Core Components

| Item | Model | Notes | Est. Cost |
|------|-------|-------|-----------|
| SBC | Raspberry Pi 4B (4GB) or Pi 5 (4GB) | 4GB minimum, 8GB recommended | $55-75 |
| Audio Interface | Creative Sound Blaster S3 | USB, low-latency, good Linux support | $40 |
| MPE Controller | Roli Seaboard Block / Lightpad Block | USB MIDI, MPE compatible | $200-300 |
| Storage | MicroSD Card 32GB+ | Class 10 or better | $10 |
| Power Supply | Official Pi Power Supply | 5V 3A (Pi 4) or 5V 5A (Pi 5) | $8-12 |
| Case | Raspberry Pi case with fan | Good cooling essential | $10-20 |

### Control Interface

| Item | Qty | Model | Notes | Est. Cost |
|------|-----|-------|-------|-----------|
| Rotary Encoders | 5 | KY-040 | Common inexpensive encoder module | $10 |
| Display | 1 | 3.5" SPI TFT | For patch names (optional for milestone 1) | $15-25 |
| Enclosure | 1 | Custom/DIY | For final assembly | $20-50 |
| Knobs | 5 | 6mm shaft knobs | Visual/tactile feedback | $5-10 |
| Wire | - | Dupont jumper wires | Male-female for GPIO connections | $5 |

**Total Estimated Cost**: $380-520 (excluding MPE controller if you already own one)

## Raspberry Pi Model Selection

### Recommended: Raspberry Pi 4B (4GB or 8GB)
- **Pros**: Mature platform, excellent Linux support, proven for audio
- **Cons**: Slightly slower than Pi 5
- **Verdict**: Best choice for stability

### Alternative: Raspberry Pi 5 (4GB or 8GB)
- **Pros**: Faster CPU, better I/O, PCIe for potential future expansion
- **Cons**: Newer platform, potential driver issues, runs hotter
- **Verdict**: Use if you need maximum performance

### Not Recommended: Pi 3 or earlier
- **Issue**: Insufficient CPU for Surge XT + effects at low latency

## Sound Blaster S3 USB Audio Interface

### Why Sound Blaster S3?
- Excellent ALSA/JACK support on Linux
- Low latency USB audio (tested with Pi)
- Built-in headphone amp
- USB-C powered
- 24-bit/96kHz capable (we'll use 48kHz for lower CPU)

### Alternatives
- **Behringer UCA222**: Cheaper but higher latency
- **Focusrite Scarlett Solo**: Great quality but overkill for this project
- **Any USB audio with ALSA support**: Check Linux compatibility first

### Connection
- USB-C to USB-A cable (included)
- Output: 1/4" or 1/8" to your mixer/amp/monitors
- Input: Not used (no recording in this build)

## KY-040 Rotary Encoders

### Specifications
- 5V tolerant (use 3.3V on Pi)
- 20 detents per revolution
- Push button switch
- No external components needed (has pull-ups)

### GPIO Wiring

Connect each encoder as follows:

```
Encoder Module          Raspberry Pi GPIO
┌──────────────┐        ┌─────────────────┐
│ +   (VCC)    ├────────┤ Pin 1 (3.3V)    │
│ GND          ├────────┤ Pin 6 (GND)     │
│ CLK (A)      ├────────┤ GPIO pin (see table)
│ DT  (B)      ├────────┤ GPIO pin (see table)
│ SW  (button) ├────────┤ GPIO pin (see table)
└──────────────┘        └─────────────────┘
```

### Complete GPIO Pin Assignment

| Encoder | Function | CLK Pin | DT Pin | SW Pin | VCC | GND |
|---------|----------|---------|--------|--------|-----|-----|
| 1 | Category Nav | GPIO 17 | GPIO 27 | GPIO 22 | 3.3V | GND |
| 2 | Patch Nav | GPIO 23 | GPIO 24 | GPIO 25 | 3.3V | GND |
| 3 | Volume | GPIO 5 | GPIO 6 | GPIO 13 | 3.3V | GND |
| 4 | Spare 1 | GPIO 19 | GPIO 26 | GPIO 16 | 3.3V | GND |
| 5 | Spare 2 | GPIO 20 | GPIO 21 | GPIO 12 | 3.3V | GND |

### Physical Pin Numbers (for reference)

If you prefer physical pin numbers instead of GPIO numbers:

| Encoder | CLK | DT | SW | +3.3V | GND |
|---------|-----|----|----|-------|-----|
| 1 | Pin 11 | Pin 13 | Pin 15 | Pin 1 | Pin 6 |
| 2 | Pin 16 | Pin 18 | Pin 22 | Pin 1 | Pin 9 |
| 3 | Pin 29 | Pin 31 | Pin 33 | Pin 1 | Pin 14 |
| 4 | Pin 35 | Pin 37 | Pin 36 | Pin 1 | Pin 20 |
| 5 | Pin 38 | Pin 40 | Pin 32 | Pin 1 | Pin 25 |

**Note**: Multiple encoders can share the same 3.3V and GND pins.

### Wiring Tips
1. Keep wires short (< 15cm) to reduce noise
2. Twist CLK/DT pairs together to reduce EMI
3. Test each encoder individually before wiring all 5
4. Use female-to-female jumpers for easy removal
5. Label wires with masking tape

## 3.5" Display (Optional - Future Expansion)

### Recommended Models
- **Waveshare 3.5" RPi LCD (A)**: SPI interface, resistive touch
- **Adafruit PiTFT 3.5"**: Good Python library support
- **Generic 3.5" SPI LCD**: Cheaper but verify driver support

### Display Use Case
- Show current preset name (large font)
- Show current category
- Show encoder assignments
- Simple monochrome enough - don't need color

### Milestone 1: NOT REQUIRED
Focus on getting audio working first. Add display later.

## Power Considerations

### Power Budget
- Raspberry Pi 4: 3W idle, 6W under load
- Sound Blaster S3: 2W
- Display: 1-2W
- Encoders: Negligible
- **Total: ~10W maximum**

### Power Supply Requirements
- **Pi 4**: 5V 3A (15W) official supply - adequate headroom
- **Pi 5**: 5V 5A (27W) official supply - future-proof

### Power Quality
- Use official Raspberry Pi power supply
- Avoid USB hub power
- Poor power = audio glitches

## Cooling

### Why Cooling Matters
Surge XT is CPU-intensive. Thermal throttling = audio dropouts.

### Cooling Solutions
1. **Minimum**: Heatsinks on CPU (included with most cases)
2. **Recommended**: Active cooling fan (5V, GPIO controlled)
3. **Optimal**: Flirc case (passive cooling, no fan noise)

### Target Temps
- Idle: < 50°C
- Under load: < 70°C
- Throttling starts: 80°C

Monitor with: `vcgencmd measure_temp`

## Assembly Order

### Phase 1: Core Audio Testing
1. Assemble Pi in case with cooling
2. Flash MicroSD with Pi OS Lite
3. Connect Sound Blaster S3 via USB
4. Connect Roli controller via USB
5. Connect power, keyboard, monitor (initial setup)
6. **DO NOT wire encoders yet**

### Phase 2: Software Validation
1. Install software stack (see INSTALL.md)
2. Validate Surge XT launches
3. Validate MPE input works
4. Validate audio output to Sound Blaster S3
5. Tune JACK for acceptable latency

### Phase 3: Control Interface
1. Wire first encoder (Category Nav)
2. Test encoder script
3. Wire remaining encoders one at a time
4. Test full encoder set

### Phase 4: Display (Future)
1. Connect display to SPI pins
2. Install display drivers
3. Create patch name display script

## Enclosure Ideas

### DIY Approach
- Laser-cut acrylic case
- Panel-mount encoders
- Cutouts for USB ports (Roli, Sound Blaster)
- Cutout for display
- Ventilation for Pi fan

### Dimensions (Approximate)
- Width: 200mm (encoders in a row)
- Depth: 150mm (Pi + cables)
- Height: 80mm (clearance for knobs)

### Panel Layout
```
┌─────────────────────────────────────────┐
│  ┌──────────────────────────┐           │
│  │    3.5" Display          │           │
│  │  (Current Patch Name)    │           │
│  └──────────────────────────┘           │
│                                          │
│   ◯      ◯      ◯      ◯      ◯         │
│  Cat   Patch   Vol   Spare  Spare       │
│  Nav    Nav           1      2          │
└─────────────────────────────────────────┘
    [USB] [USB] [Audio Out] [Power]
```

## Troubleshooting Hardware

### Encoder Not Responding
- Check wiring (CLK/DT swapped?)
- Verify 3.3V power present
- Test with multimeter: should see voltage changes on CLK/DT when rotating
- Check `gpio readall` to see pin states

### Audio Crackling/Dropouts
- Check CPU temp (thermal throttling?)
- Check JACK xruns: `jack_bufsize`
- Increase buffer size in ~/.jackdrc
- Verify power supply adequate

### USB Device Not Detected
- Check `lsusb` to see devices
- Verify USB cable quality
- Try different USB port
- Check dmesg for errors: `dmesg | tail`

### Ground Loops
- If you hear hum/buzz:
  - Use powered USB hub for Roli (isolate ground)
  - Use DI box on audio output
  - Ensure all audio gear on same power circuit
