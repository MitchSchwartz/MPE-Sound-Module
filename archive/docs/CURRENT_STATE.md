# Current System State - COMPLETE WORKING CONFIGURATION

**Last Updated**: 2025-12-26 22:05 EST
**Status**: ✅ FULLY OPERATIONAL

This document describes the **exact current state** of the working Pi-Surge-MPE system.

---

## System Overview

### What Works
- ✅ Surge XT CLI runs headless (no GUI)
- ✅ Auto-starts on boot via systemd
- ✅ MPE always enabled (48 semitones pitch bend)
- ✅ Roli Seaboard auto-connects via MIDI
- ✅ Audio output to Sound Blaster Play! 3 USB
- ✅ Church.fxp patch loaded by default
- ✅ SSH key authentication (password-free access)
- ✅ 3,192 patches available (639 factory + 2,553 third-party)

### Hardware Configuration
- **Device**: Raspberry Pi 5 (or 4)
- **OS**: Raspberry Pi OS Lite 64-bit (Debian Trixie)
- **Audio**: Sound Blaster Play! 3 (USB)
- **MIDI**: Roli Seaboard BLOCK (USB)
- **Network**: surge.local / 192.168.1.203

---

## File Structure on Pi

### Critical System Files

```
/home/mitch/
├── start-surge-cli.sh              # Surge startup script
├── surge-cli.log                   # Runtime log
├── .ssh/
│   └── authorized_keys             # SSH public key for password-free access
├── .bash_profile.gui_backup        # Old GUI config (disabled)
├── .xinitrc.gui_backup            # Old X11 config (disabled)
└── surge/                          # Surge XT source + build
    ├── build/
    │   ├── surge_xt_products/
    │   │   └── surge-xt-cli        # Main CLI binary
    │   └── src/surge-xt/surge-xt_artefacts/Release/
    │       └── Standalone/
    │           └── Surge XT        # GUI binary (not used)
    └── resources/data/
        ├── patches_factory/        # 639 factory patches
        └── patches_3rdparty/       # 2,553 community patches

/etc/systemd/system/
└── surge-xt-cli.service            # Auto-start service

/home/mitch/.local/share/surge-xt/
├── patches_factory -> ~/surge/resources/data/patches_factory
└── patches_3rdparty -> ~/surge/resources/data/patches_3rdparty
```

---

## Configuration Files

### 1. /home/mitch/start-surge-cli.sh

```bash
#!/bin/bash
# Surge XT CLI - Headless startup script

SURGE_CLI="/home/mitch/surge/build/surge_xt_products/surge-xt-cli"
INIT_PATCH="/home/mitch/surge/resources/data/patches_factory/Keys/Church.fxp"
AUDIO_DEVICE="0.23"  # Sound Blaster Play! 3 - Front output
LOG_FILE="/home/mitch/surge-cli.log"

echo "$(date): Starting Surge XT CLI..." >> "$LOG_FILE"

"$SURGE_CLI" \
  --all-midi-inputs \
  --mpe-enable \
  --mpe-pitch-bend-range=48 \
  --init-patch="$INIT_PATCH" \
  --audio-interface="$AUDIO_DEVICE" \
  --no-stdin \
  >> "$LOG_FILE" 2>&1 &

SURGE_PID=$!
echo "$(date): Surge XT CLI started with PID $SURGE_PID" >> "$LOG_FILE"
echo "Surge XT CLI running (PID: $SURGE_PID)"
```

**Permissions**: `chmod +x`

### 2. /etc/systemd/system/surge-xt-cli.service

```ini
[Unit]
Description=Surge XT CLI Synthesizer (Headless)
After=sound.target

[Service]
Type=forking
User=mitch
WorkingDirectory=/home/mitch
ExecStart=/home/mitch/start-surge-cli.sh
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Status**: Enabled (auto-starts on boot)

### 3. ~/.ssh/authorized_keys (on Pi)

Contains SSH public key:
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH7Q4UZdaGgkR8WjzVxiCTEqKZF6sbRiQ0T4EJ5IabgJ claude-code-surge-pi
```

