# SooperLooper eval scripts

**Branch:** `docs/sooperlooper-eval` · Pi binary: `~/src/sooperlooper-1.7.9/src/sooperlooper`

## APC 16-loop clip grid (target layout)

| Row | APC notes | Loops | Role |
|---|---|---|---|
| **0** (bottom) | 0–7 | 0–7 | Clip pads |
| **3** | 24–31 | 8–15 | Clip pads |
| 1, 2, 4–7 | — | — | Per-loop controllers (future) |

Mapping: `apc_grid.py` · 16-pad footswitch: `../sooperlooper-apc-bench.py` (rows 0 + 3)

**Grid sync (default):** loop 0 sets master length; loops 1–15 quantize to cycle multiples (`sl_grid_sync.py` / `configure-grid-sync.sh`). Free-form: `MPE_SL_SYNC_MODE=freeform`.

**Transport (Shift + Stop All Clips):** quick release = stop all (pause, keep audio); hold **3 s** = clear all. Per-pad hold **2 s** = clear that loop.

**Touch HUD:** `start-sooperlooper-hud-monitor.sh` writes `~/.mpe_sl_hud_state.json` (beat **1/4** from master loop). Header badge shows when playing.

## Test clips + smoke (no manual recording)

```bash
mpe looper sl-clips          # on Pi (default)
mpe looper sl-clips local    # laptop clone → tests/fixtures/sooperlooper-loops/
mpe looper sl-smoke          # restart -l 16, load, trigger, VmRSS + jack_cpu_load
mpe looper sl-diagnose       # 45s soak: fan-in, xrun/journal, peak (needs jack-capture)
```

Or directly on the appliance:

```bash
bash scripts/sooperlooper/generate-test-clips.sh
bash scripts/sooperlooper/smoke-16-loops.sh
```

Restarts SooperLooper with `-l 16`, loads fixture WAVs, triggers all loops, prints VmRSS + `jack_cpu_load`.
