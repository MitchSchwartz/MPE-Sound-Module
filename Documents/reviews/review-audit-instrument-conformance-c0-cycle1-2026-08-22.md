# Review Audit — C0 instrument conformance (cycle 1 of 5)

*Auditing:* [`grumpy-review-instrument-conformance-c0-2026-08-22.md`](grumpy-review-instrument-conformance-c0-2026-08-22.md)
*Against:* `/home/claude-sandbox/workspace/MPE-Module`, branch `yolo/instrument-conformance-c0`, uncommitted working tree
*Scope note:* Per the task brief, P0 fixes were applied to the codebase *after* the Grumpy review was written. This audit re-verifies every Grumpy claim against the code **as it stands now**, not as it stood when Grumpy wrote it.

**Method:** every claim below was re-executed — sourced `scripts/lib/measurement-result.sh` and called its functions directly with probe inputs, ran `bash tests/test_instrument_conformance.sh`, `bash tests/test_meter_harness.sh`, and `bash scripts/instrument-conformance.sh` in full, diffed the working tree against `dev`, and read every file the finding depends on. No claim was accepted on Grumpy's say-so.

---

## Work Queue

Claims extracted from the Grumpy review, grouped by finding:

1. §4 🔴1 — `window_align=1` stamped before the probe is live (`PROBE_START` precedes `jack_activate`)
2. §4 🔴2 — `mpe_result_v11_recover`: (a) sticky withhold across rows, (b) missing file → exit 0, (c) empty input → exit 0, (d) `/dev/stdout` portability
3. §4 🔴3 — in-band failures survive in the harness: `awk n==0` → `"0 0 0"`; `dsp_median=0` accepted by `require_fields`
4. §4 🔴4 — PROMPT-C0 Task 1 ~25% implemented (`xrun-corr.sh`, `measure-soak.sh`, `bench-xruns.sh` uncovered)
5. §4 🔴5 — hand-written fixtures disagree with the emitter; cross-row field merge hides it
6. §4 🟡6 — physics assertions silently abstain (empty metrics, `jitter_n="?"`, `dsp_large<=0`, `meter_live` never passed in)
7. §4 🟡7 — gate swallows unittest output via `2>/dev/null`; misleading comment; `||` fallback double-runs suites
8. §4 🟡8 — `MPE_R_*` state leaks between `load_tag` calls (hardcoded unset list)
9. §4 🟡9 — everything uncommitted, no reviewable branch state, un-ignored 64 MB `.venv`
10. §4 🟢10 — doubled `temp=temp=` key, stale placeholder in deliverable, inverted `buffer_halving` test params, no `shellcheck`
11. §2 — architecture: library sourced inside `_run_window` instead of top-of-file; no emitter↔parser contract; two incompatible RESULT grammars (`measure-soak.sh` has no `tag=`)
12. §3 — dead code: `[ "${MPE_R_tag-}" = *"-b512-"* ]` doesn't glob in `[ ]`
13. §8 — documentation asserts things the code doesn't do (`meter_max_age_s` physics check, `window_align` negative control, "no direct python3" comment, V11 verdict provenance)
14. Verdict / priority backlog (5-item list, all 🔴)

---

## Claim Verification

