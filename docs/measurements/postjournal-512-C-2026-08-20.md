# Post-journal-fix — condition C @ 512×3

**Pi:** `raspberrypi2` · **2026-08-20** · commit `221cd39`

## Fix

`JournalXrunCounter` now holds one `journalctl -f` process; `poll()` reads an in-memory
total (no fork per 0.5 s HUD tick). Verified on Pi: single `journalctl -f -u mpe-jackd.service`.

## Re-measure — C only, n=15

Log: `~/latency-postjournal-512-C.log`

| run | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| xruns | 1 | 3 | 1 | 3 | 3 | 4 | **0** | 4 | 2 | 3 | 3 | 6 | 3 | 1 | 1 |

**Mean 2.53** · **1/15 clean** · max 6 (was 13 on post-IRQ ladder C).

### vs post-IRQ ladder C (same machine, n=15)

| | mean | max | clean |
|---|---:|---:|---:|
| post-IRQ ladder | 4.20 | 13 | 0/15 |
| post-journal fix | **2.53** | **6** | 1/15 |

~40% mean drop; burst tail cut roughly in half — **prediction confirmed** (C sd
3.47→1.55, max 13→6, matching B's max of 6). The periodic `journalctl` fork was the
burst source.

### Correct decomposition (B→C, not C vs A)

| step | before fix | after fix |
|---|---:|---:|
| sooperlooper (A→B) | +2.13 (t≈4.7) | +2.13 — unchanged |
| session (B→C) | +1.93 (t≈2.0) | **+0.27 (t≈0.45)** — not distinguishable from zero |

Session contribution collapsed. **Do not merge on D yet** — the post-reboot D number
(5.4) does not reconcile with C (2.53): implied watchdog step is +2.87, not the ladder's
+0.33. Burst signature returned (sd 1.55→3.5, max 6→14) — same shape as the session fork.

**Finding:** `sl-watchdog.py` forks ~4 subprocesses per 10 s cycle, including `jack_lsp -c`
(two graph reorders per call). ~6 cycles per 60 s run. Same doctrine violation as session
`journalctl` and the old `jack_lsp` probe (35/min). Ladder called watchdog "negligible"
while session noise drowned it — see spec § ladder warning.

**Remaining after E2 (watchdog fix):** sooperlooper +2.13 (A→B, zero loops recorded — not
per-loop work). E1 (three cores) separates crowding vs serial-chain cost. Hold off on
eight-loop hypothesis until E2 clears the watchdog layer.

512 exit criterion (D = 0×n) unchanged. **Merge `feat/audio-core-affinity` after E2** so D
is not quoted while measuring a known bug.

---

## Post-E2 — watchdog meter probe + D @ n=15 (2026-08-20)

**Fix (commit `d203089`):** `sl-watchdog.py` reads `looper_client=` / `looper_playback=`
from `/run/mpe/meter.state` (5 Hz, no fork). `mpe-peak-meter` publishes those fields.
`jack_lsp` is fallback only when the meter is off or stale. `engine_running()` scans
`/proc/*/comm` instead of `pgrep`.

Log: `~/latency-measure.log` (harness) · per-run probes `/tmp/latency-D-run*-178725*.xruns`

| run | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| xruns | 3 | **0** | 3 | 2 | 6 | 6 | **0** | 1 | 4 | 1 | 7 | **0** | 5 | 6 | 3 |

**Mean 3.13** · sd ~2.4 · **3/15 clean** · max 7.

### vs post-reboot D (watchdog fork loop, same affinity)

| | mean | max | clean |
|---|---:|---:|---:|
| post-reboot (pre-E2) | 5.4 | 14 | 0/15 |
| post-E2 watchdog fix | **3.13** | **7** | 3/15 |

Watchdog layer cost drops from implied **+2.87** (5.4 − 2.53) to **+0.60** (3.13 − 2.53).
Burst tail largely gone (max 14→7). **E2 merge gate cleared** — D is no longer measuring a
known periodic fork in the watchdog.

512×3 exit criterion (0 xruns × 5×60 s in D) still not met; next steps per spec: **E1**
(three cores), **E3** (loop-count curve).

*Last updated: 2026-08-20 (America/Toronto)*
