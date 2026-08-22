# E1 (T1) — three cores instead of two

**Pi:** `raspberrypi2` · **2026-08-20** · Pi commit `c9868aa` · **experiment**, not measured ship claim

## Experiment design flaw (recorded explicitly)

E1 changed **two variables at once**: `irqaffinity=0` (was `0,1`) **and** `CPUAffinity=1 2 3`
(was `2 3`). That violates one-variable-per-comparison. This run **refutes the E1
configuration**, not the crowding hypothesis in isolation. A clean crowding test would
have changed only `CPUAffinity` while leaving `irqaffinity=0,1` — same 68 minutes, one
reboot, one answer.

Do not read "crowding hypothesis falsified" from this data. Worth keeping distinct if
anyone revisits on hardware with more cores.

## Pre-flight (measured)

```
irqaffinity=0   # /proc/cmdline
CPUAffinity=1 2 3   # mpe-jackd, surge-xt-cli, mpe-sooperlooper

 30:      47691          0          0          0  xhci_hcd
 41:      76109          0          0          0  mmc1, mmc0
 44:          0          0          0          0  feb00000.codec

jackd taskset 1-3 · surge-xt-cli taskset 1-3 · sooperlooper taskset 1-3 (during B/D)
```

Cores 1–3 take **zero** interrupts on IRQ 30/41/44 — but core 0 now takes **all** of them.

## Measurement — A, B, D @ 512×3, n=15

Logs: `~/latency-e1-512-{A,B,D}.log` · chain `~/latency-e1-T1-chain.log`

### A — synth only

| run | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| xruns | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 4 | 0 | 2 | 0 | 2 | 0 | 0 | 2 |

**Mean 0.80** · sd 1.26 · **10/15 clean** · max 4

### B — + sooperlooper (minimal three-process condition)

| run | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| xruns | 21 | 4 | 6 | 3 | 8 | 6 | 7 | 8 | 9 | 1 | 7 | 4 | 6 | **0** | 3 |

**Mean 6.20** · sd 4.86 · **1/15 clean** · max 21

### D — full stack

| run | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| xruns | 8 | 2 | 7 | 2 | 6 | 8 | 12 | 3 | 12 | 5 | 9 | 10 | 4 | 5 | 4 |

**Mean 6.47** · sd 3.31 · **0/15 clean** · max 12

## vs two-core baseline (measured, pre-E1)

| condition | two-core (measured) | E1 config (this run) | ratio |
|---|---:|---:|---:|
| A | 0.13 | 0.80 | **6.2×** |
| B | 2.27 | 6.20 | 2.7× |
| D | 3.13 | 6.47 | 2.1× |

## What the data actually supports

**Solid finding:** condition A degraded **6.2×** with **no sooperlooper involved**. That
isolates the `irqaffinity=0` change as actively harmful. Mechanism: every interrupt
crammed onto core 0, where the unmovable xhci handler lives, on hardware where xruns
originate below JACK in the USB path. We delayed the thing that was actually failing.

**Configuration verdict:** E1 refuted — revert to `irqaffinity=0,1` + `CPUAffinity=2 3`
(branch `revert/e1`, reboot verified 2026-08-20).

**Crowding question (practically closed):** four cores cannot give audio three
interrupt-free ones; that was the only alternative split. **2 IRQ cores + 2 audio cores**
is the best available arrangement on this Pi. Negative result, but it retires a whole
branch of tuning.

**Not supported from this run:** whether sooperlooper's +2.13 step is crowding vs
structural — B also worsened, but A worsened first, confounding any B-only read.

## Revert check (measured, 2026-08-20)

Post-revert config confirmed: `irqaffinity=0,1` · `CPUAffinity=2 3` · jackd/surge taskset `2,3`.

5-run condition A @ 512×3 — log `~/latency-revert-check-A.log`:

| run | 1 | 2 | 3 | 4 | 5 |
|---:|---:|---:|---:|---:|---:|
| xruns | 2 | 0 | 0 | 0 | 0 |

**Mean 0.40** · 4/5 clean. Consistent with pre-E1 baseline (0.13, n=15) — not identical,
but nowhere near E1's 0.80. Revert successful.

*Last updated: 2026-08-20 (America/Toronto)*