### `native/mpe-xrun-probe.c` + `scripts/measure-latency-run.sh` — window alignment (🔴1)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | `PROBE_START` is flushed before `jack_set_xrun_callback`/`jack_activate`, so the window opens before the instrument is live | ✅ Confirmed (historical) / **now fixed** | The probe now emits a second sentinel: `fprintf(g_log, "PROBE_ACTIVE\n"); fflush(g_log);` immediately after `if (jack_activate(g_client) != 0) {...}` succeeds (`native/mpe-xrun-probe/mpe-xrun-probe.c:192-200`). The harness's wait loop was repointed: `grep -q '^PROBE_ACTIVE' "$xrun_events"` (`scripts/measure-latency-run.sh:411,415`), replacing the old `^PROBE_START` check. Diffed against `dev` — this is a real, targeted fix, not a doc-only change. |
| 2 | The meter baseline is captured before the probe is even spawned, compounding the misalignment | ✅ Confirmed — **still true, unfixed** | `scripts/measure-latency-run.sh:399-407`: `start_xr="$(_meter_xruns)"` still runs *before* `_start_xrun_probe`, and the comment (`# Sampler window: meter baseline before probe attach (V10-b misalignment fix)`) is unchanged from `dev`. So the xrun-count window still opens earlier than the DSP-sample window opens (which now correctly waits for `PROBE_ACTIVE`). |
| 3 | `window_align=1` is a string literal, not computed, and no assertion compares the two window boundaries | ✅ Confirmed — **still true, unfixed** | `scripts/measure-latency-run.sh:515`: `echo "RESULT tag=${tag} ... window_align=1"` — still a hardcoded `1`. No code anywhere computes a gap or timestamp. `mpe_result_require_fields` does not list `window_align`; `mpe_result_physics_assert` never reads `MPE_R_window_align`. |
| 4 | The deliverable doc reports this as resolved | ✅ Confirmed — **and now additionally stale** | `docs/measurements/instrument-conformance-c0-2026-08-22.md:52-56` still reads *"sample loop starts only after `PROBE_START` in probe log"* — this sentence was true of the pre-fix code and is **false of the current code**, which waits on `PROBE_ACTIVE`. The doc was not updated when the fix landed. |

**Net assessment:** the P0 fix closes the dangerous half of this finding — DSP sampling can no longer start before `jack_activate` succeeds, which was the part that could silently undercount load. But the specific claim in the priority backlog item #1 ("emit `PROBE_ACTIVE`... gate the sample loop on that... compute `window_align` from the measured gap... instead of stamping the literal `1`") is only the first third done. The meter-baseline-before-spawn ordering and the literal `window_align=1` are exactly as Grumpy found them. **This is a partial fix, not the fix described in the P0 summary as closing the finding.**

---

### `scripts/lib/measurement-result.sh` — `mpe_result_v11_recover` (🔴2)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | (a) `withhold` is initialized once and latches across rows, making output order-dependent | ✅ Confirmed (historical) / **now fixed** | `withhold=0` now sits inside the `while IFS= read -r line; do` loop body (`scripts/lib/measurement-result.sh:150`), before the `case`. Re-ran Grumpy's exact probe both orders: `bad-then-good` and `good-then-bad` now produce identical per-row verdicts regardless of order — `tag=bad-... dsp_withheld=1`, `tag=good-... dsp_withheld=0` in both cases. Verified live (see transcript below). |
| 2 | (b) missing input file → exit 0, no output | ✅ Confirmed (historical) / **now fixed** | Line 148: `[ -r "$file" ] || { _mpe_result_die "v11 recover: missing ${file}"; return 1; }`. Re-ran against a nonexistent file: `ERROR: measurement-result: v11 recover: missing /tmp/c0probe/nope.log`, `rc=1`. Also covered by a new test (`tests/test_instrument_conformance.sh:67-70`, "v11 missing file halts"). |
| 3 | (c) empty input (file exists, zero RESULT rows) → exit 0, zero bytes | ✅ Confirmed — **still true, unfixed** | Re-ran against a `0`-byte-content, readable file: `rc=0 bytes=0`. The function still ends with an unconditional `return 0` (line 172) regardless of how many rows it emitted. **This is SKILL.md anti-pattern #4 — "assert non-empty output" — and it is not covered by the "missing file fails" fix, nor by any new test.** No test in the suite exercises an empty-but-readable file. |
| 4 | (d) `${out:-/dev/stdout}` is not portable | ✅ Confirmed — **still true, unfixed** | Reproduced independently in this sandbox: `mpe_result_v11_recover file` (no `$2`) → `mpe_result_v11_recover:5: no such device or address: /dev/stdout`, and the function **still returns 0** even though the redirect failed. This is the same failure shape the whole doctrine exists to kill — a broken output path returning success. |

**Net assessment:** 2 of 4 sub-defects fixed and verified live; the sticky-withhold fix in particular is real and correctly closes the specific order-dependency bug (confirmed by direct reproduction, not just code reading). The empty-input and `/dev/stdout` sub-defects were not addressed and were not claimed as fixed in the P0 summary, so this is exactly as much progress as advertised — no over-claiming here.

---

