# Pi-Surge-MPE Development Roadmap

## Current Status: v0.1 - Foundation

**Completed**:
- ✅ Project documentation
- ✅ Installation scripts
- ✅ Encoder controller script
- ✅ Systemd service files
- ✅ Hardware wiring guide

**Status**: Ready for hardware testing

---

## Milestone 1: Core Audio Validation (CURRENT)

**Goal**: Validate Surge XT + MPE workflow on Pi before wiring encoders

**Tasks**:
- [ ] Flash Pi OS Lite to MicroSD
- [ ] Run install.sh on Pi
- [ ] Install Surge XT ARM binary
- [ ] Configure JACK for Sound Blaster S3
- [ ] Test MPE input from Roli
- [ ] Validate audio output quality
- [ ] Measure boot time (target: < 30s)
- [ ] Measure latency (target: < 20ms)
- [ ] Test preset switching speed (target: < 1s)

**Success Criteria**:
- All MPE axes work (pitch, pressure, timbre)
- No audio dropouts during playing
- Boot to audio-ready in < 30 seconds
- CPU usage < 80%
- Temperature < 70°C

**Blockers**:
- Need Surge XT ARM64 binary (build or download)
- Verify Sound Blaster S3 ALSA compatibility

**Estimated Time**: 2-4 hours

---

## Milestone 2: Control Interface

**Goal**: Wire encoders and test parameter control

**Tasks**:
- [ ] Wire Encoder 1 (Category Navigation)
- [ ] Test encoder_controller.py with single encoder
- [ ] Wire Encoder 2 (Patch Navigation)
- [ ] Wire Encoder 3 (Volume)
- [ ] Wire Encoders 4-5 (Spares)
- [ ] Test all encoders simultaneously
- [ ] Configure MIDI CC mappings in Surge
- [ ] Test encoder response/debouncing
- [ ] Optimize encoder sensitivity

**Success Criteria**:
- All 5 encoders respond correctly
- No value jumping/noise
- MIDI CC messages reach Surge
- Parameters update smoothly

**Blockers**:
- Need KY-040 encoder modules
- Need jumper wires for GPIO connections

**Estimated Time**: 2-3 hours

---

## Milestone 3: Auto-Start & Boot Optimization

**Goal**: System boots directly to audio-ready state

**Tasks**:
- [ ] Enable systemd services (jack, surge, encoders)
- [ ] Run boot_config.sh optimization
- [ ] Test boot sequence reliability
- [ ] Measure optimized boot time
- [ ] Handle edge cases (USB not ready, etc.)
- [ ] Add error recovery/retry logic
- [ ] Create boot status indicator (LED?)

**Success Criteria**:
- Boots unattended to audio-ready state
- Boot time < 30 seconds consistently
- Survives power cycle/reboot
- Services auto-restart on failure

**Blockers**:
- Need to test on actual hardware
- May need to tune systemd service delays

**Estimated Time**: 2-3 hours

---

## Milestone 4: Preset Navigation (OSC Control)

**Goal**: Implement category/patch browsing with encoders

**Tasks**:
- [ ] Research Surge XT OSC API
- [ ] Install python-osc library
- [ ] Modify encoder_controller.py for OSC
- [ ] Test OSC preset navigation commands
- [ ] Implement category up/down
- [ ] Implement patch up/down
- [ ] Handle edge cases (first/last preset)
- [ ] Add preset change confirmation

**Success Criteria**:
- Encoders navigate Surge presets
- Category/patch browsing works intuitively
- No crashes when hitting boundaries
- Preset changes are < 1 second

**Blockers**:
- Need to understand Surge OSC message format
- May need to fork Surge if OSC API insufficient

**Estimated Time**: 4-6 hours

**Alternative Approaches**:
1. MIDI Program Change (limited to 128 presets)
2. Custom Surge fork with MIDI CC preset nav
3. External preset manager script

---

## Milestone 5: Preset Organization

**Goal**: Create organized preset library for live performance

**Tasks**:
- [ ] Audit Surge factory presets for MPE compatibility
- [ ] Create category structure (Pads, Leads, Basses, Keys, etc.)
- [ ] Copy/organize presets into numbered folders
- [ ] Test MPE response on each preset
- [ ] Create performance preset favorites
- [ ] Document preset mappings
- [ ] Backup preset library

**Success Criteria**:
- Presets organized for encoder navigation
- All presets tested with MPE
- Predictable browsing order
- Quick access to favorites

**Estimated Time**: 3-4 hours

---

## Milestone 6: Display Integration

**Goal**: Show current patch name on 3.5" display

