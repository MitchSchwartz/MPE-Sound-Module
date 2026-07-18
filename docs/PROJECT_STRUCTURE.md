# Pi-Surge-MPE Project Structure

## Repository Layout

```
pi-surge-mpe/
├── README.md                    # Project overview and architecture
├── LICENSE                      # MIT License
├── .gitignore                   # Git ignore rules
│
├── README.md                    # Project overview (START HERE!)
├── INSTALL.md                   # Detailed installation guide
├── HARDWARE.md                  # Hardware setup, wiring, BOM
├── SURGE_CONFIG.md              # Surge XT configuration guide
├── FAQ.md                       # Troubleshooting and FAQs
├── PROJECT_STRUCTURE.md         # This file
│
├── install.sh                   # Main installation script
├── boot_config.sh               # Boot time optimization script
├── encoder_controller.py        # Rotary encoder control script
├── requirements.txt             # Python dependencies
│
└── docs/                        # Additional documentation (future)
    ├── images/                  # Wiring diagrams, photos
    └── examples/                # Example configs, presets
```

## Documentation Reading Order

### For First-Time Setup
1. **README.md** - Understand what this project is
2. **README.md** - Get oriented and find the right doc
3. **INSTALL.md** - Detailed installation steps
4. **HARDWARE.md** - Wire encoders, assemble hardware
5. **SURGE_CONFIG.md** - Optimize Surge settings
6. **FAQ.md** - Reference for issues

### For Understanding the System
1. **README.md** - Architecture overview
2. **HARDWARE.md** - Hardware architecture
3. **SURGE_CONFIG.md** - Software architecture
4. **encoder_controller.py** - Code walkthrough

### For Troubleshooting
1. **README.md** - Documentation map and common issues
2. **FAQ.md** - Comprehensive troubleshooting
3. **INSTALL.md** - Step-by-step validation

## Scripts

### `install.sh`
Main installation script. Installs:
- System dependencies (JACK, build tools)
- Python dependencies (gpiozero, rtmidi)
- Systemd service files
- Helper scripts

**Run once** on initial setup.

### `boot_config.sh`
Boot time optimization. Configures:
- Disables unnecessary services
- Sets kernel parameters for realtime audio
- Configures CPU governor
- Optimizes system limits

**Run once** after verifying audio works.

### `encoder_controller.py`
Main control script. Maps:
- 5 rotary encoders to MIDI CC
- Button presses (future use)
- Auto-connects to Surge XT via MIDI

**Runs as systemd service** on boot.

### Helper Scripts (created by install.sh)
Located in `~/pisurge/`:

- `check_audio.sh` - List audio devices
- `check_midi.sh` - List MIDI devices
- `monitor.sh` - Show service status and JACK connections
- `measure_boot_time.sh` - Analyze boot performance

## Systemd Services

Created in `~/.config/systemd/user/`:

### `jack.service`
- Starts JACK audio server
- Reads config from `~/.jackdrc`
- Required by surge.service

### `surge.service`
- Starts Surge XT synth
- Depends on jack.service
- Auto-connects to JACK outputs

### `encoders.service`
- Starts encoder controller script
- Depends on surge.service
- Sends MIDI CC to Surge

## Configuration Files

### `~/.jackdrc`
JACK audio server configuration.

Example:
```bash
/usr/bin/jackd -dalsa -dhw:1 -r48000 -p512 -n3
```

Parameters:
- `hw:1` - Sound Blaster S3 card number (update for your system)
- `r48000` - Sample rate (48kHz)
- `p512` - Buffer size (512 samples = ~10ms latency)
- `n3` - Number of periods (3 = good stability)

### `~/.config/surge-xt/`
Surge XT configuration directory (auto-created).

Contents:
- `SurgeXT.conf` - Surge settings (MPE, MIDI mappings)
- User presets, wavetables, skins, etc.

## Data Directories

### User Presets
`~/.local/share/surge-xt/presets/`

Organize for encoder navigation:
```
presets/
├── 1-Live/
│   ├── 1-Pads/
│   ├── 2-Leads/
│   ├── 3-Basses/
│   └── 4-Keys/
├── 2-Experimental/
└── 3-Templates/
```

### User Wavetables
`~/.local/share/surge-xt/wavetables/`

Add custom `.wav` files (2048 samples, mono, 16-bit).

## GPIO Pin Assignments

### Encoder Wiring

| Encoder | GPIO CLK | GPIO DT | GPIO SW | Description |
|---------|----------|---------|---------|-------------|
| Category | 17 | 27 | 22 | Navigate categories |
| Patch | 23 | 24 | 25 | Navigate patches |
| Volume | 5 | 6 | 13 | Master volume |
| Spare 1 | 19 | 26 | 16 | Mod wheel (CC1) |
| Spare 2 | 20 | 21 | 12 | Filter cutoff (CC74) |

