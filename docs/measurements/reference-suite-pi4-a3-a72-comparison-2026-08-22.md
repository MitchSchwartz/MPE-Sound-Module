# A3 — `-mcpu=cortex-a72` vs stock reference suite comparison

*Completed: 2026-08-22 (America/Toronto)*

## Pre-registration (from `scripts/build-surge-a72.sh`)

| Threshold | Rule |
|-----------|------|
| **Win** | `>5%` dsp_med headroom gain on **any** cell |
| **No-effect** | `<3%` on all cells |

All nine loaded comparison cells are **≤1.19%** delta. **Unambiguous no-effect** by the pre-registered rule. Run-to-run spread has **not** been measured yet (A4); do not invoke it as a decision criterion here.

## Setup

| Item | Value |
|------|-------|
| Stock artifact | `~/reference-suite-pi4-20260822-204559/reference-suite-pi4-pass1.json` (`110977a`) |
| a72 artifact | `~/reference-suite-pi4-a72-20260822-231637/reference-suite-pi4-pass1.json` (`494e8b4`) |
| Stock binary | `~/surge/build/surge_xt_products/surge-xt-cli` · `1.4.main.253f8d86` · sha256 `c3680d6b0fa7ce5e710f72b06ed88000c2f010fad870853f1765a5b319dbd091` |
| Stock source | commit **`253f8d86`** — verified via `surge-xt-cli --version` on Pi 2026-08-23; `~/surge` is a deploy tree (no `.git`); suite JSON records `surge_revision: unknown` (harness gap) |
| a72 binary | `~/surge-src/build-a72/.../surge-xt-cli` · `1.4.HEAD.253f8d86` · sha256 `556cc6f00a2dd85385e2cf0fe041906f7057dbbcd4e948fbc51780c10355df74` |
| a72 source | commit **`253f8d86`** (`~/surge-src`, `build-surge-a72.sh` PROVENANCE) |
| **Same revision, flag-only delta** | Both binaries built from **`253f8d86`**; stock = default Release flags, a72 adds `-mcpu=cortex-a72` only |
| Backup | `~/surge-xt-cli.pre-a72` (+ `.sha256` sidecar) |
| Window | 25 s × 2 runs, governor off, Condition A |

Both passes re-validated offline at `494e8b4` parser: 12/12 loaded cells PASS.

## Results by patch (dsp_median % — lower is better)

Summary table (Δ% = a72 − stock, as % of stock):

| patch | path | 1024×2 | 512×2 | 256×3 |
|-------|------|--------|-------|-------|
| Crystals | osc | −0.21 | −0.20 | +1.13 |
| Cloud Horn | osc | −0.27 | −0.17 | +0.52 |
| Duduk | filter | +0.65 | +1.16 | +1.19 |

### Crystals @ 3 (oscillator-dominated)

| cell | config | stock | a72 | Δ | Δ% |
|------|--------|-------|-----|---|-----|
| P1 | 1024×2 | 38.399 | 38.319 | −0.080 | −0.21% |
| P2 | 512×2 | 39.295 | 39.215 | −0.079 | −0.20% |
| P3 | 256×3 | 40.741 | 41.201 | +0.460 | +1.13% |

**No win.** Osc patches: slightly better at 1024/512, worse at 256 — same directional pattern across both osc patches.

### Cloud Horn @ 5 (oscillator-dominated)

| cell | config | stock | a72 | Δ | Δ% |
|------|--------|-------|-----|---|-----|
| P4 | 1024×2 | 56.892 | 56.736 | −0.156 | −0.27% |
| P5 | 512×2 | 58.174 | 58.073 | −0.101 | −0.17% |
| P6 | 256×3 | 59.368 | 59.678 | +0.310 | +0.52% |

**No win.** Same osc pattern as Crystals; all deltas below the 3% no-effect threshold.

### Duduk @ 3 (filter-dominated)

| cell | config | stock | a72 | Δ | Δ% |
|------|--------|-------|-----|---|-----|
| P7 | 1024×2 | 38.341 | 38.590 | +0.249 | +0.65% |
| P8 | 512×2 | 39.397 | 39.854 | +0.458 | +1.16% |
| P9 | 256×3 | 40.447 | 40.927 | +0.480 | +1.19% |

**No win.** a72 is worse on all three configs; regression **monotonically worsens** as buffer shrinks (+0.65 → +1.16 → +1.19%). Possible small consistent regression on the filter path, **unresolvable at this n** (single pass per binary, no A4 noise floor yet).

**Forward note (A4):** when reference pass 2 lands, retro-compare. If run-to-run spread is well under 0.5%, the Duduk regression may be real (finding about `-mcpu` + filter inner loops). If spread is ~1%, close as noise.

## V1 fixed-cost model — independent corroboration (stock pass 1 only)

Stock `dsp_med` rises across buffer configs on the **same** binary (A2 pass 1), independent of the silence test:

| patch | 1024×2 | 512×2 | 256×3 | rise (pp) |
|-------|--------|-------|-------|-----------|
| Crystals | 38.399 | 39.295 | 40.741 | **+2.34** |
| Duduk | 38.341 | 39.397 | 40.447 | **+2.11** |
| Cloud Horn | 56.892 | 58.174 | 59.368 | **+2.48** |

V1 fitted **a = 0.13 ms** fixed per-callback cost. That is **0.61%** of the period deadline at 1024 and **2.44%** at 256 — a predicted rise of **~1.83 pp** in the fixed-cost fraction as buffer shrinks. Observed `dsp_med` rise is **2.1–2.5 pp** across three unrelated patches. Cross-validates the V1 model via loaded cells on the reference suite, without relying on the silence test.

See also **U2** in OM-Repo [`SRED-EVIDENCE-2026.md`](../../../OM-Repo/internal/projects/mpe-synth-launch/sred/SRED-EVIDENCE-2026.md).

## Decision (pre-registered closeout §A3)

**NULL RESULT.** All cells `<3%` by pre-registered rule — **no-effect**, not “within noise” (noise unmeasured). Osc patches show no win; Duduk shows a possible filter-path regression unresolvable until A4.

- **Control binary:** stock (reverted after a72 suite)
- **Shipping default:** unchanged — B3 ear test still gates any binary promotion
- **a72 artifact:** kept at `~/surge-src/build-a72/...` for reference; not installed
- **Backup:** `~/surge-xt-cli.pre-a72` retained

Well-executed null result — closes the `-mcpu=cortex-a72` lever for U7.

## Next

- **A4:** reference pass 2 on stock binary, different day (noise floor) — enables retro-compare on Duduk
- **B3:** ear test if a72 is ever reconsidered for shipping
