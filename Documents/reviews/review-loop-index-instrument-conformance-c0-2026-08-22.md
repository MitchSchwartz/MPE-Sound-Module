# Review loop — instrument-conformance-c0 (2026-08-22)

| Cycle | Grumpy | Audit | Fixes applied |
|---|---|---|---|
| 1 | [grumpy-review-instrument-conformance-c0-2026-08-22.md](grumpy-review-instrument-conformance-c0-2026-08-22.md) | [review-audit-instrument-conformance-c0-cycle1-2026-08-22.md](review-audit-instrument-conformance-c0-cycle1-2026-08-22.md) | PROBE_ACTIVE; v11 per-row withhold + missing-file halt; dsp zero sentinel; xrun-corr stdout; meter baseline after probe; primary-row parse; xrun-corr script test |

**Gate after cycle 1:** `./scripts/instrument-conformance.sh` → `SENTINEL conformance-pass` (6s)

**Open P1 (defer to Pi / follow-up):** soak/bench script coverage; `.venv` gitignore; regression tests with live probe binary (libjack-dev on nerdrack).

**Track A:** remains HALTED until C0 merges to `dev` and Mitch clears gate on appliance.

---

## Cycle findings (carried in 2026-09-23 before the cut)

### `grumpy-review-instrument-conformance-c0-2026-08-22.md`


1. **🔴 Make `window_align` mean something.** Emit `PROBE_ACTIVE` from
   `mpe-xrun-probe.c` *after* `jack_activate()` returns 0, gate the sample loop on that,
   take the meter baseline immediately after it, and compute `window_align` from the measured
   meter-baseline→first-DSP-sample gap instead of stamping the literal `1` at
   `measure-latency-run.sh:512`. Until then, stop reporting the V10-b misalignment as fixed.
2. **🔴 Make `mpe_result_v11_recover` capable of failing.** Move `withhold=0` inside the
   `RESULT` case arm (fixes the order-dependent verdicts), validate the input file with
   `[ -r "$file" ]`, and halt on zero emitted rows. Then re-run the V11 recovery and re-derive
   the `PROGRESS.md` DSP-withheld conclusion, because the current one came from this tool.
3. **🔴 Close the zero-sentinel path.** Replace `print "0 0 0"` at
   `measure-latency-run.sh:468` with an awk failure that voids the window, reject
   `0`/`0.000000` for `dsp_*` in `mpe_result_require_fields`, and add a fixture whose DSP rows
   are all `?` asserting the gate halts.
4. **🔴 Bind the fixtures to the emitter and validate the primary row.** Extract the
   RESULT-emitting block into a callable function, generate fixtures from it, and add
   `mpe_result_require_primary_row` so `require_fields` stops being satisfied by the union of
   all rows. This is the check that would have caught `dsp_med`, and it is the one still
   missing.
5. **🔴 Fix or declare the Task 1 coverage gap.** At minimum: fix occurrence #1
   (`xrun-corr.sh:25` → stdout) and add its conformance tests, and add a "Not covered"
   section to `instrument-conformance-c0-2026-08-22.md` naming `measure-soak.sh` and
   `bench-xruns.sh` as ungated, filed as C0b. A gate whose report implies coverage it lacks
   is the failure mode this whole exercise is about.

### `review-audit-instrument-conformance-c0-cycle1-2026-08-22.md`


