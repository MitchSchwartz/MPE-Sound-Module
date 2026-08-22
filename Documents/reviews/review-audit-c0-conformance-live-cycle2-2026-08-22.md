# Review Audit — C0 conformance, live half (`yolo/c0-conformance-live`) — Cycle 2

*Audited: 2026-08-22 17:07 EDT (America/Toronto)*

**Auditing:** [`grumpy-review-c0-conformance-live-2026-08-22.md`](grumpy-review-c0-conformance-live-2026-08-22.md)
**Against:** working tree on `yolo/c0-conformance-live`, HEAD `9581825` + uncommitted changes (per `git status` — same state Grumpy reviewed; nothing has been committed or fixed since).

**Method:** `Read` was blocked for every path this session by the same malfunctioning
`agentjail-hook` Grumpy hit (invalid hook response, not a policy denial) — file contents were
obtained via `Grep`/`cat -n`/`sed -n` through the `Shell` tool instead. Every claim below was
checked against the actual file content, not against Grumpy's quotes. Where a claim was
runnable, I ran it — 22 direct probe reproductions plus a full replay of both new test files,
the orphaned test file, and the full `tests/test_*.sh` set under the exact shell invocation
GitHub Actions uses by default. No product code was modified.

---

## Work Queue

Claims extracted from the Grumpy review, grouped by file/component:

**`scripts/lib/measurement-result.sh`** — physics threshold tables not monotone/uncited (Arch §2); library sets `set -uo pipefail` at source time (Arch §2); unquoted `for tok in $(...)` word-split hazard (Code Quality §3); S2 (buffer-unresolvable → silent pass); S3 (floor bypassed by `samples<30` or absent); S7 (non-numeric `xruns` bypasses physics); S8 (F5 jitter guard inert without `MPE_EXPECT_SAMPLES`); S10 (negatives assert exit status only, `MPE_EXPECT_SAMPLES=60` leaks); S11 (state leak between test blocks); dynamic `printf -v` var-name sanitization (Security, positive note); tag interpolated unescaped into regex/case (Security, hygiene note).

**`scripts/instrument-conformance.sh`** — nothing invokes the gate / nothing consumes the sentinel (Arch §2); `${1:-all}` ignores `$2`, silently lossy (Code Quality §3); S13 (venv test failure retried under system Python with stderr discarded); 15-minute budget checked after the fact, `WARNING` label on a hard `exit 1` (Perf §7).

**`scripts/measure-instrument-conformance-live.sh`** — S1 (DSP read after load killed/reaped); S5 (buffer is the configured value echoed, fail-open to the most permissive floor on resolution failure); S6 (load generator can die silently into `/dev/null`, diverges from `_as_user python3` convention used elsewhere); dead code (`"$dsp" = "?"` unreachable after the `grep -oP` extraction); comment/code drift (`15 s` comment vs. `20`-arg / `2s+8s` window); doc lying (`"under load"` error text when nothing is under load; docstring "for a loaded window" silently inapplicable under 30 samples).

