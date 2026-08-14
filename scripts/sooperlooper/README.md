# SooperLooper eval scripts

**Branch:** `yolo/looper-transport-clock` · Pi binary: `~/src/sooperlooper-1.7.9/src/sooperlooper`

## Clock

**JACK transport** is the grid (`sync_source = -1`). Start the timebase master before SL:

```bash
bash scripts/start-jack-timebase.sh          # foreground
# or: python3 scripts/sooperlooper/jack_timebase.py --bpm 120
```

OSC `/bpm` on port **9960** (env `MPE_JACK_TIMEBASE_OSC_PORT`). Task 0 gate: `python3 scripts/sooperlooper/spike-jack-transport.py`.

## APC 16-loop clip grid (target layout)

| Row | APC notes | Loops | Role |
|---|---|---|---|
| **0** (bottom) | 0–7 | 0–7 | Clip pads |
| **3** | 24–31 | 8–15 | Clip pads |
| 1, 2, 4–7 | — | — | Per-loop controllers (future) |

Mapping: `apc_grid.py` · 16-pad footswitch: `../sooperlooper-apc-bench.py` (rows 0 + 3)

**Grid sync (default):** all loops `sync` + `quantize=cycle` to JACK transport; `fade_samples` set globally. Applied **once at bench startup**. Free-form: `MPE_SL_SYNC_MODE=freeform`.

**Transport (Shift + Stop All Clips):** quick release = stop all; hold **3 s** = clear all. Verify note numbers: `sooperlooper-apc-bench.py --dump-midi`.

**Touch HUD:** `start-sooperlooper-hud-monitor.sh` → bar/beat from JACK transport (`~/.mpe_sl_hud_state.json`), including with **no clips recorded**.

**APC bench:** `start-sooperlooper-apc-bench.sh` — OSC state listener on port **9953** (all loops incl. 0).

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
