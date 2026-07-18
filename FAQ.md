# Frequently Asked Questions

## General Questions

### Q: Why build this instead of using Zynthian?
**A:** Zynthian has fundamental issues for this use case:
- Preset saving is unreliable (silent failures, wrong presets loading)
- MPE is incompatible with "Active Mode" quick switching
- Only workaround is loading multiple chains in snapshots (high CPU, limited patches)
- UI is confusing for live use
- Simple settings hidden or disabled by default (CC74/Y-axis)

This custom build is simpler, faster, and actually works for MPE.

### Q: Can I use a different synth instead of Surge XT?
**A:** Yes, but ensure it:
- Runs on ARM64 Linux
- Has native MPE support (not just poly-pressure)
- Can run standalone (not just as a plugin)
- Has fast preset switching
- Isn't too CPU-heavy for Pi

Alternatives: Vital (free), u-he Hive, Pianoteq (if you want piano)

### Q: Will this work on Pi 3?
**A:** Not recommended. Surge XT + effects is too CPU-intensive for Pi 3. You'd need to severely limit polyphony and disable effects.

### Q: Can I add more encoders?
**A:** Yes! The Pi has plenty of GPIO pins. Just add more entries to `ENCODER_PINS` in `encoder_controller.py` and wire them up.

### Q: What about using a MIDI controller with knobs instead of encoders?
**A:** That works too! But you lose the DIY integration. This project is about building a dedicated unit, not just running software.

## Hardware Questions

### Q: Sound Blaster S3 is out of stock. Alternatives?
**A:** Any USB audio interface with good ALSA support:
- **Behringer UCA222** - Cheaper, higher latency
- **Focusrite Scarlett Solo** - Excellent, but pricier
- **M-Audio M-Track Solo** - Good middle ground
- Test compatibility: `aplay -l` should list it

### Q: Can I use a different display?
**A:** Yes. The 3.5" is just a suggestion. Any SPI/I2C display will work. Just need to adapt the Python code.

### Q: My encoders are noisy/jumping values
**A:** Common issue with cheap KY-040 encoders:
- Add 0.1µF capacitors across CLK/GND and DT/GND
- Increase `bounce_time` in `encoder_controller.py`
- Use shielded wire
- Keep wires short

### Q: Can I use this with a different MPE controller?
**A:** Yes! Anything that sends MPE MIDI:
- Roli Seaboard/Lightpad
- Haken Continuum
- LinnStrument
- Eigenharp
- Sensel Morph

Just ensure USB MIDI works on Linux.

### Q: What's the total latency?
**A:** Typical setup (~48kHz, 512 buffer):
- USB MIDI: ~1-2ms
- JACK: ~10-12ms (512 samples @ 48kHz)
- USB audio: ~1-2ms
- **Total: ~13-16ms**

This is acceptable for most players. Decrease buffer for lower latency.

## Software Questions

### Q: Where can I get Surge XT ARM binary?
**A:** Options:
1. **Download**: https://github.com/surge-synthesizer/releases-xt/releases
   - Look for ARM64/aarch64 builds
2. **Build from source**: https://github.com/surge-synthesizer/surge
   - Takes ~30 min on Pi 4
   - Enables latest features

### Q: How do I update Surge XT?
**A:**
```bash
# Stop service
systemctl --user stop surge.service

# Replace binary
sudo mv /usr/local/bin/Surge-XT /usr/local/bin/Surge-XT.old
sudo cp ~/Downloads/Surge-XT /usr/local/bin/
sudo chmod +x /usr/local/bin/Surge-XT

# Restart service
systemctl --user start surge.service
```

### Q: Can I run other plugins alongside Surge?
**A:** Not in this setup. This is designed for Surge only. Adding plugin hosting (Carla, etc.) defeats the simplicity goal.

If you need multiple synths, use Zynthian or build a custom plugin host.

### Q: How do I add custom wavetables to Surge?
**A:**
```bash
# User wavetables go here:
~/.local/share/surge-xt/wavetables/

# Copy .wav files (2048 samples, mono, 16-bit)
cp mywavetable.wav ~/.local/share/surge-xt/wavetables/

# Restart Surge to load
systemctl --user restart surge.service
```

