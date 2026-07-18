# Pi-Surge-MPE

**Status**: ✅ **FULLY OPERATIONAL** - Headless MPE synthesizer module

A dedicated MPE sound module for live performance using Raspberry Pi + Surge XT CLI.

## Current System (Working)

### Hardware
- **SBC**: Raspberry Pi 5 (or 4)
- **Audio**: Sound Blaster Play! 3 USB audio interface
- **MIDI**: Roli Seaboard BLOCK (USB, auto-connects)
- **Network**: surge.local / 192.168.1.203
- **User**: mitch

### Software Stack
- **OS**: Raspberry Pi OS Lite 64-bit (Debian Trixie)
- **Audio**: ALSA (direct, no JACK)
- **Synth**: Surge XT CLI (headless, v1.3+)
- **MPE**: Always enabled (48 semitones pitch bend)
- **Auto-start**: systemd service
- **Patches**: 3,192 available (639 factory + 2,553 third-party)

### Architecture

```
[Roli Seaboard] --USB MIDI--> [Surge XT CLI] --ALSA--> [Sound Blaster Play! 3]
                                     ↑
                                     |
                            (MPE always enabled)
                            (Auto-starts on boot)
                            (Headless daemon)
```

## What Works

✅ Boot to ready in ~25 seconds
✅ MPE always enabled (no manual config)
✅ Roli auto-connects on Surge restart
✅ 44.1kHz audio, 512 buffer (~11ms latency)
✅ Church.fxp default patch
✅ SSH key authentication
✅ No GUI/VNC needed for operation

## Quick Start

```bash
# Check status
ssh 192.168.1.203 'systemctl status surge-xt-cli'

# View logs
ssh 192.168.1.203 'tail -f ~/surge-cli.log'

# Restart (e.g., after plugging in Roli)
ssh 192.168.1.203 'sudo systemctl restart surge-xt-cli'
```

**Full command reference**: See [COMMANDS.md](COMMANDS.md) and [QUICKSTART.md](QUICKSTART.md)

## Documentation

- **[COMMANDS.md](COMMANDS.md)** - Complete command reference (backup, deploy, troubleshoot) ⭐
- **[BACKUP_GUIDE.md](BACKUP_GUIDE.md)** - Backup and disaster recovery guide
- **[QUICKSTART.md](QUICKSTART.md)** - Quick reference commands
- **[CURRENT_STATE.md](CURRENT_STATE.md)** - Complete system documentation
- **[SETUP_COMPLETE.md](SETUP_COMPLETE.md)** - What was configured and why
- **[docs/SURGE_CLI_HEADLESS_SETUP.md](docs/SURGE_CLI_HEADLESS_SETUP.md)** - Technical deep dive + custom UI code

## Directory Structure (Pi)

```
/home/mitch/
├── start-surge-cli.sh              # Startup script
├── surge-cli.log                   # Runtime log
└── surge/                          # Surge XT source + build
    ├── build/surge_xt_products/
    │   └── surge-xt-cli            # Main binary
    └── resources/data/
        ├── patches_factory/        # 639 factory patches
        └── patches_3rdparty/       # 2,553 third-party patches

/etc/systemd/system/
└── surge-xt-cli.service            # Auto-start service
```

## Directory Structure (Repo)

```
MPE Module/
├── README.md                       # This file
├── CURRENT_STATE.md                # Complete system state
├── QUICKSTART.md                   # Quick reference
├── SETUP_COMPLETE.md               # Setup summary
├── STATUS.txt                      # Current status
├── scripts/
│   └── start-surge-cli.sh          # Startup script (template)
├── config/
│   └── surge-xt-cli.service        # Service file (template)
└── docs/
    └── SURGE_CLI_HEADLESS_SETUP.md # Technical documentation
```

## Backup & Disaster Recovery

This repo contains **EVERYTHING** needed to restore the device from scratch.

### Complete Device Backup

**Pull everything from device to git:**
```bash
./scripts/pull-all-from-device.sh    # One-time: pulls binary + all patches (~450MB)
./scripts/sync-from-device.sh        # Ongoing: pulls config/user changes
git add -A
git commit -m "Backup from device"
git push
```

### Disaster Recovery

**SD card failed? Restore in 3 steps:**

```bash
# 1. Flash fresh Raspberry Pi OS, configure SSH
# 2. Clone this repo
git clone https://github.com/yourusername/MPE-Module.git
cd MPE-Module

# 3. Deploy everything
./scripts/deploy-all.sh
```

✅ Device restored! Binary + patches + configs all deployed.

### What's Backed Up

- ✅ Surge XT CLI binary (24MB)
- ✅ All 3,192 patches (422MB: 639 factory + 2,553 third-party)
- ✅ System configs (systemd services, udev rules)
- ✅ Python scripts and shell scripts
- ✅ User preferences and custom patches
- ✅ Complete documentation

**Repo size:** ~450MB total

**First clone:** 2-3 minutes
**Daily pushes:** 3-5 seconds (git only sends changed files)

See [BACKUP_GUIDE.md](BACKUP_GUIDE.md) for detailed backup procedures.

---

## Future: Custom Preset Browser

**Planned hardware**:
- 1.3" OLED display (I2C)
- 2x Rotary encoders (GPIO)

**Features**:
- Browse 3,192 patches via encoders
- Display category + patch name on OLED
- Send OSC/MIDI to Surge to switch patches

**Status**: Documented, code examples provided, hardware not yet built

See [docs/SURGE_CLI_HEADLESS_SETUP.md](docs/SURGE_CLI_HEADLESS_SETUP.md) for full implementation.

## Why This Approach?

### Why Surge XT CLI (not GUI)?
- No X11/VNC overhead
- Auto MIDI connection via `--all-midi-inputs`
- MPE always on via `--mpe-enable`
- Built for headless/embedded use
- Lower latency, more reliable

### Why Not Zynthian?
- Preset saving unreliable
- MPE incompatible with quick switching
- Over-engineered for single synth
- We only need Surge XT, nothing else

### Why Direct ALSA (not JACK)?
- Simpler setup
- Lower latency
- One less thing to configure
- Surge CLI works great with ALSA

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — free for personal use, modification, and non-commercial purposes. No resale or commercial use without a separate agreement.
