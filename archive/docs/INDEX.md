# Pi-Surge-MPE Documentation Index

**Archived — superseded by [README.md](../../README.md) at repo root.**

## Quick Navigation

**New users start here**: [README.md](../../README.md)

## Documentation Overview

### Core Documentation (Read First)

| File | Purpose | When to Read |
|------|---------|--------------|
| [README.md](../../README.md) | Project overview (current) | First — start at repo root |
| [QUICKSTART.md](QUICKSTART.md) | *(archived — file removed)* | — |
| [INSTALL.md](INSTALL.md) | Detailed installation steps | During setup for complete guide |
| [HARDWARE.md](HARDWARE.md) | Hardware specs, wiring, BOM | Before ordering parts |
| [WIRING_DIAGRAM.txt](WIRING_DIAGRAM.txt) | Visual wiring guide | When connecting encoders |

### Configuration & Tuning

| File | Purpose | When to Read |
|------|---------|--------------|
| [SURGE_CONFIG.md](SURGE_CONFIG.md) | Surge XT setup, MPE config, optimization | After audio works, for tuning |
| [USB_BOOT.md](USB_BOOT.md) | Boot from USB drive instead of SD card | For testing without SD card |
| [FAQ.md](FAQ.md) | Troubleshooting, common questions | When you have problems |

### Reference Documentation

| File | Purpose | When to Read |
|------|---------|--------------|
| [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) | Repository layout, file organization | For understanding codebase |
| [ROADMAP.md](ROADMAP.md) | Development plan, milestones, future | For contributors/roadmap |
| [LICENSE](LICENSE) | MIT License terms | If redistributing/forking |
| [INDEX.md](INDEX.md) | This file - documentation index | Navigation/overview |

## Executable Files

### Scripts

| File | Purpose | Usage |
|------|---------|-------|
| [install.sh](install.sh) | Main installation script | Run once: `./install.sh` |
| [boot_config.sh](boot_config.sh) | Boot optimization | Run after Milestone 1: `sudo ./boot_config.sh` |
| [encoder_controller.py](encoder_controller.py) | Encoder → MIDI controller | Auto-runs via systemd |

### Helper Scripts (Created by install.sh)

Located in `~/pisurge/` after installation:

| File | Purpose | Usage |
|------|---------|-------|
| `check_audio.sh` | List audio devices | `~/pisurge/check_audio.sh` |
| `check_midi.sh` | List MIDI devices | `~/pisurge/check_midi.sh` |
| `monitor.sh` | Service status + JACK connections | `~/pisurge/monitor.sh` |
| `measure_boot_time.sh` | Analyze boot performance | `~/pisurge/measure_boot_time.sh` |

## Configuration Files

### Created by install.sh

| File | Location | Purpose |
|------|----------|---------|
| `.jackdrc` | `~/.jackdrc` | JACK audio server config |
| `jack.service` | `~/.config/systemd/user/` | JACK systemd service |
| `surge.service` | `~/.config/systemd/user/` | Surge XT systemd service |
| `encoders.service` | `~/.config/systemd/user/` | Encoder controller systemd service |

### Created by Surge XT

| File | Location | Purpose |
|------|----------|---------|
| `SurgeXT.conf` | `~/.config/surge-xt/` | Surge settings (MPE, MIDI) |
| User presets | `~/.local/share/surge-xt/presets/` | Custom preset library |

## Dependencies

### System Packages (installed by install.sh)
- JACK audio (jackd2, qjackctl)
- Build tools (gcc, cmake, etc.)
- Development libraries (ALSA, X11, etc.)

### Python Packages
See [requirements.txt](requirements.txt):
- python-rtmidi
- gpiozero
- RPi.GPIO