### Q: Surge XT has a GUI. Can I run this headless?
**A:** Surge XT requires X11 currently (for the GUI). To run truly headless, you'd need:
1. Virtual X server (Xvfb)
2. Or wait for Surge headless mode (not available yet)
3. Or fork Surge and build headless version

For now, just SSH with X forwarding or use VNC if you need to see the GUI remotely.

## Audio Questions

### Q: I'm getting xruns (dropouts). How to fix?
**A:**
1. **Increase buffer size**: Edit `~/.jackdrc`, change `-p512` to `-p1024`
2. **Reduce CPU load**:
   - Lower Surge polyphony (Menu > Settings > Max Voices)
   - Disable expensive effects (reverb, delay)
   - Use simpler presets
3. **Check temperature**: `vcgencmd measure_temp` - if > 80°C, add cooling
4. **Kill other processes**: `htop`, kill anything unnecessary

### Q: Sound is crackling/distorted
**A:**
- **Check CPU**: `top` - should be < 80%
- **Check temperature**: `vcgencmd measure_temp`
- **Check JACK xruns**: Look for "xrun" in logs
- **Check audio levels**: Use `alsamixer`, ensure not clipping
- **Check USB power**: Use powered USB hub if needed

### Q: Latency feels too high
**A:**
```bash
# Decrease buffer size
nano ~/.jackdrc
# Change -p512 to -p256

# Restart JACK
systemctl --user restart jack.service

# Monitor for xruns - if you get them, buffer is too small
```

### Q: How do I record audio output?
**A:** Use `jack_capture`:
```bash
sudo apt install jack-capture

# Record to WAV
jack_capture -p Surge-XT:out_1 -p Surge-XT:out_2

# Stop with Ctrl+C
```

## MPE Questions

### Q: Per-note pitch bend works, but not pressure/timbre
**A:**
- **Check Roli settings**: Use Roli Dashboard to ensure all axes enabled
- **Check Surge MPE config**: Menu > MPE Settings > ensure enabled
- **Check MIDI mapping**: Some presets don't respond to pressure/timbre
- **Check CC74**: In Surge, ensure Y-axis (CC74) is mapped correctly

### Q: How do I know if MPE is working?
**A:**
```bash
# Monitor raw MIDI
aseqdump -p <roli-port>

# Play notes - should see:
# - Note On on channel 2-15 (not 1!)
# - Pitch Bend messages
# - Poly Aftertouch (pressure)
# - CC74 (timbre/Y-axis)
```

### Q: Some presets don't respond to MPE
**A:** Normal. Not all Surge presets are designed for MPE:
- **Pads**: Usually good for MPE
- **Leads**: Good for pitch bends
- **Drums**: Don't need MPE
- **Arps**: Can be weird with MPE

Create your own MPE-optimized presets.

### Q: Can I use this with a regular MIDI keyboard?
**A:** Yes! Surge XT works fine with regular MIDI. You just won't get per-note expression. Set Surge to non-MPE mode.

## Encoder Questions

### Q: Encoders not responding
**A:**
```bash
# Check GPIO state
gpio readall

# Run encoder script manually to see errors
cd ~/pisurge
python3 encoder_controller.py

# Rotate encoder - should see output
```

### Q: How do I remap an encoder?
**A:** Edit `encoder_controller.py`:
```python
# Change MIDI CC number
MIDI_CC = {
    'spare1': 71,  # Change from 1 to 71 (resonance)
    ...
}
```

### Q: Encoder values jumping/erratic
**A:**
- Add hardware debouncing (capacitors)
- Increase `bounce_time` in encoder initialization
- Use better quality encoders (Alps, Bourns)

### Q: Can I use buttons on encoders?
**A:** Yes! Button handlers are already in the code, just not mapped yet. Add your own actions:
```python
def _on_button_press(self, encoder_name):
    if encoder_name == 'volume':
        # Mute/unmute
        self._send_midi_cc('volume', 0)
```

## Performance Questions

### Q: Boot time is > 30 seconds
**A:**
```bash
# Check what's slow
systemd-analyze blame

# Run boot optimization
sudo ./boot_config.sh

# Disable WiFi/Bluetooth if on Ethernet
sudo nano /boot/config.txt
# Add:
# dtoverlay=disable-wifi
# dtoverlay=disable-bt
```