**`tests/test_instrument_conformance_offline.sh`** — S9 ("all checks passed" printed with 4 more checks to run); S10 (harness path exercised for pass only, never for rejection — countered by Grumpy's own P11); S12 (asserts `grep`-on-source-text instead of behavior for `xrun-corr.sh`); coverage gaps (P2/P3/P5/P9 leaks not promoted to permanent tests); missing metrics/negative-controls per the prompt's Part 1/2b enumeration.

**`tests/test_instrument_conformance_live.sh`** — S15 (`MPE_METER_HARNESS_MAX_AGE_S=3` is a no-op, default is already 3); dead code (`fresh_meter` write immediately overwritten); log-honesty nit (`LIVE SKIP:` printed before `exit 1`); positive controls (2a) live only in the unexercised sibling script.

**`tests/test_instrument_conformance.sh`** (orphaned pre-split file) — "nothing references it," "dead file that will drift out of sync," Python discovery won't pick it up.

**Missing deliverables** — no `CONFORMANCE PASS`/`CONFORMANCE FAIL` string anywhere in the tree; no platform/kernel/JACK/Surge-revision/buffer provenance recorded; 9 of the prompt's enumerated metrics untested; 3 of the required 2b negative controls absent (non-existent patch, kill-jackd-mid-window, no-load DSP sample — and the last one is actively *committed*, not just absent).

**DX / hygiene** — `.venv/` untracked and not in `.gitignore`; no dry-run/`--self-test` mode for the live half; nothing here has been through CI (stated as a fact, not investigated further by Grumpy).

**Overall verdict / priority backlog** — six 🔴 items, "days not weeks," queue stays halted.

---

## Claim Verification

### `scripts/lib/measurement-result.sh`

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | Floor table (256/512/1024 → 3.0/5.0/2.0) is not monotone while the ceiling table (20/15/10) is | ✅ Confirmed | `mpe_result_dsp_plausibility_floor()` lines 26-34 vs. `_mpe_result_physics_low_dsp_ceiling()` lines 37-45, read directly — 512 (5.0) exceeds 256 (3.0), which is backwards given the prompt's own stated physics (line 108: DSP% rises as buffer shrinks). Neither function has a provenance comment. |
| 2 | `set -uo pipefail` at source time (line 6) mutates caller's options | ✅ Confirmed | Line 6 is exactly `set -uo pipefail`, at file (source) scope, not inside a function. |
| 3 | `for tok in $(echo "$line" \| sed ...)` is unquoted, word-split + glob hazard | ✅ Confirmed | Line 73, verbatim. Correctness currently depends on no file in `$PWD` matching a token like `xruns=*` — a real, if currently latent, bug. |
| 4 | S2 — unresolvable buffer makes physics silently return 0 | ✅ Confirmed, reproduced | Lines 151-162: `buf="$(_mpe_result_resolve_buffer ... \|\| true)"` then `if [ -n "$buf" ]; then …assert…; fi`, falls through to `return 0`. Reproduced directly: `MPE_R_tag=no-buffer-tag MPE_R_xruns=23 MPE_R_dsp_median=1.6` → `mpe_result_physics_assert ""` → **exit 0**. |
| 5 | S3 — floor requires `samples>=30`; `samples=12` or absent bypasses it | ✅ Confirmed, reproduced | Line 106: four-way `&&` including `[ "${MPE_R_samples}" -ge 30 ]`. Reproduced both P2 (`samples=12`, `dsp_median=0.9` at 256) and P3 (`samples` unset) → both **exit 0** through `mpe_result_require_fields dsp_median`. |
| 6 | S7 — non-numeric `xruns` disables the physics rule via `awk` coercion to 0 | ✅ Confirmed, reproduced | `mpe_result_require_fields` (line 95) only special-cases `?`/`unknown`; `awk -v x=$xr ... x+0` at line 157 turns anything else into 0. Reproduced P4: `MPE_R_xruns=garbage MPE_R_dsp_median=1.0` → `mpe_result_physics_assert 256` → **exit 0**. |
| 7 | S8 — F5's `jitter_n` numeric guard is nested inside `MPE_EXPECT_SAMPLES -ge 30` | ✅ Confirmed, reproduced | Lines 140-149, verbatim. Reproduced P5: unset `MPE_EXPECT_SAMPLES`, `MPE_R_jitter_n=not-a-number` → `mpe_result_physics_assert 256` → **exit 0**, no error. |
| 8 | S10 — every negative discards stderr, checks exit status only; `MPE_EXPECT_SAMPLES=60` set once (offline test line 14) and never unset, so later negatives may be passing on the samples-mismatch guard instead of the intended rule | ✅ Confirmed | Read the full offline test file: `MPE_EXPECT_SAMPLES=60` at line 14, no `unset`/reassignment anywhere in the file. Every `if mpe_result_..._assert ... 2>/dev/null; then fail; fi` pattern discards stderr. |
| 9 | S11 — `load_tag` clears `MPE_R_*`, `parse_line` does not; suite is order-dependent | ✅ Confirmed | `mpe_result_load_tag` lines 200-202 (`for v in $(compgen -v MPE_R_ ...); do unset "$v"; done`); `mpe_result_parse_line` (lines 58-84) has no such clearing. |
| 10 | Security: `printf -v "MPE_R_${key}"` is sanitized first and correctly placed | ✅ Confirmed | Line 78 `key="${key//[^a-zA-Z0-9_]/_}"` precedes line 79 `printf -v "MPE_R_${key}" '%s' "$val"`. |
| 11 | Security: tag interpolated unescaped into regex/case at lines 203/208/215/219 | ✅ Confirmed | `mpe_result_load_tag` — `grep -qE "^RESULT tag=${tag} "` (203), `grep -E "^RESULT tag=${tag} xruns="` (208), `case ... RESULT\ tag=${tag}\ samples=*` (215), `grep -E "^RESULT tag=${tag} "` (219) — all unescaped. |

### `scripts/instrument-conformance.sh`

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 12 | Nothing invokes the gate; nothing consumes the sentinel | ✅ Confirmed, and I found the same plus a related gap Grumpy didn't check (see "What the Review Missed") | Repeated the grep across `scripts/yolo/`, `.claude/`, `.github/` for `instrument-conformance` and `conformance-pass`/`CONFORMANCE` — no hits anywhere in the tree consume `SENTINEL conformance-pass`. |
| 13 | `${1:-all}` ignores `$2`; `--offline --live` runs offline only and reports a pass | ✅ Confirmed, reproduced | Line 12 `MODE="${1:-all}"`; `case "$MODE"` never inspects further args. Ran `bash scripts/instrument-conformance.sh --offline --live` live: exits 0, prints `SENTINEL conformance-pass mode=--offline`. |
| 14 | S13 — venv failure retried under system `python3` with `2>/dev/null` discarding unittest's stderr report | ✅ Confirmed | Lines 19-24, verbatim: `"${ROOT}/.venv/bin/python" -m unittest ... -q 2>/dev/null \|\| python3 -m unittest ... -q`. `unittest` does write its summary to stderr, so a genuine venv failure is invisible before the silent re-run. |
| 15 | 15-min budget enforced as a hard fail but checked after the fact; message says `WARNING` for an `exit 1` | ✅ Confirmed | Lines 56-61: `ELAPSED=$((SECONDS - START))` computed after both `run_offline`/`run_live` return, `echo "WARNING: ..." >&2; exit 1`. |

### `scripts/measure-instrument-conformance-live.sh`

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 16 | S1 — DSP is read via `jack_cpu_load` *after* the load PID is killed and waited on | ✅ Confirmed by code reading; not runnable here (no Pi/`jack_cpu_load`) — same limitation Grumpy stated | Lines 41-58, verbatim, unambiguous ordering: `kill "$load_pid"` (47) → `wait "$load_pid"` (48) → `dsp="$(... jack_cpu_load ...)"` (58). No branch reorders this. |
| 17 | S5 — `buf="$(mpe_jack_period ...)"` returns the configured value, not the running period; `\|\| echo 1024` selects the most permissive floor on failure | ✅ Confirmed | `mpe_jack_period()` in `audio-engine.sh:64-66` is `printf '%s' "$(mpe_buffer_env_canonical)"` — reads `MPE_JACK_BUFFER`/default, not jackd. `mpe_result_dsp_plausibility_floor(1024)` = `2.0`, the lowest of the three floors (3.0/5.0/2.0) — confirmed fail-open. `jack_bufsize` exists and is used elsewhere (`scripts/research/graph-recovery-spike.sh:39-40,105-114`, `scripts/sooperlooper/diagnose-16loop-crackle.sh:162`) and a proc-based period read-back exists in `measure-latency-run.sh` (`_jack_period_from_proc`/`_assert_jack_period`) — the repo already has the pattern this script skips. |
| 18 | S6 — `midi-load.py` invoked directly (not via `python3`), output to `/dev/null`, PID never checked for liveness; diverges from `_as_user python3 ...` used elsewhere | ✅ Confirmed | Line 41: `"${ROOT}/scripts/midi-load.py" 20 >/dev/null 2>&1 &`. Grepped all 4 other callers: `measure-latency-run.sh:733` and `measure-soak.sh:132` both use `_as_user python3 "$SCRIPT_DIR/midi-load.py" ... >"/tmp/....log" 2>&1 &` — log kept, `_as_user` used, `python3` explicit. No `kill -0` liveness check anywhere in the new script. |
| 19 | Dead code: `"$dsp" = "?"` unreachable given the `grep -oP '[0-9]+\.[0-9]+'` extraction | ✅ Confirmed | Line 58's regex can only emit digits and one dot; line 59's `"$dsp" = "?"` branch is unreachable as written. |
| 20 | Comment/code drift: `# Load positive: midi-load 15 s` vs. actual `20`-second arg and `sleep 2`+`sleep 8`=10 s window | ✅ Confirmed | Line 40 says `15 s`; line 41 passes `20`; lines 43/45 total `sleep 2` + `sleep 8` = 10 s measured window. Three different numbers. |
| 21 | Doc lying: error text says "under load" when the load has already been killed | ✅ Confirmed | Line 60's error string is `"jack_cpu_load returned no numeric DSP under load"`; at that point `kill`/`wait` on `load_pid` already executed (lines 47-48). |

### Test files

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 22 | S9 — "all checks passed" (offline, line 90) printed with 4 more checks (lines 92-118) still to run | ✅ Confirmed, reproduced | Read the file end-to-end: line 90 is the echo, lines 92-118 contain `window_align`, V11-good-row, non-sticky-withhold, and `xrun-corr` TOTAL checks. Ran the file directly — the `all checks passed` line appears mid-output, followed by four more `OK:` lines. |
| 23 | S10 — the harness entry point (`mpe_result_assert_tag`) is called exactly once, for a pass, and never proven to reject | ⚠️ Partially True | Grepped `assert_tag` across `tests/`: exactly one call site, `tests/test_instrument_conformance_offline.sh:15`, on the good fixture. Grumpy's own P11 probe (which I did not need to re-run, since it's consistent with the confirmed S2/S3 mechanics) demonstrates it *would* reject given a bad fixture — so the claim "never tested via `assert_tag`" is accurate as a statement about the *shipped suite*, but Grumpy's own report already supplies the missing negative proof outside the suite. The gap is real; calling it entirely untested undersells that Grumpy already closed the loop empirically. |
| 24 | S12 — `grep -q 'cat "$OUT"' .../xrun-corr.sh` asserts source text, not behavior | ✅ Confirmed | `tests/test_instrument_conformance_offline.sh:87`, verbatim. `xrun-corr.sh:70` currently is `cat "$OUT"` — the test would break on a behavior-preserving refactor (e.g. `tee`) and would not catch a regression that kept the literal string but broke stdout emission. |
| 25 | S15 — `MPE_METER_HARNESS_MAX_AGE_S=3` (live test, line 20) is a no-op; default already 3 | ✅ Confirmed | `audio-engine.sh:696`: `MPE_METER_HARNESS_MAX_AGE_S="${MPE_METER_HARNESS_MAX_AGE_S:-3}"`. Test line 20 sets the same value. Stale fixture offset is `EPOCHSECONDS - 120` (line 37 in current file — Grumpy's line 20/37 citations both check out), 40x the threshold. |
| 26 | Dead code: `fresh_meter "$f"` immediately overwritten by the next `printf` | ✅ Confirmed | Lines 36-37 exactly as quoted: `fresh_meter "$f"` then `printf 'xruns=0\nupdated=%s\n' ... >"$f"` on the very next line, same file, no read in between. |
| 27 | Log-honesty nit: `LIVE SKIP:` printed twice before `exit 1` | ✅ Confirmed | Lines 65-67, verbatim. |
| 28 | "Orphaned test" — `tests/test_instrument_conformance.sh` has nothing referencing it and is a dead file that will drift out of sync | ❌ Incorrect on the "nothing references it, dead" framing — real underlying risk, wrong mechanism | `instrument-conformance.sh` indeed no longer calls it. But CI's `.github/workflows/test.yml` `shell-tests` job runs `for t in tests/test_*.sh; do bash "$t"; done` — a **blanket glob that matches this file's name**. Ran it directly: `bash tests/test_instrument_conformance.sh` → 11/11 `OK`, `exit 0`, still fully alive in CI today. It is *not* dead code; it is untouched-but-executing, redundant coverage of the pre-split behavior that will silently pass or fail independently of the new offline suite, and nobody is looking at its output as meaningful signal. That is a real, if different, hazard from "dead file" — see disagreement below. |

### Missing deliverables / prompt requirements

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 29 | No `CONFORMANCE PASS`/`CONFORMANCE FAIL` string anywhere in the tree | ✅ Confirmed | Grepped the whole repo for `CONFORMANCE` (case-sensitive) — zero hits in any script; only appears in the prompt/review docs themselves. |
| 30 | No platform/kernel/JACK/Surge-revision/buffer provenance recorded | ✅ Confirmed | `instrument-conformance.sh` takes only `$1` (mode); neither it nor the live script writes any provenance line. |
| 31 | 9 of the prompt's enumerated metrics untested (`frames_late`, buffer fill, achieved clock, temp, `get_throttled`, voice count, patch identity, applied buffer/periods, applied governor) | ✅ Confirmed | Prompt lines 56-58 list all 12; grepped `require_fields`/assertions across `measurement-result.sh` and both new test files — only `xruns`, `dsp_median`, `dsp_p99`, `dsp_max`, `samples`, `jitter_n`, `meter_live`, `window_align` are ever asserted on. `frames_late_*` and `temp=`/`throttled=` are present in `good-512-a.log`/`good-1024-b.log` fixtures (confirmed by direct read) and touched by no assertion. |
| 32 | 3 of the required 2b negative controls absent, and one (no-load DSP sample) is actively committed rather than merely missing | ✅ Confirmed | Prompt lines 90-98 list 6 required 2b controls; cross-checked against both new test files — "non-existent patch → halt" and "kill jackd mid-window → invalid" have no corresponding code anywhere in the diff; "sample DSP with no load → detectably wrong" is claim #16 (S1) verbatim — the gate commits this exact scenario. |

---

## Severity Re-Assessment

| # | Issue | Reviewer Rating | My Rating | Delta | Reasoning |
|---|-------|------------------|-----------|-------|-----------|
| S1 | DSP sampled after load reaped | 🔴 Critical | **Critical** | — | Agree without reservation — this is the prompt's own named forbidden negative control, committed into the file whose entire job is to catch it. |
| S2+S3 | Physics/floor silently no-op on unresolvable buffer / short window | 🔴 Critical | **Critical** | — | Agree. Both are "unevaluatable → pass," the exact shape that hid F2, on the harness's real entry point (`assert_tag`), reproduced directly. |
| S4 | `xruns>0` under load never asserted | 🔴 Critical | **Critical** | — | Agree — this is the literal headline requirement of F1 (2a table, prompt line 76), computed and printed but not checked. |
| S5 | Buffer echoed, fail-open to most permissive floor | 🔴 Critical | **Critical** | — | Agree, and I'd underline the fail-open direction specifically: a buffer-resolution failure doesn't just skip the check, it *actively selects the weakest threshold in the table* (2.0 vs. 3.0/5.0). That's worse than a no-op. |
| S6 | Load generator can die silently | 🔴 Critical | **Critical** | — | Agree — named verbatim in AGENTS.md as the 382-pad-taps failure class this project exists to prevent, reproduced in miniature (no liveness check exists to even attempt). |
| S7 | Non-numeric `xruns` bypasses physics | 🟡 (implicit Medium) | **Medium-High** | ↑ (slight) | Reviewer notes it's "caught incidentally at 1.0% by the floor" — true, but only below the floor threshold; above it, a corrupted `xruns` field silently disables the one rule (buffer-halving/material-xruns physics) that would otherwise catch a bad reading. I'd nudge this up because it composes with S2/S3: a tag with no buffer token *and* non-numeric `xruns` fails two independent guards at once for free. |
| S8 | F5 guard inert without `MPE_EXPECT_SAMPLES>=30` | 🟡 (implicit Medium) | **High** | ↑ | Reviewer's own framing — "F5 was not closed; it moved" — is the strongest argument for a higher rating than the symbol implies. This is a checkbox in the prior review (F5) marked done that isn't; that's a process-trust issue, not just a code gap. |
| S9 | Misleading log ordering ("all checks passed" mid-run) | 🟢 | **Low** | — | Agree. Exit code is correct; only the log narrative is wrong. In a project whose entire premise is "instruments must not lie," it's worth the one-line fix, but the blast radius is a confused reader, not a wrong measurement. |
| S10 | Negatives check exit status only; `MPE_EXPECT_SAMPLES` leak | 🟡 (S10 header, unlabeled) | **Medium-High** | ↑ (slight) | Agree with the reviewer's own callout that "F2 hid for exactly this reason" — I'd make this explicit as a re-occurrence risk rather than a generic test-hygiene note, since it's the same failure mode that produced the original F2 defect, now reproduced structurally in this changeset's own tests. |
| S11 | State leak between blocks | 🟡 | **Low-Medium** | — | Agree — real, but the suite currently passes deterministically in its committed order; the risk is to future edits, not the present result. |
| S12 | Source-text assertion instead of behavior | 🟡 | **Medium** | ↑ (slight) | Agree with the mechanism, but I'd raise it slightly: this is specifically guarding the row-1 nine-failures incident (`xrun-corr.sh` writing to a file instead of stdout) and currently provides *zero* behavioral coverage of that incident — a regression there would sail through both this test and CI. |
| S13 | Venv retry masks failures with discarded stderr | 🟡 | **Medium** | — | Agree — currently inert in this sandbox (verified in cycle-1 audit that `.venv/bin/python` is a symlink to `python3` here), but the structural hazard is real wherever the venv interpreter diverges, and `2>/dev/null` on `unittest` specifically discards the one channel that carries the failure. |
| S14 | Idle control fails on single stray xrun | 🟡 | **Low** | — | Agree with reviewer's own framing ("defensible as doctrine"). |
| S15 | Staleness negative can't detect threshold regression | 🟢 | **Negligible** | — | Agree. |
| "Orphaned test" | `tests/test_instrument_conformance.sh` dead/orphaned | 🟡 | **Medium** (reframed) | ↑ (severity), ↓ (mechanism) | See disagreement below — not dead, actively running in CI today, which is arguably worse: redundant, aging coverage that nobody is watching, sitting silently alongside the "real" suite. |
| — | **CI breaks once this branch's new files are committed** | *(not raised)* | **Critical / P0** | **new** | See "What the Review Missed" below — the single highest-severity finding in this audit. |

---

## What the Review Missed

### 🔴 P0 (new) — Landing this branch as-is will permanently break the `shell-tests` CI job

Grumpy's review is scoped to the code and to what it could execute in a sandbox without a Pi. It never traced how `tests/test_*.sh` files are actually invoked in CI — and that trace surfaces the most severe finding in this audit.

`.github/workflows/test.yml`'s `shell-tests` job:

```yaml
- name: Run shell tests
  timeout-minutes: 5
  run: |
    for t in tests/test_*.sh; do
      bash "$t"
    done
```

GitHub Actions' default shell for a Linux `run:` step is `bash --noprofile --norc -eo pipefail {0}` — `-e` is set. I reproduced this exact invocation locally:

```
$ bash --noprofile --norc -eo pipefail -c '
for t in tests/test_*.sh; do
  echo "RUNNING $t"
  bash "$t" >/tmp/out.$$ 2>&1
  echo "  -> exit 0"
done
echo "ALL PASSED"
'
RUNNING tests/test_dac_volume.sh
  -> exit 0
RUNNING tests/test_gadget_persist.sh
  -> exit 0
RUNNING tests/test_instrument_conformance_live.sh
(script exits here — loop exit code: 1)
```

`tests/test_instrument_conformance_live.sh` exits 1 whenever it cannot reach `/run/mpe/meter.state` (lines 64-67) — which is **always true** on a `ubuntu-latest` GitHub-hosted runner, since there is no Pi and never will be. That is correct, doctrine-compliant behavior for a manual or Pi-deployed run (AGENTS.md: "refusing to fake a pass off-Pi is exactly the discipline"). It is fatal for CI: once this file is committed, `-e` aborts the loop at exactly this file, and every subsequent push or pull request to `dev`/`main` fails the `shell-tests` job before it even reaches `test_instrument_conformance_offline.sh`, the orphaned `test_instrument_conformance.sh`, `test_meter_harness.sh`, or `test_prepare_dsi_display.sh` — the whole job goes red, forever, for every future PR.

This is not a hypothetical: I confirmed the established repo convention is that **every** file currently matched by `tests/test_*.sh` exits 0 on a plain runner without a Pi — verified `test_dac_volume.sh`, `test_gadget_persist.sh`, `test_prepare_dsi_display.sh`, and `test_meter_harness.sh` all pass cleanly off-Pi. `test_instrument_conformance_live.sh` is the first file in the tree to break that convention, and it breaks it by design (its whole purpose is to refuse a fake pass).

**Why this matters more than S1-S6:** those six defeat the *new* gate's ability to do its job. This defeats the *existing, working* CI pipeline for the entire repository — every unrelated PR, from anyone, on any file, blocked on a shell-tests failure that has nothing to do with their change. It also means the offline suite's real, good work (F2/F3 properly closed, 17 tests passing) will never actually go green in CI either, because the job dies one file before it gets there.

*Fix:* Do not let a Pi-only positive-control test enter the blanket `tests/test_*.sh` CI glob. Either (a) exclude it explicitly in the workflow (`for t in tests/test_*.sh; do [ "$t" = "tests/test_instrument_conformance_live.sh" ] && continue; ...`), (b) rename it out of the `test_*.sh` convention the glob matches (e.g. `live-only-test_instrument_conformance.sh`, with the glob updated to exclude a `live-only-` prefix), or (c) give CI a synthetic `/run/mpe/meter.state` so the file's own negative-then-live-check structure can complete — but (c) risks quietly turning it into exactly the kind of fake pass AGENTS.md forbids, so (a) or (b) are safer. This needs to land in the same PR as the test file, not as a follow-up — it is a day-zero break, not a latent one.

### Other gaps

- **Grumpy's own probes (P2, P3, P4, P5, P9, P10) are entirely reproducible and I re-ran them independently** rather than trusting the transcripts — all matched exactly (see Claim Verification table). No fabricated evidence found anywhere in the review.
- **No security, auth, or input-validation issues beyond what Grumpy already flagged.** Confirmed no network calls, no untrusted external input in this diff — agree with Grumpy's "nothing here would keep me up."
- **No additional logic bugs found** in the core parsing/physics functions beyond S1-S15. I specifically checked for off-by-one and anchoring issues in the `grep -E "^RESULT tag=${tag} "` family (a concern given tag values like `run1` vs `run10`) — the trailing-space anchor in the pattern already prevents the `run1`/`run10` collision, so no additional bug there.

---

## What the Review Got Right (And Why It Matters)

**S1 (DSP-after-load-dead) is the single most important finding in the review, and its downstream consequence is exact, not general:** this is not "a bug in a test," it is the founding pathology of the whole C0 initiative — a broken instrument and a working one producing the same reading — reintroduced inside the script written specifically to detect that pathology. If this ships and a Pi run passes, the number that gets written into `PROGRESS.md` or a measurement doc for the Pi 4 → Pi 5 comparison C0 exists to anchor is exactly as unreliable as V11's `0.9%` — and nobody would know, because the gate that was supposed to say so passed.

**The S2/S3/S7/S8 "unevaluatable → pass" pattern is correctly identified as one shape, not four coincidences**, and Grumpy's proposed single fix (invert the default: unresolvable/unevaluatable is `VOID` via `_mpe_result_die`, never `return 0`) is the right level of fix — a local patch to each site would leave the pattern free to recur at the next metric someone adds. This compounds with S10: because every negative control in the new tests checks exit status only, a regression that turned one of these "return 0" paths into "return 0, always" (removing the guard's teeth entirely) would not be caught by the suite that exists to prevent regressions in the offline half.

