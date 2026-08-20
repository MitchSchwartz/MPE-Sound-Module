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

~40% mean drop; burst tail cut roughly in half. Session layer still adds xruns vs A (~0.27);
512 exit criterion (D = 0×n) unchanged.

*Last updated: 2026-08-20 (America/Toronto)*