**Tasks**:
- [ ] Choose display module (3.5" SPI TFT)
- [ ] Wire display to SPI GPIO pins
- [ ] Install display drivers
- [ ] Test display rendering
- [ ] Create display UI script (Python)
- [ ] Listen for OSC patch change events
- [ ] Update display on preset change
- [ ] Add systemd service for display
- [ ] Design UI layout (patch name, category, etc.)

**Success Criteria**:
- Display shows current patch name (large font)
- Updates within 100ms of preset change
- Readable from performance position
- No performance impact on audio

**Blockers**:
- Need to select/purchase display
- Need to understand Surge OSC event notifications

**Estimated Time**: 4-6 hours

**Display Layout Ideas**:
```
┌────────────────────────┐
│  Category: Pads        │
│                        │
│    Warm Pad            │
│                        │
│  Vol: 85  Mod: 64      │
└────────────────────────┘
```

---

## Milestone 7: Enclosure Design

**Goal**: Build enclosure for complete module

**Tasks**:
- [ ] Measure component dimensions
- [ ] Design enclosure (CAD or hand-drawn)
- [ ] Choose material (acrylic, wood, 3D print)
- [ ] Create panel layout for encoders
- [ ] Plan cutouts for USB, audio, power
- [ ] Account for ventilation/cooling
- [ ] Fabricate enclosure
- [ ] Mount components
- [ ] Wire management/cable routing
- [ ] Final assembly

**Success Criteria**:
- Professional appearance
- Easy access to USB ports
- Good cooling/ventilation
- Stable/secure mounting
- Appropriate size for gig bag

**Estimated Time**: 8-12 hours (design + fabrication)

---

## Future Enhancements (Post-v1.0)

### High Priority
- [ ] **Encoder LED feedback** - Visual indication of value
- [ ] **MIDI panic button** - Stop all notes
- [ ] **Preset favorites** - Quick access to frequently used
- [ ] **Web-based remote control** - Configure via browser
- [ ] **Recording capability** - Capture performances
- [ ] **Performance stats display** - CPU, temp, latency

### Medium Priority
- [ ] **Multiple synth instances** - Layer/split capability
- [ ] **Effects chain control** - Encoders control FX params
- [ ] **Bluetooth MIDI** - Wireless controller support
- [ ] **Patch randomization** - Generate variations
- [ ] **Snapshot system** - Save/recall complete states
- [ ] **MIDI learn mode** - Easy parameter assignment

### Low Priority / Experimental
- [ ] **Custom Surge fork** - Headless mode, custom features
- [ ] **Hardware MIDI DIN ports** - Traditional MIDI I/O
- [ ] **CV/Gate outputs** - Eurorack integration
- [ ] **Battery power** - Portable/mobile use
- [ ] **Built-in sequencer** - Simple pattern recorder
- [ ] **Multi-output audio** - Individual track outputs

---

## Known Issues / Bugs

**To Be Discovered**: No hardware testing yet

Potential issues to watch for:
- USB audio device initialization timing
- Encoder noise/debouncing
- JACK xruns on boot
- Thermal throttling under sustained load
- SD card corruption from power loss

---

## Performance Optimization Opportunities

### Boot Time
- Current target: < 30s
- Stretch goal: < 15s
- Ideas:
  - Parallel service startup
  - Disable more services
  - Optimize kernel parameters
  - Use tmpfs for logs

### Audio Latency
- Current target: < 20ms
- Stretch goal: < 10ms
- Ideas:
  - Smaller JACK buffer (-p256)
  - Kernel realtime patches
  - USB audio tuning
  - Dedicated CPU core for JACK

### CPU Usage
- Current limit: 80%
- Goal: Maintain headroom for complexity
- Ideas:
  - Undervolt/overclock tuning
  - Better cooling
  - Optimize Surge settings
  - Use haiku build of Surge (if available)

---

## Documentation Needs

**Current**: Comprehensive documentation for setup

**Future**:
- [ ] Video walkthrough of installation
- [ ] Wiring diagram with photos
- [ ] Enclosure design files (CAD)
- [ ] Preset library documentation
- [ ] Performance tips/best practices
- [ ] Troubleshooting flowcharts
- [ ] API documentation (for OSC control)

---

## Community / Collaboration

**Potential Contribution Areas**:
- Preset libraries optimized for MPE
- Alternative hardware configurations (Pi Zero 2 W?)
- Enclosure designs
- Display UI themes
- Alternative synth support (Vital, Dexed, etc.)
- Integration with other projects (Zynthian, Patchbox OS)

**Communication**:
- GitHub Issues for bug reports
- GitHub Discussions for questions
- Pull Requests for contributions

---

## Version Planning

### v0.1 (Current) - Foundation
- Documentation complete
- Scripts ready for testing
- Awaiting hardware validation

### v0.2 - Core Audio
- Milestone 1 complete
- Surge + MPE validated
- Performance benchmarked

### v0.3 - Control Interface
- Milestones 2-3 complete
- Encoders working
- Auto-start reliable

### v0.4 - Preset Navigation
- Milestones 4-5 complete
- OSC control working
- Preset library organized

### v1.0 - Complete Module (Target)
- Milestones 1-6 complete
- Display integrated
- Enclosure built
- Production ready for live use

### v2.0 - Advanced Features (Future)
- Multiple synth support
- Web UI
- Recording
- Advanced control options

---

## Success Metrics

### Technical
- Boot time: < 30s
- Latency: < 20ms
- CPU usage: < 80%
- Preset change: < 1s
- JACK xruns: 0 per session
- MPE axes: All functional

### Usability
- Setup time: < 2 hours for experienced user
- Live performance: No crashes over 2-hour set
- Reliability: 100% boot success rate
- Ease of use: Intuitive without manual

### Project
- Documentation: Complete for all milestones
- Community: Active users, contributions
- Stability: Tested on multiple Pi models
- Extensibility: Easy to modify/customize

---

## Timeline (Estimated)

Assuming part-time work (weekends/evenings):

| Milestone | Duration | Cumulative |
|-----------|----------|------------|
| M1: Core Audio | 1 week | 1 week |
| M2: Encoders | 1 week | 2 weeks |
| M3: Auto-Start | 1 week | 3 weeks |
| M4: OSC Control | 2 weeks | 5 weeks |
| M5: Preset Org | 1 week | 6 weeks |
| M6: Display | 2 weeks | 8 weeks |
| M7: Enclosure | 2 weeks | 10 weeks |

**Total to v1.0**: ~2-3 months part-time

---

## Risk Assessment

### High Risk
- **Surge XT ARM performance**: May not be adequate on Pi
  - Mitigation: Test early (Milestone 1), have backup synth options
- **MPE + preset nav conflict**: Surge may not support both well
  - Mitigation: Research OSC API thoroughly, consider custom fork

### Medium Risk
- **USB audio reliability**: Some USB audio has Linux issues
  - Mitigation: Sound Blaster S3 is well-tested, have alternatives
- **Boot time target**: 30s may be challenging
  - Mitigation: Aggressive optimization, accept 45s if needed

### Low Risk
- **Encoder noise**: Common issue with cheap encoders
  - Mitigation: Hardware debouncing, software filtering
- **Thermal throttling**: Pi can get hot
  - Mitigation: Good cooling, monitor temps

---

## Decision Points

### Open Questions
1. **OSC vs MIDI PC for preset nav?**
   - OSC: More flexible, requires Surge OSC support
   - MIDI PC: Simpler, limited to 128 presets
   - Decision: Try OSC first, fall back to MIDI PC

2. **Which display to use?**
   - Waveshare 3.5": Popular, good support
   - Adafruit PiTFT: Better library, pricier
   - Decision: TBD based on availability/price

3. **Enclosure material?**
   - Laser-cut acrylic: Professional look, needs access to laser
   - 3D printed: Customizable, long print time
   - Wood: Easy to work, DIY-friendly
   - Decision: TBD based on maker skills/tools

4. **Support multiple Pis?**
   - Pi 4 only: Simpler testing
   - Pi 5 also: Better performance, newer platform
   - Pi Zero 2 W: Ultra-portable, may be underpowered
   - Decision: Support Pi 4 & 5, document Pi Zero limitations

---

## Resources Needed

### Hardware (Milestone 1)
- [x] Raspberry Pi 4/5
- [x] Sound Blaster S3
- [x] Roli Seaboard/Lightpad
- [x] MicroSD card
- [ ] KY-040 encoders (x5) - **NEED TO ORDER**
- [ ] Jumper wires
- [ ] 3.5" display - **NEED TO ORDER**

### Software
- [ ] Surge XT ARM binary - **NEED TO BUILD/DOWNLOAD**
- [x] Installation scripts
- [x] Encoder controller

### Documentation
- [x] All major docs complete
- [ ] Photos/videos - after hardware build

---

## Next Immediate Steps

1. **Order missing hardware**:
   - 5x KY-040 encoders
   - Jumper wires (if needed)
   - (Display can wait until Milestone 6)

2. **Get Surge XT binary**:
   - Download from releases OR
   - Build from source on Pi

3. **Start Milestone 1 testing**:
   - Follow [README.md](../README.md)
   - Document any issues/changes needed
   - Measure performance metrics

4. **Update documentation**:
   - Fix any errors found during testing
   - Add photos of setup
   - Document actual vs expected performance

**Status**: Ready to begin hardware validation! 🚀
