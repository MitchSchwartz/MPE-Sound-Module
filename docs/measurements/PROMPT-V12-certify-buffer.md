# V12 — certify the shipping buffer, with a criterion that can actually be met

**Runs after G2.** The governor changes the process being measured, so certifying before it is
calibrated measures a configuration that will not ship.

**Requires Mitch's explicit approval before any Pi time** — total window ~60–70 minutes, over the
30-minute standing limit. Justification is in §2; do not start without it.

---

## 1. Read this first — "clean" was never true, and that reframes the task

Every prior buffer decision used the criterion **"0 xruns at confirmed voice counts."** That
criterion is unachievable and always was:

- **B2 soak, `1024x2`, Cloud Horn @5, 8 h: 991 xruns = 2.06/min.** That is the config V9 called
  *"free"* and that PROGRESS lists as *"measured free at clean load."*
- It read as clean only because the windows were 25–45 s and **the process is bursty** — Fano
  **4.32**, **33% of minutes silent** at a 3.87/min mean (`X1-RESULT-burstiness-2026-08-23.md`).

**So the question is not "which config is clean."** None of them are. The questions are:

1. **How much worse is `512x2` (21.3 ms) than `1024x2` (42.7 ms)?**
2. **Is either rate audible?**

**V12 answers (1). Only Mitch can answer (2)** — it is B3, and it is the actual acceptance test.
Do not let this measurement be reported as certifying "clean."

---

## 2. Window justification — required by the 30-minute rule

| | |
|---|---|
| Measured rate | ~2–4/min at `1024x2` |
| **Fano factor** | **4.32** — effective n is ~`n/Fano` |
| Events for a ~±20% rate estimate | ~30 effective ≈ **~130 raw** |
| At 3.87/min | ~**33 minutes** |
| **Per config** | **30 minutes** |
| Configs | `1024x2` and `512x2` = **60 min** + ~10 min setup |

**Why not shorter:** at 33% silent minutes, a 10-minute window can land majority-quiet and
produce a rate estimate wrong by more than the difference being measured.

**Why not longer:** B2 showed 8 hours adds only the decay shape, which is visible within 30
minutes. A second pass on a later day would buy more than a longer single run — propose that
instead of extending.

---

## 3. Design

**Fixed:** governor **ON** at G2's calibrated thresholds (this is the shipping config), condition
A, strict mode, stock 1800 MHz, same binary, same patch, same voice count.

**Variable: buffer config only.**

| arm | config | latency | duration |
|---|---|---|---|
| A | `1024x2` | 42.7 ms | 30 min |
| B | `512x2` | **21.3 ms** | 30 min |

**Patch: Cloud Horn @5.** Highest measured DSP in the library (56.9–59.4%), the B2 reference
point, and the case most likely to expose a difference. **Randomise or alternate the arm order
if practical** — the rate decays over the first ~30 minutes, so running A first and B second would
bias B favourably.

**Optional arm C — `256x3` (16.0 ms), 30 min** — only if B is clearly acceptable and Mitch
approves the extra window. V11 showed Crystals marginal here. Do not bundle it in by default.

**Log per minute** (the B2 format is correct): `xruns_minute`, `xruns_total`, `meter_live`,
`meter_age_s`, temp, throttle. Plus governor engagement count per minute — **if the governor is
firing during the run, that is a G2 failure and this measurement is void.**

---

## 4. What to report — and what not to

**Report, per arm:**

- Total xruns, **rate/min**, and **Fano factor**. *A rate without dispersion implies Poisson, and
  that implication is false here by 4x.*
- Per-minute series, and the **silent-minute fraction**.
- **Burst structure**: longest silent run, largest single minute. This is what determines
  audibility — 2/min in one burst per hour is a different instrument from 2/min evenly spread.
- `dsp_med` and `dsp_max`, governor engagements, thermals.

**The headline comparison:** rate at `512x2` ÷ rate at `1024x2`, with both Fano factors stated.

**Do not report "clean" or "PASS/FAIL" for a buffer.** Report the rate and its structure. The
acceptance decision is Mitch's ear, not a threshold in this document.

**Pre-register before running** (per Rule 1, and include `Conformance:` and `Pilot:` lines):

- What ratio would make `512x2` clearly acceptable, clearly unacceptable, or indeterminate.
- **What result would make you recommend staying at `1024x2`.**
- The expected rate at each config, written down first.

---

## 5. Sequence

1. **C0 conformance pass** — this session, both halves. Gate.
2. **G2 complete**, thresholds verified, governor confirmed **not** engaging on clean Cloud Horn.
3. **Pilot one loaded cell** (Rule 0.5) — 2 minutes, read every field. The certification harness
   is new code.
4. **Get Mitch's approval** for the 60-minute window with §2's arithmetic.
5. Run arm A, then arm B (or alternate — see §3).
6. **Report per §4.**
7. **B3 ear test** at whichever config the numbers support, governor on.

---

## 6. Harness note

If a new script is needed, it is a **long-window certification** harness — the B2 soak script with
a `--minutes` parameter is most of it, and it already has the per-minute logging and the
terminal-sentinel fix (`#102`, Rule -1 mechanism 5).

**Reuse it. Do not write a new one**, and do not use the confirm harness — confirm cells are
**screening only** for this metric, which is the standing consequence of X1.

**Stamp governor state into the log.** The one real gap X1 found was that `measure-v8b-playable.sh`
does not enforce governor-off and the log does not record it, so the V9-b Cloud Horn cell cannot be
verified from artifacts. Every V12 log must record governor on/off and its thresholds, read back
from the running config (Rule 3).

---

## 7. Constraints

- **One variable.** Buffer only. Governor thresholds are fixed by G2 and do not move here.
- **No Pi contact while a window is open**, including read-only.
- If the governor engages during clean play, **stop** — that is a G2 regression, not a V12 result.
- **Do not weaken the criterion to produce a shippable answer.** If `512x2` is materially worse,
  say so; `1024x2` at 42.7 ms is still a 1.5x improvement on the shipping 64.0 ms and is already
  soak-tested.
- Any window beyond the approved 60 minutes needs fresh approval.

## Hand back

Per-arm rate, Fano, silent-minute fraction, burst structure, DSP, governor engagements, the
headline ratio, the pre-registration and whether it was met, and a plain recommendation for which
config goes to B3 — with the reasoning, not a verdict.
