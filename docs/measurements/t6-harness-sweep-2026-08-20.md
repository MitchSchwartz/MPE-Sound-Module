# T6 — measurement rig sweep (2026-08-20)

Scope: `scripts/measure-*`, `scripts/lib/`, `native/mpe-xrun-probe/`, harness helpers.
Same Class B rules as T2. Fixes in `e4c32fe` (I2) and this commit.

---

## Ranked findings

| Rank | Class | File:line | What | Status |
|---:|---|---|---|---|
| P0 | B | `measure-latency-run.sh` `_meter_xruns` | `\|\| echo 0` on missing meter | **fixed I2** — `mpe_meter_xruns_read` |
| P0 | B | `xrun-corr.sh` `xr()` | same pattern | **fixed T6** |
| P0 | B | `measure-cyclictest-floor.sh` | piped usage text as data | **fixed** (validate before append) |
| P0 | B | cyclictest guard | `pipefail` + `grep -q` SIGPIPE | **fixed** (here-string, not pipe) |
| P1 | B | `measure-latency-run.sh` `_delay_stats_legacy` | `\|\| echo "0 0 0 0 0"` | probe file empty — separate from meter; window fails if jitter_n low |
| P1 | B | `measure-latency-run.sh` `_ensure_peak_meter` | was WARNING | **fixed I2** — fatal |
| P2 | B | `bench-xruns.sh` | journal xrun grep | not harness primary; document only |

---

## Guards and Pi proof

### I2 / meter liveness (`e4c32fe`)

**Pass** — `tests/test_meter_harness.sh` on Pi:

```
OK: fresh meter xruns=5 age=0s
OK: missing meter fails
OK: stale meter fails
OK: missing xruns= fails
```

**Fail** — `MPE_PEAK_METER=0`:

```
ERROR: MPE_PEAK_METER is not 1 — xrun count requires meter.state (/etc/mpe/mpe.env)
exit=1
```

**Pass** — harness self-test with live meter: `SELF-TEST PASS`, RESULT includes `meter_live=1`.

### cyclictest floor

**Fail** — invalid flag (rt-tests 2.6 `-n`): script exits non-zero, nothing appended.

**Pass** — real run: `Min:`/`Max:` line present, `SENTINEL cyclictest-complete`.

---

## T3a note — inter-procedural lint

`periodic_loop_lint.py` now walks **called functions** reachable from periodic loops,
not only literal loop bodies. The session `journalctl` fork (loop →
`collect_jack_graph_health` → `poll`) would be caught by the nested test case.

*Last updated: 2026-08-20 (America/Toronto)*
