# Changelog

Notable engineering work, grouped by session. Each per-topic doc under `docs/`
carries the detailed technical narrative; this file is the chronological index.

## 2026-08-12 — JACK Phase 1 Gate B soak complete (Pi)

Branch `yolo/jack-audio-engine-phase1` @ `4d93fe2`. Gate A approved; Pi soak on
`raspberrypi2.local` via `mpe` CLI. Spec: `Documents/specs/jack-audio-engine-spec.md`
§Gate B soak log.

- **PASS:** cold boot, pkill jackd, DAC replug (slow ~39–60 s), 2a/2d/2b2/2c, 5a,
  3 ALSA engine, 6 SCHED_FIFO, 13 HUD (partial), 14 calibrate with jackd up, 17 CLI.
- **BLOCKED:** 5b UAC2 host capture + `session_capture.py` — physical rewire.
- **DEFER:** criterion 13 full guarded badge (`MPE_LOOPER_ENABLED=1` boot); criterion
  10 to `yolo/looper-phase0` merge.
- **Fixes during soak:** `release-alsa-for-jackd` (2d promotion); calibrator
  `list_missing` dict shape; mpe-cli `engine calibrate-smoke`, stash-aware mask.
- **Backlog (post-merge):** recovery latency (~30–60 s vs 15 s budget); extract ALSA
  fallback junction for tests; `surge-xt-cli` StartLimitIntervalSec section bug.

## 2026-08-12 — JACK Phase 1 review pass 2 (verification fixes)

Independent verification review on `yolo/jack-audio-engine-phase1`. Safety theme:
never boot silent; never wedge the audio service; stop jackd before ALSA fallback.

- **Blocker:** `mpe_release_audio_device_for_alsa()` — non-blocking
  `mpe-jackd.service` stop + bounded poll before ALSA tier selection at the single
  fallback chokepoint in `start-surge-cli.sh`. Appliance rests `degraded` with jackd
  stopped until reboot or manual start.
- **Finding 11:** keep `StartLimitIntervalSec=0` (9258b68 — criterion 15 DAC replug);
  `reset-failed` before graph restart (0cc6763); skip jackd restart when Surge
  holds ALSA after fallback.
- **udev:** `scripts/install-udev-rules.sh` — all three installers templated;
  rules install regardless of UI mode.
- **Watchdog:** `is-failed` branch routed through `mpe_engine_reconcile_decision`
  cooldown; B3 tests invoke real `_reconcile_engine`.
- **Tests:** ALSA-fallback jackd-stop regression (finding 7); jackd
  `RuntimeDirectoryPreserve`; root skip for run-dir fallback; jack_lsp log-once.
- **HUD:** cached `EngineStateMonitor`; capped `engine.state` read.

## 2026-08-12 — JACK audio engine Phase 1 review fixes

Independent code review on `yolo/jack-audio-engine-phase1`. Safety theme: never boot
silent, never wedge the audio service.

- **Stripped looper cherry-picks** (`mpe-looper.py`, service wrapper, route script,
  unit) — guard policy retained in `engine-guard.sh` + `patch_browser/audio_engine.py`.
- **B2:** `RuntimeDirectoryPreserve=yes` on `surge-xt-cli` and `surge-watchdog` so
  `/run/mpe` cooldown state survives Surge restarts.
- **B3:** Watchdog publishes `degraded` and takes no action when Surge is already on
  ALSA and jackd is down (criterion 2a settle, not bounce loop).
- **B4:** udev `99-usb-audio.rules` → `restart-audio-graph.sh` (engine-aware,
  `--no-block`).
- **M1–M4:** Bounded `--list-devices`, removed dead jackd sleep loop, symmetric
  `jack_lsp` probes, atomic state writes, dead-code removal.
- **Criterion 13:** Touch HUD engine badge (`patch_browser/audio_engine.py`,
  `touch_browser_draw.py`).
- Spec: criterion 10 deferred to `yolo/looper-phase0` merge; criterion 13 marked
  implemented.

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

## 2026-08-02 — USB-host end-to-end + norm toggle chain + browse/theme polish

### USB host audio — working end-to-end