### `scripts/measure-latency-run.sh` + `measurement-result.sh` — zero-sentinel / `dsp_median=0` (🔴3)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | `awk` prints `"0 0 0"` and exits 0 when every per-second DSP row is `?`, manufacturing a fake reading | ✅ Confirmed (historical) / **now fixed** | Diff vs `dev`: `- if (n==0) { print "0 0 0"; exit }` → `+ if (n==0) { exit 1 }`. The `read -r dsp_median dsp_p99 dsp_max < <(awk ...)` is now followed by `|| { echo "ERROR: no DSP samples in run file (dead jack_cpu_load path)" >&2; return 1; }` (new in diff). Confirmed this actually propagates: when `awk` exits 1 with no output, the process substitution feeds `read` nothing, `read` hits EOF and returns non-zero, and the `||` branch fires and halts the window. This is a correct, working fix, not just a code-shape change. |
| 2 | `mpe_result_require_fields` accepts `dsp_median=0` | ✅ Confirmed (historical) / **now fixed** | New check at `scripts/lib/measurement-result.sh:58-61`: `if [ "$f" = "dsp_median" ] && awk -v v="${!var}" "BEGIN{exit !(v+0==0)}"; then _mpe_result_die "field dsp_median=0 is not a measurement (sampler dead)"; return 1; fi`. Re-ran live: `MPE_R_dsp_median=0` → `ERROR: ... field dsp_median=0 is not a measurement (sampler dead)`, `rc=1`. Also tried `dsp_median=0.000000` — same rejection (the `v+0` numeric coercion catches both). |
| 3 | No test/fixture exercises this path | ⚠️ Partially True | No fixture with all-`?` DSP rows and no bash-conformance test call `mpe_result_require_fields ... dsp_median` with a literal `0` value exists (`tests/test_instrument_conformance.sh` has no such case — checked line-by-line). The fix is real and independently verified by direct function calls above, but it ships **without regression coverage**, exactly the gap Grumpy's fix recommendation named ("add a fixture whose per-second rows are all `?` and assert the gate halts"). If someone reverts the one-line `awk` change or the `require_fields` guard six months from now, nothing in the test suite will catch it. |

**Net assessment:** this is the most solidly closed of the four 🔴 findings — both halves verified by direct execution, not just code reading. The only gap is missing regression coverage, which is a real but lesser concern (Medium, not Critical) since the fix itself is simple and unlikely to regress silently in the near term.

---

### PROMPT-C0 Task 1 coverage (🔴4)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | `xrun-corr.sh` (occurrence #1, the founding instance) still writes to `~/xrun-corr.out` instead of stdout | ✅ Confirmed — **still true, unfixed** | `scripts/xrun-corr.sh:25`: `OUT=~/xrun-corr.out` — byte-identical to what Grumpy quoted. This was **not** in the P0 fix list and remains broken. |
| 2 | `xrun-corr.sh` has zero conformance coverage | ⚠️ Partially True — a fixture test was added, but it does not test the script | A new fixture `tests/fixtures/instrument-conformance/xrun-corr-good.out` and a check in `test_instrument_conformance.sh:73-81` (`grep -q '^TOTAL .* meter_live=1' ...`) now exist. But this is a hand-typed fixture, not an invocation of `scripts/xrun-corr.sh`. The founding bug (writes to a file, not stdout) is not exercised, let alone caught, by this test — the fixture would look identical whether `xrun-corr.sh` writes to stdout or `~/xrun-corr.out`, since the test never runs the script. Grumpy's core objection — "the tool named after the anti-pattern is still untouched" — stands. |
| 3 | `measure-soak.sh` has zero conformance coverage | ✅ Confirmed — **still true, unfixed** | `rg -l measure-soak tests/` → no matches. |
| 4 | `bench-xruns.sh` has zero conformance coverage | ✅ Confirmed — **still true, unfixed** (systemd unit test is not conformance) | Only reference is `tests/test_systemd_units.py`, which checks the unit file exists, not RESULT-line correctness — same as Grumpy found. |
| 5 | The deliverable's metric-inventory table implies full coverage without saying 3 of 4 instruments were skipped | ✅ Confirmed — **still true, unfixed** | `docs/measurements/instrument-conformance-c0-2026-08-22.md:30-39` — same six-row table, no "not covered" section added. |