### 4. ~/.ssh/config (on Windows)

```
Host surge.local
    HostName surge.local
    User mitch
    IdentityFile ~/.ssh/surge_pi_key
    StrictHostKeyChecking accept-new

Host 192.168.1.203
    HostName 192.168.1.203
    User mitch
    IdentityFile ~/.ssh/surge_pi_key
    StrictHostKeyChecking accept-new
```

---

## How It Works

### Boot Sequence

```
1. Pi powers on
   └─> Raspberry Pi OS Lite boots (~15 seconds)

2. systemd starts multi-user.target
   └─> surge-xt-cli.service starts

3. Service runs /home/mitch/start-surge-cli.sh
   └─> Launches surge-xt-cli in background

4. Surge XT CLI initializes:
   ├─> Loads Church.fxp patch
   ├─> Enables MPE (48 semitones)
   ├─> Binds to all MIDI inputs
   ├─> Opens Sound Blaster audio device
   └─> Enters daemon mode (waits for MIDI)

5. System ready (~20-30 seconds total)
   └─> Plug in Roli → auto-connects → play!
```

### MIDI Auto-Connection

The `--all-midi-inputs` flag makes Surge automatically connect to:
- Roli Seaboard BLOCK
- Any USB MIDI keyboard
- Virtual MIDI ports
- MIDI interfaces

**Important**: If Roli is plugged in AFTER Surge starts, you must restart Surge:
```bash
ssh 192.168.1.203 'sudo systemctl restart surge-xt-cli'
```

### Audio Routing

```
Surge XT CLI
    ↓
ALSA (Direct)
    ↓
Sound Blaster Play! 3 (USB)
    ↓
Speakers/Headphones
```

**No JACK, no PulseAudio, no PipeWire** - direct ALSA for lowest latency.

**Audio Device**: `0.23` = "Sound Blaster Play! 3, USB Audio; Front output / input"
**Sample Rate**: 44.1kHz
**Buffer Size**: 512 samples (~11ms latency)

---

## Command Reference

### System Control

```bash
# Check status
ssh 192.168.1.203 'systemctl status surge-xt-cli'

# View logs (live)
ssh 192.168.1.203 'tail -f ~/surge-cli.log'

# View last 50 log lines
ssh 192.168.1.203 'tail -50 ~/surge-cli.log'

# Restart Surge (e.g., after plugging in Roli)
ssh 192.168.1.203 'sudo systemctl restart surge-xt-cli'

# Stop Surge
ssh 192.168.1.203 'sudo systemctl stop surge-xt-cli'

# Start Surge
ssh 192.168.1.203 'sudo systemctl start surge-xt-cli'

# Reboot Pi
ssh 192.168.1.203 'sudo reboot'

# Shutdown Pi
ssh 192.168.1.203 'sudo shutdown -h now'
```

### Diagnostics

```bash
# Check if Roli is detected
ssh 192.168.1.203 'lsusb | grep -i roli'

# List audio devices
ssh 192.168.1.203 'aplay -l'

# Check Surge process
ssh 192.168.1.203 'ps aux | grep surge-xt-cli'

# System journal for Surge service
ssh 192.168.1.203 'sudo journalctl -u surge-xt-cli -n 50'
```

### Patch Management

```bash
# List all factory patches
ssh 192.168.1.203 'find ~/surge/resources/data/patches_factory -name "*.fxp"'

# List all third-party patches
ssh 192.168.1.203 'find ~/surge/resources/data/patches_3rdparty -name "*.fxp"'

# Count total patches
ssh 192.168.1.203 'find ~/surge/resources/data -name "*.fxp" | wc -l'

# Search for specific patch
ssh 192.168.1.203 'find ~/surge/resources/data -name "*Bass*.fxp"'
```

---

## Changing Configuration

### Change Default Patch

