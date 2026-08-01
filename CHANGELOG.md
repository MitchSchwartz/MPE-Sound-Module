# Changelog

Notable engineering work, grouped by session. Each per-topic doc under `docs/`
carries the detailed technical narrative; this file is the chronological index.

## 2026-08-01 — KMSDRM/USB engineering pass + calibration regression chain

### KMSDRM crash loop, boot/shutdown splash handoff

- Fixed `touch-patch-browser.service` crash-looping (`pygame.error: kmsdrm not
  available`) by adding `scripts/prepare-dsi-display.sh` as `ExecStartPre`:
  stops the boot splash, kills orphaned `pygame` processes still holding
  `/dev/dri/card1`, and waits for DRM release before the browser starts.
- Hardened the same script to kill stale holders *before* stopping the boot
  splash, and to wait for process exit — an earlier pass left two
  `touch-patch-browser` PIDs both holding DRM (one from systemd, one from a
  leftover interactive SSH session).
- See [`docs/PI-BOOT-RECOVERY.md`](docs/PI-BOOT-RECOVERY.md), [`docs/SHUTDOWN.md`](docs/SHUTDOWN.md).

### USB-host audio passthrough — root cause, not fixed

- Extensive troubleshooting (OSC message format, ALSA device selection, MIDI
  routing via `aseqdump`, CPU/under-voltage checks) ruled out every software
  cause for silent USB-host audio.
- **Root cause: hardware.** The Pi 4's single USB-C port can't reliably carry
  both Power Delivery (from a dock) and USB gadget data simultaneously — the
  `dwc2` controller ends up `not attached` under a PD dock. Documented in
  [`docs/USB-AUDIO-PASSTHROUGH-SPIKE.md`](docs/USB-AUDIO-PASSTHROUGH-SPIKE.md);
  fix requires a PiKVM USB power/data splitter or GPIO power, not more code.

### Calibration regression chain — "1200 steps, 0 saved"

A single user report ("ran calibration all night, cancelled, kept 0") turned
into a chain of four distinct, real bugs stacked on top of each other:

1. **Launch path regression (commit `c00e6b6`).** The boot/shutdown splash
   refactor changed how the calibration loader launches — from execing
   `calibrate-with-loader.sh` (a bash wrapper that sets up env/paths/DRM
   handoff) to calling `calibration_loader.py` directly. That skipped
   environment setup calibration depended on. **Fix:** reverted to the bash
   wrapper launch path.
2. **Capture routing mismatch.** Even after the launch fix, standalone
   calibration still measured near-silence: Surge was configured to output to
   the Sound Blaster, but `ffmpeg` was capturing from a different ALSA path.
   **Fix:** built `patch_browser/calibration_standalone.py` (dedicated cal
   Surge instance on Sound Blaster Direct hardware + `dsnoop` capture aligned
   to it) and `patch_browser/calibration_loopback.py` (dynamic `snd-aloop`
   interface/device resolution, replacing a hardcoded `0.19`/`plughw:Loopback,1,0`).
3. **True-peak fallback masking bad measurements.** A fallback in
   `is_invalid_measurement()` let near-silent captures (-47 to -57 LUFS)
   through and saved extrapolated gains of +17 to +25 dB that didn't restore
   real loudness. **Fix:** removed the fallback — a capture that can't clear
   `MIN_VALID_LUFS` now fails loud (skipped) instead of saving a guessed gain.
   Added progressive gesture-length retry (`GESTURE_DURATIONS_SECONDS = (3, 5,
   8, 12)`) so slow-attack/filter-sweep patches get a real chance before
   giving up, rather than retrying the same 3s gesture and failing fast.
4. **Norm-on volume cap silently discarding calibrated gain.** The actual
   root cause of "no change with norm on or off": `NORM_MAX_AMP_VOLUME_LINEAR`
   was `0.85` (below unity), so *any* calibrated `gain_db` above roughly
   -1.4 dB got clamped down to near-unity before reaching Surge — norm on vs.
   off differed by ~1.4 dB, inaudible. Raised to match the norm-off ceiling
   (`MAX_AMP_VOLUME_LINEAR = 1.5`). Verified via an isolated Pi stress test
   (same patch, same 12-note MPE polyphony, cap=1.0 vs 1.5 → **identical**
   CPU) that the cap wasn't actually protecting against gain-driven CPU cost —
   the pre-existing polyphony/CPU ceiling on heavy patches is orthogonal to
   this fix.

A `git diff dev` / A-B test against a known-good commit (`dfa9279`) was the
tool that separated "which of yesterday's USB-audio PRs broke this" from
"what's actually still broken" — most of the USB-audio work was unrelated;
only the launch-path commit was a genuine regression. Full narrative in
[`docs/PATCH_NORMALIZATION.md`](docs/PATCH_NORMALIZATION.md).

### Test coverage added this session

- `tests/test_calibration_loopback.py`, `test_calibration_standalone.py`,
  `test_calibration_routing.py` — capture routing (loopback default,
  `MPE_CAL_ROUTE` escape hatch, dsnoop resolution).
- `tests/test_calibration_loader_messages.py` — cancel-message accuracy
  (saved vs. attempted count).
- `tests/test_calibration_progressive_gesture.py` — gesture-length escalation.
- `tests/test_calibration_integrity.py` — regression pins for the true-peak
  fallback removal and the norm-cap fix specifically, including an
  end-to-end test that a large calibrated `gain_db` actually reaches the OSC
  send instead of being silently clamped to near-unity.
- `tests/test_detect_audio_device.py`, `test_prepare_dsi_display.sh`,
  `test_touch_browser_patch_reload.py` — KMSDRM/USB-detection coverage from
  the same pass.
- All picked up automatically by CI (`.github/workflows/test.yml` runs
  `python3 -m unittest discover -s tests` on push/PR to `dev`/`main`) —
  80 tests passing as of this entry.