**Net assessment:** the "xrun-corr fixture test added" item in the P0 summary is real but does not close the finding it's associated with — it adds a fixture-format check, not a fix to the broken script or a test that would catch the script staying broken. I'd flag this as the one place in the P0 summary where the language ("fixture test added") could be read as implying more progress than occurred.

---

### Fixture/emitter drift and cross-row merge (🔴5)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | The real emitter puts `window_align=1` on the primary DSP row (the one with `xruns=`) | ✅ Confirmed | `scripts/measure-latency-run.sh:515` — unchanged, `window_align=1` is on the same line as `xruns=${total_xr}`. |
| 2 | Hand-written fixtures put it elsewhere, in three different places, none of them row 1 | ⚠️ Partially True — **one of three fixtures was corrected since the review** | `tests/fixtures/instrument-conformance/good-512-a.log` now has `window_align=1` on line 6, the primary row with `xruns=2` — this **matches** the real emitter and differs from what Grumpy quoted (Grumpy cited `window_align` on line 11, a `file=`/`xrun_events=` row). Someone edited this fixture after the review. But `good-1024-b.log:2` (`samples=60 ... window_align=1`) and `physics-low-dsp-high-xr.log:2` (`samples=60 window_align=1`) are unchanged and still place it on a secondary row, not the primary one. So the drift is now 2-of-3, not 3-of-3, but the underlying claim ("fixtures disagree with the emitter") is still true for the majority. |
| 3 | `mpe_result_load_tag` merges fields across rows into one flat namespace, so a split-row input still satisfies `require_fields` | ✅ Confirmed — **still true, unfixed** | Reproduced live: fed `load_tag` a 3-line log where `xruns=`, `meter_live=`, and `dsp_median=`/`dsp_p99=`/`dsp_max=`/`samples=` were each on a separate row (none complete alone) — `mpe_result_load_tag` still returned success. `mpe_result_require_primary_row`, the fix Grumpy proposed, does not exist (`type` lookup: not found). |
| 4 | This is a literal re-run of the incident the doctrine exists to prevent | ✅ Confirmed, agree with severity | The mechanism is exactly as described: a future emitter change that drops a required field from the primary row and leaks it into a later row would pass silently, with no test catching it, because nothing validates row-1 completeness independent of the merge. |

**Net assessment:** not in the P0 fix list, and correctly not claimed as fixed. The one fixture correction (`good-512-a.log`) is incidental progress, not a structural fix — the class of bug it belongs to (fixtures hand-typed against a belief about the format, no mechanical tie to the emitter) is fully intact.

---

### Physics assertion abstention paths (🟡6)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | `mpe_result_physics_assert` passes when all metrics are empty/absent | ✅ Confirmed — **still true, unfixed** | Re-ran with every `MPE_R_*` var unset: `mpe_result_physics_assert 512` → returns 0 ("PASSED (empty metrics pass physics)"). Every check in the function is still guarded by `[ -n "$x" ]`. |
| 2 | `jitter_n="?"` slips through the `-lt 100` check because `2>/dev/null` hides the "integer expression expected" error and the `if` reads it as false | ✅ Confirmed — **still true, unfixed** | Reproduced: `MPE_R_jitter_n="?"` with `MPE_EXPECT_SAMPLES=60` → `mpe_result_physics_assert 512` returns 0. Code at line 89 unchanged: `if [ "$jitter_n" -lt 100 ] 2>/dev/null; then`. |
| 3 | `mpe_result_physics_buffer_halving` abstains (passes) when `dsp_large <= 0` | ✅ Confirmed — **still true, unfixed** | Reproduced: `mpe_result_physics_buffer_halving 0 5` → returns 0 (pass). Line 113: `if (a+0 <= 0) exit 1` inside the `awk` `BEGIN` block, which the caller reads as "condition not met" → pass, not halt. |
| 4 | The harness never passes `meter_live` into the assertion, so that branch is dead on the real path (though justified by an earlier explicit check) | ✅ Confirmed, and I agree with Grumpy's "fairness" caveat | `scripts/measure-latency-run.sh:503-508` sets `MPE_R_xruns`, `MPE_R_dsp_median`, `MPE_R_samples`, `MPE_R_jitter_n`, `MPE_R_tag` — no `MPE_R_meter_live`. `mpe_meter_assert_live` is called and halts first at line 496, so this is genuinely belt-and-braces, not a live gap — but it does read as coverage the test doesn't actually exercise on the real path. |

