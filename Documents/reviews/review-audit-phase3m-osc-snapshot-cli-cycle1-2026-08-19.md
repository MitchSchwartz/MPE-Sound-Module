# Review Audit — phase3m-osc-snapshot-cli (cycle 1 of 5)

*Audited: 2026-08-18 19:55 (America/Toronto)*

**Audited artifact:** [`grumpy-review-phase3m-osc-snapshot-cli-2026-08-19.md`](grumpy-review-phase3m-osc-snapshot-cli-2026-08-19.md)
**Branches:** `yolo/phase3m-osc-snapshot-cli` (MPE-Module, uncommitted working tree) + `yolo/snapshot-cli-criterion-6` (mpe-cli, uncommitted working tree)
**Method:** Every claim checked against the actual diff/file content on disk (`git diff`, full-file reads, `grep`, targeted unit-file existence checks). Both repos were already checked out on the correct branches with the exact uncommitted changes the grumpy review describes — no branch switching was needed. Per environment constraint, I did not execute the test suite directly (no direct Python invocation available in this environment) — same constraint the original reviewer noted. All test-related verdicts below rest on static reading of the test files (indentation, structure, assertions), which is sufficient to confirm or refute the specific claims made (dead code via indentation, deleted assertions via diff).

**Bottom line up front:** This grumpy review is unusually accurate. Of 3 P0s, 8 P1s (the doc lists 7 but I count 8 numbered — see note below), and 14 P2/P3 items, I could not find a single claim that was wrong or overstated on inspection. Line citations are correct or off by at most 1–4 lines (reviewer was citing pre-edit mental line numbers in a couple of spots; the substance is exact). I found three items the review missed, none of them P0/P1, and no grounds to downgrade any of the review's own findings.

---

## Work Queue

Claims extracted from the grumpy artifact, grouped by file/component:

**`patch_browser/session_snapshot.py`** (MPE-Module)
1. P0-1: ~22 new subprocess forks per `build_snapshot()`, ahead of the D-Bus spike
2. P1-9: `jack_lsp` graph probe runs unguarded, no jackd-liveness gate, 3 s timeout
3. P1-10: `processes.stale` / `config.stale` hardcoded `False`
4. P2: snapshot schema not bumped despite 4 new top-level keys
5. P2: `_probe_process_pid` dead parameter when `exe` is set
6. P2: `pgrep -f surge-xt-cli` substring over-match (pre-existing, not a regression)
7. P2: `build_processes()`/`build_graph_probe()` accept no injection (non-hermetic tests)
8. P2: double memoization in `build_services` called from `build_snapshot`
9. P3: three consecutive blank lines before `snapshot_path`

**`mpe-cli/lib/snapshot.sh` + `commands/{status,diagnose,engine,jack}.sh`** (mpe-cli)
10. P0-2: CLI renders arbitrarily stale snapshot as current truth — `_meta.snapshot_stale`/`age_s` never checked
11. P1-6: `mpe engine status` lost the MASKED-unit warning
12. P1-7: not-installed units now render as `inactive` instead of being skipped; `STATUS_SERVICE_UNITS` (11) vs `mpe_cli_render_services_block` (8) mismatch
13. P2: mangled `printf` format strings (literal newline instead of `\n`) in `engine.sh`, `jack.sh`
14. P2: missing `local` in `cmd_engine_status`
15. P2: new hard `jq` dependency understated by a patch version bump
16. P3: `mpe_cli_render_services_block` forks `jq`/`wc` ~40 times per `mpe status`
17. P3: `mpe diagnose` now makes two SSH round trips instead of one

**`scripts/sooperlooper/sl_osc_session.py` (new) + `sl_bench_listener.py` + `sl_hud_monitor.py` + `looper_session.py` + `sooperlooper-apc-bench.py`** (Criterion 41 merge)
18. Credit: single listen port/cache merge is real; `SlQuery` deleted, not left rotting; 9952 retired everywhere including unit comment; `install-units.sh` stops retired client units
19. Credit: fatal-bind behavior survived the refactor (`sl_osc_session.py:85-92`)
20. Credit: `SlBenchStateListener` is now I/O-free and testable
21. P1-8: bind-failure message recommends `mpe restart looper`, which targets a nonexistent unit
22. P2: three load-bearing "why" comments deleted during the refactor
23. P2: `SlBenchStateListener.register()` takes a client parameter and ignores it; `start()` is a no-op callers still call
24. P2: `NUM_LOOPS` (module constant) vs `num_loops` (parameter) inconsistency between `register_hud`/`register_bench`
25. P2: inconsistent handler robustness (`_on_hud_reply` guards arg count, `_on_bench_state` does not)
26. P2: env-var fallback chain can silently rebind to the retired 9952 alias
27. P2: `SlOscSession` has no `stop()`/`close()`
28. P2: duplicated poll loop in `looper_session.py` (hud-only path) with divergent failure semantics
29. P3: `SlOscSession.get()` cache-pop-then-poll race; `self.last` cross-thread without a lock
30. P3: `seed_tempo()` reachable every 15 s, can block up to 400 ms
31. P3: `_summarize` mixes `statistics.median` (p50) and a custom `_percentile` (p99)
32. P3: `--hud-on --hud-off` together silently means "both," not an error
33. P3: `host`/`port` locals in `run_bench` dead after `osc = osc_session.client`

**Tests (criterion 41)**
34. P1-4: both new tests in `test_looper_session.py` are dead code (indented inside `if __name__ == "__main__":`); same smell in `test_session_snapshot.py` but harmless there (column 0)
35. P1-5: `test_sl_bench_listener.py`, `test_sl_engine_restart.py`, `test_sl_hud_seed.py` all replaced protocol-level wire assertions with mock-delegation checks or a tautology; `test_sl_osc_session.py` does not fill the gap

