# USB audio passthrough — Phase 0 spike results

*Last updated: 2026-07-31 (America/Toronto)*

Engineering spike for Pi → host UAC2 passthrough (`MPE_AUDIO_PROFILE=usb-host`). Full plan: **[USB-AUDIO-PASSTHROUGH-PLAN.md](USB-AUDIO-PASSTHROUGH-PLAN.md)**. Operator guide: **[USB-AUDIO-HOST.md](USB-AUDIO-HOST.md)**.

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
| Surge + user patch → host capture | **Open** | Tone/generator may work; live patch playback still silent on host capture in testing |
| 10+ min stable playback | Not verified | Needs timed soak on hardware |
| Latency measurement | Not verified | Blocked on stable host capture during play |

---

## Open issues

1. ~~Host capture silent during normal Surge play~~ — **root-caused 2026-07-31, see below.** Not a Surge/ALSA bug — it's a link-layer attach failure.
2. **Boot with USB-C tethered** — early boot hang if host connected before kernel ready; see [PI-BOOT-RECOVERY.md](PI-BOOT-RECOVERY.md).

---

## Root cause found: PD-power dock on the same port as gadget data (2026-07-31)

**Symptom:** Tone (`speaker-test`) captured fine on the host when the host started capturing *before* playback. MIDI notes into Surge — via OSC `/mnote`, `/patch/load` + note, or raw MIDI on any channel — produced silence on host capture, even after ruling out CPU contention (touch-patch-browser's 100% CPU bug) and a transient under-voltage/reboot event. Confirmed via `aseqdump` that MIDI notes *were* reaching Surge's ALSA sequencer input — so this was never a MIDI-routing problem.

**Actual fault:** `cat /sys/class/udc/fe980000.usb/state` on the Pi read `not attached` — the Pi's own USB-C controller believed nothing was connected, even with a confirmed well-seated cable and the gadget bound (`UDC=fe980000.usb`, `GADGET=bound`). The gadget had cleanly enumerated on the host three times earlier in the session (before power was moved to a PD dock), then never attached again after the dock took over powering the Pi through the same USB-C port used for gadget data.

**Why:** Raspberry Pi's official OTG app note is explicit about this — power the Pi via GPIO 5V/GND, "leaving the USB-C free" for the host data connection. Pi 4's USB-C port has no real ID-pin dual-role detection; when a PD-negotiating power source and a `dwc2` peripheral-mode data session share the same port, CC-line role negotiation can get stuck and `dwc2` never receives a clean attach event. This is a documented Pi 4 hardware limitation (see community writeups on Pi4 Type-C VBUS/CC conflicts), not a cable, seating, or Surge/ALSA issue — no amount of cable swapping or software config resolves it.

**Fix required (blocked on hardware, as of 2026-07-31):** Power the Pi via GPIO 5V/GND from a dedicated supply, independent of the USB-C cable carrying gadget data to the host. Until then, USB-host passthrough is not usable when the Pi's only power source is a USB-C PD dock on the same port.

---

## Verification commands

```bash
# Pi
MPE_AUDIO_PROFILE=usb-host ./scripts/usb-host-verify.sh
./scripts/test-audio-detection.sh

# Linux host (after arecord -l shows card N)
arecord -D hw:N,0 -f S16_LE -r 44100 -c 2 -d 5 /tmp/mpe-capture.wav
sox /tmp/mpe-capture.wav -n stat  # expect non-zero RMS on tone test
```
