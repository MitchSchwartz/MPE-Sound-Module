# Review loop — instrument-conformance-c0 (2026-08-22)

| Cycle | Grumpy | Audit | Fixes applied |
|---|---|---|---|
| 1 | [grumpy-review-instrument-conformance-c0-2026-08-22.md](grumpy-review-instrument-conformance-c0-2026-08-22.md) | [review-audit-instrument-conformance-c0-cycle1-2026-08-22.md](review-audit-instrument-conformance-c0-cycle1-2026-08-22.md) | PROBE_ACTIVE; v11 per-row withhold + missing-file halt; dsp zero sentinel; xrun-corr stdout; meter baseline after probe; primary-row parse; xrun-corr script test |

**Gate after cycle 1:** `./scripts/instrument-conformance.sh` → `SENTINEL conformance-pass` (6s)

**Open P1 (defer to Pi / follow-up):** soak/bench script coverage; `.venv` gitignore; regression tests with live probe binary (libjack-dev on nerdrack).

**Track A:** remains HALTED until C0 merges to `dev` and Mitch clears gate on appliance.
