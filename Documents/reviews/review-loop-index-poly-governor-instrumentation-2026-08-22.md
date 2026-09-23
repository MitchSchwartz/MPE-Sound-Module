# Review loop — cycle 2 fixes (2026-08-22)

Applied after grumpy + audit cycle 2 (5 P1):

| P1 | Fix |
|---|---|
| Spam guard unreachable at 0.15 s poll | `spam_threshold_per_s()` = `max(2, int(1/poll)-1)`; journal takes poll interval |
| SIGTERM never reaches stop() | Daemon uses `_Stop` handler + 0.2 s slice sleep (matches snapshot publisher) |
| send_polylimit failure invisible | `log_send_failed` once per (old,new,reason) until success |
| enable/disable flip silent | `log_enabled_change` on `_enabled` transition |
| test_startup_log_once thread leak | `addCleanup(governor.stop)` + patched state file |

Also: verbose trace written before suppress check; startup line includes `warm_window` and `emergency_poly`.

Tests: 16/16 `tests.test_surge_poly_governor`.

---

## Cycle findings (carried in 2026-09-23 before the cut)

### `grumpy-review-poly-governor-instrumentation-2026-08-22.md`


Ship after P1 disposition (document or minimal wire) and test import cleanup. No behavioural
regression expected with defaults.

### `grumpy-review-poly-governor-instrumentation-cycle2-2026-08-22.md`


1. **🔴 Make the spam guard reachable** (§4.1) — derive `LOG_SPAM_THRESHOLD_PER_S` from the poll
   interval, or dedupe repeated tick errors with backoff. Add the
   `threshold < 1/poll_interval` invariant test. Then correct the deliverable's claim.
2. **🔴 Handle `SIGTERM` in the daemon** (§4.3) — event-based wait plus handler, matching
   `scripts/session-snapshot-publisher.py` and the `mpe-peak-meter` precedent in
   `docs/SHUTDOWN.md`, so `stop()`/`flush_pending()` actually run on the appliance.
3. **🔴 Log actuation failure and enable/disable transitions** (§4.2, §4.4) — a guarded line when
   `send_polylimit` returns `False`, and one when `_enabled` flips. Without the second, no Plan V
   window can be certified governor-off from the journal.
4. **🔴 Stop `test_startup_log_once` leaking a worker thread** (§6.1) — `addCleanup(governor.stop)`
   and patch `POLY_STATE_FILE` everywhere a governor is constructed, so the suite stops depending
   on `$HOME`.
5. **🟡 Fix the trace so it holds what the journal drops** (§4.5) and cap its size (§4.6) — move
   `append_verbose_trace` above the suppression check, truncate at startup, stop appending past a
   byte cap.

### `review-audit-poly-governor-instrumentation-cycle1-2026-08-22.md`


| ID | Grumpy claim | Verdict | Severity | Action |
|---|---|---|---|---|
| F1 | `append_verbose_trace()` dead code | ✅ Confirmed | P1 | Document in deliverable (already noted); optional wire on transition only — defer |
| F2 | Suppressed count lost on stop | ✅ Confirmed | P2 | Skip this pass |
| F3 | Emergency `_high_since` churn | ✅ Confirmed | P2 | Skip — pre-existing |
| F4 | Test stdout leak | ✅ Confirmed | P3 | Fixed unused imports; mock print optional |
| F5 | Defaults unchanged | ✅ Confirmed | — | `test_load_governor_config_defaults` passes |
| F6 | CPUAffinity fix | ✅ Confirmed | — | Present in unit file |
| F7 | No per-tick logging | ✅ Confirmed | — | `test_unchanged_limit_logs_nothing` passes |

### `review-audit-poly-governor-instrumentation-cycle2-2026-08-22.md`

**P0 count: 0. P1 count: 5. Artifact: `/home/claude-sandbox/workspace/MPE-Module/Documents/reviews/review-audit-poly-governor-instrumentation-cycle2-2026-08-22.md`.**

| Priority | Issue | Verdict | Effort | Depends On |
|----------|-------|---------|--------|------------|
| P0 | — | — | — | — |
| P1 | Make the spam guard reachable — derive `LOG_SPAM_THRESHOLD_PER_S` from poll interval or dedupe-with-backoff on error identity; add `threshold < 1/poll_interval` invariant test (§4.1) | ✅ | Half-day | — |
| P1 | Handle `SIGTERM`/`SIGINT` in `scripts/surge-poly-governor.py` — mirror `session-snapshot-publisher.py`'s handler + `threading.Event` pattern so `stop()`/`flush_pending()` run under systemd (§4.3) | ✅ | Half-day | — |
| P1 | Log actuation failure from `_apply_limit` on the `send_polylimit() == False` branch, guarded, once per state change (§4.2) | ✅ | Quick fix | — |
| P1 | Log enable/disable transitions on change (compare-then-assign in `_tick`, `:398-400`) so a Plan V window is certifiable governor-off from the journal alone (§4.4) | ✅ | Quick fix | — |
| P1 | Fix `test_startup_log_once` thread leak — `self.addCleanup(governor.stop)`; patch `POLY_STATE_FILE`/`UI_STATE_FILE` in every test that constructs a governor so no test touches the real home directory (§6.1) | ✅ | Quick fix | — |
| P2 | Move `append_verbose_trace(line)` above the suppression check in `_emit` so the trace holds what the journal drops (§4.5) | ✅ | Quick fix | P1 spam-guard fix (fixing 4.1 first makes this matter in practice) |
| P2 | Truncate/cap `poly-governor.trace` at startup and on a byte ceiling (§4.6) | ✅ | Quick fix | — |
| P2 | Fix `_effective_poly` disk-overwrite to log on change and reset hysteresis timers when the value changes under an unchanged patch name (§4.9) | ✅ | Half-day | — |
| P2 | Add coverage: `_worker` catching a raising `_tick`; suppressed lines absent from trace; verbose-off-by-default; `_apply_limit` on `False`; enable/disable flip logged (§6.4) | ✅ | Half-day | P1/P2 code fixes above |
| P2 | Correct the three false deliverable-doc sentences once the underlying code is fixed (§8) | ✅ | Quick fix | The P1/P2 fixes above |
| P3 | Startup line: add `warm_window=` and `emergency_poly=` fields, or drop the "every constant discoverable" claim (§4.7) | ✅ | Quick fix | — |
| P3 | `log_startup` should log `self.poll_interval`, not `config.poll_interval_s` (§4.8) | ✅ | Quick fix | — |
| P3 | Log the resolved trace path in the startup line when verbose is enabled (§4.10) | ✅ | Quick fix | — |
| P3 | Patch a module-level clock indirection (or inject a clock into `PolyGovernorJournal`) instead of patching `time.monotonic` process-wide in tests (§6.3) | ✅ | Quick fix | §6.1 fix (leak already contains most of the blast radius) |
| P3 | Declare `_proc_prev = None` in `__init__`; add a one-line comment on `snapshot()`'s intentional lock-free reads; comment the mtime-before-parse ordering in `_refresh_patch_state` (§4.11, §5) | ✅ | Quick fix | — |
| P3 | Mock `print` in `test_emergency_slam_at_90` / `test_spike_steps_down_immediately` (carried from cycle 1, still open) | ✅ | Quick fix | — |
| P3 | Add `RuntimeDirectoryMode=0755` to `surge-poly-governor.service` for consistency with `mpe-jackd.service` (§8) | ✅ | Quick fix | — |

**0 P0. 5 P1.**

---

