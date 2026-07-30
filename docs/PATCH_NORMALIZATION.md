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
| `MPE_NORMALIZATION_FILE` | Env override for either script or runtime |

`enabled: false` skips normalization for that patch (falls back to global trim only).

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
3. When **Norm.** is on, combined OSC amp/volume is capped at **0.85** linear (≈ −1.4 dB) to preserve CPU/buffer headroom under heavy MPE polyphony on the Pi. Norm off uses the touch UI ceiling (**1.5**). User trim stacks below the cap.

### Polyphony and static/crackle (Pi)

Static or crackle under **many held keys** is usually **ALSA buffer underrun (xrun)**, not clip — especially on the Pi + Sound Blaster path.

| Factor | Effect |
|--------|--------|
| **Norm ON** | Quiet patches get large `gain_db` boosts; runtime cap is **0.85** (not 1.5) so Surge runs cooler under poly. Fewer voices before xrun. |
| **Norm OFF** | Unity baseline; user trim up to **1.5** — more headroom for solo, less for dense chords. |
| **ALSA buffer** | `surge-xt-cli` starts with **`MPE_SURGE_BUFFER_SIZE`** (default **1024** samples @ 44.1 kHz ≈ 23 ms). Was 512 (~12 ms) and xran under load. |
| **snd-aloop** | Loaded only during calibration loopback. Unloaded on Surge start and after `calibrate-with-loader.sh` if refcount is 0. |

If crackle persists with Norm off and moderate polyphony, try `MPE_SURGE_BUFFER_SIZE=2048` in `/etc/mpe/mpe.env` and restart `surge-xt-cli`. Tradeoff: higher latency.

**Live diagnosis:** the touch browser header **CPU** meter (see [`TOUCH_PATCH_BROWSER.md`](TOUCH_PATCH_BROWSER.md)) tracks `surge-xt-cli` process load while you play — use it to see when dense polyphony is pushing the Pi toward xrun territory. Norm on should keep typical Quick Select patches lower on that meter than uncapped gain would.

**Quick Select reference (2026-07-30):** calibrated `gain_db` spans about +4 to +18 dB. Without a runtime cap that would map to **~1.6–8.0** linear OSC — too hot for dense MPE on the Pi. With Norm on, combined amp/volume is capped at **0.85** linear.

## Calibration script

```bash
# List Quick Select patches that need entries (~estimate only)
python3 scripts/calibrate-patch-normalization.py --favorites-only --dry-run

# Calibrate favorites (Surge CLI running, OSC 53280, ffmpeg + rtmidi installed)
python3 scripts/calibrate-patch-normalization.py --favorites-only

# Specific folder under user patches
python3 scripts/calibrate-patch-normalization.py --folder "Quick Select"

# Custom output path
python3 scripts/calibrate-patch-normalization.py --favorites-only --output ~/.patch_browser_normalization.json

# Test write path without Surge/ffmpeg
python3 scripts/calibrate-patch-normalization.py --favorites-only --mock-lufs -20 --limit 1
```

### Pi touch display (loader UI)

When calibration runs on the Pi touch build, the patch browser stops and tty1 would otherwise show a Linux console. Use the loader wrapper so the DSI panel shows progress instead:

```bash
# SSH or local shell on the Pi — fullscreen progress on the 800×480 display
./scripts/calibrate-with-loader.sh --favorites-only

# Re-calibrate all Quick Select entries
./scripts/calibrate-with-loader.sh --favorites-only --force
```

**From the touch UI:** System settings (⋯) → **Calibrate Quick Select** → confirm. The browser exits, the loader takes over kmsdrm, then `surge-xt-cli` and `touch-patch-browser` restart when finished.

The loader shows patch name, `N / M` progress, elapsed time, and *Do not touch — Surge is measuring loudness*. Implementation: `patch_browser/calibration_loader.py` subprocesses `calibrate-patch-normalization.py --progress-json`.

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
- Default ALSA capture device auto-detects Sound Blaster via `arecord -l` (`plughw:1,0` typical on Pi; `--audio-device` to override)

Keep Surge alive for the whole batch — one load + gesture + capture per patch.

On the **Pi touch build**, prefer `./scripts/calibrate-with-loader.sh` (see [Pi touch display](#pi-touch-display-loader-ui)) so the DSI panel shows progress instead of a bare console.

### Timing (Quick Select pilot)

Roughly **4–5 seconds per patch** (load, 3 s capture, analysis). Ten favorites ≈ **1 minute**; fifty ≈ **4 minutes**. Use the loader from settings or `calibrate-with-loader.sh` on the Pi; raw SSH calibrate is fine for headless runs.

**Reference Pi (2026-07-30):** Quick Select folder **12/12 calibrated** with loopback capture and −3 dBFS peak cap. One outlier (**Bowed String**, ~8 MB patch) needed a load retry before LUFS measurement succeeded — the calibrator now retries when integrated LUFS is `-inf`.

## Testing on the Pi

1. Ensure Surge is up: `systemctl is-active surge-xt-cli`
2. Dry-run: `python3 scripts/calibrate-patch-normalization.py --favorites-only --dry-run`
3. Calibrate with loader: `./scripts/calibrate-with-loader.sh --favorites-only --force`
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
