# SooperLooper eval scripts

**Branch:** `docs/sooperlooper-eval` · Pi binary: `~/src/sooperlooper-1.7.9/src/sooperlooper`

## APC 16-loop clip grid (target layout)

| Row | APC notes | Loops | Role |
|---|---|---|---|
| **0** (bottom) | 0–7 | 0–7 | Clip pads |
| **3** | 24–31 | 8–15 | Clip pads |
| 1, 2, 4–7 | — | — | Per-loop controllers (future) |

Mapping: `apc_grid.py` · 16-pad footswitch: `../sooperlooper-apc-bench.py` (rows 0 + 3)

**Grid sync (default):** loop 0 sets master length (free-form); loops 1–15 use `sync` + `quantize=cycle`. On loop 0 clear, grid reference is **saved** (`~/.mpe_sl_master_clock.json`) and sync falls back to **internal tempo** — slaves keep quantizing without loop 0 alive. Full reset (Shift+Stop All 3s) clears the saved clock.

**Transport (Shift + Stop All Clips):** quick release = stop all (pause, keep audio); hold **3 s** = clear all. Per-pad hold **2 s** = clear that loop.

**Touch HUD:** `scripts/start-sooperlooper-hud-monitor.sh` → `sooperlooper/sl-hud-monitor.py` writes `~/.mpe_sl_hud_state.json` (beat **1/4** when loop 0 is playing). Header badge left of **Analog**.

**APC bench:** `scripts/start-sooperlooper-apc-bench.sh` — OSC state listener on port **9953** keeps pad LEDs in sync during quantize wait.

**Workflow:** Record **loop 0** first (sets grid + enables HUD). Then loops 1–15 quantize to bar boundaries. Slaves blocked until loop 0 exists.

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
