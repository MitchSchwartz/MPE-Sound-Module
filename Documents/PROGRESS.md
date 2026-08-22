# Progress — MPE-Module low-latency / measurement track

*Last updated: 2026-08-22 (America/Toronto)*  
*Integration branch: `dev`*

---

## Track A — buffer / stream-variance / shipping claim

> **HALTED — queue frozen at C0.**  
> Resume at **A1** only after `./scripts/instrument-conformance.sh` exits 0 on the
> branch under test. No Pi measurement that affects the shipping claim until then.

| # | Task | Status | Blocked by |
|---|---|---|---|
| **C0** | Instrument conformance gate + doctrine in six places | **gate green (nerdrack)** | review-loop cycle 1 |
| A1 | T14 — 1024×3 condition D, 8 loops, 10 streams × 3 runs | **HALTED** | C0 |
| A2 | T12b — 240×3 vs 256×3 alignment isolation | **HALTED** | C0 |
| A3 | T15 — 1008×3 vs 1024×3 (if T12b confirms alignment) | **HALTED** | C0, A2 |
| A4 | Scarlett baseline ladder | **HALTED** | C0 |

Queue detail: `Documents/specs/queue-2026-08-21-evening.md`.

---

## V11 — withheld pending C0

**V11 recovery attempted offline** (see `docs/measurements/instrument-conformance-c0-2026-08-22.md`).

| Column | Status | Notes |
|---|---|---|
| **xruns** | stands | Parsed from RESULT lines with strict field checks |
| **DSP** | **withheld** | `dsp_med`/`dsp_median` mismatch voided DSP column; do not quote until C0 parser green |
| Artifacts | noted | Raw logs retained; aggregation re-run after conformance pass |

---

## Standing rules (measurement track)

1. Label confidence — measured / experiment / guess.
2. Verify on the device, not only in unit tests.
3. Fail loud — no in-band failures (`Rule −1` in `MEASUREMENT-DISCIPLINE.md`).
4. **Instrument conformance before every measurement** — `./scripts/instrument-conformance.sh` ≤ 15 min; positive, negative, and physics controls per metric; do not weaken assertions to pass.
5. No forks in periodic loops (`Documents/DECISIONS.md`).
6. Bisect before you grid.
7. Certification (soak) comes last.
8. Announce blocks > 15 min.

---

## SR&ED

Measurement-system development (C0, conformance library, physics model) is eligible work.
Documented finding: *instrument value and failure share a channel* — nine instances, one
root cause.
