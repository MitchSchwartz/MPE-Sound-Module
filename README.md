# Pi-Surge-MPE

**A dedicated MPE compatible sound module for your MPE instrument. No laptop required for daily play — just a Pi, a screen, and a way to browse patches.** Optional USB desk audio, MIDI clock, session recording, and a looper stack when you want more.

Plug a Roli into a Raspberry Pi. Turn it on. Play. That's the whole interaction.

## Demo

**Touch screen build** (recommended) — Freenove 5″ DSI panel ([B0B455LDKH](https://www.amazon.ca/dp/B0B455LDKH)), 800×480 landscape

https://github.com/user-attachments/assets/e5875064-644f-459f-acdb-6f6bf30de0cc


**Encoder + OLED build** (legacy — usable but not gig-polished)

https://github.com/user-attachments/assets/74652240-74af-48be-9db1-608f54805d25

---


## Why this exists

I wanted to just show up and play my MPE instruments without tech in the way. Latency, tech issues, big bright screens... wrong vibe.

What if my MPE instruments were just... instruments? ...portable 5-D electric pianos.

Under the hood it's [Surge XT](https://surge-synthesizer.github.io/) (free, open-source, genuinely powerful) running headless on a Pi, always in MPE mode. It boots straight to your sound library.

Every patch is fully editable on your computer and plays back with full MPE on the device — it turns a Roli (or LinnStrument, Osmose, etc.) into a standalone instrument outside the Equator ecosystem. The deep expression story is Surge's, not a thin preset player wrapper; see **MPE expression** below.

**Think of it as the 'dumb phone' of digital instruments** — with optional looping and USB I/O when you want them. If you want a multi-engine DAWless workstation, that's [Zynthian](https://zynthian.org/). This stays narrow on purpose (and doesn't fight you on MPE persistence the way Zynthian's Surge integration currently does).

## What it does



### MPE expression (what Surge brings)

Surge XT is a full synth engine, not a sample player with fixed MIDI CC maps. On the Pi it always runs in **MPE mode** — each note gets its own channel and its own expression, so polyphonic bends, per-finger pressure, and timbre sweeps work the way Seaboard-class controllers expect.

**Five expression dimensions** map to Surge's MPE inputs:


| Dimension    | Typical controller input | What you can route it to                          |
| ------------ | ------------------------ | ------------------------------------------------- |
| **Strike**   | Note velocity            | Level, brightness, attack shape                   |
| **Pressure** | Aftertouch / Z-axis      | Filter cutoff, resonance, drive, volume           |
| **Timbre**   | Y-axis / CC74            | Wavetable position, FM index, morph targets       |
| **Pitch**    | Per-note bend            | Detune, interval shifts (±48 semitones on the Pi) |
| **Slide**    | X-axis horizontal        | Pan, detune, timbral drift                        |


The **mod matrix** is the payoff: almost any parameter — filter cutoff, wavetable position, FM amount, envelope times, LFO depth, effect sends, scene morph — can be a destination for any MPE source. You're not stuck with "pressure = louder." Patches can be as deep as desktop Surge; the Pi is the playback engine.

**Design on a computer, play on the instrument:**

1. Edit in the **real Surge XT GUI** on Mac, Windows, or Linux (wavetables, FM, effects, mod routing, MPE sources — the whole editor).
2. **MPE assignments live in the patch file** (`.fxp`); they travel with the preset when you deploy.
3. **Push to the Pi** in seconds — browser picks up the new or updated patch immediately; no re-mapping on the device.
4. Start from **3,000+ factory and community patches** and remap expression on anything that catches your ear.

Full deploy walkthrough: **[docs/PATCH-EDITING-WORKFLOW.md](docs/PATCH-EDITING-WORKFLOW.md)**. Device-side **Vol / Tail / Touch** faders on the touch UI are performance trims on top of the patch; they don't replace Surge's mod matrix.

### Audio

- **MPE sound module** — compatible with any MPE MIDI controller (Roli Seaboard, LinnStrument, Osmose, etc.)
- **Runs Surge XT** — full synth engine in MPE mode; mod matrix and per-note expression (see **MPE expression** above)
- **3,192 patches** (via Surge build) — 639 factory + 2,553 community; not shipped inside this repo
- **Analog and USB audio out** — 3.5mm jack standalone, or USB to a laptop/PC as a standard audio input (`[docs/USB-AUDIO-HOST.md](docs/USB-AUDIO-HOST.md)`)
- **USB session record** — optional profile that captures the full loop mix (Surge → pedal → return) to a tethered PC (`[docs/USB-SESSION-RECORD.md](docs/USB-SESSION-RECORD.md)`)
- **MIDI clock out** — sync external gear (e.g. Boss RC-5) from the Pi (`[docs/MIDI-CLOCK.md](docs/MIDI-CLOCK.md)`)
- **Selectable sample rate** — 44.1 kHz or 48 kHz on-device; persists and restarts Surge with the new rate
- **Selectable audio buffer** — touch settings or `MPE_JACK_BUFFER` / `MPE_JACK_PERIODS` (defaults 256 × 3); includes a 32-frame period option on Pi 5. JACK is the only audio engine — restarts the graph when changed; failed restarts roll back the setting
- **Per-patch volume normalization** — calibrate once (strike + sustain anchors, peak-safe closed loop); every patch loads at a matched level. The same run sets **Touch** pressure floors for light vs full press on favorites.
- **Reuse Single on load** — patches are rewritten at load so restrikes on the same key reuse the voice instead of stacking new ones (lighter CPU on dense patches)
- **Dynamic voice limit** — background governor (`surge-poly-governor.service`) tracks JACK deadline load and moves Surge's poly limit on a continuous curve under sustained playing, then recovers when headroom returns; Surge softkill handles voice stealing (no MIDI panic). See **[docs/POLY-GOVERNOR.md](docs/POLY-GOVERNOR.md)**.



### Looper (optional — Phase 2, Pi 5)

Grid/free-form looping via **SooperLooper** on the JACK graph, alongside Surge — not the core "browse and play" path, but live on the Pi 5 reference stack:

- **APC MINI** transport/scene control when the looper is enabled
- **Song save/load** from the touch UI
- **Tempo readout** and looper-aware navigation
- Seam/overdub behaviour is under active polish — see **[Documents/DIRECTION.md](Documents/DIRECTION.md)** and **[Documents/specs/looper-loop-seam-spec.md](Documents/specs/looper-loop-seam-spec.md)**. No standalone user guide yet; builder docs and specs are the source of truth.

Enable via `MPE_LOOPER_ENABLED=1` in `/etc/mpe/mpe.env` (Pi 5 only in practice).

### UI

- **Two interface options** — fullscreen **Freenove 5″ touch** (recommended) or legacy rotary encoder + OLED
- **Full-library browsing** — folder view or a flat, alphabetical list with an A–Z scrub rail
- **Quick Select** — your personal patch shortlist. Heart a patch on the detail pane to save it; browse saved patches under **Quick Select** like any other category. Add subfolders for gig sets or moods (long-press to create, rename, or organize). Long-press a patch or library folder for shortcuts — add a whole folder at once, move patches between folders, remove from the list. Copies live on the Pi; the originals in the main library stay put.
- **Filter by instrument** — browsing a deep folder or the full A–Z list? Tap the funnel icon next to the **A–Z** button to narrow by type — bass, pad, keys, and so on. Tap **All** to see everything again.
- **Per-patch mixer (touch)** — vertical faders on the patch detail pane: **Vol** (level), **Tail** (envelope length; **0** = patch-as-loaded), **Touch** (MPE pressure floor; **cal value** = default handle position on **−50…+50**). See **[docs/TOUCH_PATCH_BROWSER.md](docs/TOUCH_PATCH_BROWSER.md)** §Mixer faders.
- **Theming** — light/dark base themes with custom accent colors
- **CPU meter** — live engine headroom while playing
- **Dynamic voice limit toggle (touch)** — System settings → turn poly limiting on or off (default on). v2 uses jack deadline load when `MPE_POLY_GOVERNOR_METER=jack`. Full behaviour: **[docs/POLY-GOVERNOR.md](docs/POLY-GOVERNOR.md)**.
- **On-screen keyboard** — Wi‑Fi passwords, folder rename, and other text entry on the panel
- **Recovery** — **Restart everything** in System settings unwedges the audio/MIDI stack without a reboot; clearer toasts for MIDI connect/disconnect and missing audio devices

**Status:**

- **Synth + touch browser (Pi 5):** solid for hours-long playing sessions
- **Looper:** live on Pi 5; ear-gated polish and multi-clip work still in flight
- **Touch UI:** primary interface; encoder/OLED is legacy — scrolling is unreliable (missed and double steps), usable if you're patient, not gig-polished
- **Pi 4:** fine for the original synth-only baseline; **borderline** for current `main` — do not target for new builds

**Bootstrap, not a product.** This repo is a reference design and doc set for technical builders — SSH, git, CMake, wiring, systemd. Comfortable with a terminal (or AI-guided setup) is assumed. No installer, no prebuilt Surge binary yet, no plug-and-play path for non-dev Surge users.

## Platform (Pi 5 focus)

**Active development targets Raspberry Pi 5.** On the reference unit it is spectacularly better: lower JACK buffers, more polyphony headroom, and enough CPU to run the touch UI plus an optional looper stack at the same time. **Build new units on Pi 5.**

**RAM: use the 4 GB model.** That is the board we develop and test against (the current Pi 5 SKU — not the older 2 GB or 8 GB variants unless you know why you want 8 GB). Surge, JACK, the touch browser, and optional SooperLooper all share that RAM; 4 GB is the sweet spot we size for.


|                      | **Pi 5 (4 GB)**                                                                                                                                                                                            | **Pi 4 (4 GB)**                                                                                                                                                                                             |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Role today**       | **Primary player platform** — daily use, looper, lower buffers                                                                                                                                             | **Legacy / measurement baseline** — still documented, not recommended for new builds                                                                                                                        |
| **Touch UI + synth** | Playable at 64×2 @ 48 kHz on reference unit (ear-validated; no xrun soak)                                                                                                                                  | Worked for the original stack; **borderline** once you add governor, touch polish, or looper                                                                                                                |
| **Looper**           | Reference stack runs here                                                                                                                                                                                  | Not a supported target for Phase 2 looper work                                                                                                                                                              |
| **Validation**       | Player tuning in progress — `[docs/measurements/PI5-SESSION-CLOSEOUT-2026-08-23.md](docs/measurements/PI5-SESSION-CLOSEOUT-2026-08-23.md)`, `[docs/PI5-PLAYER-SETUP-LOG.md](docs/PI5-PLAYER-SETUP-LOG.md)` | Latency arc closed for synth-only claims — `[docs/measurements/PI4-CLOSEOUT-2026-08-23.md](docs/measurements/PI4-CLOSEOUT-2026-08-23.md)`, clone-SD baseline `[docs/PI4-CLONE-SD.md](docs/PI4-CLONE-SD.md)` |


Pi 4 got this project off the ground and remains useful as a measurement control and golden-image baseline. For a new instrument you'd actually play, Pi 5 is the answer; Pi 4 is increasingly tight on CPU and RAM for what `main` does today.

Appliance deploys pin git branch `main` (`[config/platform/appliance-git-ref](config/platform/appliance-git-ref)`).

## Disclaimer

**Use at your own risk.** I take no responsibility if your device bricks, catches fire, disrespects your coworkers, or causes any other collateral damage. This is DIY hardware and software on a single-board computer running a realtime audio stack — things can go wrong.

The expectation is that you **know what you're doing**, or you're **learning and taking reasonable safety precautions** (proper power, ventilation, sane wiring, backups before you flash anything).

**This project assumes an AI is in the loop.** Docs and tooling are written for builders who will have an assistant walk you through SSH, git, systemd, and wiring — not for a polished installer UX. Developer experience and feature velocity come first; the AI is expected to fill the gaps a product would normally paper over.

## Git workflow

`dev` is the integration branch for day-to-day development and agent work. `main` is the release line — land changes there only via pull request or explicit promotion from `dev`. Pi deploy can keep tracking `main` until you promote. Details: **[docs/GIT-WORKFLOW.md](docs/GIT-WORKFLOW.md)**.

## Build one

Everything to replicate the hardware — parts list, wiring, GPIO pinout:

- **[REFERENCE_BOM.md](REFERENCE_BOM.md)** — what to buy, what to skip, touch vs encoder builds
- **[docs/HARDWARE_WIRING.md](docs/HARDWARE_WIRING.md)** — encoder/OLED wiring (skip for touch)



### Touch build (recommended)

**Compute:** **Raspberry Pi 5, 4 GB RAM** — this is the board we use and recommend. Do not assume 2 GB will work; 8 GB is fine but unnecessary for the reference stack.

Pi 4 (4 GB) remains documented for people maintaining an older unit. It is **not** recommended for new builds — CPU and RAM are borderline for the current software, and looper work does not target Pi 4.

**Display stack:**


| Piece                | Reference spec                                                                                                                                                                                 |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Panel**            | **Freenove 5″ IPS DSI** — [B0B455LDKH](https://www.amazon.ca/dp/B0B455LDKH) / FNK0078A, **800×480**, 5-point capacitive                                                                        |
| **Touch controller** | **EDT FT5x06** on reference units — Linux driver `edt_ft5x06`                                                                                                                                  |
| **Connection**       | MIPI DSI ribbon only (Pi 5: **CAM/DISP**; Pi 4: **DISPLAY**) — no HDMI                                                                                                                         |
| **Boot overlay**     | `dtoverlay=vc4-kms-dsi-7inch` on reference units — confirm with `kmsprint` on your panel                                                                                                       |
| **Pi power**         | USB-C PSU **works**. **GPIO 5V/GND (or barrel PSU) recommended** — frees USB-C for PC tether + `usb-host` audio (`[docs/USB-AUDIO-PASSTHROUGH-SPIKE.md](docs/USB-AUDIO-PASSTHROUGH-SPIKE.md)`) |


Optional enclosure: SmartiPi Touch case or the Freenove included stand.

No OLED or encoder wiring. Full setup: **[docs/TOUCH_PATCH_BROWSER.md](docs/TOUCH_PATCH_BROWSER.md)** · Pi 5 from an existing Pi 4: **[docs/PI5-PLAYER-SETUP-LOG.md](docs/PI5-PLAYER-SETUP-LOG.md)**.

### Encoder + OLED build (legacy)

Raspberry Pi 4 or 5 + 1.3″ I2C OLED + one KY-040 encoder + USB sound dongle (no DAC HAT). Software targets this configuration; other displays/encoders aren't supported yet. Encoder UX is functional but not gig-polished — see **How to navigate it** below.

### Getting the patch library

The 3,192 patches on the device aren't in this repo — they ship inside Surge XT's own source tree, so building Surge XT (see below) gets you the same patches in Surge's normal folder layout. No separate download or repo needed.

Never built any of this before? Start with **[docs/BUILD-FROM-ZERO.md](docs/BUILD-FROM-ZERO.md)** — full walkthrough from a blank Pi to a working module.

**Repo:** [github.com/MitchSchwartz/MPE-Sound-Module](https://github.com/MitchSchwartz/MPE-Sound-Module) (clone path is often `MPE-Module` locally — same project)

## How to navigate it

*This section covers the encoder/OLED build. On the touch build, everything is on-screen — see [docs/TOUCH_PATCH_BROWSER.md](docs/TOUCH_PATCH_BROWSER.md) instead.*

One encoder (rotate + push), one OLED. **There is no normal tap/click** — releases under ~0.5s are ignored on purpose because the KY-040 button is too noisy for short presses.

What actually works today:

- **Rotate** — browse categories or patches (unreliable — expect missed/double steps)
- **Hold ~0.5s+ and release** — toggle category ↔ patch mode (aim ~1s; works up to the 8s power menu)
- **Hold 8s+** — power menu
- **Stop scrolling ~1.25s** — patch loads

Folder name config: `MPE_FAVORITES_NAME` in `/etc/mpe/mpe.env` — see **[docs/PATCH_BROWSER_UI.md](docs/PATCH_BROWSER_UI.md)** (controls + configuration table).

**Next major UI upgrade needed:** separate reliable **enter (~1s hold)** and **back (~3s hold)** instead of one overloaded toggle; **second encoder** for scroll vs confirm (down the road).

Full detail: **[docs/PATCH_BROWSER_UI.md](docs/PATCH_BROWSER_UI.md)**

## Power Controls

**[docs/POWER_BUTTON_SETUP.md](docs/POWER_BUTTON_SETUP.md)** — Shut Down: hold the encoder 8 seconds to shut down. Power On: auto with power switch or press encoder for 3 seconds if already powered.

## Optional: foot pedal

**[docs/FOOT_PEDAL.md](docs/FOOT_PEDAL.md)** — hands-free sustain/reverb/chorus via a USB footswitch, auto-starts when plugged in, remappable to other pedals

## Sound design workflow

The **MPE expression** section above is the why; this is the how. Patches are edited on a normal computer with the real Surge XT GUI, then pushed to the Pi:

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

On every patch load, **Reuse Single** is applied automatically (XML rewrite — not an OSC toggle). A static poly **ceiling** is applied via Surge OSC; the **dynamic voice limit** governor can lower and raise that limit under load.


| Control                          | Where                                                                                                    |
| -------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Dynamic voice limit on/off       | Touch UI → System settings → Audio…                                                                      |
| Mode, baseline, rest cap, ramp   | `/etc/mpe/mpe.env` — see **[docs/POLY-GOVERNOR.md](docs/POLY-GOVERNOR.md)** and `config/mpe.env.example` |
| Poly ceiling / floor / emergency | `MPE_POLY_CEILING`, `MPE_POLY_FLOOR`, `MPE_POLY_EMERGENCY`                                               |
| Disable Reuse Single             | `MPE_REUSE_SINGLE=0` in `mpe.env`                                                                        |
| Disable governor entirely        | `MPE_POLY_GOVERNOR=0` or touch settings                                                                  |


Manual OSC smoke test: `python3 scripts/manual/test-poly-governor-osc.py`

## Quick reference (if you already have one running)

Prefer the global `mpe` **CLI** from [mpe-cli](https://github.com/MitchSchwartz/mpe-cli) — see **[docs/LAPTOP-MPE-CLI.md](docs/LAPTOP-MPE-CLI.md)** for multi-Pi config.

```bash
# One-time: install mpe-cli, set PI_HOST / PI_USER in ~/.config/mpe/mpe.env
mpe ping
mpe status
mpe logs surge -n 50
mpe restart surge
```

Raw SSH still works — full reference: **[COMMANDS.md](COMMANDS.md)**.

## How it's built (for the curious)

```
[Roli Seaboard] --USB MIDI--> [Surge XT CLI] --JACK client--> [jackd graph server] --USB--> [USB audio dongle] --> speakers/headphones
                                     ↑
                            MPE always enabled, headless, auto-starts on boot
```

Optional: `[SooperLooper]` on the same JACK graph when looper is enabled.

- **Surge XT CLI, not GUI** — no X11/VNC overhead, auto MIDI connect, MPE hardcoded on, lower latency
- **JACK graph server (only engine)** — Surge is a JACK client; jackd owns the DAC so everything on the graph shares one clock. Measured keeper on Pi 5: 128 frames × 2 periods @ 48 kHz with looper off; 256 × 3 is a safe default for synth-only. Server buffer is `MPE_JACK_BUFFER` / `MPE_JACK_PERIODS`. A jackd that will not start is a hard failure — there is no ALSA fallback. Engine states: `ok`, `recovering`, `failed`.
- **Not Zynthian** — different category. Zynthian is a multi-engine workstation; getting persistent, always-on MPE through its generalized preset architecture is a known unsolved friction point (confirmed on Zynthian's own forum as recently as 2025). This project sidesteps that by being narrow on purpose.



## Documentation map


| Doc                                                                  | For                                                   |
| -------------------------------------------------------------------- | ----------------------------------------------------- |
| [docs/POLY-GOVERNOR.md](docs/POLY-GOVERNOR.md)                       | Dynamic voice limit (poly governor) behaviour and env |
| [Documents/DIRECTION.md](Documents/DIRECTION.md)                     | Phase 2 looper direction and locked decisions         |
| [docs/BUILD-FROM-ZERO.md](docs/BUILD-FROM-ZERO.md)                   | Full walkthrough: blank Pi → working module           |
| [docs/PI5-PLAYER-SETUP-LOG.md](docs/PI5-PLAYER-SETUP-LOG.md)         | Pi 5 player bring-up from an existing Pi 4            |
| [docs/LAPTOP-MPE-CLI.md](docs/LAPTOP-MPE-CLI.md)                     | Laptop `mpe` CLI — multi-Pi configs                   |
| [docs/GIT-WORKFLOW.md](docs/GIT-WORKFLOW.md)                         | Branches, Pi deploy, promotion                        |
| [docs/CODE-MAP.md](docs/CODE-MAP.md)                                 | Boot/lifecycle map for builders                       |
| [REFERENCE_BOM.md](REFERENCE_BOM.md)                                 | Building the hardware                                 |
| [docs/HARDWARE_WIRING.md](docs/HARDWARE_WIRING.md)                   | Wiring the OLED + encoder                             |
| [docs/PATCH_BROWSER_UI.md](docs/PATCH_BROWSER_UI.md)                 | How the encoder/button navigation actually works      |
| [docs/TOUCH_PATCH_BROWSER.md](docs/TOUCH_PATCH_BROWSER.md)           | Freenove 5″ touch browser setup and interaction       |
| [docs/PATCH_NORMALIZATION.md](docs/PATCH_NORMALIZATION.md)           | Per-patch loudness calibration and Norm toggle        |
| [docs/USB-AUDIO-HOST.md](docs/USB-AUDIO-HOST.md)                     | USB desk-tether audio (route synth to a laptop/PC)    |
| [docs/USB-SESSION-RECORD.md](docs/USB-SESSION-RECORD.md)             | USB session record (full loop mix to PC)              |
| [docs/PATCH-EDITING-WORKFLOW.md](docs/PATCH-EDITING-WORKFLOW.md)     | Editing sounds, pushing to the Pi                     |
| [docs/FOOT_PEDAL.md](docs/FOOT_PEDAL.md)                             | USB footswitch setup + remapping                      |
| [docs/MIDI-CLOCK.md](docs/MIDI-CLOCK.md)                             | MIDI clock out for Boss RC-5 / external sync          |
| [docs/POWER_BUTTON_SETUP.md](docs/POWER_BUTTON_SETUP.md)             | Shutdown/power-on via the encoder button              |
| [docs/measurements/README.md](docs/measurements/README.md)           | Pi 4/5 validation and measurement index               |
| [COMMANDS.md](COMMANDS.md)                                           | Backup, deploy, restore, day-to-day ops               |
| [docs/BACKUP_GUIDE.md](docs/BACKUP_GUIDE.md)                         | Full disaster recovery                                |
| [FAQ.md](FAQ.md)                                                     | Alternatives, troubleshooting, "can I use X instead"  |
| [docs/SURGE_CLI_HEADLESS_SETUP.md](docs/SURGE_CLI_HEADLESS_SETUP.md) | Full technical deep dive                              |
| [docs/WHATS-NEW.md](docs/WHATS-NEW.md)                               | Recent feature updates, in plain English              |
| [CHANGELOG.md](CHANGELOG.md)                                         | Full engineering log                                  |




## Reporting bugs

Something broken? Check **[FAQ.md](FAQ.md)** first — a lot of "bugs" are config or wiring gotchas with a known fix.

If it's still wrong, [open a bug report](https://github.com/MitchSchwartz/MPE-Sound-Module/issues/new?template=bug_report.md). The template asks for Pi model, UI mode, audio profile, and repro steps so we can actually chase it.

## Credits

This runs on top of, and ships with, **[Surge XT](https://surge-synthesizer.github.io/)** — a free, open-source synth engine built by the **Surge Synth Team** (originally released under GPL-3.0 by Claes Johanson/Vember Audio in 2018). None of the sound engine, MPE handling, or patch format is this project's work — this repo is the headless Pi wrapper around it.

The **3,192 bundled patches** are Surge XT's own stock library, not custom content for this project — get them by [installing Surge XT](https://surge-synthesizer.github.io/) on any platform, not by cloning this repo:

- **639 factory patches** — created by the Surge Synth Team
- **2,553 third-party patches** — contributed by the wider Surge community

Optional **CC0 / permissive community packs** (drums and more) curated in the private [MPE-Library](https://github.com/MitchSchwartz/MPE-Library) assets repo — Italo Disco Drum Pack (CC0), ironcross32/Surge-XT-Patches (CC0-1.0), Phasor Space Vol. 1 (CC0), Hefxthoth collection (DWTFYWPL). See that repo's README §Third-party patch credits.

Surge XT itself is licensed **GPL-3.0**. Sounds/patches you make or perform with it are yours to use freely, commercially or otherwise — see the [Surge XT license FAQ](https://github.com/surge-synthesizer/surge) for specifics. This repo's own code (Pi setup, wiring, UI, deploy scripts) is licensed separately below.

MPE-Module drives Surge as a **separate process** over OSC, MIDI and JACK — it does not link
Surge as a library or host it in-process. That arms-length boundary is what lets this repo
carry a different license from Surge's GPL-3.0. Full component list, corresponding-source
pointers, and distribution obligations: `[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)`.

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — free for personal use, modification, and non-commercial purposes. No resale or commercial use without a separate agreement.