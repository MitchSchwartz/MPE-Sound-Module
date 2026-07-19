# Pi-Surge-MPE

**A dedicated MPE compatible sound module for your MPE instrument. No laptop. A pi with a  1.5" screen and one knob.**

Plug a Roli into a Raspberry Pi. Turn it on. Play. That's the whole interaction.

**Bootstrap, not a product.** This repo is a reference design and doc set for technical builders — SSH, git, CMake, wiring, systemd. Comfortable with a terminal (or AI-guided setup) is assumed. No installer, no prebuilt Surge binary yet, no plug-and-play path for non-dev Surge users.

---

## Why this exists

I wanted to just show up and play my MPE instruments without tech in the way. Latency, tech issue, big birght screens... wrong vibe.

What if my MPE instruments were just... instruments? ...portable 5-D electric pianos.

Under the hood it's [Surge XT](https://surge-synthesizer.github.io/) (free, open-source, genuinely powerful) running headless on a Pi, always in MPE mode. It boots straight to your sound library.   

Every patch is fully editable and MPE-assignable from your computer, across all five expression dimensions — it turns a Roli into a standalone instrument, outside the Equator ecosystem entirely. Surge XT's mod matrix lets you map pretty much any synth parameter (filter cutoff, wavetable position, FM amount, envelope times, effect sends, etc.) to any MPE dimension — pressure, timbre/Y-axis, per-note pitch bend, slide — so patches aren't limited to the handful of expression targets a controller ships with. Reasonably deep sound design, not just "pressure = volume."

**Think of is as the 'dumb phone' of digital instruments**  If you want a multi-engine DAWless workstation, that's [Zynthian](https://zynthian.org/). This is one instrument that does one thing without fuss (and doesn't fight you on MPE persistence the way Zynthian's Surge integration currently does).

## What it does

- Boots to sound-ready in **~25 seconds**
- Roli auto-connects
- MPE always on (48-semitone pitch bend, full pressure/timbre)
- **Stock loaded with Surge XT's 3,192 patches** on board (639 factory + 2,553 community), browsable live via one rotary encoder + a small OLED screen
- **Quick-access patch folder** — default `!Quick Access` under Surge user patches (leading `!` sorts first); rename via `MPE_FAVORITES_NAME`. Curate on PC and deploy; optional on-device copy via **2s+ hold** (unreliable on KY-040)

**Status:** core (boot, audio, MPE) is solid and has been performance-tested for hours at a time. The knob/screen UI is **not good yet** — scrolling is unreliable (missed and double steps), and the button has **no normal click** (short taps are deliberately ignored). What the debounce stack mostly fixed is **false presses** — ghost scrolls and accidental actions from button/encoder coupling. Usable if you're patient; not gig-polished.

## Build one

Everything to replicate the reference hardware — exact parts (with purchase links), wiring diagrams, GPIO pinout:

- **[REFERENCE_BOM.md](REFERENCE_BOM.md)** — the parts list, what to buy, what to skip
- **[docs/HARDWARE_WIRING.md](docs/HARDWARE_WIRING.md)** — full wiring diagram

Reference stack: Raspberry Pi 5 + 1.3″ I2C OLED + one KY-040 encoder + a USB sound dongle (no DAC HAT needed). Software targets this configuration; other displays/encoders aren't supported yet.

### Getting the patch library

The 3,192 patches on the device aren't in this repo — they ship inside Surge XT's own source tree, so building Surge XT (see below) gets you the same patches in Surge's normal folder layout. No separate download or repo needed.

Never built any of this before? Start with **[docs/BUILD-FROM-ZERO.md](docs/BUILD-FROM-ZERO.md)** — full walkthrough from a blank Pi to a working module.

