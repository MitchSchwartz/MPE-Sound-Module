# Appliance state

Unversioned runtime state captured from the live appliance so a reflash does not lose it. This is **data, not source** — nothing here is built, and nothing here is read by the running system directly. It exists so [`docs/RESTORE.md`](../docs/RESTORE.md) has something to restore *from*.

Captured 2026-08-16 from `raspberrypi2`.

## `calibration/`

| File | Size | What it is |
|---|---|---|
| `surge-cli-calibration.log` | 48 KB | Loopback calibration run log, from 2026-07-30 |
| `patch_browser_calibration_backups/20260803-044307/.patch_browser_normalization.json` | | Patch loudness normalization |
| `patch_browser_calibration_backups/20260803-044307/.patch_browser_pressure.json` | | Touch pressure curve |

**Device-specific.** These describe *this* touchscreen and *this* audio path. A reflash of the same hardware can restore them directly. **A replacement panel or DAC needs recalibration from scratch** — restoring stale values would be worse than starting clean, because the instrument would look calibrated while being wrong.

### Restore

```bash
cp appliance-state/calibration/surge-cli-calibration.log ~/
cp -r appliance-state/calibration/patch_browser_calibration_backups \
      ~/.patch_browser_calibration_backups
```

### Refresh

Run after any recalibration, or this directory silently drifts from the appliance and a restore quietly reverts your tuning:

```bash
./scripts/backup-appliance-state.sh
```

## What is deliberately NOT here

- **Credentials.** The appliance holds none by design — see [`docs/PI-GITHUB-ACCESS.md`](../docs/PI-GITHUB-ACCESS.md). Nothing credential-shaped should ever land in this directory; the repo is public and gitleaks runs pre-commit.
- **`/etc/mpe/mpe.env`.** Two non-secret keys, documented in `RESTORE.md` instead. Note `MPE_SURGE_BUFFER_SIZE` there is dead config and must not be restored.
- **The Surge build** (`~/surge`, 238 MB) and **the patch library** (`MPE-Library`, 279 MB). Too large and separately sourced — see `RESTORE.md`.