All encoders share:
- **VCC**: 3.3V (Pin 1)
- **GND**: Ground (Pins 6, 9, 14, 20, 25)

### Reserved for Display (Future)
- **SPI0**: GPIO 10 (MOSI), 9 (MISO), 11 (SCLK), 8 (CE0)
- **Additional**: GPIO 25 (DC), 24 (RST)

## MIDI Mapping

### From Roli to Surge
- **MPE Input**: Channels 2-15 (per-note expression)
- **Channel 1**: Global controls
- **Pitch Bend**: ±48 semitones (configurable)
- **Poly Aftertouch**: Pressure (Z-axis)
- **CC74**: Timbre (Y-axis)

### From Encoders to Surge
| Encoder | MIDI CC | Function | Range |
|---------|---------|----------|-------|
| Category | CC 20 | Category nav | 0-127 (relative) |
| Patch | CC 21 | Patch nav | 0-127 (relative) |
| Volume | CC 7 | Master volume | 0-127 (absolute) |
| Spare 1 | CC 1 | Mod wheel | 0-127 (absolute) |
| Spare 2 | CC 74 | Filter cutoff | 0-127 (absolute) |

## Audio Routing

```
[Roli Seaboard]
    |
    | USB MIDI (MPE channels 2-15)
    v
[Surge XT]
    |
    | JACK (stereo audio)
    v
[JACK Server]
    |
    | ALSA
    v
[Sound Blaster S3]
    |
    | Analog audio (1/4" or 1/8")
    v
[Mixer/Amp/Monitors]
```

## Boot Sequence

1. **Kernel boot** (~5s)
2. **System services** (~10s)
   - Minimal services (SSH, audio, GPIO)
3. **User login** (auto)
4. **User services** (~10s)
   - `jack.service` starts
   - `surge.service` starts (waits 3s for JACK)
   - `encoders.service` starts (waits 5s for Surge)
5. **Audio ready** (~25-30s total)

## Milestone Progression

### Milestone 1: Core Audio ✓
- Surge XT runs on Pi
- MPE input from Roli works
- Audio output to Sound Blaster S3
- Boot time < 30s
- No encoders needed yet

### Milestone 2: Control Interface (Next)
- Wire 5 encoders
- Test encoder script
- Map encoders to Surge parameters
- Enable auto-start services

### Milestone 3: Preset Management (Future)
- Organize preset library
- Implement OSC preset navigation
- Create category/patch browsing

### Milestone 4: Display Integration (Future)
- Wire 3.5" display
- Show current patch name
- Show encoder assignments
- Show performance stats

### Milestone 5: Enclosure (Future)
- Design/build enclosure
- Panel layout for encoders
- Cable management
- Final assembly

## Development Workflow

### Testing Changes
```bash
# Stop services
systemctl --user stop jack.service surge.service encoders.service

# Make changes to scripts
nano ~/pisurge/encoder_controller.py

# Test manually
cd ~/pisurge
python3 encoder_controller.py

# If working, restart services
systemctl --user start jack.service surge.service encoders.service
```

### Debugging
```bash
# View logs in real-time
journalctl --user -f

# View specific service logs
journalctl --user -u encoders.service -f

# Check service status
systemctl --user status encoders.service

# Monitor JACK
jack_lsp -c
```

### Making Backups
```bash
# Backup configs
tar czf ~/pisurge-backup-$(date +%Y%m%d).tar.gz \
    ~/pisurge \
    ~/.jackdrc \
    ~/.config/systemd/user/*.service \
    ~/.config/surge-xt \
    ~/.local/share/surge-xt/presets

# Restore
tar xzf ~/pisurge-backup-YYYYMMDD.tar.gz -C ~/
```

## Future Enhancements

### Planned
- [ ] OSC-based preset navigation
- [ ] Display support (show patch names)
- [ ] Performance preset library
- [ ] Encoder LED feedback
- [ ] MIDI panic button
- [ ] Preset favorites system

### Possible
- [ ] Multiple synth instances (layer/split)
- [ ] Effects chain control
- [ ] Recording capability (jack_capture integration)
- [ ] Bluetooth MIDI support
- [ ] Web-based remote control
- [ ] Patch randomization

### Advanced
- [ ] Custom Surge fork with headless mode
- [ ] Hardware MIDI DIN ports
- [ ] CV/Gate outputs (Eurorack integration)
- [ ] Battery power option
- [ ] Standalone sequencer

## Contributing

See GitHub issues for tasks that need work.

Pull requests welcome for:
- Bug fixes
- Documentation improvements
- New features (discuss in issues first)
- Preset libraries
- Hardware variations

## Version History

- **v0.1** (Current) - Initial release
  - Basic JACK + Surge XT setup
  - Encoder control script
  - Documentation

Future versions TBD.
