# V12 — compare buffer configs at a rate the burstiness allows

**Stack position:** **G2 → V12 → B3 → Gate 1 ship.** Runs after G2 closes — the governor changes
the process being measured, so certifying before it is calibrated measures a configuration that
will not ship.

**Requires Mitch's explicit approval before any Pi time** — total window ~70 minutes, over the
30-minute standing limit. Justification is in §2; do not start without it.

**Read first:** [`X1-RESULT-burstiness-2026-08-23.md`](X1-RESULT-burstiness-2026-08-23.md),
[`PROMPT-G2-governor-recalibration.md`](PROMPT-G2-governor-recalibration.md).

---

## 1. Read this first — "clean" was never true, and that reframes the task

Every prior buffer decision used the criterion **"0 xruns at confirmed voice counts."** That
criterion was never achievable:

- **B2 soak, `1024×2`, Cloud Horn @5, 8 h: 991 xruns = 2.06/min.** That is the config V9 called
  *"free"* and that PROGRESS once listed as *"measured free at clean load."*
- It read as clean only because the windows were **25–45 s** against a **bursty** process — Fano
  **4.32**, **33% of minutes silent** at a 3.87/min mean in the first 15 minutes
  (`X1-RESULT-burstiness-2026-08-23.md`).

**So V12 does not ask which config is clean. None are.** The questions are:

1. **How much worse is `512×2` (21.3 ms) than `1024×2` (42.7 ms)?**
2. **Is either rate audible?**

**V12 answers (1). Only Mitch can answer (2)** — that is B3, and it is the real acceptance test.

**Hard rule:** **Do not report PASS/FAIL or "clean" for a buffer.** Report rate and burst
structure. That is how *"free"* got into PROGRESS in the first place.

---

## 2. Window justification — required by the 30-minute rule

Fano-corrected arithmetic (effective n ≈ `n / Fano`):

| | |
|---|---|
| Measured rate (B2 / X1) | ~2–4/min at `1024×2`; **3.87/min** in minutes 1–15 |
| **Fano factor** | **4.32** |
| Events for a ~±20% rate estimate | ~30 effective ≈ **~130 raw** |
| At 3.87/min | **~33 minutes** → **30 minutes per arm** (rounded down; pre-registered) |
| Two configs | **`1024×2` + `512×2` = 60 min** + ~10 min setup ≈ **~70 min total** |

**Why not shorter:** at 33% silent minutes, a 10-minute window can land majority-quiet and
produce a rate estimate wrong by more than the difference being measured.

**Why not longer:** B2 showed 8 hours adds only the decay shape, which is visible within 30
minutes. A second pass on a later day would buy more than a longer single run — propose that
instead of extending.

---

## 3. Design

**Fixed:** governor **ON** at G2's calibrated thresholds (shipping config), condition A, strict
mode, stock 1800 MHz, same binary, same patch, same voice count.

**Variable: buffer config only.**

| arm | config | latency | duration |
|---|---|---|---|
| A | `1024×2` | 42.7 ms | 30 min |
| B | `512×2` | **21.3 ms** | 30 min |

**Patch: Cloud Horn @5** — highest DSP in the library (56.9–59.4%), the B2 reference point, and
the case most likely to expose a difference.

**Order:** **Randomise or alternate the two arms.** The rate **decays over the first ~30
minutes**, so running A then B in fixed order would bias B favourably (second arm sees a cooler,
quieter process).

**Optional arm C — `256×3` (16.0 ms), 30 min** — only if B is clearly acceptable and Mitch
approves the extra window. V11 showed Crystals marginal here. Do not bundle it in by default.

**Log per minute** (B2 format): `xruns_minute`, `xruns_total`, `meter_live`, `meter_age_s`, temp,
throttle, **governor engagement count**. If the governor fires during clean play, that is a **G2
regression** — stop and void the run.

---

## 4. What to report — and what not to

**Report, per arm:**

- Total xruns, **rate/min**, and **Fano factor**. A rate without dispersion implies Poisson; on
  this appliance that implication is false by ~4×.
- Per-minute series and **silent-minute fraction**.
- **Burst structure** — longest silent run, largest single minute. **2/min in one burst per hour
  is a completely different instrument from 2/min evenly spread**, and that distinction is what
  determines audibility. A mean alone cannot tell you which one you have.
- `dsp_med` and `dsp_max`, governor engagements, thermals.

**Headline comparison:** rate at `512×2` ÷ rate at `1024×2`, with both Fano factors stated.

**Do not report "clean", "PASS", or "FAIL" for a buffer.** Report the rate and its structure.
Acceptance is Mitch's ear (B3), not a threshold in this document.

**Pre-register before running** (Rule 1; include `Conformance:` and `Pilot:` lines):

- What ratio would make `512×2` clearly acceptable, clearly unacceptable, or indeterminate.
- **What result would make you recommend staying at `1024×2`.**
- Expected rate at each config, written down first.

---

## 5. Sequence

1. **C0 conformance pass** — this session, both halves. Gate.
2. **G2 complete** — thresholds verified, governor confirmed **not** engaging on clean Cloud Horn.
3. **Pilot one arm** (Rule 0.5) — `sudo ./scripts/measure-v12-buffer-compare.sh --pilot` (2 min @
   1024×2, governor on). Read every field including governor stamp and `governor_engagements`.
4. **Get Mitch's approval** for the ~70-minute window with §2's arithmetic.
5. Run both arms: `sudo ./scripts/measure-v12-buffer-compare.sh --minutes 30 --order random`
   (or `--order alternate`). **Alternate or randomise order** (§3).
6. **Report per §4.**
7. **B3 ear test** at whichever config the numbers support, governor on.

---

## 6. Harness — implemented

| Script | Role |
|---|---|
| `scripts/measure-soak-instrument.sh` | Single arm: `--minutes`, `--governor on\|off`, provenance stamp, per-minute `governor_engagements`, `dsp_median`/`dsp_max` in RESULT |
| `scripts/measure-v12-buffer-compare.sh` | Two arms + Fano/burst summary; **no PASS/FAIL** |

**Do not use the confirm harness** for this metric. Confirm cells are **screening only** — the
standing consequence of the burstiness finding (`X1-RESULT` §doctrine 2).

Every log header includes `_provenance_line` output, e.g.:

```
PROVENANCE governor=on MPE_POLY_CPU_HIGH=<readback> MPE_POLY_CPU_LOW=<readback> ...
```

Governor-off B2 logs are not comparable to governor-on V12 runs — stamp explicitly.

---

## 7. Constraints

- **One variable.** Buffer only. Governor thresholds are fixed by G2 and do not move here.
- **No Pi contact while a window is open**, including read-only.
- If the governor engages during clean play, **stop** — G2 regression, not a V12 result.
- **Do not weaken the criterion to produce a shippable answer.** If `512×2` is materially worse,
  say so. **`1024×2` at 42.7 ms is still a 1.5× improvement on the shipping 64.0 ms** and is
  already soak-tested — that fallback is a real win, not a failure.
- Any window beyond the approved ~70 minutes needs fresh approval.

## Hand back

Per-arm rate, Fano, silent-minute fraction, burst structure (longest silent run, largest minute),
DSP, governor engagements, the headline ratio, pre-registration vs outcome, and a plain
recommendation for **which config goes to B3** — with reasoning, not a verdict.