**Net assessment:** none of this was in the P0 list, and none of it has changed. All four sub-claims reproduce exactly as described.

---

### Gate silences a third of its own output (🟡7)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | The comment "no direct python3 discover — project rule" is misleading; the next lines call `python3 -m unittest` directly | ✅ Confirmed — **still true, unfixed** | `scripts/instrument-conformance.sh:13-19` byte-identical to what Grumpy quoted. |
| 2 | `2>/dev/null` discards all output from the python suites because `unittest -q` writes to stderr | ✅ Confirmed — **still true, unfixed** | Re-ran the full gate: `bash scripts/instrument-conformance.sh` produced 13 `OK:` lines from the two bash suites and **zero** lines attributable to `tests.test_audio_engine` / `tests.test_periodic_loop_lint`, then `conformance wall_time_s=6` / `SENTINEL conformance-pass`. Exact match to Grumpy's transcript. |
| 3 | The `||` fallback structurally can convert a venv-specific failure into a silent pass, though it doesn't currently mask anything | ✅ Confirmed, agree with Grumpy's own caveat | Code unchanged; `.venv/bin/python` is a symlink to `python3` in this environment (`ls -la .venv/bin/python` → `-> python3`), so both branches run the identical interpreter here — not a live masking risk in this environment specifically, but the structural hazard is real for any environment where the venv interpreter differs. |

**Net assessment:** entirely unaddressed, correctly not in the P0 list.

---

### `MPE_R_*` state leak (🟡8)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | `mpe_result_load_tag` unsets a hardcoded nine-field allowlist; anything else persists across calls | ✅ Confirmed — **still true, unfixed** | `scripts/lib/measurement-result.sh:129-131` — same fixed `unset` list. No `compgen -v MPE_R_` or equivalent dynamic sweep. |

