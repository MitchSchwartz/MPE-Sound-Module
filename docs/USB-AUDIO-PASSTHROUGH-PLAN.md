# USB Audio Passthrough to Host PC — Research & Plan

*Last updated: 2026-07-31 (America/Toronto)*

**GitHub:** [Issue #6](https://github.com/MitchSchwartz/MPE-Sound-Module/issues/6) — USB audio to host when tethered  
**Related (separate scope):** [Issue #4](https://github.com/MitchSchwartz/MPE-Sound-Module/issues/4) — external line-in through Surge FX

**Status:** Phase 1 scripts landed — Pi spike pending.

**Related (future):** multichannel per-clip stems + loop master — **[USB-MULTICHANNEL-STEMS.md](USB-MULTICHANNEL-STEMS.md)** (design only; stereo `usb-host` unchanged).

---

## Problem statement

The MPE Sound Module today outputs audio only through a **Creative Sound Blaster Play! 3** USB DAC → **3.5 mm analog** to amp, headphones, or monitor. When the Pi is at a desk tethered to a laptop or PC (SSH, patch deploy, demos), the user still needs a separate aux cable from the dongle to the host — many laptops lack line-in, and the desk rig accumulates power + network + aux. The goal is: when USB-tethered to a host, the host should see a **standard USB audio playback device** carrying Surge’s mix, eliminating the aux run while **standalone gig/couch mode** continues to use the Sound Blaster analog path unchanged.

---

## Complexity rating: **Moderate**

| Factor | Assessment |
|--------|------------|
| **Kernel / gadget stack** | Moderate — well-documented on Pi 4/5, but not a one-liner; configfs + `uac2` preferred over legacy `g_audio`. |
| **Host OS matrix** | Moderate — UAC1/UAC2 are plug-and-play on Windows, macOS, and Linux, but **Pi 5 + USB-C-to-USB-C + recent Apple hosts** have known PD/negotiation quirks; cable choice matters. |
| **ALSA routing** | Moderate — dual output profiles (Sound Blaster vs gadget), explicit device selection, hot-plug rules must not fight `99-usb-audio.rules`. |
| **Latency** | Moderate for desk use — adds one USB isochronous hop + host buffer; acceptable for monitoring/recording, **not** for low-latency MPE through host speakers. |
| **Power / ports** | Moderate — gadget uses **USB-C OTG** on Pi 4/5; Roli + Sound Blaster stay on **USB-A host ports**; power budget when bus-powered from laptop. |
| **Cross-machine “it just works”** | Moderate — no custom host drivers, but Phase 2 should document cable and OS-specific gotchas rather than assuming zero friction. |

**Not Basic** because dual-mode routing, port topology, and Pi 5 edge cases require deliberate design — not merely “enable a module.”

**Not Hard** because the path uses standard Linux UAC2 gadget + built-in host class drivers; no custom firmware, no Windows INF, no JACK/Carla pipeline required for v1.

---

## Current architecture (reference)

```
[Roli / MPE controller] ──USB host (Pi USB-A)──► [Surge XT CLI]
                                                        │
                                                        ▼
                                                   ALSA (direct, no JACK)
                                                        │
                                                        ▼
                              [Sound Blaster Play! 3] ──USB host (Pi USB-A)──► 3.5 mm analog out
```

**Key scripts:** `scripts/detect-audio-device.sh` (tiered output selection), `scripts/start-surge-cli.sh` (`--audio-interface`, `MPE_SURGE_BUFFER_SIZE` default 1024, `MPE_SURGE_SAMPLE_RATE` default 48000 Hz), `config/99-usb-audio.rules` (restart Surge on USB sound card hot-plug).

**Sample rate:** Surge/ALSA path tuned for **48 kHz** (see `docs/PATCH_NORMALIZATION.md`). Gadget should advertise **48000** as primary rate to avoid resampling on host or Pi.

---

## Standard approaches

| Approach | Pros | Cons | Host compatibility |
|----------|------|------|-------------------|
| **A. configfs `uac2` gadget (recommended)** | Modern UAC2; stereo playback; tunable rates/channels via configfs; composable with other functions later | Requires boot script/systemd; Pi USB-C port dedicated to host when active; kernel ≥ 6.1 helpful | **Windows 10+** good; **Linux** good; **macOS** generally good — some UAC2 channel-mask configs finicky; prefer stereo 44.1/48 kHz |
| **B. Legacy `g_audio` module** | Fastest spike (`modprobe g_audio`); fewer moving parts | Deprecated; often **UAC1** only; limited params; macOS pitch/quirks reported; not future-proof | **Universal** UAC1 fallback — use only for Phase 0 smoke test |
| **C. Surge → gadget ALSA direct** | Lowest latency; matches existing `--audio-interface` pattern; no extra process | Profile switch must restart Surge; only one output at a time unless duplicated | N/A (device-side) |
| **D. ALSA loopback + `alsaloop` / piper → gadget** | Could mirror to analog + USB simultaneously | Extra buffer + CPU; xrun risk on Pi; contradicts “direct ALSA” philosophy | N/A |
| **E. Composite gadget (UAC2 + MIDI + ECM)** | One cable: audio + SSH/network + optional MIDI to host | More enumeration failure modes; ECM/RNDIS Windows vs macOS split; MIDI to host ≠ Roli path | Mixed — ECM fine on Linux/macOS; Windows wants RNDIS; **Apple USB PD** issues on Pi 5 |
| **F. Network audio (RTP, PipeWire, NetJack)** | No USB gadget port conflict; works over existing WiFi/Ethernet | WiFi latency/jitter; second stack to maintain; does not achieve “one USB cable” goal | All OSes with client software — **deprioritized** |
| **G. Keep analog aux (status quo)** | Zero dev risk; lowest Pi-side latency; gig-proven | Extra cable; poor laptop ergonomics | Universal |

---

## USB gadget mode — Pi 4 / Pi 5 notes

### Hardware port topology

| Pi model | Gadget port | Host peripherals (Roli, Sound Blaster) |
|----------|-------------|----------------------------------------|
| **Pi 4** | **USB-C** on board (`dwc2`, peripheral mode) | USB-A ports remain host |
| **Pi 5** | **USB-C** on board (SoC OTG — **not** the PCIe USB3 controller) | USB-A ports remain host |

Official guidance ([RPi OTG app note](https://pip-assets.raspberrypi.com/categories/685-app-notes-guides-whitepapers/documents/RP-009276-WP-1-Using%20OTG%20mode%20on%20Raspberry%20Pi%20SBCs.pdf), [Pi OS USB gadget blog](https://www.raspberrypi.com/news/usb-gadget-mode-in-raspberry-pi-os-ssh-over-usb/)):

- Add `dtoverlay=dwc2,dr_mode=peripheral` to `/boot/firmware/config.txt` (Bookworm).
- Load `libcomposite`; build gadget via configfs (not concurrent legacy `g_audio` + `g_midi`).
- Pi 4/5 may **draw power from the host** over USB-C — verify stability with official PSU vs laptop port; undervoltage causes audio glitches.

### Known host-side quirks (multi-OS)

| Host | UAC1 | UAC2 | Notes |
|------|------|------|-------|
| **Windows 10/11** | Plug-and-play | Plug-and-play | Often names device “USB Audio Device” / manufacturer string from gadget |
| **Linux** | Plug-and-play | Plug-and-play | PipeWire/PulseAudio auto-probe; may need to select output in `pavucontrol` |
| **macOS** | Plug-and-play | Usually plug-and-play | Reports of UAC2 channel-mask sensitivity; **Pi 5 USB-C↔USB-C** to recent Macs/iPads can fail if USB PD negotiation conflicts — workaround: USB-A adapter cable, or EEPROM `PSU_MAX_CURRENT` tuning ([linux#6569](https://github.com/raspberrypi/linux/issues/6569), [linux#6289](https://github.com/raspberrypi/linux/issues/6289)) |

**Practical cable guidance for Phase 2:** document **USB-A (host) → USB-C (Pi)** as the most reliable tether cable; treat USB-C ↔ USB-C as best-effort on Pi 5 + Apple Silicon until verified on target hardware.

### Clock master

> **⚠️ Unverified assumption (flagged 2026-08-25).** The Phase 0 drift confirmation
> requested below **was never performed** — the spike was consumed by the Surge/JUCE writer
> stall ([`USB-AUDIO-PASSTHROUGH-SPIKE.md`](USB-AUDIO-PASSTHROUGH-SPIKE.md)). The
> host-is-master claim has been inherited unmeasured ever since. It is load-bearing for any
> dual-output design — see **[`USB-DUAL-OUTPUT-CLOCK.md`](USB-DUAL-OUTPUT-CLOCK.md)**, which
> treats it as an open question rather than a fact.

In gadget playback (Pi → host), the **USB host typically clocks the isochronous stream** (host is master). Surge/ALSA on the Pi should run at a fixed **48000 Hz** matching gadget `p_srate`. Mismatch causes resampling or periodic underruns. Phase 0 spike must log `dmesg` + `aplay -l` and confirm stable playback without drift over several minutes.

### Composite MIDI + audio?

- **Roli stays on Pi USB-A host port** — unchanged.
- A composite **gadget MIDI** function would expose Pi ↔ **host** MIDI, not replace Roli routing.
- **Defer** composite MIDI unless desk workflow needs DAW MIDI to/from the module (Issue #6 lists this as optional stretch).

---

## Routing recommendation

**Prefer Approach C (direct Surge → gadget ALSA device)** for v1.

```
Profile: usb-host
[Roli] ──► [Surge XT CLI] ──► ALSA ──► UAC2 gadget card ──► USB-C ──► [Host PC speakers/DAW]

Profile: standalone (default, unchanged)
[Roli] ──► [Surge XT CLI] ──► ALSA ──► [Sound Blaster] ──► 3.5 mm analog
```

**Do not use loopback** unless a future requirement demands simultaneous analog + USB mirroring (unlikely for desk vs gig modes).

**Avoid JACK/Carla** — consistent with project architecture (`README.md`, Issue #4 notes).

When switching profiles, **restart `surge-xt-cli`** (same pattern as `99-usb-audio.rules` today).

---

## Latency expectations

| Stage | Approx. contribution @ 48 kHz |
|-------|----------------------------------|
| Surge buffer (`MPE_SURGE_BUFFER_SIZE=1024`, standalone default) | ~21 ms |
| ALSA → gadget | Part of Surge callback (same buffer if direct) |
| USB isochronous + host buffer | ~10–30 ms typical (host-dependent) |
| **Desk tether total (estimate)** | **~40–65 ms** |

**Implication:** Fine for patch editing, demos, recording, casual listening at desk. **Do not position as low-latency MPE monitor path through laptop speakers** — standalone Sound Blaster remains the performance path.

For desk use, document that **headphones on the Sound Blaster** still win for playing feel; USB-to-host is for convenience and capture.

---

## Recommended phased plan

### Phase 0 — Feasibility spike (manual, one reference Pi)

**Goal:** Prove Surge → UAC2 gadget → host audio with no aux, measure xruns/latency.

| Step | Action |
|------|--------|
| 0.1 | On reference Pi (Pi 5 preferred if that's primary BOM), enable `dtoverlay=dwc2,dr_mode=peripheral`, `libcomposite` |
| 0.2 | Deploy configfs script: `uac2.usb0` stereo, `p_srate=48000`, `p_ssize=2` (16-bit) or `4` (32-bit) — match Surge format |
| 0.3 | Confirm gadget ALSA card appears: `aplay -l`, `surge-xt-cli --list-devices` |
| 0.4 | Start Surge manually with `--audio-interface=<gadget-id>` while Roli + touch/OLED UI run — log xruns under typical MPE load |
| 0.5 | Tether to **one Windows or Linux desk PC** via USB-A→USB-C; confirm host lists playback device and audio is audible |
| 0.6 | Record rough round-trip latency (clap test / loopback measurement) and note cable type |
| 0.7 | Document Pi 5 USB-C↔USB-C vs A→C behavior on any Mac available |

**Exit criteria:** Host hears Surge for ≥10 min without xruns; latency noted; port/power layout confirmed feasible with Roli + Sound Blaster still attached.

**Deliverable:** Short spike log in issue #6 comment or `docs/USB-AUDIO-PASSTHROUGH-SPIKE.md` (after spike only).

### Phase 1 — Single-host profile (MVP)

**Goal:** Repeatable `usb-host` profile on Mitch's primary desk machine.

| Step | Action |
|------|--------|
| 1.1 | Add `MPE_AUDIO_PROFILE=standalone\|usb-host` to `/etc/mpe/mpe.env` (default `standalone`) |
| 1.2 | Extend `scripts/detect-audio-device.sh`: when `usb-host`, prefer gadget card (new Tier 0) before Sound Blaster Tier 1 |
| 1.3 | Add `scripts/setup-usb-audio-gadget.sh` + `config/usb-audio-gadget.service` — create/bind configfs gadget at boot **only if** profile is `usb-host` (or on demand) |
| 1.4 | Gate `99-usb-audio.rules`: do not restart-loop when gadget card flaps during bind — narrow match rules if needed |
| 1.5 | Document manual switch: set profile, plug USB-C to host, `sudo systemctl restart surge-xt-cli` (and gadget service) |
| 1.6 | Update `FAQ.md` + `REFERENCE_BOM.md`: aux optional when tethered; USB-C cable added to desk kit |

**Exit criteria:** Issue #6 acceptance rows 1–3 for **one** documented host OS; standalone regression test passes.

### Phase 2 — Multi-host hardening + optional UX

**Goal:** Reduce surprise across OSes; optional auto-detection.

| Step | Action |
|------|--------|
| 2.1 | Host matrix test: Windows 11, Linux (PipeWire), macOS if available — document device names and settings |
| 2.2 | Cable / PD troubleshooting section (A→C recommended; Pi 5 EEPROM note) |
| 2.3 | Optional: udev/heuristic auto-switch when `UDC` bound + host enumerated (`/sys/class/udc/.../state`) — **only after** manual profile proves stable |
| 2.4 | Optional touch UI indicator: “Audio → USB host” vs “Audio → Analog” |
| 2.5 | Optional composite **ECM/RNDIS + UAC2** for true one-cable power+SSH+audio — evaluate only if network cable is also undesirable |

**Exit criteria:** Issue #6 fully closed; known limitations documented per OS.

---

## Open questions / hardware prerequisites

| # | Question | Impact |
|---|----------|--------|
| 1 | **Desk power strategy:** Pi powered from official PSU + USB-C data-only to host, or bus-powered from laptop? | Stability under Surge + UI + gadget load |
| 2 | **Primary target host OS** for Phase 1? | Test order, docs |
| 3 | **Pi 4 vs Pi 5** — both supported, or Pi 5 only for gadget feature? | BOM / testing matrix |
| 4 | **Auto vs manual profile switch** — is `MPE_AUDIO_PROFILE` env enough for v1? | UX complexity |
| 5 | **UAC1 fallback** — ship dual gadget config for older hosts? | macOS/embedded hosts |
| 6 | **Sample format:** 16-bit vs 32-bit gadget `p_ssize` — match Surge internal path | CPU / compatibility |
| 7 | **Simultaneous analog monitor** while tethered — required? | Would force loopback (Approach D) |
| 8 | **USB port physical layout** in SmartiPi / desk enclosure — can user reach USB-C for tether without unplugging Roli? | Industrial design |

**Hardware prerequisites:**

- Raspberry Pi **4 or 5** (not Pi 3 — no OTG on model)
- **USB-C cable** (data-capable) from Pi to host — prefer **USB-A plug → USB-C Pi** for Pi 5 + Mac
- Existing Sound Blaster + Roli on USB-A ports unchanged
- Kernel with `libcomposite`, `uac2` function (Pi OS Bookworm 64-bit Lite — verify on spike)

---

## Relation to Issue #4 (FX passthrough)

These are **orthogonal problems**:

| | Issue #6 (this plan) | Issue #4 |
|---|---------------------|----------|
| **Direction** | Surge **output → host PC** | External **line-in → Surge FX → module out** |
| **Hardware** | USB-C gadget (Pi as USB device) | Sound Blaster **3.5 mm input** (Pi as USB host) |
| **ALSA** | Output device switch / gadget card | Input + output on same Play! 3 |
| **Surge** | Same synth patches | Dual scene / Audio Input oscillator patches |
| **Priority** | Active feature request (desk workflow) | Low / parked |
| **Complexity** | Moderate (gadget + routing) | Moderate+ (patch design, duplex ALSA, CPU, gate/latch quirks) |

If both were ever built:

- **`usb-host` profile** likely bypasses Sound Blaster entirely → Issue #4 passthrough **inactive** in that mode unless loopback or a second interface is added.
- **`standalone` profile** remains the target for Issue #4.

Do not conflate acceptance tests or spike work across the two issues.

---

## Alternatives deprioritized

| Alternative | Why deprioritized |
|-------------|-------------------|
| **Network audio** | Does not eliminate aux unless USB also carries audio; adds WiFi jitter and client setup |
| **Composite ECM + audio in v1** | Solves SSH without network but increases enumeration risk; network already works for deploy |
| **HDMI audio to monitor** | Wrong form factor for laptop desk; not cable reduction |
| **Second USB audio interface dedicated to host** | Pi has no USB device port other than OTG — still need gadget |

---

## References

### In repo

- `scripts/detect-audio-device.sh` — tiered ALSA output selection
- `scripts/start-surge-cli.sh` — Surge startup, buffer size
- `config/99-usb-audio.rules` — hot-plug restart behavior
- `REFERENCE_BOM.md` — Sound Blaster analog path
- `FAQ.md` — DAW / latency notes (update after implementation)
- GitHub Issue [#6](https://github.com/MitchSchwartz/MPE-Sound-Module/issues/6), [#4](https://github.com/MitchSchwartz/MPE-Sound-Module/issues/4)

### External

- [Linux kernel — configfs USB gadget](https://docs.kernel.org/usb/gadget_configfs.html)
- [configfs-usb-gadget-uac2 ABI](https://www.kernel.org/doc/Documentation/ABI/testing/configfs-usb-gadget-uac2)
- [Raspberry Pi — Using OTG mode on SBCs (PDF)](https://pip-assets.raspberrypi.com/categories/685-app-notes-guides-whitepapers/documents/RP-009276-WP-1-Using%20OTG%20mode%20on%20Raspberry%20Pi%20SBCs.pdf)
- [Raspberry Pi OS — USB gadget mode / SSH over USB](https://www.raspberrypi.com/news/usb-gadget-mode-in-raspberry-pi-os-ssh-over-usb/)
- [pi-audio-duplex](https://github.com/AlexanderPavlenko/pi-audio-duplex) — composite UAC2 + Ethernet reference
- [Raspberry Pi Forums — composite UAC2 + MIDI](https://forums.raspberrypi.com/viewtopic.php?t=333504)
- [diyAudio — Pi4 UAC2 gadget thread](https://www.diyaudio.com/community/threads/linux-usb-audio-gadget-rpi4-otg.342070/)
