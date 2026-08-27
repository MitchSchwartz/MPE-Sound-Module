# SooperLooper 1.7.9 loop ceiling — 15 usable, indices 0–14

**Date:** 2026-08-27 · **Platform:** Pi 5, arm64 SooperLooper 1.7.9, JACK 128×2 @ 48 kHz
**Status:** Resolved. Hard platform constraint. SR&ED U12.

## Question

Does the engine supply the loop count it reports? The appliance had run a 16-track
configuration for weeks and one track behaved unlike the rest.

## Symptom

The top track recorded, but its stop did not obey the bar grid. Every other track did.
Reading the engine showed why — and showed that nothing had reported a fault:

```
loop  state       sync  quantize
0     Playing     1.0   1.0
...
14    Playing     1.0   1.0
15    Off         0.0   0.0     <- never configured
```

`apply_grid_sync` had sent those settings to loop 15 like every other loop. The engine
discarded them silently.

## Hypotheses refuted before the answer

| hypothesis | how it was refuted |
|---|---|
| Off-by-one in the grid-sync bound | Source is `range(num_loops)`; messages were emitted for every index |
| OSC datagram loss in the 96-message burst | Re-sent alone, 20 ms apart — still ignored |
| Memory exhaustion (16 × 40 s ≈ 245 MB) | 2.6 GB free; reproduces at `-t 10` |
| Engine reserves its last index | `-l 4` gives four fully usable loops |
| Control-layer state desync | Reproduces on a second engine with no control layer attached |

## Method

Isolated second engine on port 9971 and JACK client `sl-probe`, so the running instrument
was never touched. For each index: write a control, read it back, compare.

Writing rather than reading is the whole point — every index answers reads.

## Result

| launch | indices that ignore writes |
|---|---|
| `-l 8 -t 40` | none |
| `-l 16 -t 10` | 15 |
| `-l 16 -t 40` | 15 |
| `-l 17 -t 40` | 15, 16 |
| `-l 20 -t 40` | 15, 16, 17, 18, 19 |

**Fifteen usable loops, indices 0–14.** Independent of `-l` and of `-t`. Asking for more
produces more phantoms, not more loops.

## Why it stayed invisible

A phantom index is worse than a missing one. Index 16 under `-l 16` times out — honest.
Index 15 answers `get` with plausible defaults and discards every `set`. So:

- `/ping` reports the count requested, not the count delivered
- every read-based health check passes
- configuration vanishes with no error

`check_command_path` proved the engine's non-realtime queue was draining — using **one**
loop. That is a different question, and it is why this survived.

## Knowledge decay

The ceiling had been found once before. The only record was a string in a shell log line:

    16 loops, scratch 14 — loop 15 empty on Pi

The workaround (reserve the top index as "scratch") outlived its explanation. When the
feature that used the scratch buffer was deleted, the line read as stale copy about a
removed feature and was deleted with it — releasing the workaround and handing the phantom
to the player as a track.

**The measurement now lives beside the constant it justifies** (`sl_limits.py`), and
`resolve_num_loops()` clamps a request rather than passing an oversized count to `-l`.

## Instruments added

- `check_loops_writable` — writes to every index the engine claims and names the phantoms.
  `sl-health` fails on mismatch and states the remedy.
- `tests/test_no_undefined_names.py` — added the same day, and immediately caught a missing
  import in the fix for *this* finding that a 1,213-test suite passed over, because no test
  exercises a `main()` entry point. That defect had already shipped one deploy with grid
  sync dead.

## Bearing on the design

The instrument is a **15-track** machine. The multi-clip spec's "16 contiguous tracks" is
wrong and reverts to 15. `MAX_VIEW_OFFSET` is 7: fifteen tracks through an eight-wide
viewport.
