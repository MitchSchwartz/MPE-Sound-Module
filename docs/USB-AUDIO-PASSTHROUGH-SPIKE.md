# USB audio passthrough — Phase 0 spike results

*Last updated: 2026-08-05 (America/Toronto)*

Engineering spike for Pi → host UAC2 passthrough (`MPE_AUDIO_PROFILE=usb-host`). Full plan: **[USB-AUDIO-PASSTHROUGH-PLAN.md](USB-AUDIO-PASSTHROUGH-PLAN.md)**. Operator guide: **[USB-AUDIO-HOST.md](USB-AUDIO-HOST.md)**.

**Reference hardware:** **Pi 4 Model B** (live unit).

Run on Pi: `./scripts/usb-host-verify.sh`

---

## Phase 0 results

| Check | Result | Notes |
|-------|--------|-------|
| `dtoverlay=dwc2,dr_mode=peripheral` + configfs UAC2 | Pass | Gadget binds via `usb-audio-gadget.service` |
| Surge Tier 0 device selection | Pass (after fix) | Prefer **Direct hardware** on UAC2 gadget card — see PR #17 |
| Host sees gadget | Pass | Linux/Windows list "USB Audio Passthrough" / "MPE Sound Module" |
| Host must use **capture/input**, not playback | Pass (documented) | Pi playback → host recording device |
| Linux `arecord -D hw:N,0` | Pass | Tone test peak ~26267 on `hw:N,0` |
| Linux `arecord -D plughw:N,0` | Fail (silent) | Do not use plughw for capture — use `hw:N,0` |
| `speaker-test` on Pi without host reader | Expected fail | I/O error -5 when nothing consumes UAC2 stream |
| Surge + user patch → host capture | **Pass (2026-08-02)** | Root-caused to a JUCE ALSA writer stall — see below. Peak 0.66 captured live at 512-sample buffer |
| 10+ min stable playback | Not verified | Needs timed soak on hardware |
| Latency measurement | Not verified | Blocked on stable host capture during play |

---

## Open issues

1. ~~Host capture silent during normal Surge play~~ — **fully root-caused 2026-08-02: a Surge/JUCE ALSA writer stall.** See §Writer stall below. The 2026-07-31 PD-power finding was a *separate, real* fault that masked this one; with the UDC reading `configured`, the stall is what remained.
2. **Boot with USB-C tethered** — early boot hang if host connected before kernel ready; see [PI-BOOT-RECOVERY.md](PI-BOOT-RECOVERY.md).
3. **Host capture left open blocks aux (2026-08-05)** — In `usb-host` profile, any open host capture stream (Reaper armed, PipeWire link in qpwgraph, or ALSA capture default → gadget) keeps Surge on UAC2; Sound Blaster aux stays silent until capture fully closes. Fix: disarm DAW, remove stray PipeWire links, use explicit `pcm.mpe_pi` only (`config/host/asoundrc.mpe-pi`). See [USB-AUDIO-HOST.md](USB-AUDIO-HOST.md) §Host quirks.

---

## Root cause found: PD-power dock on the same port as gadget data (2026-07-31)

**Symptom:** Tone (`speaker-test`) captured fine on the host when the host started capturing *before* playback. MIDI notes into Surge — via OSC `/mnote`, `/patch/load` + note, or raw MIDI on any channel — produced silence on host capture, even after ruling out CPU contention (touch-patch-browser's 100% CPU bug) and a transient under-voltage/reboot event. Confirmed via `aseqdump` that MIDI notes *were* reaching Surge's ALSA sequencer input — so this was never a MIDI-routing problem.

**Actual fault:** `cat /sys/class/udc/fe980000.usb/state` on the Pi read `not attached` — the Pi's own USB-C controller believed nothing was connected, even with a confirmed well-seated cable and the gadget bound (`UDC=fe980000.usb`, `GADGET=bound`). The gadget had cleanly enumerated on the host three times earlier in the session (before power was moved to a PD dock), then never attached again after the dock took over powering the Pi through the same USB-C port used for gadget data.

**Why:** Raspberry Pi's official OTG app note is explicit about this — power the Pi via GPIO 5V/GND, "leaving the USB-C free" for the host data connection. Pi 4's USB-C port has no real ID-pin dual-role detection; when a PD-negotiating power source and a `dwc2` peripheral-mode data session share the same port, CC-line role negotiation can get stuck and `dwc2` never receives a clean attach event. This is a documented Pi 4 hardware limitation (see community writeups on Pi4 Type-C VBUS/CC conflicts), not a cable, seating, or Surge/ALSA issue — no amount of cable swapping or software config resolves it.

**Fix required (Pi 4):** Power the Pi via **GPIO 5V/GND or official PSU**, independent of the USB-C cable carrying gadget data to the host. Do not rely on a PD dock on the same USB-C port used for gadget data.

---

## Verification commands

```bash
# Pi
MPE_AUDIO_PROFILE=usb-host ./scripts/usb-host-verify.sh
./scripts/test-audio-detection.sh

# Linux host (after arecord -l shows card N)
arecord -D hw:N,0 -f S16_LE -r 48000 -c 2 -d 5 /tmp/mpe-capture.wav
sox /tmp/mpe-capture.wav -n stat  # expect non-zero RMS on tone test
```
