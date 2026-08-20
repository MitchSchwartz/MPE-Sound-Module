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

Session contribution collapsed; **remaining gap at 512 is sooperlooper** (+2.13, steady,
not bursty). Next suspect: SL processing all eight loops every period regardless of
recording state.

512 exit criterion (D = 0×n) unchanged.

---

## Post-reboot — affinity persistence + D @ n=15 (2026-08-20)

**Reboot test passed:** `irqaffinity=0,1` in cmdline; `CPUAffinity=2-3` on
`mpe-jackd`, `surge-xt-cli`, `mpe-sooperlooper`; live `taskset` on jackd/surge
**2,3**; no `repin-audio` loop.

Log: `~/latency-postreboot-512-D.log`

| run | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| xruns | 5 | 9 | 3 | 7 | 5 | 7 | 1 | 3 | 1 | 14 | 3 | 5 | 6 | 3 | 9 |

**Mean 5.4** · sd ~3.5 · **0/15 clean** · max 14.

Higher than the ~2.6–2.9 estimate (B + session + watchdog); run 10 (14 xruns) is the
main outlier. Still ~half pre-fix ladder D (10.0). **512 not shippable** — remaining
stack cost is sooperlooper-dominated (+2.13 A→B, steady).

*Last updated: 2026-08-20 (America/Toronto)*

*Last updated: 2026-08-20 (America/Toronto)*
