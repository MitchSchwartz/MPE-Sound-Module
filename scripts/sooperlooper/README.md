# SooperLooper eval scripts

**Branch:** `yolo/looper-transport-clock` · Pi binary: `~/src/sooperlooper-1.7.9/src/sooperlooper`

## Clock (gate order — see DECISIONS.md 2026-08-14)

1. **Internal sync phase (try first, no new process):**
   `python3 scripts/sooperlooper/spike-internal-sync-phase.py` → ear test 0.3
2. **If that fails — JACK transport spike only (not for ship):**
   `bash scripts/start-jack-timebase.sh` then `spike-jack-transport.py`
3. **Production clock:** compiled JACK timebase client (TBD after gate)

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