**S5's fail-open direction is worth restating plainly:** a `jack_bufsize` read failure doesn't just skip the buffer-dependent floor, it *selects the 1024 floor (2.0), the lowest number in the table*, on the theory that no answer defaults to the most permissive one. Combined with S1 (load already dead) and S6 (load may never have started), a fully broken load-generation path can still produce a `dsp` reading that clears a 2.0% floor on pure hardware idle noise — this is the exact "confident wrong number" the founding table describes, assembled from three independent bugs each individually "just missing a check."

**The Test Strategy section's core argument — "the harness entry point is tested only for acceptance, never for rejection" — holds up under re-verification and matters specifically because of how F2 was hidden originally** (per the prior review, quoted verbatim in this diff's own test file). A suite that only exercises its happy path through the real entry point provides zero regression protection for exactly the class of defect this whole task exists to close.

---

## Prioritized Action Matrix

| Priority | Issue | Verdict | Effort | Depends On |
|----------|-------|---------|--------|------------|
| P0 | **CI breaks permanently once these files are committed** — `test_instrument_conformance_live.sh` exits 1 off-Pi inside the `-e` shell-tests glob | ✅ (new, this audit) | Quick fix | — |
| P0 | S1 — move the `jack_cpu_load` read inside the load window, before `kill`/`wait` | ✅ | Quick fix | — |
| P0 | S2+S3+S7+S8 — invert the "unevaluatable → pass" default to `_mpe_result_die` in all four sites | ✅ | Half-day | — |
| P0 | S4 — assert `end_load - start_load > 0` (or emit VOID and say so) | ✅ | Quick fix | — |
| P0 | S5 — use `jack_bufsize`/proc read-back for the applied buffer; delete `\|\| echo 1024` | ✅ | Half-day | Pattern exists in `measure-latency-run.sh` |
| P0 | S6 — invoke `midi-load.py` via `_as_user python3`, keep a log, assert `kill -0` after the window opens | ✅ | Quick fix | — |
| P1 | S10 + Test Strategy — route every negative through `mpe_result_assert_tag` against a fixture; assert on the stderr message, not exit status alone; promote P2/P3/P5/P9 to permanent tests; reset `MPE_EXPECT_SAMPLES` between blocks | ✅ | Half-day | P0 fixes above (assert_tag behavior changes once S2/S3 invert) |
| P1 | Missing deliverables — emit `CONFORMANCE PASS`/`CONFORMANCE FAIL`; record platform/kernel/JACK/Surge-revision/buffer provenance; wire the queue runner to require `mode=all` | ✅ | Multi-day | — |
| P1 | S11 — reset `MPE_R_*` between test blocks (`mpe_result_reset` helper) | ✅ | Quick fix | — |
| P2 | Missing metric coverage — add assertions for `frames_late`, temp/throttled, applied buffer/periods, governor, patch identity, voice count (9 of 12 prompt-enumerated metrics) | ✅ | Multi-day | — |
| P2 | Missing 2b controls — non-existent patch → halt; kill jackd mid-window → invalid | ✅ | Multi-day | — |
| P2 | Physics threshold tables — derive from measured V9/V11 bands, cite the run, make both monotone | ⚠️ | Half-day | Needs V9/V11 data reference, not just code |
| P2 | S13 — deterministic interpreter choice for the venv `unittest` call; stop discarding stderr | ✅ | Quick fix | — |
| P2 | S12 — replace `grep -q 'cat "$OUT"'` with an actual `xrun-corr.sh` invocation against a fixture | ✅ | Half-day | — |
| P3 | S9 — move "all checks passed" to the actual end of the offline test | ✅ | Quick fix | — |
| P3 | "Orphaned" `test_instrument_conformance.sh` — delete it or fold unique cases into `_offline`, and confirm the CI glob no longer double-covers this ground | ✅ (reframed) | Quick fix | Coordinate with P0 CI fix — same glob |
| P3 | Library scope pollution — move `set -uo pipefail` out of the sourced library into calling scripts | ✅ | Half-day (touches every caller) | — |
| P3 | S14 log clarity — log meter age/liveness alongside a spurious idle-xrun trip; note retry is the first step | ✅ | Quick fix | — |
| P3 | Unquoted `for tok in $(...)` — switch to `read -ra` or `set -f` | ✅ | Quick fix | — |
| P3 | Argument handling — validate `$2` in `instrument-conformance.sh`, reject unknown extra args | ✅ | Quick fix | — |
| P3 | `.venv/` — add to `.gitignore` before first commit | ✅ | Quick fix | — |
| P3 | `--self-test`/dry-run mode for the live script's instrument wiring (per AGENTS.md self-test doctrine) | ✅ | Half-day | — |
| P3 | S15 — add a boundary test at `MPE_METER_HARNESS_MAX_AGE_S ± 1s` | ✅ | Quick fix | — |
| P3 | Log-honesty — relabel `LIVE SKIP:` before an `exit 1` as `LIVE FAIL:` | ✅ | Quick fix | — |
| P3 | Dead code cleanup — unreachable `"$dsp" = "?"` branch; `fresh_meter`-then-overwrite in the live test; comment drift on `15 s`/`20`/`10 s` | ✅ | Quick fix | — |

