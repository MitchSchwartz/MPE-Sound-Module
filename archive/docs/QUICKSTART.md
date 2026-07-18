# Pi-Surge-MPE - Quick Start Guide

## ✅ System Ready!

Your Raspberry Pi is now running **Surge XT in headless mode** with full MPE support.

**Status**: Surge XT CLI configured and auto-starting on boot

## What You Have

- **Surge XT CLI** running as a background service
- **Auto MIDI connection** - plug in ANY MIDI device and it works
- **MPE always enabled** (48 semitones pitch bend)
- **Church patch** loaded by default
- **No GUI overhead** - efficient performance mode

### Check if Surge is running
```bash
ssh mitch@surge.local 'systemctl status surge-xt-cli'
```

### View logs
```bash
ssh mitch@surge.local 'tail -f ~/surge-cli.log'
```

### Restart Surge
```bash
ssh mitch@surge.local 'sudo systemctl restart surge-xt-cli'
```

## Testing

1. **Power on your Pi** (it auto-starts Surge)
2. **Plug in your Roli** (auto-connects via MIDI)
3. **Play!** (MPE expression should work immediately)

## Change the Default Patch

Edit the startup script:
```bash
ssh mitch@surge.local 'nano ~/start-surge-cli.sh'
```

Change this line:
```bash
INIT_PATCH="/home/mitch/surge/resources/data/patches_factory/Keys/Church.fxp"
```

To any patch you want, then restart:
```bash
ssh mitch@surge.local 'sudo systemctl restart surge-xt-cli'
```

## Available Patches

Located at:
- `/home/mitch/surge/resources/data/patches_factory` (639 patches)
- `/home/mitch/surge/resources/data/patches_3rdparty` (2,553 patches)

Browse them with:
```bash
ssh mitch@surge.local 'find ~/surge/resources/data/patches_factory -name "*.fxp"'
```

## Switching to GUI Mode (for Patch Editing)

The GUI is still available when you want to edit patches or explore the full interface.

### Switch to GUI Mode

```bash
# 1. SSH into Pi
ssh mitch@surge.local

# 2. Stop CLI service
~/switch-to-gui.sh

# 3. Launch GUI (via VNC)
~/launch-gui-vnc.sh

# Or launch manually with X11 forwarding:
# (From Windows: ssh -X mitch@surge.local)
# Then: ~/surge/build/src/surge-xt/surge-xt_artefacts/Release/Standalone/Surge\ XT &
```

### Switch Back to CLI Mode

When done editing patches:

```bash
# On the Pi (or via SSH)
~/switch-to-cli.sh
```

This stops the GUI and restarts the headless CLI service.

**Note:**
- MPE settings made in GUI won't persist (use CLI mode for performance)
- GUI is for patch editing/exploration only
- Any patches you save in GUI will be available to CLI mode

## Milestone 1 Validation

Run through this checklist:

```bash
# Start monitoring in one terminal
~/pisurge/monitor.sh

# In another terminal, measure boot time
~/pisurge/measure_boot_time.sh
```

### Performance Checklist

- [ ] **Boot time**: System ready in < 30 seconds
- [ ] **JACK starts**: No xruns during startup
- [ ] **Surge launches**: Connects to JACK within 5 seconds
- [ ] **MPE input**: All axes (pitch, pressure, timbre) work
- [ ] **Preset switching**: < 1 second between presets
- [ ] **Audio output**: Clean sound, no dropouts
- [ ] **Latency**: Feels responsive (< 20ms total)

### Performance Targets

| Metric | Target | Command |
|--------|--------|---------|
| Boot time | < 30s | `systemd-analyze` |
| JACK latency | < 12ms | `jack_latency` |
| CPU usage | < 80% | `top` (watch Surge-XT) |
| Temperature | < 70°C | `vcgencmd measure_temp` |
| Xruns | 0 | Watch JACK logs |

## Common Issues & Fixes

### "No JACK server found"
```bash
# Start JACK manually to see errors
jackd -dalsa -dhw:1 -r48000 -p512 -n3 -v

# Check device number
aplay -l
```

### "No sound from Surge"
```bash
# Check JACK connections
jack_lsp -c

# Should show Surge-XT connected to system:playback_1/2
# If not, manually connect with qjackctl or:
jack_connect Surge-XT:out_1 system:playback_1
jack_connect Surge-XT:out_2 system:playback_2
```

