# A3 — `-mcpu=cortex-a72` vs stock reference suite comparison

*Completed: 2026-08-22 (America/Toronto)*

## Setup

| Item | Value |
|------|-------|
| Stock artifact | `~/reference-suite-pi4-20260822-204559/reference-suite-pi4-pass1.json` (`110977a`) |
| a72 artifact | `~/reference-suite-pi4-a72-20260822-231637/reference-suite-pi4-pass1.json` (`494e8b4`) |
| Stock binary | `~/surge/build/surge_xt_products/surge-xt-cli` sha256 `c3680d6b…` |
| a72 binary | `~/surge-src/build-a72/.../surge-xt-cli` sha256 `556cc6f0…` |
| Backup | `~/surge-xt-cli.pre-a72` (+ `.sha256` sidecar) |
| Window | 25 s × 2 runs, governor off, Condition A |

Both passes re-validated offline at `494e8b4` parser: 12/12 loaded cells PASS.

## Results by patch (dsp_median % — lower is better)

### Crystals @ 3 (oscillator-dominated)

| cell | config | stock | a72 | Δ | Δ% |
|------|--------|-------|-----|---|-----|
| P1 | 1024×2 | 38.399 | 38.319 | −0.080 | −0.21% |
| P2 | 512×2 | 39.295 | 39.215 | −0.079 | −0.20% |
| P3 | 256×3 | 40.741 | 41.201 | +0.460 | +1.13% |

Mixed; 256×3 slightly worse on a72. Within single-pass noise.

### Cloud Horn @ 5 (oscillator-dominated)

| cell | config | stock | a72 | Δ | Δ% |
|------|--------|-------|-----|---|-----|
| P4 | 1024×2 | 56.892 | 56.736 | −0.156 | −0.27% |
| P5 | 512×2 | 58.174 | 58.073 | −0.101 | −0.17% |
| P6 | 256×3 | 59.368 | 59.678 | +0.310 | +0.52% |

No consistent win; deltas &lt;0.5% except 256×3 (+0.52% worse on a72).

### Duduk @ 3 (filter-dominated)

| cell | config | stock | a72 | Δ | Δ% |
|------|--------|-------|-----|---|-----|
| P7 | 1024×2 | 38.341 | 38.590 | +0.249 | +0.65% |
| P8 | 512×2 | 39.397 | 39.854 | +0.458 | +1.16% |
| P9 | 256×3 | 40.447 | 40.927 | +0.480 | +1.19% |

a72 **worse** on all three configs (+0.6–1.2%). Filter path does not benefit.

## Decision (pre-registered closeout §A3)

**NULL RESULT.** No patch shows a72 beating stock beyond run-to-run spread. Duduk
(filter-bound) is consistently slightly worse on a72.

- **Control binary:** stock (reverted after a72 suite)
- **Shipping default:** unchanged — B3 ear test still gates any binary promotion
- **a72 artifact:** kept at `~/surge-src/build-a72/...` for reference; not installed

## Next

- **A4:** reference pass 2 on stock binary, different day (noise floor)
- **B3:** ear test if a72 is ever reconsidered for shipping