1. SSH into Pi
2. Edit startup script:
```bash
ssh 192.168.1.203
nano ~/start-surge-cli.sh
```

3. Change this line:
```bash
INIT_PATCH="/home/mitch/surge/resources/data/patches_factory/Keys/Church.fxp"
```

4. Save and exit (Ctrl+O, Enter, Ctrl+X)

5. Restart Surge:
```bash
sudo systemctl restart surge-xt-cli
```

### Change Audio Device

If you want to use a different audio output (headphones, HDMI, etc.):

1. List available devices:
```bash
ssh 192.168.1.203 '/home/mitch/surge/build/surge_xt_products/surge-xt-cli --list-devices | grep "Output Audio"'
```

2. Edit startup script:
```bash
nano ~/start-surge-cli.sh
```

3. Change `AUDIO_DEVICE="0.23"` to your desired device number

4. Restart Surge

### Disable MPE (if needed)

Edit `start-surge-cli.sh` and remove these lines:
```bash
--mpe-enable \
--mpe-pitch-bend-range=48 \
```

---

## Network Configuration

### Hostnames

- **Primary**: `surge.local` (mDNS/Avahi)
- **Fallback**: `192.168.1.203` (DHCP - may change)

### SSH Access

**Password-free SSH** is configured using SSH keys:

**Private key location (Windows)**: `C:\Users\mitch\.ssh\surge_pi_key`
**Public key location (Pi)**: `/home/mitch/.ssh/authorized_keys`

**To connect**:
```bash
ssh surge.local
# or
ssh 192.168.1.203
```

---

## Performance Metrics

### Expected Performance
- **Boot time**: 20-30 seconds (from power-on to ready)
- **MIDI latency**: < 10ms
- **Audio latency**: ~11ms (512 sample buffer)
- **CPU usage**: 30-50% during playback
- **Memory**: ~250MB
- **Temperature**: < 60°C (idle), < 70°C (load)

### Actual Measured Performance
```
Service start time: ~1 second after boot completes
Surge initialization: ~1 second
Total boot-to-ready: ~25 seconds
Sample rate: 44100 Hz
Buffer size: 512 samples
Audio dropouts: None observed
Xruns: 0
```

---

## Troubleshooting

### Problem: Service won't start

**Check**:
```bash
sudo journalctl -u surge-xt-cli -n 50
```

**Common causes**:
- Audio device not found (check `aplay -l`)
- Script permissions (should be `chmod +x ~/start-surge-cli.sh`)
- Surge binary path incorrect

### Problem: No audio

**Check**:
1. Is Sound Blaster detected?
   ```bash
   aplay -l
   ```
2. Is correct device selected in script?
   ```bash
   cat ~/start-surge-cli.sh | grep AUDIO_DEVICE
   ```
3. Check Surge log:
   ```bash
   tail -30 ~/surge-cli.log
   ```

**Look for**: "Audio Starting: Sample Rate 44100 Hz"

### Problem: Roli not connecting

**Check**:
1. Is Roli detected via USB?
   ```bash
   lsusb | grep -i roli
   ```
2. Did Surge detect it?
   ```bash
   tail ~/surge-cli.log | grep "Seaboard BLOCK"
   ```

**Solution**: Restart Surge after plugging in Roli:
```bash
sudo systemctl restart surge-xt-cli
```

### Problem: MPE not working

**Check log for**:
```
MPE Status: Enabled
MPE Bend Range: 48
```

If it says "Disabled", the startup script didn't apply MPE flags correctly.

### Problem: Wrong patch loads

**Check**:
```bash
cat ~/start-surge-cli.sh | grep INIT_PATCH
```

Should show the full path to the desired .fxp file.

---

## Development History

### What We Tried (and Failed)