**Net assessment:** unaddressed, correctly not in the P0 list. (Not independently re-reproduced with a two-file sequence in this audit beyond confirming the code is unchanged, since the mechanism is a straightforward reading of a fixed unset-list — the earlier attempt with mismatched fixture tags in this audit didn't reproduce the exact scenario, but the code path is unchanged from what Grumpy already demonstrated working.)

---

### Uncommitted / no reviewable branch state (🟡9)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | `yolo/instrument-conformance-c0` is byte-identical to `dev`; all changes are working-tree state | ⚠️ Partially True — still uncommitted, but P0 fixes are additional working-tree changes on top | `git status` shows the same files still modified/untracked, now with the P0 fixes layered in as further uncommitted edits. Nothing has been committed. `git diff --stat dev...HEAD` would still show empty since `HEAD` hasn't moved. |
| 2 | The un-ignored 64 MB `.venv` compounds the risk of the first commit | ✅ Confirmed — **still true, unfixed** | `du -sh .venv` → `64M`; `git check-ignore -v .venv/` → exit 1 (not ignored); `rg -n venv .gitignore` → no match. Identical to Grumpy's finding. |

**Net assessment:** unaddressed. This is worth flagging explicitly because the P0 fixes make the eventual first commit *larger and riskier*, not smaller — more uncommitted logic now sits in the working tree with no `.gitignore` guard against sweeping `.venv` into it.

---

### Minor findings (🟢10)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | Doubled key prefix: `temp="$(vcgencmd measure_temp ...)"` already returns `temp=54.0'C`, so the RESULT line emits `temp=temp=54.0'C` | ❌ **Incorrect as stated about the emitter** — real defect is in the fixture, not the code | Traced the full chain: `scripts/measure-latency-run.sh:493` sets `temp="$(vcgencmd measure_temp 2>/dev/null || echo 'temp=unknown')"`, and the emission line (`:519`, unchanged from `dev`) is `echo "RESULT tag=${tag} samples=${samples} ${temp} ${throttle}"` — it interpolates `${temp}` **once**, with no extra `temp=` prefix added by the emitter in either the live-`vcgencmd` or fallback case. Diffed against `dev`: this exact line is untouched by the C0 changeset. The `temp=temp=54.0'C` string exists **only** in the hand-typed fixtures (`good-512-a.log:10`, `good-1024-b.log:2`), which someone apparently wrote with an extra `temp=` under the mistaken belief the code needed one. This is real evidence *for* finding 🔴5 (fixtures invented independent of the emitter) but is not, itself, an emitter bug as Grumpy states it. |
| 2 | Placeholder left in the deliverable ("recorded at end of this doc after test run") | 🔍 Can't Verify precisely | Did not re-check the exact line in this pass (out of scope for the fixed-item re-verification); flagging as carried over unless someone confirms otherwise. |
| 3 | `mpe_result_physics_buffer_halving 19.14 38.52` inverts the documented `dsp_large, dsp_small` parameter order | ✅ Confirmed — **still true, unfixed** | `tests/test_instrument_conformance.sh:53` — identical call, identical comment ("plausible DSP increase passes"), params still in ascending order against a `dsp_large, dsp_small` signature. |
| 4 | No `shellcheck` in the gate; not installed here either | ✅ Confirmed | `which shellcheck` → not found, exit 1, in this environment too. |

---

## Severity Re-Assessment

| # | Issue | Reviewer Rating | My Rating | Delta | Reasoning |
|---|-------|----------------|-----------|-------|-----------|
| 1 | Window alignment (🔴1) | Critical | **High** (was Critical) | ↓ | The dangerous half — DSP sampling starting before the instrument is live — is genuinely closed. What remains (literal `window_align=1`, meter-baseline-before-spawn) is a real but narrower gap: it no longer manufactures false liveness, it just fails to *prove* alignment. Still High because the deliverable doc now makes a claim ("starts only after `PROBE_START`") that is stale relative to the actual fixed code, which is a new, small but real accuracy problem layered on top. |
| 2 | V11 recovery (🔴2) | Critical | **Medium** (was Critical) | ↓↓ | The two sub-defects that actually changed historical numbers (sticky withhold, silent success on missing file) are fixed and independently reproduced. The remaining two (empty input, `/dev/stdout` portability) are real but lower-consequence — empty input producing zero rows is easy to notice downstream even without a hard halt, unlike the sticky-withhold bug which silently corrupted specific rows' verdicts. |
| 3 | Zero-sentinel path (🔴3) | Critical | **Low** (was Critical) | ↓↓↓ | Fully and correctly fixed, verified by direct execution at both the harness (`awk exit 1`) and library (`require_fields` rejects `0`) layers. Residual severity is only "no regression test," which I rate Low-Medium, not Critical. |
| 4 | Task 1 coverage (🔴4) | Critical | **High** (unchanged) | — | `xrun-corr.sh` — occurrence #1, the tool the whole doctrine is named after — is still broken and still untested against its real behavior. The new fixture test creates a false impression of coverage without closing the gap; if anything this raises the risk slightly, because a reviewer skimming test names now sees "xrun-corr" pass and may assume the script is fixed. |
| 5 | Fixture/emitter drift (🔴5) | Critical | **High** (unchanged) | — | Structural gap fully intact: cross-row merge still lets an incomplete primary row pass. This is the exact failure class ("dsp_med incident with the detector installed") the changeset was built to prevent, and the detector still doesn't detect it. |
| 6 | Physics abstention (🟡6) | Medium (implied by 🟡) | **Medium** (unchanged) | — | Agree with Grumpy's tier. None of the four sub-issues touch data currently in production use (Track A is HALTED), so this is real debt, not an active data-integrity emergency — but it should not survive to when A1–A4 resume. |
| 7 | Gate silences unittest output (🟡7) | Medium | **Medium** (unchanged) | — | Agree. Not masking a real failure today (venv python == system python here), but the structural risk is real and the fix is a five-minute change. |
| 8 | State leak (🟡8) | Medium | **Low-Medium** | ↓ (slightly) | Only matters for multi-tag sequences within one process; the existing harness invocation pattern (one `_run_window` call sourcing fresh each time, one tag) makes this latent rather than active on the primary path. Still worth fixing since the test-suite tell (hand `unset` workaround) shows it already bit someone once. |
| 9 | Uncommitted state + `.venv` hygiene (🟡9) | Medium | **Medium-High** | ↑ (slightly) | Raising this because the P0 fixes made the uncommitted diff *larger*, and the `.venv` hygiene gap is a one-line, zero-risk fix (`echo '.venv/' >> .gitignore`) that has now been available to fix across two review cycles and still hasn't been. |
| 10 | Doubled `temp=` prefix | Low (🟢) | **Negligible / Misattributed** | ↓ | See Claim Verification above — this is a fixture-authoring artifact, not an emitter defect. Recommend Grumpy's finding be corrected in future passes, though it doesn't change the overall picture since 🔴5 already covers fixture/emitter drift generally. |

---

## What the Review Missed

**1. The deliverable doc is now stale in a way it wasn't before the P0 fixes — this is new and Grumpy could not have caught it.** `docs/measurements/instrument-conformance-c0-2026-08-22.md:52-56` still says the sample loop "starts only after `PROBE_START` in probe log." That was accurate when Grumpy reviewed it. It is **inaccurate now** — the code was changed to gate on `PROBE_ACTIVE`. Nobody updated the doc when the fix landed. This is exactly the failure mode §8 of the original review is about (documentation that's "lying in specific, findable places"), and it's a fresh instance introduced by the very fix that was supposed to help. Anyone reading the deliverable today gets a description of a sentinel name that no longer exists in the harness's wait loop.