### External Downloads
- **Surge XT ARM binary**: Download from [Surge releases](https://github.com/surge-synthesizer/releases-xt/releases)

## Documentation by Topic

### Installation

1. Read [README.md](README.md) - Understand project
2. Read [HARDWARE.md](HARDWARE.md) - Order parts
3. Follow [README.md](../../README.md) - Get running
4. Reference [INSTALL.md](INSTALL.md) - Detailed steps
5. Check [FAQ.md](FAQ.md) - If problems arise

### Hardware Assembly

1. Read [HARDWARE.md](HARDWARE.md) - Complete hardware guide
2. Reference [WIRING_DIAGRAM.txt](WIRING_DIAGRAM.txt) - Visual wiring
3. Check [FAQ.md](FAQ.md) - Hardware troubleshooting

### Software Configuration

1. Follow [INSTALL.md](INSTALL.md) - Base installation
2. Read [SURGE_CONFIG.md](SURGE_CONFIG.md) - Surge optimization
3. Run `boot_config.sh` - Boot optimization
4. Check [FAQ.md](FAQ.md) - Software troubleshooting

### Performance Tuning

1. Read [SURGE_CONFIG.md](SURGE_CONFIG.md) - Surge CPU optimization
2. Read [INSTALL.md](INSTALL.md) - JACK tuning
3. Run `boot_config.sh` - System optimization
4. Check [FAQ.md](FAQ.md) - Performance issues

### Development/Customization

1. Read [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) - Codebase layout
2. Read [encoder_controller.py](encoder_controller.py) - Controller code
3. Read [ROADMAP.md](ROADMAP.md) - Future plans
4. Check GitHub Issues - Known bugs/features

### Troubleshooting

1. Check [README.md](../../README.md) - Quick fixes
2. Check [FAQ.md](FAQ.md) - Comprehensive troubleshooting
3. Check [INSTALL.md](INSTALL.md) - Validation steps
4. Check service logs: `journalctl --user -u <service>`

## File Size Summary

Total documentation: ~90 KB
- Largest: ROADMAP.md (13 KB), FAQ.md (12 KB), WIRING_DIAGRAM.txt (12 KB)
- Scripts: ~17 KB total
- Python code: 7 KB

**Complete repository**: < 150 KB (excluding .git)

## External Resources

### Official Documentation
- [Surge XT Manual](https://surge-synthesizer.github.io/)
- [JACK Audio Documentation](https://jackaudio.org/documentation/)
- [Raspberry Pi Documentation](https://www.raspberrypi.com/documentation/)

### Hardware Datasheets
- [KY-040 Rotary Encoder](https://www.handsontec.com/dataspecs/module/Rotary%20Encoder.pdf)
- [Raspberry Pi GPIO Pinout](https://pinout.xyz/)
- [Sound Blaster S3 Specs](https://www.creative.com/products/sound-blaster-s3)

### Related Projects
- [Surge Synthesizer](https://github.com/surge-synthesizer/surge)
- [Zynthian](https://zynthian.org/)
- [Patchbox OS](https://blokas.io/patchbox-os/)

## Contribution Guidelines

### Reporting Issues
1. Check [FAQ.md](FAQ.md) first
2. Search existing GitHub Issues
3. If new, provide:
   - Output of `~/pisurge/monitor.sh`
   - Service logs: `journalctl --user -u <service>`
   - Pi model, OS version, Surge version
   - Steps to reproduce

### Contributing Code
1. Read [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)
2. Check [ROADMAP.md](ROADMAP.md) for planned features
3. Discuss major changes in GitHub Issues first
4. Submit Pull Request with:
   - Clear description
   - Testing on actual hardware
   - Updated documentation if needed

### Contributing Documentation
- Fix typos/errors: Direct PR
- Add examples: PR with description
- New guides: Discuss in Issues first

## Version History

### v0.1 (Current)
- Initial documentation release
- All core scripts complete
- Ready for hardware testing

See [ROADMAP.md](ROADMAP.md) for future versions.

## Support

### Self-Help
1. [README.md](../../README.md) - Current project overview
2. [FAQ.md](FAQ.md) - Comprehensive Q&A
3. [INSTALL.md](INSTALL.md) - Validation steps

### Community Support
- GitHub Issues (bugs)
- GitHub Discussions (questions)

### Related Communities
- [Surge Discord](https://discord.gg/surge-synth)
- [Lines Forum](https://llllllll.co/)
- [Raspberry Pi Forums](https://forums.raspberrypi.com/)

## License

MIT License - See [LICENSE](LICENSE)

Free to use, modify, distribute with attribution.

## Credits

### Software Used
- [Surge XT](https://surge-synthesizer.github.io/) - Synthesizer (GPL3)
- [JACK Audio](https://jackaudio.org/) - Audio server (GPL/LGPL)
- [Python](https://www.python.org/) - Scripting (PSF)
- [gpiozero](https://gpiozero.readthedocs.io/) - GPIO library (BSD)
- [python-rtmidi](https://spotlightkid.github.io/python-rtmidi/) - MIDI library (MIT)

### Inspiration
- Zynthian project (for showing what's possible)
- Surge community (for amazing synth)
- DIY synth community (for encouragement)

---

**Last Updated**: 2025-01-XX (v0.1)

**Maintained By**: Pi-Surge-MPE Project

**Repository**: https://github.com/[your-username]/pi-surge-mpe
