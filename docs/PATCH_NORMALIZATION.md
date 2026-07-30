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

### Runtime volume

Surge OSC `/param/a/amp/volume` and `/param/b/amp/volume` use a linear scale (`1.0` = unity). Stored `gain_db` converts via `10^(gain_db/20)`.

On load:

1. Normalization sets `_patch_gain_linear` baseline
2. User volume slider (`set_volume`) is a trim multiplier on top: `combined = trim × baseline`

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

### Dependencies

- **Surge XT CLI** running (`surge-xt-cli.service`) with OSC in on port **53280**
- **ffmpeg** (capture + `loudnorm` measurement)
- **python-osc**, **python-rtmidi** (gesture MIDI into Surge)
- Default ALSA capture device auto-detects Sound Blaster via `arecord -l` (`plughw:1,0` typical on Pi; `--audio-device` to override)

Keep Surge alive for the whole batch — one load + gesture + capture per patch.

### Timing (Quick Select pilot)

Roughly **4–5 seconds per patch** (load, 3 s capture, analysis). Ten favorites ≈ **1 minute**; fifty ≈ **4 minutes**. Full library scales linearly — run from PC/SSH, not as a blocking Pi menu action.

## Testing on the Pi

1. Ensure Surge is up: `systemctl is-active surge-xt-cli`
2. Dry-run: `python3 scripts/calibrate-patch-normalization.py --favorites-only --dry-run`
3. Calibrate: `python3 scripts/calibrate-patch-normalization.py --favorites-only --force`
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