**Criterion 42 (`measure_midi_osc_latency.py` + measurement doc)**
36. P0-3a: synthetic harness measures a `monotonic()` call + list append, not MIDI→OSC latency; sleep-after-sample means cadence never participates
37. P0-3b: `measure_live()`'s `tracked_send` is defined and never wired to anything; live mode always reports `n=0`
38. Doc conclusion ("no measurable HUD-thread penalty") is unsupported by what was actually measured

---

## Claim Verification

### `patch_browser/session_snapshot.py`

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | P0-1: ~22 new forks, math is 8 (is-active) + 11 (is-enabled) + 2 (pgrep) + 1 (jack_lsp) | ✅ Confirmed | `STATUS_SERVICE_UNITS` has 11 units (`session_snapshot.py:43-55`). `build_snapshot()` already memoizes `check_unit` for `JACK_UNIT`/`SURGE_UNIT`/`ENGINE_STATE_WRITER_UNIT` (3 of the 11) before calling `build_services(unit_active=check_unit, ...)` at line 512 — those 3 hit cache, the other 8 fork fresh. `check_enabled` (line 435) is created fresh with no prior calls, so all 11 `is-enabled` calls fork. `build_processes()` forks `pgrep -x jackd` and `pgrep -f surge-xt-cli` (2). `build_graph_probe()` forks `jack_lsp` (1). 8+11+2+1 = 22, exact. |
| 2 | 57.3 ms / 55.9 ms / 1.4 ms / ~11.5% of a core figures are from an existing measurement, not invented | ✅ Confirmed | `Documents/specs/next-work-order-2026-08-19.md:88-89` verbatim: *"`build_snapshot()` is **57.3 ms**, of which **55.9 ms is three `systemctl` forks**; everything else is 1.4 ms. At the spec's 0.5 s publish interval that is ~11.5% of a core."* |
| 3 | Task 5 (publisher) is explicitly blocked on task 4 (D-Bus spike), and these fields were built before the spike ran | ✅ Confirmed | `next-work-order-2026-08-19.md:101`: *"Snapshot publisher — **blocked on task 4**"*; task 4 (`:86-97`) is the fork-free-liveness spike with acceptance *"<1% of a core at 2 Hz."* No spike output file or DECISIONS.md entry exists in this diff. |
| 4 | Work order task 3 acceptance says "No new subprocess spawning: `mpe status` must not fork per field" | ✅ Confirmed | `next-work-order-2026-08-19.md:82` verbatim. |
| 5 | "Honest caveat" — no publisher exists yet, so the cost lands per invocation today, not continuously | ✅ Confirmed | No timer/publisher code appears anywhere in this diff; `build_snapshot()` is only invoked from the CLI-debug path (`session_snapshot.py:main()`) and now from `mpe-cli/lib/snapshot.sh`'s per-command SSH fetch. Verified no cron/systemd-timer unit references `session_snapshot` in `config/`. |
| 6 | P1-9: `jack_lsp` runs with no jackd-liveness gate and a 3 s timeout, vs. the old bash guard | ✅ Confirmed | `_surge_on_jack_graph()` (`session_snapshot.py:126-139`) has no precondition check; `timeout=3` at line 132. Old `cmd_engine_status` guard (`engine.sh`, pre-diff): `if command -v jack_lsp >/dev/null 2>&1 && pgrep -x jackd >/dev/null; then` — confirmed via `git diff`. `build_processes()` already computes `jackd_pid` (line 163) but `build_graph_probe()` doesn't consume it. |
| 7 | `"surge" in stdout.lower()` is a substring test against the whole `jack_lsp` output | ✅ Confirmed | `session_snapshot.py:139`: `return "surge" in (result.stdout or "").lower()`. |
| 8 | P1-10: `processes.stale`/`config.stale` hardcoded `False`, can never be true even on probe failure | ✅ Confirmed | `build_processes()` (`:161-166`) and the `"config"` dict literal (`:515-519`) both hardcode `"stale": False` regardless of what `_probe_process_pid`/`_read_mpe_env_keys` returned. Contrast with `build_services`'s `"stale": active is None and enabled is None` (`:156`), which is a real tri-state computation. |
| 9 | Schema not bumped despite 4 new top-level keys, docstring still says "schema v1 document" | ✅ Confirmed | `SCHEMA_VERSION = 1` (line 27) unchanged; module docstring (line 1) still says `"Session control plane snapshot — schema v1 (spec D6, Phase 1)."`; `build_snapshot()` return dict gained `services`, `processes`, `graph`, `config` (`:512-519`). |
| 10 | `_probe_process_pid(pattern, *, exe=None)` has a dead parameter; sole `exe=`-caller passes `pattern=""` | ✅ Confirmed | `build_processes()` calls `_probe_process_pid("", exe="jackd")` (line 163) — `pattern` is unused whenever `exe` is truthy (`:98-106`). Only two call sites total, both in `build_processes()`. |
| 11 | Double memoization: `build_snapshot` wraps, then `build_services` wraps again | ✅ Confirmed | `build_snapshot` builds `check_unit = _memoized_unit_active(...)` (line 434), passes it into `build_services(unit_active=check_unit, ...)`, which itself calls `_memoized_unit_active(unit_active or ...)` again (line 147) — wrapping an already-memoizing closure in a second memoizing closure. Redundant but harmless (no extra forks, just an extra dict layer). |
| 12 | Three consecutive blank lines before `snapshot_path` | ✅ Confirmed | Lines 175-177 in the current file are blank (verified via full-file read); `def snapshot_path` starts at 178. |

