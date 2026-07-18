# Pi-Surge-MPE Project Plan

## Project Vision

Build a dedicated, headless MPE synthesizer module for live performance using:
- Raspberry Pi 5
- Surge XT synthesizer (CLI mode)
- Roli Seaboard BLOCK
- Custom hardware UI for preset browsing

## Current Status (as of 2025-12-27)

### ✅ Phase 1: Core System - COMPLETE

**What's Working:**
- Surge XT CLI runs headless (no GUI/VNC needed)
- Auto-starts on boot via systemd (~25 second boot time)
- MPE always enabled (48 semitones pitch bend)
- Roli Seaboard auto-connects via `--all-midi-inputs`
- Audio output to Sound Blaster Play! 3 USB (44.1kHz, 512 buffer, ~11ms latency)
- Church.fxp default patch loads automatically
- SSH key authentication configured
- 3,192 patches available (639 factory + 2,553 third-party)

**Key Technical Decisions:**
- Use Surge XT CLI instead of GUI (headless-native, command-line flags for MPE)
- Direct ALSA audio (no JACK complexity)
- systemd service for auto-start
- All configuration via command-line flags (no manual UI interaction needed)

**Documentation:**
- [README.md](README.md) - Project overview
- [CURRENT_STATE.md](CURRENT_STATE.md) - Complete system state (500+ lines)
- [SETUP_COMPLETE.md](SETUP_COMPLETE.md) - What was configured and why
- [QUICKSTART.md](QUICKSTART.md) - Quick reference commands
- [docs/SURGE_CLI_HEADLESS_SETUP.md](docs/SURGE_CLI_HEADLESS_SETUP.md) - Technical deep dive

**Configuration Files:**
- [scripts/start-surge-cli.sh](scripts/start-surge-cli.sh) - Startup script (actual working copy from Pi)
- [config/surge-xt-cli.service](config/surge-xt-cli.service) - systemd service (actual working copy from Pi)

### 🔄 Phase 2: Custom Preset Browser - PLANNED

**Goal:** Add hardware UI for browsing and selecting patches without external control

**Hardware Components:**
- 1x 1.3" OLED display (SSD1306 or SH1106, I2C)
- 2x Rotary encoders (KY-040 or similar)
- Breadboard/protoboard for prototyping
- Jumper wires for GPIO connections

**Functionality:**
- Display current patch category + name on OLED
- Encoder 1: Navigate patch categories
- Encoder 2: Select patches within category
- Button press: Load selected patch
- Send OSC or MIDI Program Change to Surge to switch patches

**Implementation Approach:**
- Python app using:
  - `luma.oled` for OLED display
  - `RPi.GPIO` for encoder input
  - `python-osc` for communicating with Surge
- Scan preset directories on startup
- Run as separate systemd service alongside Surge
- Code examples already provided in [docs/SURGE_CLI_HEADLESS_SETUP.md](docs/SURGE_CLI_HEADLESS_SETUP.md)

**Status:**
- Architecture designed
- Code examples written
- Hardware not yet ordered
- Not yet implemented

### 🔮 Phase 3: Future Enhancements - IDEAS

**Potential additions:**
- Web interface for control via phone/tablet
- OLED status display showing CPU, temp, current patch
- Foot controller for hands-free patch switching
- OSC control from DAW or mobile apps
- Multiple Surge instances with different patches
- MIDI learn for parameter mapping

## Technical Architecture

### System Flow
```
[Boot] → [systemd] → [start-surge-cli.sh] → [surge-xt-cli]
                                                    ↓
                                            [MPE enabled]
                                            [MIDI auto-connect]
                                            [ALSA audio out]
```

### Key Files on Pi
```
/home/mitch/
├── start-surge-cli.sh              # Startup script
├── surge-cli.log                   # Runtime log
└── surge/
    ├── build/surge_xt_products/
    │   └── surge-xt-cli            # Main binary
    └── resources/data/
        ├── patches_factory/        # 639 patches
        └── patches_3rdparty/       # 2,553 patches

/etc/systemd/system/
└── surge-xt-cli.service            # Auto-start service
```

### Network Access
- **Primary hostname:** surge.local (mDNS)
- **Fallback IP:** 192.168.1.203
- **User:** mitch
- **SSH:** Password-free via SSH key (~/.ssh/surge_pi_key)

## Development History

