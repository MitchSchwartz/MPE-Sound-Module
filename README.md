# Pi-Surge-MPE

**A dedicated MPE compatible sound module for your MPE instrument. No laptop — just a Pi, a screen, and a way to browse patches.**

Plug a Roli into a Raspberry Pi. Turn it on. Play. That's the whole interaction.

**Bootstrap, not a product.** This repo is a reference design and doc set for technical builders — SSH, git, CMake, wiring, systemd. Comfortable with a terminal (or AI-guided setup) is assumed. No installer, no prebuilt Surge binary yet, no plug-and-play path for non-dev Surge users.

## Demo
#Touchscreen Version

https://github.com/user-attachments/assets/c3e12d33-1f02-4c8b-8dda-a7b0a855dd4a


#Encoder Version

https://github.com/user-attachments/assets/74652240-74af-48be-9db1-608f54805d25

---

## Why this exists

I wanted to just show up and play my MPE instruments without tech in the way. Latency, tech issues, big bright screens... wrong vibe.

What if my MPE instruments were just... instruments? ...portable 5-D electric pianos.

Under the hood it's [Surge XT](https://surge-synthesizer.github.io/) (free, open-source, genuinely powerful) running headless on a Pi, always in MPE mode. It boots straight to your sound library.   

Every patch is fully editable and MPE-assignable from your computer, across all five expression dimensions — it turns a Roli into a standalone instrument, outside the Equator ecosystem entirely. Surge XT's mod matrix lets you map pretty much any synth parameter (filter cutoff, wavetable position, FM amount, envelope times, effect sends, etc.) to any MPE dimension — pressure, timbre/Y-axis, per-note pitch bend, slide — so patches aren't limited to the handful of expression targets a controller ships with. Reasonably deep sound design, not just "pressure = volume."

**Think of is as the 'dumb phone' of digital instruments**  If you want a multi-engine DAWless workstation, that's [Zynthian](https://zynthian.org/). This is one instrument that does one thing without fuss (and doesn't fight you on MPE persistence the way Zynthian's Surge integration currently does).

## What it does

### Audio

- **MPE sound module** — compatible with any MPE MIDI controller (Roli Seaboard, LinnStrument, Osmose, etc.)
- **Runs Surge XT** — free, open-source synth engine, always in MPE mode, full mod matrix across all 5 expression dimensions
- **3,192 patches included** — 639 factory + 2,553 community
- **Analog and USB audio out** — 3.5mm jack standalone, or USB to a laptop/PC as a standard audio input
- **Per-patch volume normalization** — calibrate once (strike + sustain anchors, peak-safe closed loop); every patch loads at a matched level. The same run sets **Touch** pressure floors for light vs full press on favorites.
- **Reuse Single on load** — patches are rewritten at load so restrikes on the same key reuse the voice instead of stacking new ones (lighter CPU on dense patches)
- **Dynamic voice limit** — a background governor watches CPU and steps Surge's poly limit down under sustained load, then recovers when headroom returns; Surge's built-in softkill handles voice stealing (no MIDI panic)
- **Favorites folder** — curate a quick-access set of patches on your PC, deploy to the device

### UI

- **Two interface options** — rotary encoder + OLED screen, or fullscreen touch display (SmartiPi 5″)
- **Full-library browsing** — folder view or a flat, alphabetical searchable list
- **Per-patch mixer (touch)** — vertical faders on the patch detail pane: **Vol** (level), **Tail** (envelope length; **0** = patch-as-loaded), **Touch** (MPE pressure floor; **cal value** = default handle position on **−50…+50**). See **[docs/TOUCH_PATCH_BROWSER.md](docs/TOUCH_PATCH_BROWSER.md)** §Mixer faders.
- **Theming** — light/dark base themes with custom accent colors
- **CPU meter** — live engine headroom while playing
- **Dynamic voice limit toggle (touch)** — System settings → turn CPU-aware poly limiting on or off (default on). No in-Surge output limiter — loudness headroom comes from per-patch normalization at calibration time; use host/USB gain staging if you need a safety ceiling live.

**Status:** 

- core (boot, audio, MPE) is solid and has been performance-tested for hours at a time. 
- The **touch UI** is the more polished, actively developed interface.
- The **encoder/OLED UI** is still rough — scrolling is unreliable (missed and double steps), is usable if you're patient; not gig-polished. 

