# Measurements

Bench results from experiments on the reference Pi. One file per run, named
`<experiment>-<YYYY-MM-DD>.md`.

**Why this exists:** a measurement without its conditions recorded is not
reproducible, and an unreproducible number becomes a claim that outlives what
it described. Record the conditions even when they seem obvious — especially
governor, buffer/periods, power source, and the exact software versions.

## Standard conditions

Unless a run says otherwise, use the fixed conditions Phase 1 was measured
under, so numbers stay comparable:

| Setting | Value |
|---|---|
| Profile | `standalone` |
| `MPE_JACK_BUFFER` | 256 |
| `MPE_JACK_PERIODS` | 3 |
| Sample rate / depth | 48 kHz, 24-bit |
| CPU governor | `performance` (pin it — `ondemand` makes latency numbers non-baseline) |
| Power | USB-C supply, **not** GPIO jumpers |

Check RT with **`mpe rt status`**, which reads the audio thread. Do **not** use
`mpe sysinfo` — it reads process-level scheduling, which is `SCHED_OTHER` by
design under JACK and reports a false negative on a healthy appliance.

## Current templates

- [`TEMPLATE-sooperlooper-eval.md`](TEMPLATE-sooperlooper-eval.md) — Session A/B
  of the SooperLooper adoption test. Plan lives in OM-Repo
  `internal/projects/mpe-synth-launch/research/looper-vetting.md` §7.

## Timestamps — timezone changed 2026-08-21

The Pi ran **Europe/London (+01:00 BST)** until 2026-08-21, then was set to
**America/Toronto (EDT, -04:00)** to match the laptop. Both are NTP-synced and now agree.

**Consequence for reading logs.** Timestamps in any measurement predating the change are
BST and sit **5 hours ahead** of the laptop's clock. The T5 soak is the one that bites:
its `expected_finish=2026-08-21T12:16:48+01:00` is **07:16 Toronto**, not 12:16 — the run
had already been finished for two hours when it looked overdue.

Read the offset in the timestamp; do not assume the Pi and the laptop shared a clock
before 2026-08-21. Durations *within* a single pre-change log are unaffected — only
cross-machine and cross-date comparisons are.

## Low-latency arc (2026-08-18 -> 2026-08-21)

Read in order. Later docs correct earlier ones; where they disagree, the later one wins.

| doc | what it settled |
|---|---|
| [`crackle-root-cause-2026-08-18.md`](crackle-root-cause-2026-08-18.md) | USB / card-dry failure mode |
| [`looper-stack-cost-2026-08-19.md`](looper-stack-cost-2026-08-19.md) | DSP headroom per loop |
| [`low-latency-step0-step1-2026-08-19.md`](low-latency-step0-step1-2026-08-19.md) | GICv2 interrupt pile-up on CPU0; `CPUAffinity=2 3` |
| [`e1-three-cores-T1-2026-08-20.md`](e1-three-cores-T1-2026-08-20.md) | E1 refuted -- two variables changed at once; reverted |
| [`i3-config-diff-2026-08-20.md`](i3-config-diff-2026-08-20.md) | what E1 actually changed |
| [`i3-n15-e1-revert-2026-08-20.md`](i3-n15-e1-revert-2026-08-20.md) | baseline 0.13 confirmed at n=15; blocker cleared |
| [`t2-bug-class-sweep-2026-08-20.md`](t2-bug-class-sweep-2026-08-20.md) | silent-failure bug classes, findings only |
| [`t6-harness-sweep-2026-08-20.md`](t6-harness-sweep-2026-08-20.md) | trap definitions; trap 5 = read the value back from JACK |
| [`t4a-512-loop-curve-2026-08-20.md`](t4a-512-loop-curve-2026-08-20.md) | **512: loop count irrelevant**, structural cost ~3/min |
| [`t4c-1024-loop-curve-finish-2026-08-20.md`](t4c-1024-loop-curve-finish-2026-08-20.md) | 1024 condition B: 0/4/8 loops all 0.00 |
| [`t5-pre-jack-lsp-removal-2026-08-20.md`](t5-pre-jack-lsp-removal-2026-08-20.md) | fork-free watchdog path |
| [`t5-soak-2026-08-21.md`](t5-soak-2026-08-21.md) | 8 h @ 16 loops: 445 xruns, **Poisson** -- drift ruled out |
| [`t9-loops8-d-2026-08-21.md`](t9-loops8-d-2026-08-21.md) | **8 loops @ 1024x3 condition D: 0.00, 15/15.** Shipping config |

### Standing conclusions

- **Ship 1024x3, condition D, up to 8 loops.** Measured clean at n=15.
- 16 loops costs 0.93/min -- needs *both* high loop load and the full stack.
- 512 is not shippable with the looper: ~3/min regardless of loop count.
- Two independent cost terms. **Structural** (extra process hop in JACK's serial chain)
  dominates at 512, invisible at 1024. **Load** (loop DSP) invisible at 8 loops, appears
  at 16. Do not conflate them -- doing so produced the retracted "fixed +0.80/min" claim.
- Xrun arrivals are Poisson (dispersion 1.09). **Not clock drift.** Adaptive-resampling
  bridges and PipeWire dynamic quantum are ruled out as fixes.
- Callback duration is not the limit (917 us max against a 10.7 ms deadline, r = -0.07).
  The open question is scheduling delay vs the USB path -- that is T10.

### Open

- **Condition A below 512 has never been measured** -- the instrument-only low-latency
  number. That is T11.
- What the structural term actually is. T10.

## Completed runs

- [`looper-p0-latency-calibration.md`](looper-p0-latency-calibration.md) — P0 input_latency ear procedure (do before seam tuning)
- [`seam-weld-spike-2026-08-18.md`](seam-weld-spike-2026-08-18.md) — Option B/E/Tier 2 archaeology; stop-then-weld is current (2026-08-19)
- [`sooperlooper-eval-2026-08-14.md`](sooperlooper-eval-2026-08-14.md) — Session A
  **continue**, Session B **inconclusive** (Pi bench, branch `docs/sooperlooper-eval`).

## Rig enforcement (T6)

The appliance is guarded by T3 (`periodic_loop_lint.py`, `health_source_liveness.py`).
The **measurement rig** is held to the same standard:

- `mpe_meter_assert_live` / `mpe_meter_xruns_read` in `scripts/lib/audio-engine.sh` — 3 s
  freshness; never `|| echo 0`
- `tests/test_meter_harness.sh` — missing/stale paths fail loudly
- `measure-latency-run.sh` — `MPE_PEAK_METER != 1` is fatal; RESULT lines carry
  `meter_live=1` and `meter_max_age_s`
- `measure-cyclictest-floor.sh` — validate cyclictest output before append
- `scripts/xrun-corr.sh` — same meter rules as the latency harness

Sweep: [`t6-harness-sweep-2026-08-20.md`](t6-harness-sweep-2026-08-20.md).
