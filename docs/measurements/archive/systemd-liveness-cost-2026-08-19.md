# systemd liveness cost — D-Bus vs fork (work order task 4)

**Question.** `build_snapshot()` was 57.3 ms, of which 55.9 ms was three `systemctl`
forks. Criterion 7 wants the whole snapshot under 1% of a core at 2 Hz. Can systemd's
`ActiveState` be read without spawning a process?

**Answer: yes, and it is ~29× cheaper than forking per unit.** But the second half of the
question — `UnitFileState`, the `enabled` flag — is expensive over D-Bus *too*, and that
turns out to be the finding that matters.

## Method

`raspberrypi2`, systemd 257, python3-dbus 1.4.0 (already installed — no new dependency).
11 units, the `STATUS_SERVICE_UNITS` set. Median of 3–10 runs, warm connection.

## Results — active state (the runtime fact)

| approach | 11 units | per unit | at 2 Hz |
|---|---:|---:|---:|
| `systemctl is-active` × 11 forks | 201.93 ms | 18.4 ms | 40% of a core |
| `systemctl is-active u1 … u11` (one fork) | 46.45 ms | 4.2 ms | 9.3% |
| **`ListUnitsByNames` over D-Bus** | **6.98 ms** | **0.63 ms** | **1.4%** |
| `ListUnitsByNames`, 1 unit | 0.44 ms | 0.44 ms | — |

The 1-unit figure shows ~0.4 ms of fixed round-trip and ~0.6 ms per unit after that.

## Results — enabled state (the configuration fact)

| approach | 11 units | at 2 Hz |
|---|---:|---:|
| `ListUnitFilesByPatterns` over D-Bus | 31.43 ms | 6.3% |
| `GetUnitFileState` × 11 over D-Bus | 42.71 ms | 8.5% |
| `systemctl is-enabled` × 11 forks | ~220 ms | 44% |

D-Bus barely helps here, because the cost is not IPC — systemd reads unit files off disk
and walks the enablement symlink tree. Nothing makes that cheap.

## What this decides

**`enabled` must not be sampled at publish rate.** It is not a runtime signal. It changes
only when someone runs `systemctl enable/disable` or a deploy runs `install-units.sh` —
events measured in weeks, not milliseconds. Sampling a configuration fact at 2 Hz was the
whole mistake; the fix is not a faster probe, it is a correct cadence.

Both probes are batched, and both are served from one cache that the per-unit entry points
also consult — `systemd_unit_active()` and `systemd_unit_enabled_raw()` read the batch when
the unit is one of the eleven, rather than reimplementing it.

## Implemented result, measured on the appliance

| | probe cost (11 units) | TTL | contribution at 2 Hz |
|---|---:|---:|---:|
| `active` — `ListUnitsByNames` | 7.60 ms | 5 s | 1.52 ms/s |
| `enabled` — `ListUnitFilesByPatterns` | 32.36 ms | 30 s | 1.08 ms/s |
| everything else in the build | 1.35 ms | per build | 2.70 ms/s |
| **total** | | | **5.29 ms/s = 0.53% of a core** |

At 1 Hz it is 0.39%. **Criterion 7 passes**, against 11.5% before.

| | before | after |
|---|---:|---:|
| cold build (both caches empty) | 424.50 ms | **42.47 ms** |
| warm build | 57.30 ms | **1.35 ms** |

### The bug that hid inside the fix

The first cut batched both probes and the cold build did not move — still 424 ms. The
reason was that `build_snapshot()` constructed the per-unit default callables and injected
them into `build_services()`, so the batch was built and then bypassed, 22 forks deep.
Profiling caught it; inspection would not have. `build_snapshot()` now forwards only the
caller's own injections, and the defaults route through the batch.

Worth stating plainly: the batching was written, tested green, and measurably did nothing
until it was measured end to end. The unit tests asserted the batch was *called*, which was
true, and irrelevant.

## The condition attached to caching

The work order is explicit that a cached judgement is the last-known-good problem wearing
a different hat, and that a TTL is only acceptable if the snapshot carries the age of what
it cached. Implemented — every service entry now carries both the age and the transport:

```json
"mpe-jackd": {
  "active": "active", "enabled": "enabled", "stale": false,
  "active_source": "dbus", "enabled_source": "dbus",
  "active_age_s": 0.072, "enabled_age_s": 0.067
}
```

A reader can tell a fresh `enabled` from a 29-second-old one, which is the difference
between a reading and a memory. `*_source` is recorded for the same reason: if the fallback
is ever in use, the 6x cost difference is visible in the snapshot rather than inferred.

## Fallback

D-Bus absent or refusing (no `python3-dbus`, systemd not on the bus) falls back to a
**single batched** fork — `systemctl is-active u1 … u11` at 46 ms, still 4.3x better than
the per-unit forking it replaces. A short answer (fewer lines than units) is treated as
unknown for every unit rather than mapped positionally onto a guess.

## Recommendation for the publisher

Run at **1 Hz**, not 2. Nothing in the snapshot changes faster than a second except
`loop_pos`, which the HUD already owns on its own path. 1 Hz halves the dominant term
(the per-build work) for no loss of fidelity.