## Build one

Everything to replicate the reference hardware — exact parts (with purchase links), wiring diagrams, GPIO pinout:

- **[REFERENCE_BOM.md](REFERENCE_BOM.md)** — the parts list, what to buy, what to skip
- **[docs/HARDWARE_WIRING.md](docs/HARDWARE_WIRING.md)** — full wiring diagram

Reference stack: Raspberry Pi 5 + 1.3″ I2C OLED + one KY-040 encoder + a USB sound dongle (no DAC HAT needed). Software targets this configuration; other displays/encoders aren't supported yet.

**Prefer a touch screen?** A SmartiPi + 5″ landscape touch panel is a fully supported alternate build — skip the OLED/encoder wiring entirely. See **[docs/TOUCH_PATCH_BROWSER.md](docs/TOUCH_PATCH_BROWSER.md)**.

### Getting the patch library

The 3,192 patches on the device aren't in this repo — they ship inside Surge XT's own source tree, so building Surge XT (see below) gets you the same patches in Surge's normal folder layout. No separate download or repo needed.

Never built any of this before? Start with **[docs/BUILD-FROM-ZERO.md](docs/BUILD-FROM-ZERO.md)** — full walkthrough from a blank Pi to a working module.

**Repo:** [github.com/MitchSchwartz/MPE-Sound-Module](https://github.com/MitchSchwartz/MPE-Sound-Module)

## How to navigate it

*This section covers the encoder/OLED build. On the touch build, everything is on-screen — see [docs/TOUCH_PATCH_BROWSER.md](docs/TOUCH_PATCH_BROWSER.md) instead.*

One encoder (rotate + push), one OLED. **There is no normal tap/click** — releases under ~0.5s are ignored on purpose because the KY-040 button is too noisy for short presses.

What actually works today:

- **Rotate** — browse categories or patches (unreliable — expect missed/double steps)
- **Hold ~0.5s+ and release** — toggle category ↔ patch mode (aim ~1s; works up to the 8s power menu)
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
2. Deploy the changed patches to the Pi with `scripts/deploy-patch-browser.sh` or `scripts/deploy-all.sh`, or sync via your own private assets repo (see workflow doc).
3. Pick up the Roli and play — the browser on the device shows the new patch immediately

Full walkthrough: **[docs/PATCH-EDITING-WORKFLOW.md](docs/PATCH-EDITING-WORKFLOW.md)**

> **Known rough edge:** this workflow currently runs through git, which is fine if you're comfortable with it and clunky if you're not. A simpler sync path (drag-and-drop or a one-click push) is a likely next improvement — not built yet.

### Per-patch normalization (touch or SSH)

Calibrate once; every `load_patch()` applies a stored gain baseline (MPE expression untouched). The calibrator measures each patch at **strike** (hard hit, light pressure) and **sustain** (moderate velocity, full pressure), picks the safer of the two gains, then verifies peak level in a short closed loop before saving. When **Norm.** is on, the full calibrated gain reaches Surge — peak safety is baked in at calibration time.

The same run captures a **light-touch** gesture and writes **Touch** pressure floors to `~/.patch_browser_pressure.json` (cohort alignment plus extra lift for patches with a wide strike/sustain gap). **Tail** fader: **0** at center = patch default; double-tap resets to **0**. **Touch** fader: handle sits at the calibrated value; drag to override; double-tap restores calibration. Details: **[docs/TOUCH_PATCH_BROWSER.md](docs/TOUCH_PATCH_BROWSER.md)** §Mixer faders.

```bash
# Pi touch display — fullscreen loader on the 800×480 panel (~5–12 min for Quick Select)
./scripts/calibrate-with-loader.sh --favorites-only --force   # re-cal entire favorites folder
./scripts/calibrate-with-loader.sh --favorites-only           # only patches missing gain_db

# Or from touch UI: System settings → Calibrate Quick Select
```

Toggle **Norm.** on the patch detail pane to bypass normalization for one patch; the choice persists in `~/.patch_browser_normalization.json`. Full design: **[docs/PATCH_NORMALIZATION.md](docs/PATCH_NORMALIZATION.md)**.

### Playback policy (Pi)

On every patch load, **Reuse Single** is applied automatically (XML rewrite — not an OSC toggle). A static poly **ceiling** is applied via Surge OSC; the **dynamic voice limit** governor (`surge-poly-governor.service`) can step that limit down further when CPU stays high.

| Control | Where |
| -------- | ----- |
| Dynamic voice limit on/off | Touch UI → System settings |
| Poly ceiling / floor / emergency | `/etc/mpe/mpe.env` — `MPE_POLY_CEILING` (12), `MPE_POLY_FLOOR` (4), `MPE_POLY_EMERGENCY` (3 at ≥90% CPU) |
| Disable Reuse Single | `MPE_REUSE_SINGLE=0` in `mpe.env` |
| Disable governor entirely | `MPE_POLY_GOVERNOR=0` or turn off in touch settings |

Manual OSC smoke test: `python3 scripts/manual/test-poly-governor-osc.py`

## Quick reference (if you already have one running)

```bash
# Set PI_HOST / PI_USER in config/mpe.env first (see COMMANDS.md)
ssh $PI_USER@$PI_HOST 'systemctl status surge-xt-cli'   # check it's alive
ssh $PI_USER@$PI_HOST 'tail -f ~/surge-cli.log'          # watch logs
ssh $PI_USER@$PI_HOST 'sudo systemctl restart surge-xt-cli'  # restart (e.g. after plugging in Roli)
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
| [REFERENCE_BOM.md](REFERENCE_BOM.md)                                 | Building the hardware                                |
| [docs/HARDWARE_WIRING.md](docs/HARDWARE_WIRING.md)                   | Wiring the OLED + encoder                            |
| [docs/PATCH_BROWSER_UI.md](docs/PATCH_BROWSER_UI.md)                 | How the encoder/button navigation actually works     |
| [docs/TOUCH_PATCH_BROWSER.md](docs/TOUCH_PATCH_BROWSER.md)           | SmartiPi / 5″ touch browser setup and interaction    |
| [docs/PATCH_NORMALIZATION.md](docs/PATCH_NORMALIZATION.md)           | Per-patch loudness calibration and Norm toggle       |
| [docs/USB-AUDIO-HOST.md](docs/USB-AUDIO-HOST.md)                     | USB desk-tether audio (route to a laptop/PC)         |
| [docs/PATCH-EDITING-WORKFLOW.md](docs/PATCH-EDITING-WORKFLOW.md)     | Editing sounds, pushing to the Pi                    |
| [docs/FOOT_PEDAL.md](docs/FOOT_PEDAL.md)                             | USB footswitch setup + remapping                     |
| [docs/POWER_BUTTON_SETUP.md](docs/POWER_BUTTON_SETUP.md)             | Shutdown/power-on via the encoder button             |
| [COMMANDS.md](COMMANDS.md)                                           | Backup, deploy, restore, day-to-day ops              |
| [docs/BACKUP_GUIDE.md](docs/BACKUP_GUIDE.md)                         | Full disaster recovery                               |
| [FAQ.md](FAQ.md)                                                     | Alternatives, troubleshooting, "can I use X instead" |
| [docs/SURGE_CLI_HEADLESS_SETUP.md](docs/SURGE_CLI_HEADLESS_SETUP.md) | Full technical deep dive                             |
| [docs/WHATS-NEW.md](docs/WHATS-NEW.md)                               | Recent feature updates, in plain English             |
| [CHANGELOG.md](CHANGELOG.md)                                         | Full engineering log                                 |


## Credits

This runs on top of, and ships with, **[Surge XT](https://surge-synthesizer.github.io/)** — a free, open-source synth engine built by the **Surge Synth Team** (originally released under GPL-3.0 by Claes Johanson/Vember Audio in 2018). None of the sound engine, MPE handling, or patch format is this project's work — this repo is the headless Pi wrapper around it.

The **3,192 bundled patches** are Surge XT's own stock library, not custom content for this project — get them by [installing Surge XT](https://surge-synthesizer.github.io/) on any platform, not by cloning this repo:

- **639 factory patches** — created by the Surge Synth Team
- **2,553 third-party patches** — contributed by the wider Surge community

Surge XT itself is licensed **GPL-3.0**. Sounds/patches you make or perform with it are yours to use freely, commercially or otherwise — see the [Surge XT license FAQ](https://github.com/surge-synthesizer/surge) for specifics. This repo's own code (Pi setup, wiring, UI, deploy scripts) is licensed separately below.

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — free for personal use, modification, and non-commercial purposes. No resale or commercial use without a separate agreement.
