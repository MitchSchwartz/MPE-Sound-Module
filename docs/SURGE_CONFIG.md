# Surge XT Configuration Guide

## Initial Setup

### First Launch
```bash
# Start JACK
systemctl --user start jack.service

# Launch Surge XT manually
Surge-XT
```

On first launch, Surge XT will create config files in `~/.config/surge-xt/`

## MPE Configuration

### Enable MPE Mode
1. Menu > MPE Settings > Enable MPE
2. MPE Pitch Bend Range: **48 semitones** (recommended for Roli Seaboard)
3. Channel Split: **Lower Zone** (channels 2-15)
4. Channel 1: Global controls

### MPE Pitch Bend Range Explained
- **24 semitones** (±2 octaves): More control, less range
- **48 semitones** (±4 octaves): Sweet spot for expressive playing
- **96 semitones** (±8 octaves): Maximum range, harder to control

Test different values to find your preference.

### Verify MPE Input
1. Open Surge XT
2. Play a note on Roli
3. Slide finger left/right - pitch should bend smoothly
4. Increase pressure - timbre should change
5. Slide finger up/down - brightness/filter should change

If any axis doesn't work:
- Check Roli MPE mode is enabled (Roli Dashboard app)
- Verify MIDI mappings in Surge

## MIDI Configuration

### MIDI Input
Surge XT should auto-detect Roli controller.

Verify in: Menu > MIDI Settings
- Enable all Roli MIDI inputs
- Disable keyboard input (reduces latency)

### MIDI Learn for Encoders

To map encoders to Surge parameters:

1. Right-click any Surge knob/slider
2. Select "MIDI Learn"
3. Rotate the encoder
4. Mapping is saved automatically

### Recommended Encoder Mappings

| Encoder | MIDI CC | Surge Parameter | Notes |
|---------|---------|-----------------|-------|
| Category | CC 20 | N/A | Handled by encoder script |
| Patch | CC 21 | N/A | Handled by encoder script |
| Volume | CC 7 | Scene > Volume | Standard MIDI volume |
| Spare 1 | CC 1 | Mod Wheel | Global modulation depth |
| Spare 2 | CC 74 | Scene > Filter Cutoff | Quick tone adjustment |

**Note**: Category/Patch navigation is handled by the encoder controller script sending program change or CC messages. This may require custom scripting depending on Surge's preset navigation API.

## Audio Configuration

### JACK Output
Surge XT should automatically connect to JACK outputs.

Verify connections:
```bash
jack_lsp -c
```

You should see:
```
Surge-XT:out_1
  system:playback_1
Surge-XT:out_2
  system:playback_2
```

### Sample Rate
Match Surge and JACK sample rates:
- **48kHz recommended** - good balance of quality and CPU
- 44.1kHz - lower CPU, CD quality
- 96kHz - higher CPU, diminishing returns on Pi

Set in ~/.jackdrc: `-r48000`

### Buffer Size
Larger buffers = less CPU, more latency
Smaller buffers = more CPU, less latency

Start with: `-p512` (10-12ms latency at 48kHz)

If you get xruns (dropouts):
- Increase to `-p1024` (20-24ms latency)

If latency feels sluggish:
- Decrease to `-p256` (5-6ms latency)
- May need CPU optimization

### Monitoring Latency
```bash
# Check JACK latency
jack_latency

# Monitor xruns (audio dropouts)
# Should show "0 xruns" during playback
```

## Preset Organization

### Surge XT Preset Locations
- **Factory presets**: `/usr/share/surge-xt/presets/` (or similar)
- **User presets**: `~/.local/share/surge-xt/presets/`

### Organizing for Encoder Navigation

Create a performance preset structure:

```
~/.local/share/surge-xt/presets/
├── 1-Live/
│   ├── 1-Pads/
│   │   ├── Warm Pad.fxp
│   │   ├── Bright Pad.fxp
│   │   └── Dark Pad.fxp
│   ├── 2-Leads/
│   │   ├── Smooth Lead.fxp
│   │   └── Aggressive Lead.fxp
│   ├── 3-Basses/
│   │   ├── Sub Bass.fxp
│   │   └── Growl Bass.fxp
│   └── 4-Keys/
│       ├── Electric Piano.fxp
│       └── Wurlitzer.fxp
├── 2-Experimental/
│   └── ...
└── 3-Templates/
    └── ...
```