### Q: CPU usage is too high
**A:**
1. **Check temperature first**: Thermal throttling?
2. **Reduce Surge CPU**:
   - Lower polyphony
   - Disable HQ mode
   - Use simpler effects
3. **Stop encoders** (if testing): `systemctl --user stop encoders.service`
4. **Overclock Pi** (advanced): Edit `/boot/config.txt`

### Q: What's the polyphony limit?
**A:** Depends on preset complexity:
- Simple presets: 32+ voices
- Complex presets: 8-16 voices
- With effects: 8-12 voices

Test and tune for your needs.

## Preset Questions

### Q: How do I navigate presets with encoders?
**A:** The encoder script sends MIDI CC 20/21 for category/patch navigation. However, Surge XT doesn't natively support MIDI preset navigation.

**Solutions**:
1. **Use OSC** (recommended): See SURGE_CONFIG.md for OSC setup
2. **MIDI Program Change**: Load presets into banks, send PC messages
3. **Custom Surge fork**: Add MIDI CC preset nav (advanced)

### Q: How many presets can I have?
**A:** Limited only by storage:
- MicroSD has plenty of space
- Surge loads presets on-demand (fast)
- Organize into categories for easy navigation

### Q: Can I import DX7 patches?
**A:** No. Surge XT is not a DX7 emulator. It's FM-capable but not DX7-compatible.

For DX7: Use Dexed plugin (but adds plugin hosting complexity)

### Q: Where do I find more Surge presets?
**A:**
- Surge XT comes with 3000+ factory presets
- Community presets: Check Surge Discord/forum
- Create your own!

## Networking Questions

### Q: How do I access the Pi remotely?
**A:**
```bash
# SSH (command line)
ssh pi@<pi-ip>

# VNC (GUI, if needed)
# Install: sudo apt install realvnc-vnc-server
# Enable: sudo raspi-config > Interface > VNC > Enable
```

### Q: Can I control this over WiFi/Ethernet?
**A:** Yes! The encoders are local (GPIO), but you can:
- SSH in to change settings
- Send OSC messages over network
- Use MIDI over network (rtpmidi)

### Q: Can I use this with Ableton/DAW?
**A:** Not directly. This is a standalone synth module.

To use with DAW:
- Send MIDI from DAW to Roli
- Roli sends MPE to Pi
- Pi sends audio back to DAW (via USB audio)
- Adds latency, not ideal

## Troubleshooting

### Q: Nothing works after reboot
**A:**
```bash
# Check all services
~/pisurge/monitor.sh

# Look for errors
journalctl --user -u jack.service
journalctl --user -u surge.service
```

### Q: Surge XT won't launch
**A:**
```bash
# Try running manually
Surge-XT

# Check for missing libraries
ldd /usr/local/bin/Surge-XT

# Reinstall if needed
```

### Q: USB devices not detected
**A:**
```bash
# List USB devices
lsusb

# Check dmesg for errors
dmesg | tail -50

# Try different USB port
# Try powered USB hub
```

## Advanced Topics

### Q: Can I run multiple instances of Surge?
**A:** Yes, but defeats the purpose of this build. Each instance = more CPU.

Better: Use Surge's layering features within one instance.

### Q: Can I add a sequencer?
**A:** Not in this build. For sequencing, use:
- External hardware sequencer → Roli → Pi
- Or add Seq24/Ardour (but adds complexity)

### Q: Can I build a custom Surge fork with headless mode?
**A:** Yes! Surge is open source. You'd need to:
1. Fork Surge repo
2. Add headless rendering (remove GUI dependencies)
3. Add MIDI preset navigation
4. Build for ARM64

This is advanced but doable.

### Q: How do I contribute to this project?
**A:** Great! Areas that need work:
- OSC-based preset navigation
- Display support (show patch names)
- Better boot time optimization
- Surge preset library for MPE
- Alternative synth support

See GitHub issues for current tasks.

## Still Having Issues?

1. **Check logs**: `journalctl --user -u <service-name> -f`
2. **Search issues**: GitHub repo issues page
3. **Ask community**: Surge Discord, Lines forum
4. **File bug report**: Include logs and `monitor.sh` output
