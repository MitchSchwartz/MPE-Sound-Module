# Per-patch volume normalization

Static loudness matching for Surge XT patches on the MPE appliance. Calibrate once offline; apply a cheap JSON lookup + OSC volume send on every `load_patch()`.

**Issue:** [MPE-Sound-Module #5](https://github.com/MitchSchwartz/MPE-Sound-Module/issues/5)

## Design

| When | What |
|------|------|
| **Scan** | Log count of patches missing calibration — no rendering during scan |
| **Offline / SSH** | Render gesture → measure LUFS → write gain to JSON |
| **Load** | Lookup by patch name (stem) → set amp/volume baseline → user trim stacks on top |

**Not** a runtime limiter — MPE expression (velocity, pressure) stays untouched.

### Measurement

- **Standard:** integrated LUFS (EBU R128) via ffmpeg `loudnorm`
- **Gesture per patch:** strike mid note → hold with channel pressure sweep low→max → release
- **Target:** mid-loudness (~−18 LUFS integrated) for relative matching, capped so true peak lands ~−3 dBFS below clip

### Storage

File: `patch_normalization.json` keyed by **patch name** (stem). Favorites copies share the same key.

```json
{
  "My Patch": {
    "gain_db": -2.5,
    "enabled": true,
    "lufs_measured": -15.5,
    "true_peak_dbtp": -3.2,
    "calibrated_at": "2026-07-30T17:00:00+00:00"
  }
}
```

| Path | Purpose |
|------|---------|
| `~/.patch_browser_normalization.json` | Runtime store on Pi/PC (default) |
| `config/patch_normalization.json` | Shipped starter `{}` in repo |
| `config/patch_normalization.pi-backup-*.json` | Tracked Pi runtime snapshots (e.g. partial cal); restore to `~/.patch_browser_normalization.json` on deploy — not overwritten by git pull |
| `MPE_NORMALIZATION_FILE` | Env override for either script or runtime |

`enabled: false` skips normalization for that patch (falls back to global trim only).

### Global master switch

System settings (⋯) → **Patch normalization** turns all per-patch normalization off at once. Per-patch `enabled` flags and calibration data in `~/.patch_browser_normalization.json` are **not** cleared — the global state is stored under the reserved `_global` key in the same file:

```json
{
  "_global": { "enabled": false },
  "My Patch": { "gain_db": -2.5, "enabled": true, "lufs_measured": -15.5 }
}
```

When global is off, the patch detail **Norm.** control is greyed out and non-interactive; turning global back on restores each patch’s stored `enabled` setting.

### Toggle persistence

The touch UI **Norm.** checkbox writes only the `enabled` flag for that patch stem. Calibration fields (`gain_db`, `lufs_measured`, etc.) are preserved:

- **User file** `~/.patch_browser_normalization.json` overlays the repo starter at load time (field-wise merge per patch).
- **Re-enable** after turning Norm off restores the stored gain immediately — no re-calibration.
- **Disable** with no prior entry creates a minimal `{"enabled": false}` row; enabling again without calibration shows *Normalize on (no calibration)* and leaves volume at the user trim only.

### Runtime volume

Surge OSC `/param/a/amp/volume` and `/param/b/amp/volume` use a linear scale (`1.0` = unity). Stored `gain_db` converts via `10^(gain_db/20)`.

On load:

1. Normalization sets `_patch_gain_linear` baseline
2. User volume slider (`set_volume`) is a trim multiplier on top: `combined = trim × baseline`
3. When **Norm.** is on or off, combined OSC amp/volume shares the same ceiling (**1.5** linear). User trim stacks below the cap. (The norm-on cap was **0.85** until 2026-08-01 — see below — which silently discarded most calibrated gain.)

### Polyphony and static/crackle (Pi)

Static or crackle under **many held keys** is usually **ALSA buffer underrun (xrun)**, not clip — especially on the Pi + Sound Blaster path.

| Factor | Effect |
|--------|--------|
| **Norm ON** | Quiet patches get large `gain_db` boosts; runtime cap now matches norm-off (**1.5**, was 0.85 — see 2026-08-01 fix below). |
| **Norm OFF** | Unity baseline; user trim up to **1.5** — more headroom for solo, less for dense chords. |
| **ALSA buffer** | `surge-xt-cli` starts with **`MPE_SURGE_BUFFER_SIZE`** (default **1024** samples @ 44.1 kHz ≈ 23 ms). Was 512 (~12 ms) and xran under load. |
| **snd-aloop** | Loaded only during calibration loopback. Unloaded on Surge start and after `calibrate-with-loader.sh` if refcount is 0. |

**Calibration capture path (default: loopback):** Stops production Surge, starts a **cal-only** Surge instance routed through `snd-aloop` (`calibration_loopback.py` dynamically resolves the interface/capture device — no hardcoded card index). Headphones/Sound Blaster are silent during cal; that is expected. Launch from **System → Calibrate** (`calibrate-with-loader.sh`).

**2026-08-01 A/B (see below):** loopback measured **4–14 dB hotter** than the Sound Blaster/`dsnoop` path on the same patches, so it's now the default on every profile, not just `usb-host`. The `dsnoop:CARD=S3,DEV=0` path (`calibration_standalone.py`) is kept as an escape hatch — set `MPE_CAL_ROUTE=standalone` (env) or `--no-use-loopback` (CLI) to force it if loopback ever regresses again.

**Measurement validity (peak-based).** A capture is rejected only when true peak is below **−45 dBTP** or LUFS/peak is non-finite — i.e. no audible Surge output in the recording. Quiet-but-real patches (e.g. −47 LUFS, −29 dBTP) are **accepted** and get a large corrective `gain_db`; that is intentional. The old integrated-LUFS floor (−39) wrongly skipped ~170 overnight near-misses that needed normalization most. Silent/broken captures (−60 LUFS, peak buried below −45 dBTP) still fail after progressive gesture retries.

**Progressive gesture retry.** Instead of retrying a below-threshold patch with the same fixed 3s gesture and giving up, each retry holds the note longer: `GESTURE_DURATIONS_SECONDS = (3.0, 5.0, 8.0, 12.0)` (`MEASURE_MAX_ATTEMPTS` now derives from this tuple). Note-hold time scales with gesture length via `hold_seconds_for_gesture()`, capped by pre-roll/tail overhead. This targets slow-attack/filter-sweep patches (long acid filter opens, slow pads) that never reach real loudness in a short gesture — they now get up to ~27s total across 4 attempts before being skipped for real.

**Root cause of "Acid is quiet, no change with norm on/off" (2026-08-01):** not a capture or measurement bug — the patch's own `.fxp` routing (`a_vca_velsense=-5.14`, inverted velocity: harder hits are quieter; scene `volume=-15.7dB`) makes it genuinely quiet by design. Calibration correctly computed a large corrective `gain_db` (+16 to +26 dB depending on capture route), but `_send_combined_volume()` clamped the norm-on send to **`NORM_MAX_AMP_VOLUME_LINEAR=0.85`** — below unity — so almost the entire calibrated gain was thrown away before it ever reached Surge. Norm-on (0.85) vs norm-off (1.0, capped at `MAX_AMP_VOLUME_LINEAR=1.5`) differed by only ~1.4 dB, which is inaudible. Every patch needing more than a ~-1.4dB correction was silently under-corrected the same way — `Acid` just made the gap obvious.

**Fix:** raised `NORM_MAX_AMP_VOLUME_LINEAR` to match `MAX_AMP_VOLUME_LINEAR` (1.5) — same Surge `amp/volume` ceiling either way, pending the Pi xrun/CPU stress test (dense MPE polyphony) that motivated the lower cap originally. If that test shows real xrun/CPU regression, the next lever is reducing max voice count for heavily-boosted patches rather than re-lowering the global cap.

If crackle persists with Norm off and moderate polyphony, try `MPE_SURGE_BUFFER_SIZE=2048` in `/etc/mpe/mpe.env` and restart `surge-xt-cli`. Tradeoff: higher latency.

**Live diagnosis:** the touch browser header **CPU** meter (see [`TOUCH_PATCH_BROWSER.md`](TOUCH_PATCH_BROWSER.md)) tracks `surge-xt-cli` process load while you play — use it to see when dense polyphony is pushing the Pi toward xrun territory. Norm on should keep typical Quick Select patches lower on that meter than uncapped gain would.

**Quick Select reference (2026-07-30):** calibrated `gain_db` spans about +4 to +18 dB. Without a runtime cap that would map to **~1.6–8.0** linear OSC — too hot for dense MPE on the Pi. With Norm on, combined amp/volume is capped at **1.5** linear (same as norm off).

### Norm toggle behavior (2026-08-02 fixes)

Headless Surge keeps `amp/volume` **sticky** across patch loads — it is not reset by `/patch/load`. Two bugs made norm on/off sound identical:

1. **Stale OSC when norm off.** An early pass skipped the OSC send when norm was off at unity trim, leaving the parameter stuck at whatever a prior norm-on session set (e.g. 1.5). **Fix:** `_send_combined_volume()` always asserts `trim × baseline` via OSC for both on and off.
2. **Global/per-patch re-enable.** Toggling global norm back on did not reload the patch or re-apply stored calibration. **Fix:** reload loaded patch after global toggle; per-patch toggle reloads when needed.

When norm is off at unity user trim, OSC sends **1.0** (not "skip send").

## Calibration script

Patch discovery scans all folders under the Surge patch symlink roots (`SURGE_PATCH_DIRS` in `patch_browser/patch_scanner.py`), deduplicated by patch stem. **Default scope is the full library**, not Quick Select.

| Flag | Effect |
|------|--------|
| *(none)* | All scanned patches **missing** a `gain_db` entry |
| `--force` | Re-calibrate **every** scanned patch (overwrites `gain_db`) |
| `--favorites-only` | Quick Select folder only (legacy / ad-hoc) |
| `--folder "Name"` | One category folder under user patches |
| `--patch "Stem"` | Single patch by name |
| `--dry-run` | List targets; no Surge/ffmpeg |
| `--limit N` | Process at most N patches |
| `--progress-json` | Machine-readable progress on stdout (loader UI) |

The loader progress line shows **`patch index / total · saved N`**. On cancel, **saved** is the count of patches that measured successfully and were written to JSON — not the attempt index. If capture fails for every patch, cancel reads *saved 0 calibrations (N attempted; none measured successfully)* even after a long run.

```bash
# List patches missing calibration (~estimate only)
python3 scripts/calibrate-patch-normalization.py --dry-run

# Incremental — only patches without gain_db (full library scan)
python3 scripts/calibrate-patch-normalization.py

# Force full library re-calibration
python3 scripts/calibrate-patch-normalization.py --force

# Quick Select only (optional; not the touch UI default)
python3 scripts/calibrate-patch-normalization.py --favorites-only

# Specific folder under user patches
python3 scripts/calibrate-patch-normalization.py --folder "Quick Select"

# Custom output path
python3 scripts/calibrate-patch-normalization.py --output ~/.patch_browser_normalization.json

# Test write path without Surge/ffmpeg
python3 scripts/calibrate-patch-normalization.py --mock-lufs -20 --limit 1
```

### Pi touch display (loader UI)

When calibration runs on the Pi touch build, the patch browser stops and tty1 would otherwise show a Linux console. Use the loader wrapper so the DSI panel shows progress instead:

```bash
# SSH or local shell on the Pi — incremental (missing gain_db only)
./scripts/calibrate-with-loader.sh

# Force full library re-calibration
./scripts/calibrate-with-loader.sh --force

# Quick Select only (ad-hoc)
./scripts/calibrate-with-loader.sh --favorites-only --force
```

**From the touch UI:** System settings (⋯) → **Calibrate missing patches** or **Force full re-calibration** → confirm modal (mode, duration hint, DSI handoff) → **Start**. The browser exits, the loader takes over kmsdrm, then `surge-xt-cli` and `touch-patch-browser` restart when finished. **Cancel** on the loader stops the calibrator, tears down loopback Surge, and restores services (partial JSON writes are kept).

The loader shows patch name, `N / M` progress, elapsed time, *Do not touch — Surge is measuring loudness*, and a **Cancel** button. Implementation: `patch_browser/calibration_loader.py` subprocesses `calibrate-patch-normalization.py --progress-json`.

**What happens to services**

| Step | touch-patch-browser | surge-xt-cli |
|------|---------------------|--------------|
| Loader starts | Stopped (wrapper + calibrator) | Stopped; temporary loopback Surge for capture |
| During run | Loader fullscreen on DSI | Calibration Surge instance |
| Done | systemd restart | systemd restart |

Running the raw calibrator over SSH without the loader still works; the display stays on bash until services restore.

### Dependencies

- **Surge XT CLI** running (`surge-xt-cli.service`) with OSC in on port **53280**
- **ffmpeg** (capture + `loudnorm` measurement)
- **python-osc**, **python-rtmidi** (gesture MIDI into Surge)
- Default capture is **loopback** (`snd-aloop`, dynamic card-index resolution); `--audio-device` to override. `MPE_CAL_ROUTE=standalone` / `--no-use-loopback` falls back to **`dsnoop:CARD=S3,DEV=0`** Sound Blaster capture (`arecord -L`) if loopback breaks.

Keep Surge alive for the whole batch — one load + gesture + capture per patch.

On the **Pi touch build**, prefer `./scripts/calibrate-with-loader.sh` (see [Pi touch display](#pi-touch-display-loader-ui)) so the DSI panel shows progress instead of a bare console.

### Timing (Quick Select pilot)

Roughly **4–5 seconds per patch** (load, 3 s capture, analysis). Ten favorites ≈ **1 minute**; fifty ≈ **4 minutes**. Use the loader from settings or `calibrate-with-loader.sh` on the Pi; raw SSH calibrate is fine for headless runs.

**Reference Pi (2026-07-30):** Quick Select folder **12/12 calibrated** with loopback capture and −3 dBFS peak cap. One outlier (**Bowed String**, ~8 MB patch) needed a load retry before LUFS measurement succeeded — the calibrator now retries when integrated LUFS is `-inf`.

## Testing on the Pi

1. Ensure Surge is up: `systemctl is-active surge-xt-cli`
2. Dry-run: `python3 scripts/calibrate-patch-normalization.py --dry-run`
3. Calibrate with loader: `./scripts/calibrate-with-loader.sh --force`
4. Copy output to runtime path if you used `--output`:
   `cp config/patch_normalization.json ~/.patch_browser_normalization.json`
5. Restart patch browser; switch patches — loudness should stay closer without re-trimming every time
6. Boot scan log should show: `Patch normalization: N of M patches missing calibration`

Global volume slider remains a performance trim on top of per-patch baseline.

## Module API

`patch_browser/patch_normalization.py`:

- `PatchNormalizationStore` — load/save, `get_gain_db`, `set_calibration`, `list_missing`
- `compute_gain_db(lufs, true_peak)` — mid-target + safe peak cap
- `log_missing_normalization_summary(patch_names)` — scan-complete logging
