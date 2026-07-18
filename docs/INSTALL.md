# Installation Guide

## Prerequisites

- Raspberry Pi 4 or 5 (4GB+ RAM recommended)
- MicroSD card (32GB+ recommended)
- Sound Blaster S3 USB audio interface
- Roli Seaboard/Lightpad MPE controller
- 5x KY-040 rotary encoders
- 3.5" display (optional for milestone 1)

## Phase 1: Base System Setup

### 1. Flash Raspberry Pi OS Lite

Download Raspberry Pi OS Lite (64-bit) from https://www.raspberrypi.com/software/

Use Raspberry Pi Imager:
- OS: Raspberry Pi OS Lite (64-bit)
- Configure WiFi/SSH via imager settings
- Username: `pi`
- Enable SSH

### 2. First Boot

```bash
# SSH into Pi
ssh pi@<pi-ip-address>

# Update system
sudo apt update && sudo apt upgrade -y

# Clone this repository
cd ~
git clone <repository-url> pisurge
cd pisurge
```

### 3. Run Installation Script

```bash
chmod +x install.sh
./install.sh
```

This script will:
- Install JACK and dependencies
- Build/install Surge XT ARM binary
- Install Python dependencies
- Configure system services
- Optimize boot time

### 4. Reboot

```bash
sudo reboot
```

## Phase 2: Audio Configuration

### 1. Identify Sound Blaster S3

```bash
# List audio devices
aplay -l
cat /proc/asound/cards

# Should see Sound Blaster S3, note card number
```

### 2. Configure JACK

Edit `~/.jackdrc` (created by install script) and update device:

```bash
/usr/bin/jackd -dalsa -dhw:X -r48000 -p512 -n3
```

Replace `X` with Sound Blaster S3 card number.

### 3. Test JACK

```bash
# Stop auto-started JACK if running
systemctl --user stop jack.service

# Start manually for testing
jackd -dalsa -dhw:X -r48000 -p512 -n3

# In another terminal, check JACK is running
jack_lsp
```

## Phase 3: Surge XT Setup

### 1. Verify Surge Build

```bash
# Should be installed at /usr/local/bin/Surge-XT
Surge-XT --help
```

### 2. Configure Surge for MPE

Launch Surge manually first time to generate config:

```bash
# Start JACK first
systemctl --user start jack.service

# Launch Surge
Surge-XT

# In Surge:
# - Menu > MPE Settings > Enable MPE
# - Set MPE pitch bend range (typically 48 semitones)
# - Menu > MIDI Settings > Select Roli device as input
# - Menu > Audio Settings > JACK output
# - Load a preset and test with Roli controller
```

### 3. Test MPE Input

```bash
# List MIDI devices
aconnect -l

# Should see Roli device
# Surge will auto-connect to it
```

## Phase 4: Encoder Setup

### 1. Wire Encoders to GPIO

Wiring guide for KY-040 encoders:

| Encoder | CLK (A) | DT (B) | SW | + | GND |
|---------|---------|--------|-----|-----|-----|
| Category| GPIO 17 | GPIO 27| GPIO 22 | 3.3V | GND |
| Patch   | GPIO 23 | GPIO 24| GPIO 25 | 3.3V | GND |
| Volume  | GPIO 5  | GPIO 6 | GPIO 13 | 3.3V | GND |
| Spare 1 | GPIO 19 | GPIO 26| GPIO 16 | 3.3V | GND |
| Spare 2 | GPIO 20 | GPIO 21| GPIO 12 | 3.3V | GND |

### 2. Test Encoder Script

```bash
# With JACK and Surge running
cd ~/pisurge
python3 encoder_controller.py

# Rotate encoders and verify MIDI messages in console
```

## Phase 5: Auto-Start Services

### 1. Enable Services

```bash
systemctl --user enable jack.service
systemctl --user enable surge.service
systemctl --user enable encoders.service
```

### 2. Reboot and Test

```bash
sudo reboot

# After boot, verify all running
systemctl --user status jack.service
systemctl --user status surge.service
systemctl --user status encoders.service
```

## Phase 6: Boot Optimization

Run boot optimization script:

```bash
cd ~/pisurge
sudo ./boot_config.sh
sudo reboot
```

This disables unnecessary services and optimizes boot time.

## Milestone 1 Validation

**Goal**: Surge XT runs acceptably with MPE input before wiring encoders.

Test checklist:
- [ ] JACK starts without xruns
- [ ] Surge XT launches and connects to JACK
- [ ] Roli controller sends MPE to Surge
- [ ] Per-note pitch bend, pressure, timbre work correctly
- [ ] Preset switching is < 1 second
- [ ] Audio output to Sound Blaster S3 is clean
- [ ] Boot to audio-ready < 30 seconds

## Troubleshooting

### JACK won't start
```bash
# Check device number
aplay -l

# Test JACK manually
jackd -dalsa -dhw:X -r48000 -p512 -n3 -v

# Check for conflicts
sudo fuser -v /dev/snd/*
```

### Surge won't connect to JACK
```bash
# Verify JACK running
jack_lsp

# Check Surge logs
journalctl --user -u surge.service -f
```

### No MIDI from Roli
```bash
# List MIDI devices
aconnect -l

# Monitor raw MIDI
aseqdump -p <roli-port>

# Verify MPE enabled in Surge
```

### Audio dropouts/xruns
```bash
# Increase JACK buffer size in ~/.jackdrc
# -p512 -> -p1024

# Reduce CPU usage
# Stop encoders.service if needed
systemctl --user stop encoders.service
```

## Next Steps

After milestone 1 validation:
- Wire and test encoders
- Configure preset organization
- Add display support for patch names
- Create performance preset library