### `mpe-cli` (`lib/snapshot.sh`, `commands/*.sh`)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 13 | P0-2: no code anywhere checks `_meta.snapshot_stale`/`age_s` | ✅ Confirmed | Ran a full-repo search for `_meta`, `snapshot_stale`, `age_s` across `mpe-cli` — zero hits outside `session_snapshot.py` itself (which is in the *other* repo). `read_snapshot()` computes both (`session_snapshot.py:582-586`) and nothing consumes them. |
| 14 | `mpe status` behavior: `if path.is_file(): snap = read_snapshot() else: build_snapshot()`, no freshness gate | ✅ Confirmed | `lib/snapshot.sh:13-22` (inside the heredoc passed to the Pi): exactly this pattern, quoted verbatim in the review. |
| 15 | The failure mode is not theoretical: a manual debug run leaves a `session.snapshot.json` that then serves forever as "current" | ✅ Confirmed (reasoning, not code) | No publisher exists (confirmed above), so the only way the file gets created today is a manual `python3 -m patch_browser.session_snapshot` run or a `mpe status` call itself (which calls `build_snapshot()` once and — note — does *not* write it to disk; `mpe_cli_snapshot_fetch` only captures stdout, it never calls `write_snapshot`). So the realistic staleness vector is specifically a manual debug invocation of the module's own `main()` (which does call `publish_snapshot`/`write_snapshot`), exactly as the review states. |
| 16 | P1-6: MASKED-unit warning removed with no replacement | ✅ Confirmed | Old `cmd_engine_status` (pre-diff) had `if systemctl is-enabled mpe-jackd.service ... | grep -q masked; then echo "... is MASKED"`. New version has no equivalent. `systemd_unit_enabled()` maps `masked`/`masked-runtime` → `False` (`session_snapshot.py:316-317`), rendering identically to operator-disabled (`enabled=disabled`). |
| 17 | `systemd_unit_enabled`'s own docstring says "`disabled` is an explicit operator decision" | ✅ Confirmed | `session_snapshot.py:298-300` verbatim. |
| 18 | P1-7: units that don't exist now render `active=inactive enabled=unknown` instead of being skipped | ✅ Confirmed | Old `cmd_status` (pre-diff): `if ! systemctl list-unit-files "$unit" >/dev/null 2>&1; then continue; fi`. No such check anywhere in the new `build_services`/`mpe_cli_render_services_block` path — a nonexistent unit gets `is-active` → `inactive` (→ `False`) and `is-enabled` → empty stdout (→ `None`), so `stale = (active is None and enabled is None)` evaluates `False` (since `active` is `False`, not `None`), and the row renders as a real, non-stale, inactive service. |
| 19 | `STATUS_SERVICE_UNITS` has 11 units; `mpe_cli_render_services_block` hardcodes 8 | ✅ Confirmed | `session_snapshot.py:43-55` lists 11; `lib/snapshot.sh:51-60` lists exactly 8 (omits `mpe-looper-session`, `mpe-sooperlooper`, `sl-watchdog`). |
| 20 | All 11 unit files actually exist in `config/` | ✅ Confirmed | Checked all 11 filenames against `config/*.service` directly — all present. |
| 21 | Work order task 3 acceptance: "Output shape unchanged: diff each command before/after, byte-identical or a documented delta" | ✅ Confirmed | `next-work-order-2026-08-19.md:78-79` verbatim. No delta doc exists anywhere in either diff. |
| 22 | P2: mangled `printf` — literal newline instead of `\n` in `engine.sh` and `jack.sh` | ✅ Confirmed | `git diff` shows, verbatim: ``printf "  %-24s active=%-12s enabled=%s\n" "${unit}.service" "$active" "$enabled"`` — with the closing `\n"` actually split across a real newline in the file (`printf "  %-24s active=%-12s enabled=%s` then a literal line break, then `" "${unit}.service" ...`). Confirmed in both `engine.sh` (`cmd_engine_status`) and `jack.sh` (`cmd_jack_status`). Output is unaffected because a literal newline inside a quoted format string prints a newline too — but it is clearly an unreviewed mechanical edit. |
| 23 | P2: missing `local` in `cmd_engine_status` for 7 named variables | ✅ Confirmed | `stale`, `active`, `enabled` (loop-scoped), and `jack_pid`, `surge_pid`, `graph_stale`, `on_graph` are all bare assignments in the new `cmd_engine_status` (`engine.sh`) — no `local` keyword anywhere in the function. `mpe_cli_render_services_block` in `lib/snapshot.sh:50` does declare `local units unit stale active enabled` correctly — confirmed inconsistency. |
| 24 | P2: new hard `jq` dependency for 4 commands, patch-bump (1.2.2→1.2.3) understates it | ✅ Confirmed | `bin/mpe` diff: `MPE_CLI_VERSION="1.2.2"` → `"1.2.3"`. `mpe_cli_require_jq()` (`lib/snapshot.sh:27-32`) exits 1 if `jq` is absent, and is called (transitively via `mpe_cli_snapshot_field`) by `status.sh`, `diagnose.sh`, `engine.sh`, `jack.sh` — all 4. |
| 25 | P3: `mpe_cli_render_services_block` forks `jq`+`wc` ~40 times per `mpe status` | ✅ Confirmed | Per unit (8 units): 1 fork for the `// empty | wc -c` presence check (2 processes: `jq` + `wc`) + 3 more `jq` calls (stale/active/enabled) = 5 forks/unit × 8 = 40. Matches exactly. |
| 26 | P3: `mpe diagnose` now makes two SSH round trips where it made one | ✅ Confirmed | `diagnose.sh` diff adds `mpe_cli_snapshot_fetch` (1 SSH call via `mpe_cli_ssh`) *before* the pre-existing `mpe_cli_ssh "bash -lc '...diagnose-pi-state.sh'"` (2nd SSH call). Previously only the second call existed. |