**Canonical repo:** [github.com/MitchSchwartz/MPE-Sound-Module](https://github.com/MitchSchwartz/MPE-Sound-Module) · **Pi device setup:** [docs/PI-GITHUB-ISOLATION.md](docs/PI-GITHUB-ISOLATION.md) (M-Ferda account — not MitchSchwartz).

## How to navigate it

One encoder (rotate + push), one OLED. **There is no normal tap/click** — releases under ~0.5s are ignored on purpose because the KY-040 button is too noisy for short presses.

What actually works today:

- **Rotate** — browse categories or patches (unreliable — expect missed/double steps)
- **Hold ~0.5–2s and release** — toggle category ↔ patch mode (aim ~1s)
- **Hold 2s+ and release** — copy patch to quick-access folder (`MPE_FAVORITES_NAME`, default `!Quick Access`) — confirm with another ~1s hold; unreliable, PC workflow preferred
- **Hold 8s+** — power menu
- **Stop scrolling ~1.25s** — patch loads

Folder name config: **`MPE_FAVORITES_NAME`** in `/etc/mpe/mpe.env` — see **[docs/PATCH_BROWSER_UI.md](docs/PATCH_BROWSER_UI.md)** (controls + configuration table).

**Next major UI upgrade needed:** separate reliable **enter (~1s hold)** and **back (~3s hold)** instead of one overloaded toggle; **second encoder** for scroll vs confirm (down the road).

Full detail: **[docs/PATCH_BROWSER_UI.md](docs/PATCH_BROWSER_UI.md)**

## Power Controls

**[docs/POWER_BUTTON_SETUP.md](docs/POWER_BUTTON_SETUP.md)** — Shut Down: hold the encoder 8 seconds to shut down. Power On: auto with power switch or press encoder for 3 seconds if already powered.

## Optional: foot pedal

**[docs/FOOT_PEDAL.md](docs/FOOT_PEDAL.md)** — hands-free sustain/reverb/chorus via a USB footswitch, auto-starts when plugged in, remappable to other pedals

## Sound design workflow

Patches are edited on a normal computer with the real Surge XT GUI, then pushed to the Pi in seconds:

1. Edit patches in Surge XT on your PC, using its regular interface
2. Deploy the changed patches to the Pi with `scripts/deploy-patch-browser.sh` or `scripts/deploy-all.sh` or use a github repo like I do.
3. Pick up the Roli and play — the browser on the device shows the new patch immediately

Full walkthrough: **[docs/PATCH-EDITING-WORKFLOW.md](docs/PATCH-EDITING-WORKFLOW.md)**

> **Known rough edge:** this workflow currently runs through git, which is fine if you're comfortable with it and clunky if you're not. A simpler sync path (drag-and-drop or a one-click push) is a likely next improvement — not built yet.

## Quick reference (if you already have one running)

```bash
ssh surge.local 'systemctl status surge-xt-cli'   # check it's alive
ssh surge.local 'tail -f ~/surge-cli.log'          # watch logs
ssh surge.local 'sudo systemctl restart surge-xt-cli'  # restart (e.g. after plugging in Roli)
```

Full command reference: **[COMMANDS.md](COMMANDS.md)**

## How it's built (for the curious)

```
[Roli Seaboard] --USB MIDI--> [Surge XT CLI] --ALSA--> [USB audio dongle] --> speakers/headphones
                                     ↑
                            MPE always enabled, headless, auto-starts on boot
```

- **Surge XT CLI, not GUI** — no X11/VNC overhead, auto MIDI connect, MPE hardcoded on, lower latency
- **Direct ALSA, not JACK** — simpler, lower latency, one less thing to configure
- **Not Zynthian** — different category. Zynthian is a multi-engine workstation; getting persistent, always-on MPE through its generalized preset architecture is a known unsolved friction point (confirmed on Zynthian's own forum as recently as 2025). This project sidesteps that by being narrow on purpose.

## Documentation map

| Doc                                                                  | For                                                  |
| -------------------------------------------------------------------- | ---------------------------------------------------- |
| [docs/BUILD-FROM-ZERO.md](docs/BUILD-FROM-ZERO.md)                   | Full walkthrough: blank Pi → working module          |
| [docs/PI-GITHUB-ISOLATION.md](docs/PI-GITHUB-ISOLATION.md)           | Pi must use M-Ferda device account, not MitchSchwartz |
| [REFERENCE_BOM.md](REFERENCE_BOM.md)                                 | Building the hardware                                |
| [docs/HARDWARE_WIRING.md](docs/HARDWARE_WIRING.md)                   | Wiring the OLED + encoder                            |
| [docs/PATCH_BROWSER_UI.md](docs/PATCH_BROWSER_UI.md)                 | How the encoder/button navigation actually works     |
| [docs/PATCH-EDITING-WORKFLOW.md](docs/PATCH-EDITING-WORKFLOW.md)     | Editing sounds, pushing to the Pi                    |
| [docs/FOOT_PEDAL.md](docs/FOOT_PEDAL.md)                             | USB footswitch setup + remapping                     |
| [docs/POWER_BUTTON_SETUP.md](docs/POWER_BUTTON_SETUP.md)             | Shutdown/power-on via the encoder button             |
| [COMMANDS.md](COMMANDS.md)                                           | Backup, deploy, restore, day-to-day ops              |
| [docs/BACKUP_GUIDE.md](docs/BACKUP_GUIDE.md)                         | Full disaster recovery                               |
| [FAQ.md](FAQ.md)                                                     | Alternatives, troubleshooting, "can I use X instead" |
| [docs/SURGE_CLI_HEADLESS_SETUP.md](docs/SURGE_CLI_HEADLESS_SETUP.md) | Full technical deep dive                             |

## Credits

This runs on top of, and ships with, **[Surge XT](https://surge-synthesizer.github.io/)** — a free, open-source synth engine built by the **Surge Synth Team** (originally released under GPL-3.0 by Claes Johanson/Vember Audio in 2018). None of the sound engine, MPE handling, or patch format is this project's work — this repo is the headless Pi wrapper around it.

The **3,192 bundled patches** are Surge XT's own stock library, not custom content for this project — get them by [installing Surge XT](https://surge-synthesizer.github.io/) on any platform, not by cloning this repo:

- **639 factory patches** — created by the Surge Synth Team
- **2,553 third-party patches** — contributed by the wider Surge community

Surge XT itself is licensed **GPL-3.0**. Sounds/patches you make or perform with it are yours to use freely, commercially or otherwise — see the [Surge XT license FAQ](https://github.com/surge-synthesizer/surge) for specifics. This repo's own code (Pi setup, wiring, UI, deploy scripts) is licensed separately below.

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — free for personal use, modification, and non-commercial purposes. No resale or commercial use without a separate agreement.