**2. `Documents/PROGRESS.md` still lists C0 as "in progress" and the HALTED banner is unchanged, even though the gate has run green multiple times during this audit (`SENTINEL conformance-pass`, exit 0).** Grumpy flagged this as friction in §8 but it's worth restating in the audit because it directly affects whether the next agent believes A1 is unblocked — and per the P0 fix note, real progress (3 of 5 backlog items partially or fully addressed) has happened since PROGRESS.md was last touched, but the document doesn't reflect any of it.

**3. The `good-512-a.log` fixture edit is undocumented and partially inconsistent with itself.** Someone moved `window_align=1` from a secondary row to the primary row in this one fixture (matching the real emitter) but left the other two fixtures (`good-1024-b.log`, `physics-low-dsp-high-xr.log`) with `window_align` on a secondary row. This inconsistency isn't called out anywhere — a future reader diffing the three fixtures would reasonably wonder whether the placement is meaningful (e.g., some rows genuinely lack it) or accidental. Given `require_fields` doesn't check `window_align`, this asymmetry currently has no test consequence, but it's a discoverability trap for the next person extending the test.

**4. No test proves the gate itself can fail end-to-end.** Grumpy noted this in §6 ("Missing category") but it's worth elevating: with three of five 🔴 items now fixed, this becomes more urgent, not less — every fix that lacks a regression test (🔴3's `dsp_median=0` path, 🔴2's sticky-withhold fix) is one `git revert` or one well-intentioned refactor away from silently regressing, and there is no mechanism (a deliberately-broken-fixture self-test) that would catch it.

**5. The parser's unquoted `for tok in $(echo "$line" | sed ...)` (§7, "loose but not exploitable") is unchanged and now has more call sites** — `mpe_result_v11_recover` reads full lines with `read -r` (safe), but `mpe_result_parse_line`, called from `mpe_result_load_tag` and directly from the test file, still tokenizes via unquoted command substitution. Confirmed unchanged at `scripts/lib/measurement-result.sh:32`. Agree with Grumpy this is a robustness issue, not a vulnerability, given self-generated inputs only.

I did not find any missed security, auth, or data-loss issues beyond what Grumpy already covered — this remains a correctly-scoped "no attacker-controlled input" assessment.

---

## What the Review Got Right (And Why It Matters)

**The sticky-withhold bug (🔴2a) was the sharpest catch in the review**, and it's now confirmed fixed by direct reproduction, not just code reading. Before the fix, `PROGRESS.md`'s "DSP withheld" conclusion for specific historical runs was dependent on row order in the source log — a purely incidental property of how `awk`/`grep` happened to emit lines, unrelated to whether the DSP reading was actually bad. Now that `withhold=0` resets per row, `PROGRESS.md`'s existing V11 conclusions should be **re-derived**, exactly as backlog item #2 says, because the tool that produced them was non-deterministic with respect to input ordering until this fix landed. This should happen even though the fix is done, because the *conclusions on record* were generated by the broken tool.