### "Roli not sending MIDI"
```bash
# List MIDI devices
~/pisurge/check_midi.sh

# Monitor raw MIDI from Roli
aseqdump -p <roli-port-number>

# Play notes - you should see messages
```

### "Audio crackling/dropouts"
```bash
# Increase JACK buffer size
nano ~/.jackdrc

# Change -p512 to -p1024
# Restart JACK
systemctl --user restart jack.service
```

### "CPU too high"
```bash
# Check temperature first
vcgencmd measure_temp

# If > 80°C, thermal throttling is the issue
# Add cooling (fan/heatsink)

# Reduce Surge CPU usage:
# - Menu > Settings > Max Voices: 16
# - Disable expensive effects
# - Use simpler presets
```

## Next Steps After Milestone 1

Once you've validated MPE audio works:

1. **Optimize boot time**:
   ```bash
   sudo ./boot_config.sh
   ```

2. **Wire first encoder** (Category Navigation):
   - See HARDWARE.md for GPIO wiring
   - Test with: `python3 encoder_controller.py`

3. **Wire remaining encoders**:
   - One at a time
   - Test each before wiring next

4. **Enable auto-start**:
   ```bash
   systemctl --user enable jack.service
   systemctl --user enable surge.service
   systemctl --user enable encoders.service
   ```

5. **Organize presets**:
   - See SURGE_CONFIG.md
   - Create performance preset library

6. **Add display** (future):
   - Show current patch name
   - Show encoder assignments

## Useful Commands

```bash
# Service management
systemctl --user start jack.service
systemctl --user stop jack.service
systemctl --user restart jack.service
systemctl --user status jack.service

# Same for surge.service and encoders.service

# Audio debugging
~/pisurge/check_audio.sh      # List audio devices
~/pisurge/check_midi.sh       # List MIDI devices
~/pisurge/monitor.sh          # Monitor all services
jack_lsp -c                    # Show JACK connections
alsamixer                      # Adjust volumes

# Performance monitoring
top                            # CPU usage
htop                           # Better CPU monitor
vcgencmd measure_temp          # CPU temperature
journalctl --user -u surge.service -f  # Surge logs
```

## Getting Help

### Check Logs
```bash
# JACK logs
journalctl --user -u jack.service -f

# Surge logs
journalctl --user -u surge.service -f

# Encoder logs
journalctl --user -u encoders.service -f

# System logs
dmesg | tail
```

### Debugging Mode

Run services manually to see detailed output:

```bash
# Stop auto-started services
systemctl --user stop jack.service surge.service encoders.service

# Run JACK manually (verbose)
jackd -dalsa -dhw:1 -r48000 -p512 -n3 -v

# In another terminal, run Surge manually
Surge-XT

# In another terminal, run encoder script manually
cd ~/pisurge
python3 encoder_controller.py
```

## Performance Tuning

### Low Latency (Advanced)
For < 10ms latency, try:

```bash
# Edit ~/.jackdrc
/usr/bin/jackd -dalsa -dhw:1 -r48000 -p256 -n2

# May cause xruns if CPU can't keep up
# Monitor with: jack_latency
```

### High Stability (Recommended)
For dropout-free performance:

```bash
# Edit ~/.jackdrc
/usr/bin/jackd -dalsa -dhw:1 -r48000 -p1024 -n3

# Higher latency (~20ms) but rock solid
```

### Balance (Default)
```bash
# Edit ~/.jackdrc
/usr/bin/jackd -dalsa -dhw:1 -r48000 -p512 -n3

# ~12ms latency, good stability
```

## Reset Everything

If you need to start over:

```bash
# Stop all services
systemctl --user stop jack.service surge.service encoders.service

# Disable auto-start
systemctl --user disable jack.service surge.service encoders.service

# Remove configs
rm -rf ~/.config/surge-xt
rm ~/.jackdrc

# Re-run installer
cd ~/pisurge
./install.sh
```

## Success Criteria

You've completed Milestone 1 when:

✓ Surge XT launches automatically on boot
✓ MPE from Roli works perfectly (all axes)
✓ Audio outputs cleanly to Sound Blaster S3
✓ Preset switching is fast (< 1 second)
✓ No xruns during normal playing
✓ Boot time < 30 seconds
✓ CPU < 80%, temp < 70°C

**Congratulations! You're ready to add encoders.**
