# Measurements

Bench results from experiments on the reference Pi. One file per run, named
`<experiment>-<YYYY-MM-DD>.md`.

**Start here:** [`PROGRESS.md`](../../PROGRESS.md) — canonical thread, queue, standing rules.

**Why this exists:** a measurement without its conditions recorded is not
reproducible, and an unreproducible number becomes a claim that outlives what
it described. Record the conditions even when they seem obvious — especially
governor, buffer/periods, power source, and the exact software versions.

**Rule −1:** run `./scripts/instrument-conformance.sh` before any Pi measurement or
shipping claim. See [`MEASUREMENT-DISCIPLINE.md`](MEASUREMENT-DISCIPLINE.md).

## Standard conditions

Unless a run says otherwise, use the fixed conditions Phase 1 was measured
under, so numbers stay comparable:

| Setting | Value |
|---|---|
| **Platform** | **Raspberry Pi 4B / BCM2711 / Cortex-A72 @ 1800 MHz (`arm_boost=1`)** — *state this in every doc; a Pi 5 is incoming, see [`PI5-TRANSITION-PLAN.md`](../PI5-TRANSITION-PLAN.md)* |
| Profile | `standalone` |
| `MPE_JACK_BUFFER` | 256 |
| `MPE_JACK_PERIODS` | 3 |
| Sample rate / depth | 48 kHz, 24-bit |
| CPU governor | `performance` (pin it — `ondemand` makes latency numbers non-baseline) |
| Power | USB-C supply, **not** GPIO jumpers |

Check RT with **`mpe rt status`**, which reads the audio thread. Do **not** use
`mpe sysinfo` — it reads process-level scheduling, which is `SCHED_OTHER` by
design under JACK and reports a false negative on a healthy appliance.

## Live documents (instrument-latency thread)

| doc | what it is |
|---|---|
| [`REVIEW-line-of-thought-2026-08-22.md`](REVIEW-line-of-thought-2026-08-22.md) | Current roadmap argument |
| [`MEASUREMENT-DISCIPLINE.md`](MEASUREMENT-DISCIPLINE.md) | Doctrine — rules 0–7 |
| [`CEILING-ANALYSIS-what-maxed-out-means.md`](CEILING-ANALYSIS-what-maxed-out-means.md) | Assumption stack behind "CPU maxed" |
| [`PATCH-COST-what-makes-them-heavy.md`](PATCH-COST-what-makes-them-heavy.md) | What makes patches expensive |
| [`MULTITHREADING-ASSESSMENT.md`](MULTITHREADING-ASSESSMENT.md) | Multithreading lever assessment |
| [`V1-VERDICT-no-fixed-cost-2026-08-21.md`](V1-VERDICT-no-fixed-cost-2026-08-21.md) | a = 0.13 ms |
| [`W1-VERDICT-compute-bound-2026-08-21.md`](W1-VERDICT-compute-bound-2026-08-21.md) | Graph overrun, not underrun |
| [`v8-patch-capacity-2026-08-21.md`](v8-patch-capacity-2026-08-21.md) + [`V8-REVIEW`](V8-REVIEW-ceiling-is-optimistic.md) | 53-patch survey |
| [`V9-REVIEW-2026-08-22.md`](V9-REVIEW-2026-08-22.md) | 1024×2 free at clean load; confirm floors |
| [`V10-b-ramp-probe-fix-2026-08-22.md`](V10-b-ramp-probe-fix-2026-08-22.md) | Ramp ceilings screening-only |
| [`session-handoff-2026-08-22.md`](session-handoff-2026-08-22.md) | Last session state |

Queued prompts: [`PROMPT-P7`](PROMPT-P7-overclock-diagnostic.md),
[`PROMPT-P8`](PROMPT-P8-mcpu-cortex-a72.md),
[`HANDOVER-census-unison-fix`](HANDOVER-census-unison-fix.md).

Evidence-record tasks (offline, not measurements): [`PROMPT-G1`](PROMPT-G1-effort-reconstruction.md)
effort reconstruction, [`PROMPT-G3`](PROMPT-G3-archive-raw-logs.md) raw-log archival — both
from [`SRED-EVIDENCE-2026.md`](../SRED-EVIDENCE-2026.md).

## Archive

**53 files** moved to [`archive/`](archive/) on 2026-08-22 — refuted lines (600 µs hunt,
cushion model, Scarlett swap), superseded IRQ/core/hygiene work, ladder sweeps, looper-era
measurements, and completed prompts. **Not deleted** — provenance for retractions still
matters. Later docs correct earlier ones; where they disagree, the live table above wins.

If a link elsewhere in the repo points at a top-level path that no longer exists, prepend
`archive/`.

## Timestamps — timezone changed 2026-08-21

The Pi ran **Europe/London (+01:00 BST)** until 2026-08-21, then was set to
**America/Toronto (EDT, -04:00)** to match the laptop. Both are NTP-synced and now agree.

**Consequence for reading logs.** Timestamps in any measurement predating the change are
BST and sit **5 hours ahead** of the laptop's clock. Durations *within* a single pre-change
log are unaffected — only cross-machine and cross-date comparisons are.

## Rig enforcement

- `mpe_meter_assert_live` / `mpe_meter_xruns_read` in `scripts/lib/audio-engine.sh` — 3 s
  freshness; never `|| echo 0`
- `measure-latency-run.sh` — `MPE_PEAK_METER != 1` is fatal; RESULT lines carry
  `meter_live=1` and `meter_max_age_s`
- Instrument audit: [`xrun-counter-audit-2026-08-21.md`](xrun-counter-audit-2026-08-21.md)

### Parsing harness logs

`measure-latency-run.sh` writes `RESULT tag=A-runN xruns=…` lines. When aggregating
from a log, use word boundaries so run 1 does not match run 10:

```bash
grep -oE 'tag=A-run[0-9]+ xruns=[0-9]+' ~/latency-measure.log | sort -u
```

Bare `grep A-run1` also matches `A-run10`…`A-run15` and silently doubles counts.