1. **GUI Surge XT with VNC**
   - ❌ X11 input issues (keyboard/mouse didn't work)
   - ❌ MPE settings didn't persist across reboots
   - ❌ MIDI device selection required manual GUI interaction
   - ❌ Heavy overhead from X11/Openbox

2. **JACK Audio**
   - ❌ Complex setup
   - ❌ Additional latency
   - ❌ Not needed for standalone Surge

3. **ALSA aconnect for MIDI**
   - ❌ Doesn't work with PipeWire
   - ❌ Required manual scripting

4. **Python MIDI RPN to enable MPE**
   - ❌ Complex, fragile
   - ❌ Timing issues
   - ❌ Not needed with CLI flags

### What Actually Worked

**Surge XT CLI** with command-line flags:
- `--all-midi-inputs` → auto MIDI connection
- `--mpe-enable` → MPE always on
- `--mpe-pitch-bend-range=48` → proper pitch bend
- `--init-patch` → default patch
- `--audio-interface` → specific audio device
- `--no-stdin` → daemon mode

**Key insight**: The CLI version was designed EXACTLY for this use case - embedded/headless operation.

---

## Future Enhancements

### Custom Preset Browser (Planned)

**Hardware**:
- 1.3" OLED display (SSD1306 or SH1106, I2C)
- 2x Rotary encoders (KY-040)

**Software**:
- Python app using `luma.oled`, `RPi.GPIO`, `python-osc`
- Scans preset directories
- Displays category/patch name on OLED
- Encoder 1: Navigate categories
- Encoder 2: Select patches
- Sends OSC or MIDI PC to Surge to switch patches

**See**: `docs/SURGE_CLI_HEADLESS_SETUP.md` for full implementation code

### Other Ideas

- **Foot controller**: USB MIDI foot pedal for patch switching
- **OLED status display**: Show current patch, CPU, temp
- **Web interface**: Control via browser on phone/tablet
- **OSC control**: Control Surge parameters from DAW or mobile app
- **Multiple instances**: Run multiple Surge instances with different patches

---

## Git Repository State

### Files That Should Be in Repo

**Documentation**:
- `README.md` (main project overview)
- `CURRENT_STATE.md` (this file)
- `SETUP_COMPLETE.md` (what was done)
- `QUICKSTART.md` (quick reference)
- `docs/SURGE_CLI_HEADLESS_SETUP.md` (technical deep dive)
- `STATUS.txt` (quick status check)

**Scripts** (to be created):
- `scripts/install-surge-cli.sh` (automate Surge install)
- `scripts/setup-service.sh` (automate service setup)
- `scripts/start-surge-cli.sh` (copy of Pi's startup script)

**Configuration** (as templates):
- `config/surge-xt-cli.service` (systemd service template)
- `config/ssh-config-example` (SSH config example)

### Files NOT in Repo

- Surge XT source code (it's in its own repo)
- Surge XT compiled binaries
- SSH private keys
- Log files

---

## Quick Start for New Setup

If you need to replicate this on another Pi:

1. **Install Pi OS Lite 64-bit**

2. **Clone Surge and build**:
```bash
cd ~
git clone https://github.com/surge-synthesizer/surge.git
cd surge
git submodule update --init --recursive
./build-linux.sh build --project=surge-xt-cli
```

3. **Copy configuration files**:
```bash
# From this repo (to be created)
cp config/start-surge-cli.sh ~/
chmod +x ~/start-surge-cli.sh
sudo cp config/surge-xt-cli.service /etc/systemd/system/
```

4. **Enable service**:
```bash
sudo systemctl daemon-reload
sudo systemctl enable surge-xt-cli.service
sudo systemctl start surge-xt-cli.service
```

5. **Set up SSH key** (from Windows):
```bash
ssh-copy-id mitch@surge.local
```

6. **Test**:
```bash
ssh surge.local 'systemctl status surge-xt-cli'
```

---

## Contact & Support

**This system was configured on**: 2025-12-26
**By**: Claude (Anthropic) with Mitch
**Pi Hostname**: surge.local
**Pi User**: mitch

**For future sessions**: Read this document first to understand the complete working state.

---

**System is OPERATIONAL and TESTED** ✅
