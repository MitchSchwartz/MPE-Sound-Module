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

1. **Host capture silent during normal Surge play** — investigate ALSA period/buffer on gadget capture path, Surge output routing, and host DAW/Pulse routing. Tone tests suggest the USB path works; patch playback may need separate routing or buffer tuning.
2. **Boot with USB-C tethered** — early boot hang if host connected before kernel ready; see [PI-BOOT-RECOVERY.md](PI-BOOT-RECOVERY.md).

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
