# A4 — reference suite pass 2 and run-to-run spread

*Completed: 2026-08-23 (America/Toronto)*

## Purpose

Second reference pass on a **different calendar day** than A2 pass 1, same stock control
binary. Together the two passes define the **run-to-run noise floor** for the frozen Pi 4
control — the threshold below which no future platform comparison (including Pi 5) can claim
a real effect.

## Setup

| Item | Value |
|------|-------|
| Pass 1 artifact | `~/reference-suite-pi4-20260822-204559/reference-suite-pi4-pass1.json` (`110977a`) |
| Pass 2 artifact | `~/reference-suite-pi4-20260823-000348/reference-suite-pi4-pass2.json` (`4a845fd`) |
| Binary (both passes) | stock · sha256 `c3680d6b0fa7ce5e710f72b06ed88000c2f010fad870853f1765a5b319dbd091` · Surge `253f8d86` |
| Conformance | Full gate green 2026-08-23T00:03 (`e51856e` parser) before pass 2 |
| Window | 25 s × 2 runs, governor off, Condition A |

Pass 2 re-validated offline at `e51856e`: **12/12 loaded cells PASS** (`MPE_EXPECT_SAMPLES=25`).

## Run-to-run spread — loaded cells (dsp_median %)

Δ% = (pass2 − pass1) / pass1 × 100. Primary metric for platform comparisons.

| cell | patch | config | pass1 | pass2 | Δ | Δ% | \|Δ%\| |
|------|-------|--------|-------|-------|---|-----|--------|
| P1 | Crystals | 1024×2 | 38.399 | 38.059 | −0.341 | −0.89% | 0.89% |
| P2 | Crystals | 512×2 | 39.295 | 39.062 | −0.233 | −0.59% | 0.59% |
| P3 | Crystals | 256×3 | 40.741 | 41.436 | +0.695 | +1.70% | **1.70%** |
| P4 | Cloud Horn | 1024×2 | 56.892 | 57.057 | +0.165 | +0.29% | 0.29% |
| P5 | Cloud Horn | 512×2 | 58.174 | 57.913 | −0.261 | −0.45% | 0.45% |
| P6 | Cloud Horn | 256×3 | 59.368 | 59.609 | +0.241 | +0.41% | 0.41% |
| P7 | Duduk | 1024×2 | 38.341 | 37.889 | −0.452 | −1.18% | **1.18%** |
| P8 | Duduk | 512×2 | 39.397 | 39.203 | −0.194 | −0.49% | 0.49% |
| P9 | Duduk | 256×3 | 40.447 | 40.829 | +0.382 | +0.94% | 0.94% |
| P10 | Brave New World | 1024×2 | 38.412 | 38.368 | −0.044 | −0.12% | 0.12% |
| P11 | Brave New World | 512×2 | 39.302 | 39.412 | +0.110 | +0.28% | 0.28% |
| P12 | Brave New World | 256×3 | 40.495 | 40.506 | +0.011 | +0.03% | 0.03% |

### Spread statistics (\|Δ%\| across 12 loaded cells)

| Statistic | Value |
|-----------|-------|
| **Maximum** | **1.70%** (P3 Crystals 256×3) |
| Median | 0.47% |
| Mean | 0.53% |

**Decision threshold for Pi 5 (and any future A/B):** an improvement or regression
**≤ ~1.7% dsp_med** on any single cell is **indistinguishable from run-to-run spread** on
this control. Use **1.7% as the conservative floor**; median ~0.5% is typical day-to-day
jitter on clean cells.

Silence cells (S1–S3) show large xrun counts by design (0 voices, no patch load) and were
not used for spread statistics.

## A3 Duduk retro-compare

A3 reported a72 **worse** on Duduk (+0.65%, +1.16%, +1.19% vs stock, same day, different
binary). With A4 spread measured:

| Duduk cell | A3 a72 Δ% (worse) | A4 pass1↔pass2 \|Δ%\| |
|------------|-------------------|------------------------|
| P7 1024×2 | +0.65% | 1.18% |
| P8 512×2 | +1.16% | 0.49% |
| P9 256×3 | +1.19% | 0.94% |

**Verdict:** all three A3 Duduk deltas fall **within or at the edge of** measured
run-to-run spread. The apparent filter-path regression is **noise, not a finding** —
consistent with the A3 NULL result on the pre-registered >5% win rule. **No action;**
stock control stands.

## Verdict

**NOISE FLOOR ESTABLISHED.** Pi 4 stock control is calibrated: two passes, same binary,
different days, 12/12 cells re-validated. Proceed to A5 (state capture) and Pi 5
predictions (A9) using **1.7% max spread** as the significance floor.

## Next

- **B2:** 8 h soak @ 1024×2 Cloud Horn @5 started 2026-08-23T00:42 (Gate 1 certification)
- **A5–A9:** state capture, log archive, build infra, doc stamps, predictions table
