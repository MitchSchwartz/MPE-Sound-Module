# I3 — config diff: baseline A = 0.13 vs now (2026-08-20)

**Purpose:** I3 blocker requires stating what differs between the post-IRQ measurement
that produced **A = 0.13** (n=15) and the appliance **now** (reverted config, fixed harness).

Baseline reference: [`low-latency-512-256-spec.md`](../../Documents/specs/low-latency-512-256-spec.md)
— A dropped **4.20 → 0.13** after `irqaffinity=0,1` + `CPUAffinity=2 3`.

---

## Config — expected same (revert verified)

| item | at 0.13 | now (2026-08-20) |
|---|---|---|
| cmdline `irqaffinity` | `0,1` | `0,1` |
| `CPUAffinity` jackd/surge/looper | `2-3` | `2-3` |
| buffer for A ladder | 512×3 | 512×3 |

---

## Config — may differ (check Pi snapshot)

| item | at 0.13 (typical) | now | touches audio path? |
|---|---|---|---|
| `MPE_PEAK_METER` | likely 1 for meter xruns | snapshot | **read path only** |
| `MPE_JACK_SOFTMODE` | shipping softmode (1)? | harness sets **0** for measure | **yes during run** |
| `MPE_SL_LOOPS` | 16 engine slots | 16 | no for condition A |
| governor | performance | snapshot | yes if not performance |

**Pi snapshot (live):** paste from `scripts/measure-config-snapshot.sh` into §Snapshot below.

---

## Code / behaviour — changed since 0.13 (does not require config diff)

These landed **after** the post-IRQ ladder; none should move IRQ/core pinning, but all
change what the harness **reports**:

| change | commit era | effect on A count |
|---|---|---|
| `_meter_xruns` `\|\| echo 0` | pre-I2 | **under-count** if meter blind → artifact toward 0 |
| I2 `mpe_meter_xruns_read` fail-loud | `e4c32fe` | abort or true count; no silent 0 |
| sl-watchdog: `jack_lsp` → meter.state | E2 `d203089` | less graph churn at D; **A unchanged in theory** |
| xrun counters → meter.state | `c9868aa` | HUD/watchdog only |
| harness strict softmode off during run | measure-latency-run | **may increase counted xruns** vs softmode |

**Testable hypothesis:** if I3@n=15 ≈ **0.80**, baseline was never 0.13 under an honest
meter — revise claim rather than hunt a silent regression.

---

## Snapshot (Pi)

```
(paste measure-config-snapshot.sh output after I3 run)
```

*Last updated: 2026-08-20 (America/Toronto)*