| Priority | Issue | Verdict | Effort | Depends On |
|----------|-------|---------|--------|------------|
| P0 | Fix `scripts/xrun-corr.sh:25` to write to stdout, not `~/xrun-corr.out`, and add a conformance test that invokes the real script (not just a hand-typed fixture) | ✅ Confirmed, unfixed | Quick fix | — |
| P0 | Add `mpe_result_require_primary_row` (or equivalent) so `require_fields` validates row 1 in isolation, not the union of all merged rows; regenerate at least the two remaining drifted fixtures (`good-1024-b.log`, `physics-low-dsp-high-xr.log`) to place `window_align` correctly | ✅ Confirmed, unfixed | Half-day | — |
| P0 | Update `docs/measurements/instrument-conformance-c0-2026-08-22.md:52-56` to describe `PROBE_ACTIVE` (not stale `PROBE_START`) and stop claiming `window_align` is "fixed" while it's still a literal `1`; add a "Not covered" section naming `measure-soak.sh`/`bench-xruns.sh` | ✅ Confirmed, unfixed (and worsened by the P0 fix landing without a doc update) | Quick fix | — |
| P0 | Compute `window_align` from the actual meter-baseline→`PROBE_ACTIVE` gap and halt above a threshold, instead of stamping `1`; move the meter baseline capture to after probe spawn | ✅ Confirmed, unfixed | Half-day | — |
| P1 | Add regression tests for the two now-fixed 🔴 defects that ship without coverage: `dsp_median=0`/all-`?` fixture (🔴3), and a fixture with the `good`/`bad` row order already covered but also test with 3+ rows and mixed withhold states (🔴2 extra coverage) | ✅ Confirmed gap | Half-day | — |
| P1 | Fix `mpe_result_v11_recover` empty-input case (zero RESULT rows → halt, not silent `rc=0`); fix `${out:-/dev/stdout}` portability by writing to stdout unconditionally when `$2` is empty | ✅ Confirmed, unfixed | Quick fix | — |
| P1 | Make `mpe_result_physics_assert` halt (not pass) on empty/absent metrics; validate numeric-ness before `-lt`/`-ge`; make `mpe_result_physics_buffer_halving` halt (not pass) when `dsp_large<=0` | ✅ Confirmed, unfixed | Half-day | — |
| P1 | Fix `.venv` hygiene: add `.venv/` to `.gitignore` before the first commit | ✅ Confirmed, unfixed | Quick fix (5 min) | — |
| P1 | Add coverage for `measure-soak.sh` (fix its RESULT grammar to include `tag=` first, or give `measurement-result.sh` a second entry point) and `bench-xruns.sh` | ✅ Confirmed gap | Multi-day | Depends on grammar decision |
| P2 | Fix `scripts/instrument-conformance.sh`: drop `2>/dev/null` on the unittest calls, drop the `||` python-interpreter fallback, fix the misleading comment | ✅ Confirmed, unfixed | Quick fix | — |
| P2 | Fix `MPE_R_*` state leak: enumerate and unset dynamically via `compgen -v MPE_R_` at the top of `mpe_result_load_tag` | ✅ Confirmed, unfixed | Quick fix | — |
| P2 | Fix the `[ "${MPE_R_tag-}" = *"-b512-"* ]` dead glob guard to `[[ ... == *-b512-* ]]`; add a test with empty `$1` and a `-b512-` tag | ✅ Confirmed, unfixed | Quick fix | — |
| P2 | Update `Documents/PROGRESS.md` to reflect current fix status and reconsider the C0 "in progress" label now that the gate is passing; re-derive the V11 "DSP withheld" verdicts now that the sticky-withhold bug is fixed | ✅ Confirmed, unfixed | Quick fix | Depends on P1 re-derivation |
| P2 | Move `source lib/measurement-result.sh` to the top of `measure-latency-run.sh` beside the other `source` calls | ✅ Confirmed, unfixed | Quick fix | — |
| P3 | Reconcile the two incompatible RESULT grammars (`tag=` required vs. `measure-soak.sh`'s tag-less format) — either add `tag=` to `measure-soak.sh` or document the exception in SKILL.md | ✅ Confirmed, unfixed | Half-day | Coordinate with measure-soak.sh coverage above |
| P3 | Correct the `good-512-a.log`/`good-1024-b.log` `temp=temp=` fixture typo (a fixture-authoring bug, not an emitter bug — see disagreement below) | ⚠️ Partially True — miscategorized by original review | Quick fix | — |
| P3 | Fix the inverted `dsp_large`/`dsp_small` parameter order in the `buffer_halving` "plausible DSP increase" test call | ✅ Confirmed, unfixed | Quick fix | — |
| P3 | Add `shellcheck` to the gate (or CI) once available | ✅ Confirmed, unfixed | Quick fix (once installed) | Tooling availability |
| P3 | Collapse the "recorded at end of this doc" placeholder in the deliverable now that the wall-time number exists | 🔍 Not re-verified this cycle | Quick fix | — |

**Rollup: 4 P0, 6 P1, 6 P2, 5 P3** (one item — the `temp=temp=` fixture typo — is P3 but carries a verdict correction, not a "still broken" confirmation).

---