---

## Disagreements and Judgment Calls

**1. Disagree with characterizing `tests/test_instrument_conformance.sh` as "dead"/"orphaned"/"nothing references it."** It is currently executed by CI's `shell-tests` job (`for t in tests/test_*.sh`) and passes, 11/11, exit 0 — I ran it directly to confirm. The accurate framing is: *not referenced by the new gate script, but still executed by the blanket CI glob*, providing redundant coverage of the pre-split behavior that nobody is treating as meaningful signal and that will silently rot in parallel with the "real" offline suite. That's a different (and arguably worse — a false sense of "it's covered" plus wasted CI time) hazard than a file that never runs at all, and it's directly coupled to the P0 CI finding above: both are consequences of the same blanket-glob mechanism, and the same workflow edit that fixes the P0 should account for this file too (delete it, per Grumpy's own recommendation, rather than leaving it to keep executing unnoticed).

**2. Partially disagree with S10's framing that the harness entry point is "never tested via `assert_tag`" for rejection.** Technically accurate about the *shipped test suite* — but Grumpy's own P11 probe in the same review already demonstrates, empirically, that `assert_tag` does reject the known-bad fixture. I'd separate these into two distinct, differently-urgent claims: (a) the suite as committed has no permanent regression test proving this (real gap, P1, as rated), and (b) the mechanism itself currently works (already proven, no action needed beyond (a)). Folding both into one "never proven to reject" statement risks a reader assuming the rejection path is unverified in general, when Grumpy has already verified it — only the *permanent test coverage* of it is missing.