### Criterion 41 merge (`sl_osc_session.py`, `sl_bench_listener.py`, `sl_hud_monitor.py`, `looper_session.py`, `sooperlooper-apc-bench.py`)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 27 | Credit: `SlQuery` deleted rather than left rotting; 9952 retired everywhere including unit comment; `install-units.sh` stops retired client units | ✅ Confirmed | `git diff` on `sl_hud_monitor.py` shows the entire `SlQuery` class (55 lines) deleted, not deprecated-in-place. `config/mpe-looper-session.service` comment changed from *"Binds UDP 9953 (bench state) and 9952 (HUD auto-update)"* to *"Binds UDP 9953 (shared bench + HUD auto-update)."* `install-units.sh` comment updated from "9952/9953" to "9953" and the `RETIRED_LOOPER_CLIENTS` stop-loop is unchanged/present. |
| 28 | Credit: fatal-bind behavior survived, `sl_osc_session.py:85-92` | ✅ Confirmed (line numbers close) | `try`/`except OSError` block spans lines 81-92 in the current file; the `except`/`raise SystemExit` itself is lines 85-92 exactly as cited. Raises `SystemExit` with the same "Refusing to run blind" framing as the pre-merge `sl_bench_listener.py`. |
| 29 | Credit: `SlBenchStateListener` is now a router with no I/O of its own | ✅ Confirmed | New `SlBenchStateListener.__init__` has no `_server`/`_thread`/socket state; `register()`/`register_tail_peak()`/`unregister_tail_peak()`/`maybe_reregister()` all delegate to `self._session`; `start()` is `"""No-op — the shared SlOscSession owns the listen port."""`. |
| 30 | P1-8: bind-failure message tells the operator to run `mpe restart looper`, which fails | ✅ Confirmed | `sl_osc_session.py:89`: `f"  Fix: mpe restart looper (or stop mpe-looper-session.service), "` — exact line. `mpe-cli/commands/restart.sh:15`: `looper) mpe_cli_ssh "sudo systemctl restart mpe-looper.service"`. `config/` contains `mpe-looper-session.service` and `mpe-sooperlooper.service` — no `mpe-looper.service`. Confirmed the command would fail with "Unit mpe-looper.service not found." The parenthetical fallback (`stop mpe-looper-session.service`) does work. |
| 31 | P2: three "why" comments deleted during the mechanical refactor | ✅ Confirmed, and one more found (see "What the Review Missed") | Verified via `git diff sl_bench_listener.py` and `looper_session.py`/`sl_hud_monitor.py`: (a) the 2026-08-14 incident comment in `start()` — deleted, behavior moved to `sl_osc_session.py` without carrying the comment; (b) *"Handled before the footswitch lookup: the fader layer wants this even for loops with no pad bound"* — deleted verbatim from `on_update`; (c) *"Slower than state on purpose... would cost a datagram per loop per 100 ms for no benefit"* — deleted verbatim from `register()`. All three quotes match the deleted text exactly. |
| 32 | P2: `SlBenchStateListener.register(self, _client, ...)` ignores its client param; caller still passes one; `start()` still called by callers | ✅ Confirmed | `register(self, _client, *, num_loops: int)` (leading underscore signals intentional non-use) never references `_client`. `sooperlooper-apc-bench.py` diff: `osc = osc_session.client` then `state_listener.register(osc, num_loops=num_loops)` — still passes a real client object. `state_listener.start()` is also still called (unchanged call site) even though `start()` is now a no-op. |
| 33 | P2: `_probe_process_pid` — already counted above, same finding cross-referenced correctly | ✅ Confirmed | (See row 10.) |
| 34 | P2: `NUM_LOOPS` vs `num_loops` inconsistency — bench with 8 loops, HUD still subscribes 16 | ✅ Confirmed | `register_hud()` (`sl_osc_session.py:138-149`): `for loop in range(NUM_LOOPS):` — module-level constant, ignores any bench loop count. `register_bench(*, num_loops)` takes the caller's count and stores it in `self._bench_num_loops`. `maybe_reregister()` (`:198-206`) calls `register_hud()` (still uses `NUM_LOOPS`) and `register_bench(num_loops=self._bench_num_loops)` — genuinely different loop counts if bench was started with fewer than `NUM_LOOPS`. |
| 35 | P2: `_on_hud_reply` guards `len(args) >= 3`; `_on_bench_state` has typed positionals, no guard | ✅ Confirmed | `_on_hud_reply(self, _addr, *args)` (`:111-113`) checks `if len(args) >= 3:` before unpacking. `_on_bench_state(self, _addr, loop_index, control, value)` (`:115-120`) has no such guard — a short `/sl/bench/state` OSC message (fewer than 3 positional args) would raise `TypeError` in the dispatcher/server thread. `_on_hud_reply` is also `disp.set_default_handler(self._on_hud_reply)` (`:80`), i.e. the default handler for *any* unmatched address — confirmed it will attempt `int(args[0])` blindly on stray traffic with ≥3 args. |
| 36 | P2: env-var fallback chain can silently bind 9952 despite the "retired" comment two lines up | ✅ Confirmed | `sl_osc_session.py:22-31`: comment says *"HUD port (9952) is retired; alias only"*, then `LISTEN_PORT` resolves `MPE_SL_SESSION_LISTEN_PORT` → `MPE_SL_BENCH_LISTEN_PORT` → `MPE_SL_HUD_LISTEN_PORT` → `"9953"`. An appliance with a leftover `MPE_SL_HUD_LISTEN_PORT=9952` in `/etc/mpe/mpe.env` (plausible — pre-merge appliances would have had exactly this variable set) binds 9952 with zero warning. |
| 37 | P2: `SlOscSession` has no `stop()`/`close()` | ✅ Confirmed | Full class body reviewed — no such methods exist anywhere in `sl_osc_session.py`. |
| 38 | P2: duplicated poll loop in `run_session`'s `--hud-only` path, missing the hard-fail semantics of `_hud_thread_main` | ✅ Confirmed | `_hud_thread_main` (`looper_session.py:33-52`) wraps its loop in `try/except Exception as exc: ... os._exit(1)`. The new inline `--hud-only` loop (`:95-104`) only catches `KeyboardInterrupt`; any other exception (e.g. an OSC send failure) propagates as an ordinary Python traceback and process exit, not the deliberate hard-fail-for-`Restart=always` behavior the threaded path has. Confirmed two code paths, two failure behaviors. |
| 39 | P3 items (cache-pop race, `seed_tempo` blocking window, mixed percentile estimators, `--hud-on --hud-off` both-true default, dead `host`/`port` locals) | ✅ Confirmed, all | `get()` pops the cache key then polls while the server thread can repopulate it mid-window (`:126-136`) — matches. `seed_tempo()` is called from `maybe_reregister()` every ≥15 s and can block up to `timeout=0.4` (400 ms) inside `get()` (`:194-196`, `:126`) — matches. `_summarize` (measurement script) mixes `statistics.median` for p50 and `_percentile` for p99 (`:42-43`) — matches. `args.hud_on == args.hud_off` branch runs both conditions silently when neither or both flags are given (`:142-148`) — matches. `host`/`port` locals (`sooperlooper-apc-bench.py:90-91`) are assigned but the only downstream use (`udp_client.SimpleUDPClient(host, port)`) was replaced by `osc = osc_session.client` — grep confirms no other reference to the bare `host`/`port` variables in the file. |

