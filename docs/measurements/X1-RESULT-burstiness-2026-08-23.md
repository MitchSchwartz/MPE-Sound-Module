# X1 result — the xrun process is bursty, not Poisson. No harness defect.

*2026-08-23.* Resolves the confirm-vs-soak discrepancy from `PROMPT-X1-confirm-vs-soak.md`,
**offline, from the B2 soak log alone. No Pi time used.**

## The data

Per-minute xruns, B2 attempt #3 (Cloud Horn @5, `1024x2`), minutes 1–15:

```
4, 11, 6, 2, 0, 4, 0, 2, 12, 0, 0, 7, 0, 2, 8      sum 58
```

| statistic | value | Poisson expectation |
|---|---|---|
| mean | **3.87/min** | — |
| variance | **16.70** | 3.87 |
| **Fano factor** (var/mean) | **4.32** | **1.00** |
| zero-minutes | **5 of 15 (33%)** | 0.31 of 15 (2%) |

**The process is over-dispersed by a factor of ~4.3.** A third of all minutes are completely
silent at a mean rate of nearly four per minute. Poisson predicts 0.31 such minutes; we observed
**5** — a 16x discrepancy. **Xruns on this appliance arrive in bursts separated by genuinely
quiet stretches.**

## What this resolves

**1. Both hypotheses in the prompt are refuted.**

- *Quiet-start / ramp:* refuted directly. Minutes 1–2 are the **hottest** segment (4, 11), and
  minutes 1–15 average **3.87/min against the 8-hour mean of 2.06**. The curve is front-loaded
  and decays. My earlier "peaks ~3.5/min near minute 14" was a misreading of a **cumulative
  average** (50÷14 ≈ 3.57) as an instantaneous rate — corrected in
  `MEASUREMENT-DISCIPLINE.md`.
- *Harness defect:* not required to explain anything. V11 demonstrably sees xruns when they are
  present (Cloud Horn `512x2` 0/0/8; `256x3` 15/19/23).

**2. `0/0/0` over three short windows is entirely consistent with a real 2–4/min rate.**

The Poisson arithmetic in the prompt — *P(zero in 75 s) ≈ 8%* — **was wrong, and wrong in the
direction that mattered.** It assumed a Poisson process. Empirically 33% of minutes are silent,
and because the silence is **clustered** rather than independent, consecutive short windows
landing in the same quiet stretch is far more likely still.

**There is no discrepancy to explain.** Confirm and soak measured the same appliance correctly;
one sampled a bursty process with windows far too short to characterise it.

## What it costs us

**The 21.3 ms claim is unsupported — not refuted, unsupported.** V11's `512x2` `0/0/0` over
3 x 25 s is consistent with any true rate from 0 to several per minute. It never was evidence of
"clean"; it is evidence of *"no events observed in 75 seconds,"* which for this process is close
to no information at all.

The same applies to every short-window "clean" cell in the project's history, including the
confirmed floors.

## Doctrine consequences

**1. Event-count windows must be sized for over-dispersion, not just rate.** Effective sample
size is roughly `n / Fano`. At Fano ≈ 4.3, a window must be **~4x longer** than Poisson
arithmetic suggests for the same confidence. To accumulate ~30 *effective* events at 3.87/min
requires ~130 raw events ≈ **33 minutes**.

This **retroactively justifies the 30-minute minimum** on better grounds than it was set, and
means **60 minutes** is the right size for a rate claim. It does not justify 8 hours.

**2. Short windows are screening, never certification — for this metric.** A 25–45 s xrun count
cannot certify anything about a process with a 4x Fano factor and 33% silent minutes.

**3. Use a continuous metric for short windows.** This is the discipline doc's existing rule
arriving with teeth: *when the shortest useful version is implausibly long, the metric is wrong
for the question.* `dsp_max` / headroom is continuous, has no burst structure, and is
informative in 25 seconds. **Screen on DSP headroom; certify on a long-window xrun count.**

**4. Report the Fano factor** wherever an event rate is claimed. A rate without a dispersion
figure implies Poisson, and on this appliance that implication is false by 4x.

## Open, and unchanged by this

`measure-v8b-playable.sh` does **not** enforce governor-off (it calls `measure-latency-run.sh`
directly, which sets strict mode only). The V9-b Cloud Horn `1024x2` `0/0/0` came through that
path and relied on Plan V having left `MPE_POLY_GOVERNOR=0` in `/etc/mpe/mpe.env`. **The log does
not stamp governor state, so it cannot be proven from artifacts.**

V11 ran via `measure-plan-v11.sh`, which sets it explicitly — so the confirmed floors and G2's
inputs are **not** governor-limited. Exposure is confined to that one V9-b cell.

**Flagged, not fixed** — per X1's instruction not to repair a harness before the diff is
recorded. The fix is one line and should land with the diff.

## Recommendation

- **Do not run X1 Step 3.** It was designed to detect a harness defect; there is none, and the
  question it would answer is now answered.
- **Do run a 30–60 minute certification** at whatever config Gate 1 ships, **after** G2 —
  because the governor changes the process being measured.
- **Re-measure `512x2`** over 30–60 minutes before the 21.3 ms claim is used for anything.
- Add governor-state stamping to every measurement log (Rule 3: record actual state).