**The zero-sentinel / `dsp_median=0` chain (🔴3) is the review's second-best catch and is now the most solidly closed.** This is the one place I'd call the P0 fix unambiguously complete at the code level — both the harness-side `awk exit 1` and the library-side `require_fields` numeric check independently reject the same failure mode, which is good defense in depth (either one alone would have sufficed, but having both means a future harness that forgets to check `n==0` is still caught by `require_fields`, and vice versa). The only real gap is missing regression tests, which is a Medium finding, not a reason to distrust the fix itself.

**The distinction between "occurrence #1 is untouched" (🔴4) and "a fixture test exists" is exactly right and matters more with the P0 fixes than it did before.** Now that three other 🔴 items show real, verified progress, it becomes easy to read the overall changeset as "mostly fixed." Grumpy's insistence on checking whether the gate would actually catch the founding incident — and finding that it wouldn't, because the new test never invokes the real script — is the single most important discipline in the whole review, and it holds up entirely under re-verification.

---

## Prioritized Action Matrix

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

## Disagreements and Judgment Calls

**1. Disagree with the "doubled key prefix" finding as stated.** Grumpy attributes the `temp=temp=54.0'C` string to the emitter ("line 517 emits temp=temp=..."). Traced the full call chain against both the current code and the unmodified `dev` baseline: the emitter has never done this. The doubling exists only in the hand-typed fixtures. This doesn't change the bottom-line risk (fixture/emitter drift is already 🔴5, a Critical/High finding), but it should be corrected for accuracy — citing this as an emitter bug in a future report would send someone hunting through `measure-latency-run.sh` for a defect that isn't there.

**2. Disagree with folding the "xrun-corr fixture test added" work into implicit credit toward closing 🔴4.** The task brief describes this fix as part of the P0 remediation batch, and it is real, additive test infrastructure — but it tests a fixture file, not `scripts/xrun-corr.sh`. I'd push back on any framing that treats this as progress on the actual defect (the script still writes to `~/xrun-corr.out`). It's better understood as scaffolding for a future real fix than as a partial fix itself.

**3. Agree with Grumpy's overall verdict structure (fix the five 🔴 items, none is large) but the "none is large" framing needs updating.** Three of five are now smaller than they were (🔴2, 🔴3 substantially closed; 🔴1 partially closed), but 🔴4 and 🔴5 are exactly as large as before — 🔴5 in particular (bind fixtures to the emitter mechanically, add primary-row validation) is a "half-day" item that touches the core parsing contract, not a "quick fix." I'd recommend the next iteration's priority backlog rewrite items 4 and 5 with updated scope now that 1–3 have partial code behind them, rather than repeating the original five verbatim.

**4. Push back gently on treating "P0 fixes applied" as closing the corresponding Grumpy findings 1:1.** Of the four bullet points in the fix summary, three (`v11_recover` fixes, `dsp n==0`/`require_fields`) map cleanly onto verified, complete-enough fixes. The fourth ("PROBE_ACTIVE... harness waits for PROBE_ACTIVE") accurately describes what was done, but the finding it's meant to close (🔴1, "window_align=1 certifies an alignment the code never establishes") is not fully closed by that change alone — the literal `window_align=1` and the pre-spawn meter baseline are exactly the parts of 🔴1 that make it "certify an alignment," and neither changed. I'd recommend the fix summary be worded as "🔴1 partially addressed" rather than implied-fixed, to avoid the deliverable doc's own failure mode (claiming more than the code does) recurring at the fix-tracking level.

**5. Do not agree that Track A should stay HALTED purely on tooling completeness grounds once P1 items are done.** Grumpy's verdict says not to lift the HALTED banner "on the strength of the current green, because the current green is partly the fixtures agreeing with themselves." I'd sharpen this: the banner's stated release condition is "exits 0 on the branch under test," which is now satisfied and was already satisfied at review time. If the actual gating criterion is "the five 🔴 items are closed," `Documents/PROGRESS.md` should say that explicitly rather than leave the release condition and the actual bar out of sync — that ambiguity is itself a documentation defect on top of the ones already catalogued.

---

## Summary for reporting

- **P0 count:** 4
- **P1 count:** 6
- **Artifact path:** `/home/claude-sandbox/workspace/MPE-Module/Documents/reviews/review-audit-instrument-conformance-c0-cycle1-2026-08-22.md`