**Benefits**:
- Numbered folders load in order
- Easy to navigate with category/patch encoders
- Separate live vs studio presets

### Preset Naming Convention
Use numbered prefixes for predictable ordering:

```
001_Warm_Pad.fxp
002_Bright_Pad.fxp
003_Dark_Pad.fxp
```

## Performance Optimization

### Reduce CPU Usage

1. **Limit polyphony**: Menu > MPE Settings > Max Voices
   - Start with 16 voices
   - Increase if you need more
   - Each voice = more CPU

2. **Disable unused effects**:
   - Only enable effects you need
   - Reverb is most expensive
   - Use simpler algorithms when possible

3. **Reduce oscillator quality** (if needed):
   - Menu > Settings > Audio > High Quality
   - Disable if you need more CPU headroom

4. **Use simpler wavetables**:
   - Classic wavetables < Modern wavetables < User wavetables

### Monitoring CPU
```bash
# Check overall CPU usage
top

# Check per-core usage
htop

# Surge XT should use < 80% of one core for stable performance
```

## Troubleshooting

### No Sound
1. Check JACK is running: `jack_lsp`
2. Check Surge connected to JACK: `jack_lsp -c`
3. Check audio output not muted in `alsamixer`
4. Check Sound Blaster S3 output volume

### MPE Not Working
1. Verify MPE enabled in Surge (Menu > MPE Settings)
2. Check Roli is in MPE mode (Roli Dashboard app)
3. Test MIDI input: `aseqdump -p <roli-port>`
4. Look for channel 2-15 note messages (not channel 1)

### Preset Won't Load
1. Check preset file format (.fxp for Surge XT)
2. Verify preset path is readable: `ls -la ~/.local/share/surge-xt/presets/`
3. Check Surge XT logs for errors

### High Latency
1. Decrease JACK buffer: `-p512` → `-p256`
2. Check CPU isn't maxed: `top`
3. Verify no thermal throttling: `vcgencmd measure_temp`
4. Close unnecessary services: `systemctl --user stop encoders.service`

### Crackling/Distortion
1. Check for JACK xruns (buffer underruns)
2. Increase buffer size: `-p512` → `-p1024`
3. Reduce Surge polyphony
4. Disable expensive effects (reverb)
5. Check CPU temperature

## Advanced: Scripting Preset Navigation

Surge XT doesn't expose a MIDI-mappable "next/previous preset" function by default.

### Option 1: OSC Control (Recommended)
Surge XT supports OSC (Open Sound Control) for parameter control.

Enable OSC in Surge:
- Menu > Settings > OSC > Enable

Send OSC messages from Python:
```python
from pythonosc import udp_client

client = udp_client.SimpleUDPClient("127.0.0.1", 53280)
client.send_message("/surge/preset/next", 1)  # Next preset
client.send_message("/surge/preset/prev", 1)  # Previous preset
```

### Option 2: MIDI Program Change
Configure Surge to map MIDI Program Change to preset slots:
- Load presets 0-127 in order
- Send MIDI PC messages from encoder script

### Option 3: Custom Surge Fork
Fork Surge XT and add MIDI CC support for preset navigation.
- More work, but cleanest solution
- Can submit as upstream PR

## Recommended Starting Presets

Test these factory presets for MPE compatibility:

**Good for MPE**:
- "Pad" category - most respond well to pressure/timbre
- "Lead" category - expressive with pitch bends
- "Keys" category - piano/EP sounds with MPE feel

**Avoid for MPE**:
- Arpeggiated patches (timing gets weird)
- Drum kits (MPE not needed)
- Heavy modulation patches (can be overwhelming)

## Saving Your Configuration

Surge XT auto-saves:
- MPE settings
- MIDI mappings
- Audio settings

But NOT:
- Current preset (reloads default on launch)

To load a specific preset on launch, create a startup script:
```bash
# Future enhancement: Load default.fxp on boot
```

## Backup Your Presets

```bash
# Backup user presets
cd ~/.local/share/surge-xt/
tar czf ~/surge-presets-backup-$(date +%Y%m%d).tar.gz presets/

# Restore
cd ~/.local/share/surge-xt/
tar xzf ~/surge-presets-backup-YYYYMMDD.tar.gz
```