**3. Agree with the overall "days not weeks" framing, but would sequence the CI fix ahead of everything else in the backlog.** Grumpy's priority list (S1 → S2/S3 → S4 → S5/S6 → S10 → deliverables) is a reasonable order for *code correctness*, but none of it matters to the rest of the team if the `shell-tests` CI job goes red on merge and stays red — that failure has zero relationship to whether S1-S6 get fixed, and blocks unrelated work immediately. I'd put the CI exclusion as literally the first line of the first commit in this changeset, ahead of even S1.

**4. Agree with, and would not soften, Grumpy's core verdict** that the live half is "not a gate yet" and the queue should stay halted. Six 🔴s that each independently defeat the negative control they were written to implement, discovered via four two-line fixtures and one code read with no cleverness required, is a real and serious gap — not a nitpick pass. Nothing in this audit found the review to be overstated anywhere; if anything (S8, S10, the orphaned-test mechanism, and the CI finding), the accurate picture is slightly worse than what Grumpy reported, not better.

**5. No disagreement found with any Confirmed verdict in the Claim Verification tables above.** Every code quote, line number, and probe result I re-checked matched the review exactly — this is an unusually accurate review for a document of this length and technical density.

---

## Summary

- **P0 count: 6** (5 from Grumpy's own 🔴 backlog — S1, S2+S3, S4, S5+S6 — plus 1 new: the CI breakage this audit found)
- **P1 count: 3** (S10/Test-Strategy consolidation, missing deliverables, S11)
- **Artifact audited:** [`Documents/reviews/grumpy-review-c0-conformance-live-2026-08-22.md`](grumpy-review-c0-conformance-live-2026-08-22.md)
- **This audit:** [`Documents/reviews/review-audit-c0-conformance-live-cycle2-2026-08-22.md`](review-audit-c0-conformance-live-cycle2-2026-08-22.md)

**Bottom line:** the Grumpy review is highly accurate — every reproducible claim reproduced exactly as described, every code citation matched the actual file, and no fabricated findings were found. The one material gap is scope, not correctness: the review never traced how `tests/test_*.sh` gets invoked in CI, and that trace turns up a P0 that outranks everything else in the backlog — this branch's two new files, committed as-is, will fail the `shell-tests` job on every future push to `dev`/`main`, independent of whether S1-S6 are fixed. Fix the CI glob exclusion alongside the six 🔴 code fixes before this is called done.