### What We Tried (and Failed)
1. **GUI Surge XT with VNC**
   - X11 input issues (keyboard/mouse didn't work)
   - MPE settings didn't persist across reboots
   - MIDI device selection required manual GUI interaction
   - Heavy overhead from X11/Openbox

2. **JACK Audio**
   - Complex setup, additional latency
   - Not needed for standalone Surge

3. **ALSA aconnect for MIDI**
   - Doesn't work with PipeWire
   - Required manual scripting

4. **Python MIDI RPN to enable MPE**
   - Complex, fragile, timing issues
   - Not needed with CLI flags

### What Actually Worked
**Surge XT CLI with command-line flags:**
- `--all-midi-inputs` → auto MIDI connection
- `--mpe-enable` → MPE always on
- `--mpe-pitch-bend-range=48` → proper pitch bend
- `--init-patch` → default patch
- `--audio-interface` → specific audio device
- `--no-stdin` → daemon mode

**Key insight:** The CLI version was designed EXACTLY for this use case - embedded/headless operation.

## Next Steps (Priority Order)

### Immediate (Phase 2 - Preset Browser)
1. **Order hardware components:**
   - [ ] 1.3" OLED display (I2C, SSD1306 or SH1106)
   - [ ] 2x Rotary encoders (KY-040)
   - [ ] Breadboard/jumpers if needed

2. **Hardware setup:**
   - [ ] Wire OLED to I2C pins (GPIO 2/3)
   - [ ] Wire encoders to GPIO pins
   - [ ] Test basic I2C communication
   - [ ] Test encoder input

3. **Software development:**
   - [ ] Install Python libraries (`luma.oled`, `RPi.GPIO`, `python-osc`)
   - [ ] Write preset scanner (parse .fxp files)
   - [ ] Implement OLED display code
   - [ ] Implement encoder input handling
   - [ ] Add OSC/MIDI communication to Surge
   - [ ] Test patch switching
   - [ ] Create systemd service for preset browser

4. **Integration testing:**
   - [ ] Test alongside running Surge
   - [ ] Verify patch changes work reliably
   - [ ] Test all 3,192 patches are accessible
   - [ ] Optimize for performance (screen refresh, responsiveness)

### Future Considerations
- Enclosure design (3D printed or off-the-shelf case)
- Physical mounting for encoders/display
- Power supply considerations
- Backup/restore system for configuration
- Version control for Surge patches

## Design Principles

1. **Zero Manual Intervention:** System must work completely on boot without any configuration
2. **Reliability Over Features:** Simple, robust solutions preferred over complex ones
3. **Performance First:** Low latency, minimal CPU overhead
4. **Maintainability:** Clear documentation, version-controlled configuration
5. **Expandability:** Design for future enhancements without breaking existing functionality

## Key Learnings

- Surge XT CLI is far superior to GUI for embedded use
- Command-line flags eliminate all persistence/configuration issues
- Direct ALSA is simpler and lower latency than JACK for single-app use
- systemd "forking" type required when script backgrounds a process
- Documentation is critical for multi-session projects

## User Requirements (Non-Negotiable)

From user feedback during development:
- "I need it to boot up from zero to where this works without my input"
- "It is completely unacceptable that every time I reboot it I have to use a ui or run a command manually"
- MPE must always be enabled (48 semitones)
- Roli must auto-connect (restart acceptable if plugged in after boot)
- No GUI/VNC interaction required for normal operation

## Success Criteria

### Phase 1 (Complete) ✅
- [x] Boot to ready in < 30 seconds
- [x] MPE always enabled automatically
- [x] Roli auto-connects without manual intervention
- [x] Audio output works reliably
- [x] No GUI/VNC needed
- [x] Comprehensive documentation
- [x] Version-controlled configuration

### Phase 2 (In Planning) 🔄
- [ ] Hardware UI can browse all patches
- [ ] Encoder navigation is responsive (< 100ms)
- [ ] OLED display shows category + patch name clearly
- [ ] Patch changes execute in < 1 second
- [ ] System remains stable with UI running
- [ ] Documentation updated with UI setup

### Phase 3 (Future) 🔮
- TBD based on Phase 2 outcomes

---

**Last Updated:** 2025-12-27
**Project Status:** Phase 1 Complete, Phase 2 Planning
**Next Milestone:** Order hardware for preset browser