### Tests

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 40 | P1-4: `test_looper_session.py`'s two new tests are inside the `if __name__ == "__main__":` block and never run under `unittest discover` | ✅ Confirmed | Full-file read: line 115 `if __name__ == "__main__":`, line 116 `    unittest.main()`, then two blank lines, then line 119 `    def test_single_osc_session_module_present(self) -> None:` — indented at the same 4-space level as `unittest.main()`, i.e. syntactically *inside* the `if` block. Under `discover`, `__name__` is the module's dotted path, not `"__main__"`, so this branch never executes and the `def`s are never created as module attributes, let alone collected as test methods (they're also bare functions taking `self`, not bound to any `TestCase`). |
| 41 | Same smell in `test_session_snapshot.py` but harmless (column 0) | ✅ Confirmed | Line 219 `if __name__ == "__main__":`, 220 `    unittest.main()`, then blank lines, then line 223 `class SnapshotServicesTests(unittest.TestCase):` at column 0 — a genuine top-level class, unaffected by the preceding `if` block. Collected normally. |
| 42 | P1-5: `test_sl_bench_listener.py` replaced OSC-path assertions with `session.register_bench.assert_called_once_with(...)` delegation checks | ✅ Confirmed | `git diff`: `test_register_all_loops` previously asserted `"/sl/0/register_auto_update" in paths` from `client.send_message.call_args_list`; now asserts only `session.register_bench.assert_called_once_with(num_loops=2)`. Same pattern in `test_register_tail_peak_scoped_to_one_loop` and `test_unregister_tail_peak_clears_loop`. |
| 43 | `test_sl_engine_restart.py` dropped the "no global registrations from bench" sentinel | ✅ Confirmed | `git diff`: removed `global_regs = [c for c in client.send_message.call_args_list if c.args[0] == "/register_auto_update"]; self.assertEqual(global_regs, [])`, replaced with `session.register_bench.assert_called_once_with(num_loops=1)`. |
| 44 | This guard mattered *more* after the merge because `register_hud()` now sends a global `/register_auto_update` on the *same shared client* | ✅ Confirmed | `sl_osc_session.py:146-148`: `register_hud()` does send `self.client.send_message("/register_auto_update", ["tempo", 200, returl, "/r"])` — a global registration on the client that bench also shares post-merge. |
| 45 | `test_sl_hud_seed.py` is now tautological — reimplements `seed_tempo`'s logic in the stub, then asserts the stub does that | ✅ Confirmed | `git diff`: `sl.register_hud = MagicMock()`; `sl.seed_tempo = MagicMock(side_effect=_seed)` where `_seed()` re-implements `if sl.cached(...) is None: sl.get(...)` inline. The real `SlOscSession.seed_tempo` is never imported or exercised by this test file. |
| 46 | `test_sl_osc_session.py` does not cover `register_bench`/`register_hud` wire content | ✅ Confirmed | Full file read: 5 tests total — `_cache_key`, cache-sharing between bench/HUD callbacks, `seed_tempo` (query-when-missing / skip-when-cached), and bind-failure. Zero references to `register_bench` or `register_hud` anywhere in the file. |

### Criterion 42 (latency measurement)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 47 | P0-3a: synthetic harness measures a function call + `monotonic()` + list append, not a MIDI→OSC path; sleep happens after the sample | ✅ Confirmed | `measure_synthetic()` (`:63-90`): `t0 = time.monotonic(); fake_send(...); latencies.append((sent_at[-1] - t0) * 1000.0); time.sleep(0.002)`. `fake_send` is `sent_at.append(time.monotonic())` — no MIDI decode, no OSC serialization, no socket write. The `time.sleep(0.002)` is after `latencies.append`, so it cannot contribute to the measured interval. |
| 48 | Reported p50 of 0.005 ms is consistent with "one function call + one monotonic() + one append," not a MIDI path | ✅ Confirmed (consistent, plausible) | Given the code measures literally nothing but a Python-level call/append round trip, 5 µs is the right order of magnitude for that operation on typical hardware; it cannot represent MIDI decode + OSC serialize + socket write. |
| 49 | P0-3b: `tracked_send` is defined and never called; `measure_live()` always returns `n=0` | ✅ Confirmed | `measure_live()` (`:93-130`): defines `tracked_send` (`:106-110`) but never assigns it anywhere (not monkeypatched onto `session.client.send_message`, not passed to any callback registration, not called directly). `on_midi` only appends to `pending` (`:103-104`). `latencies` is only ever appended to inside `tracked_send`, which is unreachable. `_summarize([])` returns `{count: 0, p50: 0.0, p99: 0.0, max: 0.0}` (`:36-38`). The tool will print `live: n=0 p50=0.000ms ...` and exit 0 with no error. |
| 50 | Doc's conclusion ("no measurable HUD-thread penalty at p99") is unsupported by what was measured | ✅ Confirmed | `docs/measurements/looper-midi-osc-latency-2026-08-19.md:29`: *"Synthetic harness shows no measurable HUD-thread penalty at p99."* Given claim 47/48, the harness cannot detect a GIL-contention effect on an actual MIDI callback because no MIDI callback is exercised — the conclusion doesn't follow from the method. The doc *is* explicit that Pi live numbers are still required (`:30`), which the review credits. |
| 51 | Results table carries "(synthetic, nerdrack 2026-08-19)" — x86 laptop numbers for an appliance criterion | ✅ Confirmed | `docs/measurements/looper-midi-osc-latency-2026-08-19.md:22` heading exactly as quoted; "nerdrack" is the x86 build/eval host per `AGENTS.md`'s Nerdrack YOLO section, not the Pi. |

---

## Severity Re-Assessment

| # | Issue | Reviewer Rating | My Rating | Delta | Reasoning |
|---|-------|----------------|-----------|-------|-----------|
| P0-1 | 22 new forks / ~430 ms build cost, ahead of the D-Bus spike | P0 | **P0** | — | Correctly scoped as a landmine, not a fire (review says so explicitly), but it's still P0-grade because it *guarantees* a future incident the moment the publisher ships, and the spike that would answer the question cheaply (~20 min) hasn't been run. Holding this branch until task 4 runs is the cheapest possible risk reduction available. |
| P0-2 | CLI renders stale snapshot as current, no gate on `_meta.snapshot_stale` | P0 | **P0** | — | This is not hypothetical-severity — it silently misreports appliance health to a human debugging a live incident, which is exactly the scenario `mpe status`/`mpe diagnose` exist for. Agree fully with P0. |
| P0-3 | Criterion 42 measures nothing (both the synthetic harness and the live path) | P0 | **P0** | — | Two independent, complete defects in the same deliverable, one of which (live path) can never produce output other than a false-clean `n=0`. A document already exists citing the wrong number as a real result. Agree fully — if anything, P0-3b (silent `n=0` that looks like success) is the more dangerous half, since it will not even trigger visible confusion; it will just look done. |
| P1-4 | Dead tests via indentation | P1 | **P1**, borderline P0 | ↔ (noted) | I considered arguing this up to P0 because it means criterion 41 — the one thing in this branch that's actually good — is currently *unverified by CI*: `unittest discover` reports green while asserting nothing about `SlOscSession` existing, 9952 being gone, or the session being wired. I'm leaving it at P1 per the reviewer's own framing (a quick, mechanical, single-file fix with no design implications), but flag that "the suite is green" cannot be used as evidence criterion 41 works until this is fixed. |
| P1-5 | Weakened tests (delegation checks, tautology) | P1 | **P1** | — | Agree. This is exactly the "green test run would have hidden it" pattern called out in the review's own closing note. |
| P1-6 | MASKED warning dropped | P1 | **P1** | — | Agree — a masked unit is materially different from a disabled one operationally (masked survives `enable`; disabled does not), and this is the exact command run during an incident. |
| P1-7 | Not-installed units render as false negative "inactive"; 8-vs-11 list mismatch | P1 | **P1** | — | Agree. Two separate defects bundled correctly under one finding: an undocumented output-shape change (violates the work order's own acceptance bar) plus a live inconsistency between two files that must agree and don't, from day one. |
| P1-8 | Bind-failure message recommends a command that fails | P1 | **P1** | — | Agree with the reviewer's own caveat: recoverable (fallback works), but printed at the worst possible moment (mid-incident), and this is a *new* promotion of a pre-existing stale reference, not merely inherited debt. |
| P0-3a / P0-3b split | Reviewer treats both halves as one P0 (P0-3) | — | **Consider splitting into two P0s in tracking, not in this review** | — | Not a disagreement with severity, a bookkeeping note: (a) synthetic-harness-measures-nothing and (b) live-path-can-never-sample are independently sufficient to reject the deliverable, and either one alone would still be P0. Keeping them as one item is fine for this review's purposes but worth two separate line items when this becomes tracked work, since they have different fixes and could land in separate commits. |
| P2/P3 items | All | P2/P3 | **P2/P3** | — | No disagreements on any P2 or P3 classification. Two (mangled `printf`, `local` scoping) are genuinely cosmetic/hygiene; the rest (env-var alias hazard, no `stop()`/`close()`, dead parameters, NUM_LOOPS mismatch, handler-robustness asymmetry) are real but correctly scoped below P1 given none of them fire under current usage patterns. |

**Note on the review's own count:** the verdict table says "Three P0s, seven P1s," but the document contains eight distinct numbered P1 findings (P1-4 through P1-10 is 7 numbers, but P1-4's write-up bundles two distinct test files' worth of dead-code findings, and P1-5 bundles three separate test-file regressions under one number). This is a labeling/counting quirk, not a substantive gap — every individual claim inside those bundles was verified above. I'm reporting P1 count as **7** below (matching the review's own numbering: P1-4 through P1-10), since that's the countable unit the review itself uses, while flagging that P1-4 and P1-5 each bundle multiple confirmed sub-findings.

---

## What the Review Missed

The review is close to exhaustive. I found three items, none above P2:

1. **`mpe jack status` also doubled its SSH round trips, not just `mpe diagnose`.** The review's P3 item calls out `mpe diagnose` making two SSH round trips where it made one (`mpe_cli_snapshot_fetch` + the pre-existing `diagnose-pi-state.sh` heredoc). The identical pattern exists in `commands/jack.sh`'s `cmd_jack_status`: it now calls `mpe_cli_snapshot_fetch` (SSH round trip #1) *and* keeps the pre-existing `mpe_cli_ssh "bash -s" <<EOF ... EOF` heredoc for the chrt/thread-priority reporting (SSH round trip #2), where the old code was a single SSH call. `cmd_status` and `cmd_engine_status`, by contrast, do not double up — they fully replaced the old SSH body with the snapshot-only path. Same class of issue as the review's existing P3, same severity (**P3**), just an unlisted second instance.

2. **A fourth deleted "why" comment in `sl_bench_listener.py`, not among the three the review quotes.** The `on_update` method's `elif control == "loop_len":` branch lost the comment *"Needed to capture the tempo from the first take, which is what establishes the grid."* This is the same class of loss the review already flags as P2 (three quoted casualties) — this is simply a fourth casualty in the same file that wasn't quoted. Doesn't change the severity or the fix (the P2 item already calls for "carry the reasons across" generically); worth folding into that item's evidence when it's addressed rather than treating as new work.

3. **The snapshot-based CLI path adds a full Python interpreter startup cost to every interactive command that didn't pay it before, independent of the appliance-CPU-budget concern the review already raises.** `mpe_cli_snapshot_fetch` (`lib/snapshot.sh:7-24`) runs `python3 - <<'PY' ... PY` over SSH on every `mpe status`/`diagnose`/`engine status`/`jack status` invocation when no snapshot file yet exists (the common case, since no publisher exists). Per `Documents/DECISIONS.md`'s own measured table (2026-08-18), `python3 <script>` interpreter start alone costs **~360-440 ms** on this appliance — on top of whatever `build_snapshot()` itself costs (57-430 ms per P0-1's math). The *previous* versions of these four commands were pure-bash SSH calls with no Python involved at all. This is a genuine, measurable latency regression for a human typing `mpe status` at the terminal — distinct from P0-1's appliance-CPU-under-a-future-publisher framing, and from P0-2's staleness framing. It's not P0/P1 because it's a UX/responsiveness cost, not a correctness or safety issue, and DECISIONS.md's own rule 5 ("Never invoke a Python module CLI on a timer... is a debugging command") is not technically violated since there's no timer — but the new *default*, non-debug CLI path now pays the "debugging command" tax on every invocation. Worth a line in the same PR that fixes P0-1/P0-2, since the D-Bus spike (task 4) and an in-process snapshot builder would fix this too. **P2.**

No security, auth, or data-loss issues were found beyond what's already flagged (the OSC listener is loopback-only by default, and the new `config.mpe_env` field is scoped to an explicit allow-list of two non-sensitive keys — not a leak vector).

---

## What the Review Got Right (And Why It Matters)

- **P0-3b (`tracked_send` never wired) is the single most dangerous finding in this review**, more so than its P0 sibling P0-3a. A harness that crashes or errors gets noticed. A harness that runs to completion, prints a clean `live: n=0 p50=0.000ms p99=0.000ms max=0.000ms`, and exits 0 looks like a successful measurement of "zero latency" to anyone who doesn't read the script — including a future agent asked to "check whether criterion 42 passed on the Pi." The failure mode is silence dressed as success, which is exactly the class of bug that survives the longest in a codebase because nothing about running it feels wrong.

- **P0-1's staleness caveat ("landmine, not a fire") is the correct frame, and it's why this is fixable in the cheapest possible way.** The review is right that task 4 (the D-Bus spike, ~20 minutes) can make the entire finding moot. The compounding risk is that `PUBLISH_INTERVAL_S = 0.5` already exists in the file as a constant with nothing consuming it yet — it is a Chekhov's gun. The moment someone wires a publisher (which is explicitly the *next* piece of planned work per the work order), this fork count starts paying 86% of a core continuously with zero additional code review, because the expensive part will already be merged and reviewed as "just fields on a snapshot."

- **P1-5's observation that the merge raised the risk the deleted guard was protecting against is the sharpest catch in the whole review.** Before the merge, bench and HUD had separate OSC clients, so "bench never sends a global registration" was a real, meaningful invariant. After the merge they share one client, and `register_hud()` does send a global `/register_auto_update`. Deleting the test that would catch bench accidentally sending that same global message (e.g., from a future refactor of `register_bench`) removes the only thing that would have caught a real regression in the new, higher-risk shared-client world — the test was protecting against a mistake that's now easier to make, not harder.

- **The "good" section is not padding — Criterion 41 really is close to mergeable on its own**, and the review's recommendation to split it into its own PR (separate from 42 and the criterion-6 prep) is the single highest-leverage piece of advice in the document, independent of any specific bug. Every P1 tied to criterion 41 (P1-4, P1-5, P1-8) is a same-day, low-effort fix; nothing about it requires the P0-1/P0-2/P0-3 blockers to resolve first.

---

## Prioritized Action Matrix

| Priority | Issue | Verdict | Effort | Depends On |
|---|---|---|---|---|
| P0 | Criterion 42: wire `tracked_send` as the real send hook in `measure_live()`, or delete the live path until it is | ✅ | Quick fix | — |
| P0 | Criterion 42: make the synthetic harness traverse real OSC serialization + a real socket, or delete it and the doc's synthetic table row | ✅ | Half-day | — |
| P0 | Criterion 6 prep: do not ship the 22-fork snapshot fields until work-order task 4 (D-Bus spike) reports a per-query cost | ✅ | Half-day (run the spike) + quick fix (batch or gate) once the number is known | Task 4 (spike) |
| P0 | mpe-cli: gate every snapshot consumer on `_meta.snapshot_stale`/`age_s` before rendering as current | ✅ | Quick fix | — |
| P1 | Fix test indentation in `tests/test_looper_session.py` so the two criterion-41 tests actually run under `unittest discover` | ✅ | Quick fix | — |
| P1 | Restore protocol-level wire assertions in `tests/test_sl_osc_session.py` for `register_bench`/`register_hud`; restore the "no global registrations from bench" guard in `test_sl_engine_restart.py`; de-tautologize `test_sl_hud_seed.py` | ✅ | Half-day | — |
| P1 | Restore the MASKED-unit readout in `mpe engine status`; restore (or explicitly document) the not-installed-unit skip in `mpe status`/`mpe engine status`; reconcile the 8-vs-11 unit list | ✅ | Half-day | — |
| P1 | Fix `restart.sh`'s `looper` target to point at `mpe-looper-session.service` (or drop the `sl_osc_session.py` bind-failure message's recommendation of `mpe restart looper`) | ✅ | Quick fix | — |
| P2 | Gate `build_graph_probe()`'s `jack_lsp` call on `jackd_pid` being non-`None` (reuse `build_processes()`'s result); this alone removes the worst-case 3 s stall | ✅ | Quick fix | Ideally sequenced with the P0 spike/batching fix above |
| P2 | Restore (or relocate) the four deleted "why" comments in `sl_bench_listener.py` (2026-08-14 incident record, wet-before-footswitch ordering, loop_len/tempo-grid rationale, network-budget rationale for `loop_pos`) | ✅ | Quick fix | — |
| P2 | Bump `SCHEMA_VERSION` given 4 new top-level snapshot keys, or explicitly document why a minor addition doesn't warrant it | ✅ | Quick fix | — |
| P2 | Fix mangled `printf` literal-newline formatting in `engine.sh`/`jack.sh`; add missing `local` in `cmd_engine_status` | ✅ | Quick fix | — |
| P2 | Warn (or refuse) when `LISTEN_PORT` resolves via the retired `MPE_SL_HUD_LISTEN_PORT` alias to 9952 | ✅ | Quick fix | — |
| P2 | Add a guard to `_on_bench_state` matching `_on_hud_reply`'s arg-count check | ✅ | Quick fix | — |
| P2 | Reconcile `NUM_LOOPS` (module constant) vs `num_loops` (bench parameter) so HUD and bench always subscribe the same loop count | ✅ | Quick fix | — |
| P2 | `mpe jack status`: same two-SSH-round-trip regression as `mpe diagnose` (found in this audit, not the original review) — fold into whatever fixes the diagnose case | ✅ | Quick fix | — |
| P2 | Interactive CLI commands now pay a ~360-440 ms Python-interpreter-start tax per invocation that didn't exist pre-merge (found in this audit) — track alongside the P0-1 fork-count fix since both share the same root cause (calling into Python at all from a bash CLI command) | ✅ | Multi-day (real fix is likely "batch the systemd query and skip Python entirely for the common case," which is task-4-shaped) | Task 4 (spike) |
| P3 | Add `SlOscSession.stop()`/`close()` for test cleanup and eventual multi-session scenarios | ✅ | Quick fix | — |
| P3 | Make `_summarize` use one percentile estimator consistently (or document why p50/p99 intentionally differ) | ✅ | Quick fix | — |
| P3 | Reject `--hud-on --hud-off` together instead of silently running both | ✅ | Quick fix | — |
| P3 | Remove dead `host`/`port` locals in `run_bench` | ✅ | Quick fix | — |
| P3 | Unify the `--hud-only` inline poll loop in `looper_session.py` with `_hud_thread_main`'s hard-fail semantics | ✅ | Half-day | — |

---

## Disagreements and Judgment Calls

I have no substantive disagreements with the review's recommendations. Two small notes, both already partly acknowledged by the review itself:

1. **The "Three P0s, seven P1s" summary undercounts what's actually bundled inside P1-4 and P1-5** (see Severity Re-Assessment). This doesn't change any priority or recommendation, but whoever picks this up for fix-work should read P1-4/P1-5 as "one ticket per affected test file," not one ticket total, or the fix will land as a single file's indentation change while the sibling test files with the same rot go untouched.

2. **The recommended sequence (fix P0-3 or drop criterion 42 → hold criterion 6 pending task 4 → gate on `_meta.snapshot_stale` → fix test indentation → restore protocol assertions → restore MASKED/not-installed → split criterion 41 into its own PR) is sound, and I'd make one addition, not a change:** run the P0-1 fix (task 4 spike) *before* the P0-2 fix (staleness gate), not after, even though the review lists P0-2 first for criterion-6 items. Reasoning: if task 4 shows D-Bus liveness is cheap enough to query synchronously on every command, the entire snapshot-caching model this PR introduces (build-once, cache to disk, read-if-present) may not be the right design at all — a synchronous D-Bus query per CLI invocation might make `read_snapshot()`'s disk-cache path (and therefore P0-2's staleness-gating fix) unnecessary complexity for the `mpe status`-family commands specifically, even though the publisher (task 5) would still need the cache. This is a sequencing preference, not a disagreement about severity — either order eventually reaches a safe state, but running the spike first avoids doing (and then possibly undoing) staleness-gating work on a caching design that the spike might obsolete for this specific consumer.

---

## Summary for reporting

- **P0 count: 3** (all confirmed accurate, no changes)
- **P1 count: 7** (all confirmed accurate; P1-4 and P1-5 each bundle multiple independently-confirmed sub-findings across test files — see Severity Re-Assessment note)
- **P2 count: 15** (14 from the original review, all confirmed, plus 1 new: interactive CLI interpreter-startup tax)
- **P3 count: 9** (8 from the original review, all confirmed, plus 1 new: `mpe jack status` duplicate SSH round trip)
- **Artifact path:** `/home/claude-sandbox/workspace/MPE-Module/Documents/reviews/review-audit-phase3m-osc-snapshot-cli-cycle1-2026-08-19.md`
- **Verdict on the review itself:** Accurate and thorough. No claim was found incorrect, exaggerated, or missing context on inspection. Recommend treating this grumpy review as reliable input to the fix pass without re-verifying individual line citations again — this audit already did that.