- **Root-caused** the long-standing "host hears silence while playing" issue to a
  Surge/JUCE ALSA writer stall (`appl_ptr` frozen, ~0 CPU) once the host stops
  consuming the UAC2 stream — not cable, PD, or DAW routing. Documented in
  [`docs/USB-AUDIO-HOST.md`](docs/USB-AUDIO-HOST.md) §Writer stall and
  [`docs/USB-AUDIO-PASSTHROUGH-SPIKE.md`](docs/USB-AUDIO-PASSTHROUGH-SPIKE.md).
- Added **`uac2-stall-watchdog.service`** — restarts Surge when the host opens
  capture but the gadget writer is wedged. Verified live: peak 0.66 on host at
  512-sample buffer, 0 xruns.
- **Profile persistence:** `MPE_AUDIO_PROFILE` in `/etc/mpe/mpe.env` survives
  reboot; touch settings toggle + `mpe-audio-profile-sync.service` at boot;
  `configure-pi-paths.sh --force` preserves profile and buffer size.
- **In-app toggle:** System settings → USB host audio; keeps loaded patch when
  switching analog ↔ USB; background overlay during switch.
- Tests: `tests/test_uac2_card.py`, stall watchdog helpers.

### Norm toggle regression chain (follow-on to 2026-08-01 cap fix)

Even after raising `NORM_MAX_AMP_VOLUME_LINEAR` to 1.5, norm on/off still
sounded the same on some patches:

1. **Skip-send on norm off** — left sticky `amp/volume` from prior norm-on session.
2. **Global toggle re-enable** — did not reload patch / re-apply calibration.
3. **Unity-trim skip** — briefly skipped OSC when norm off at unity (reverted).

**Fix:** always assert OSC amp/volume; reload patch on global enable; preserve
calibration data on disable. See [`docs/PATCH_NORMALIZATION.md`](docs/PATCH_NORMALIZATION.md)
§Norm toggle behavior.

### Touch UI — All patches browse (#10) + theme system

- **All** nav: flat A→Z list, folder subtitle, A–Z jump rail, Quick Access hearts.
- **Theme modal:** base theme (standard / OLED dark), accent style (monochrome /
  minimal), accent presets + custom RGB picker; applies to boot/shutdown splash
  and calibration loader.
- **Settings panel** slide-out (replaces modal); CPU meter toggle; finger-up
  activation on scroll areas.
- Documented in [`docs/TOUCH_PATCH_BROWSER.md`](docs/TOUCH_PATCH_BROWSER.md).

### Calibration + loader handoff

- Accept quiet-but-real patches (peak-based validity, not integrated-LUFS floor).
- Post-calibration browser crash loop fixed; loader `paint_immediate` handoff.
- Pi normalization snapshot tracked in repo (`config/patch_normalization.pi-backup-*`).

### Test coverage

- **136 tests** passing locally as of this entry (was 80 on 2026-08-01).
- New: norm toggle/global restore, UAC2 card helpers, calibration integrity pins.

## 2026-08-03 — Touch fader semantics (cal anchor + trim)

- **Touch** fader canon documented in `docs/TOUCH_PATCH_BROWSER.md` §Mixer faders.
  Calibrated `cal_floor` sets the default handle position on **−50…+50**; user trim
  moves from there; double-tap clears trim and restores cal. **Not** the same as
  **Tail** (0 at center = patch-as-loaded).
- Removed legacy alias helpers in `patch_pressure.py` that implied three competing
  mapping models.

## 2026-08-03 — Output limiter removed + Tail/Touch fader alignment

### Removed in-Surge output limiter

Surge has no synth-wide FX bus — Global FX slot 4 is part of each patch, and the
LinnStrument MPE pack uses it on most patches (mostly reverb). Commandeering that
slot for a Conditioner limiter overwrote patch sound design. Removed limiter OSC
sync, **LIM** header badge, settings toggle, peak monitor, `surge-output-limiter`
systemd unit, and `docs/SURGE-OSC-PARAMS.md`. Real peak safety stays in offline
calibration; live limiting belongs in the host/USB chain if needed.

### Tail/Touch fader UI

- **Tail** fader: **0** at center = patch-as-loaded (1.0× multiplier); display
  **−50…+50**; log mapping preserves **0.25×–4.0×** at full throw.
- **Touch** uses the same **±50** display range but **cal-anchored** semantics (see
  entry above and `docs/TOUCH_PATCH_BROWSER.md`).
