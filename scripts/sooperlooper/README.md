# SooperLooper eval scripts

**Branch:** `docs/sooperlooper-eval` · Pi binary: `~/src/sooperlooper-1.7.9/src/sooperlooper`

## APC 16-loop clip grid (target layout)

| Row | APC notes | Loops | Role |
|---|---|---|---|
| **0** (bottom) | 0–7 | 0–7 | Clip pads |
| **3** | 24–31 | 8–15 | Clip pads |
| 1, 2, 4–7 | — | — | Per-loop controllers (future) |

Mapping: `apc_grid.py` · single-pad footswitch bench: `../sooperlooper-apc-bench.py`

## Test clips + smoke (no manual recording)

```bash
bash scripts/sooperlooper/generate-test-clips.sh
bash scripts/sooperlooper/smoke-16-loops.sh
```

Restarts SooperLooper with `-l 16`, loads fixture WAVs, triggers all loops, prints VmRSS + `jack_cpu_load`.
