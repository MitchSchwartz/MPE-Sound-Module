# Review loop index — c0-conformance-live

| Cycle | Grumpy | Audit | Fixes |
|-------|--------|-------|-------|
| 1 | (pre-merge, instrument-conformance-c0) | review-audit-instrument-conformance-c0-cycle1-2026-08-22.md | PR #96 merged |
| 2 | grumpy-review-c0-conformance-live-2026-08-22.md | review-audit-c0-conformance-live-cycle2-2026-08-22.md | F2–F5 strict parsers; --offline/--live split; live harness S1–S6; CI-safe negative controls |

**Status:** Offline half green. Live half requires Pi soak (`measure-instrument-conformance-live.sh`). Track A remains **HALTED** until full `--live` passes on appliance.

---

## Cycle findings (carried in 2026-09-23 before the cut)

### `grumpy-review-c0-conformance-live-2026-08-22.md`


1. **🔴 S1 — Move the `jack_cpu_load` read inside the load window** (`measure-instrument-conformance-live.sh:47-58`). The gate currently commits the prompt's own forbidden negative control and publishes the number.
2. **🔴 S2 + S3 — Make unevaluatable checks VOID, never pass.** An unresolvable buffer and a sub-30-sample window must `_mpe_result_die`, not `return 0`. Two proven leaks (P7, P9) accept the V11 signature through `mpe_result_assert_tag` today; fold S7 and S8 in with the same inversion.
3. **🔴 S4 — Assert `xruns > 0` under forced load.** Both ends or neither; this is the outstanding half of F1.
4. **🔴 S5 + S6 — Trust nothing you did not read back.** Use `jack_bufsize` for the applied buffer (delete `|| echo 1024`), and invoke `midi-load.py` as every other harness does, with a log and a `kill -0` liveness assert after the window opens.
5. **🔴/🟡 S10 + §6 — Make the tests capable of catching the next F2.** Route every negative control through `mpe_result_assert_tag` against a fixture, assert on the stderr message rather than exit status alone, and add P2/P3/P5/P9 as permanent cases.
6. **🟡 Emit the specified contract.** `CONFORMANCE PASS` / `CONFORMANCE FAIL` plus platform, kernel, JACK, Surge revision, and applied buffer/periods — without which a C0 pass cannot serve the Pi 4 → Pi 5 comparison it was built for. Then wire the queue runner to require `mode=all`.

---

**Reviewer note on method:** `Read` was blocked for every path this session by a malfunctioning
`agentjail-hook` (invalid hook response, not a policy denial), so file contents were obtained via
`Grep`. Blank lines are therefore absent from what I read; line numbers cited are real and were
cross-checked against the executed suite. No product code was modified.

### `review-audit-c0-conformance-live-cycle2-2026-08-22.md`


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

