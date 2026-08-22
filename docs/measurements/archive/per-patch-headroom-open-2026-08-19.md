# Open: some patches still need 1024 after the crackle fix

**Status:** open. Cause not yet measured. Do not act on it before the measurement in
"Next step" below — three different failures look the same from the player's seat and
need three different fixes.

## What we know

After the 2026-08-18 graph-probe fix (`0f9875c`), the appliance runs **0 xruns/min at
`512 x 3`** under a deterministic load at DSP median 42%
([root cause](crackle-root-cause-2026-08-18.md)).

**Mitch, 2026-08-19, having re-tried them post-fix:** *"512 works for most, some patches
seem to need 1024 still."*

That retry matters. It rules out the first of the two readings we recorded in
[`next-work-order-2026-08-19.md`](../Documents/specs/next-work-order-2026-08-19.md) §7:
this is **not** pre-fix residue. It is genuine per-patch variance, and it is bounded —
specific presets, not the appliance.

**Not yet known:** which patches, and what actually fails on them.

## Why "tune the governor" is not obviously the answer

`SurgePolyGovernor` (`patch_browser/surge_poly_governor.py`, run by
`surge-poly-governor.service`) polls at 150 ms and steps the Surge voice cap down on CPU
thresholds — warm 48%, high 50%, spike 78%, emergency 90% — stepping back up after 5 s
below 40%.

**1. It watches a proxy, not the deadline.** Its input is `SurgeCpuMonitor`, whose own
docstring says: *"Upstream Surge does not expose that value via OSC… we fall back to
`/proc` CPU time for the `surge-xt-cli` process (good Pi diagnostic proxy)."* Process CPU
is not deadline pressure. Two patches at the same 60% process CPU can behave completely
differently depending on how that work distributes across periods.

When the governor was written, DSP load and xrun counts were not cheaply available. Since
2026-08-18 they are: `jack_cpu_load`, and `xruns=` in `/run/mpe/meter.state` published by
the compiled meter — which works under shipping softmode, where `journalctl` reports
nothing.

**2. It cannot prevent an xrun.** 150 ms poll plus a 150 ms hold is ~14 periods at
`512 x 3`. It suppresses a *sustained* overload; it does nothing for the transient a
player actually hears.

**3. Lowering thresholds trades one artefact for another.** Cutting polyphony sooner
means voice stealing — notes vanishing mid-phrase. For a player that is usually worse
than latency. Tuning the numbers harder buys 512 with dropped notes.

## Next step — measure before choosing a lever

Name a patch that needs 1024, load it at `512 x 3`, hold a full chord with heavy MPE
gesture, and run:

```sh
scripts/xrun-corr.sh 60      # xruns and DSP, 1 Hz, side by side
```

Three outcomes, three different fixes:

| Observation | Meaning | Lever |
|---|---|---|
| DSP approaching/exceeding 100% | genuine compute overload | governor, or the patch is too heavy |
| DSP comfortable, xruns present | something interrupting the graph — the 2026-08-18 shape | find the interrupter; buffer will not fix it |
| DSP high, no xruns, still sounds wrong | not a deadline problem | voice stealing, or the patch itself |

Record the patch names. Per-patch DSP is worth having as data regardless.

## Candidate levers, ranked (do not start before measuring)

1. **Point the governor at the real signal.** Drive it from DSP load and the `xruns=`
   delta instead of process CPU. Same daemon, better input, no new polling cost — both
   values are already published, so this respects
   [`DECISIONS.md`](../Documents/DECISIONS.md) 2026-08-18 rule 9 (a probe has two costs;
   prefer an observer already on the graph).
2. **Per-patch voice ceiling.** `MPE_SURGE_MAX_VOICES` is already applied on each patch
   load. A heavy preset carrying its own cap is predictable and local, and beats a global
   reaction after the fact.
3. **Global `1024 x 3`.** Last resort: it penalises every patch for the worst one and
   costs 21.3 ms of latency everywhere, against 10.7 ms at 512.

## Why this matters beyond these patches

Criterion 35 of the session-control spec wants `128 x 3` (2.7 ms) under playing load with
zero xruns. Per-patch variance is the thing standing between the current 512 and that
target, so understanding it is on the path to the latency Mitch originally asked for —
not a side quest.
