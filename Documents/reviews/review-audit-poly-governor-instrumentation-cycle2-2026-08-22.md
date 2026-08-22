# Review audit — poly-governor-instrumentation, cycle 2 (2026-08-22)

**Grumpy artifact:** `Documents/reviews/grumpy-review-poly-governor-instrumentation-cycle2-2026-08-22.md`
**Prior audit:** `Documents/reviews/review-audit-poly-governor-instrumentation-cycle1-2026-08-22.md`
**Branch verified against:** `yolo/poly-governor-instrumentation` @ `3cc74a4` (working tree clean except this file; confirmed via `git status` / `git rev-parse --abbrev-ref HEAD`)
**Method:** Every claim checked against the actual source (`patch_browser/surge_poly_governor.py`, `scripts/surge-poly-governor.py`, `patch_browser/surge_playback.py`, `patch_browser/mpe_run_dir.py`, `patch_browser/ui_prefs.py`, `patch_browser/json_store.py`, `patch_browser/surge_cpu_monitor.py`, `tests/test_surge_poly_governor.py`, `config/surge-poly-governor.service`, `config/mpe-jackd.service`, `config/mpe-peak-meter.service`, `scripts/session-snapshot-publisher.py`, `scripts/lib/periodic_loop_lint.py`, `docs/SHUTDOWN.md`, `docs/measurements/poly-governor-instrumentation-2026-08-21.md`) and the commit diff `118a65c..3cc74a4`. Read via `Shell`/`cat`/`sed`/`grep` (Read tool blocked by hook; no `python3` invoked anywhere in this audit per operator rules — verification of the test-thread-leak claim (§6.1) used file-existence checks only, not test execution).

---

## Work Queue

Claims extracted from the grumpy review, grouped by area:

- **§1 First Impressions** — spam-guard-cannot-fire arithmetic claim
- **§2 Architecture** — no SIGTERM handler; worker thread death unobservable
- **§3 Code Quality** — error path routes through unreachable guard; `_apply_limit` silent on `False`; `send_polylimit` unprefixed print; `append_verbose_trace` docstring stale; `_proc_prev` undeclared in `__init__`
- **§4.1–4.11** — ten code smells (four 🔴, five 🟡, one 🟢 bundle)
- **§5 Logic** — hysteresis ladder correctness; threading of `snapshot()`; corrupt-state-file `OverflowError` ordering; calibration gap
- **§6.1–6.4** — test thread leak, unreachable-machinery tests, process-wide `time.monotonic` patch, coverage gaps
- **§7 Security & Performance** — no security surface; cadence/cost table; `load_ui_preference` cost; `CPUAffinity` fix
- **§8 Developer Experience** — three doc claims alleged false; `RuntimeDirectoryMode` inconsistency
- **Prior-cycle table** — four cycle-1 findings re-verified against this cycle's diff

---

## Claim Verification

### §1 / §4.1 — Spam guard cannot fire at shipped defaults

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | `DEFAULT_POLL_INTERVAL_S = 0.15` → 6.67 ticks/s, `LOG_SPAM_THRESHOLD_PER_S = 10` → guard cannot trip | ✅ Confirmed | `DEFAULT_POLL_INTERVAL_S = 0.15` and `LOG_SPAM_THRESHOLD_PER_S = 10` at `patch_browser/surge_poly_governor.py:26,39`. `1/0.15 = 6.667`. `_worker` (`:390-395`) calls `_tick()` once per `poll_interval`; `_tick()` (`:397-507`) is a single `if/elif` ladder where every branch that calls `_apply_limit` returns immediately after — at most one `_apply_limit`/emit per tick. `6.67 < 10`, so `self._window_count >= LOG_SPAM_THRESHOLD_PER_S` (`:180`) can never be true from transitions alone at defaults. Math is exact, not approximated. |
| 2 | ~400/min, ~576k/day is the harm the guard is supposed to prevent, and it's exactly the rate that passes | ✅ Confirmed | `6.6667 × 60 = 400.0`/min, `6.6667 × 86400 ≈ 576,048`/day. The deliverable doc states this number verbatim: "at 0.15 s poll, per-tick journal logging would be ~400 unbuffered syscalls/min" (`docs/measurements/poly-governor-instrumentation-2026-08-21.md:35`). |

### §2 — Structural complaints

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 3 | Daemon has no shutdown path — `time.sleep(3600)`, catches only `KeyboardInterrupt` | ✅ Confirmed | `scripts/surge-poly-governor.py:31-39` is exactly the quoted code. `SIGTERM` does not raise `KeyboardInterrupt` in Python (default disposition terminates the process without running `except`/`finally`), so under `KillMode=mixed`/`TimeoutStopSec=5` neither `governor.stop()` nor `cpu_monitor.stop()` runs. |
| 4 | `session-snapshot-publisher.py` already solved this and `SHUTDOWN.md` documents the `mpe-peak-meter` precedent, quoted verbatim | ✅ Confirmed | `scripts/session-snapshot-publisher.py:42-43` installs `SIGTERM`/`SIGINT`/`SIGHUP` handlers with the exact comment `# Sleep in slices so SIGTERM is honoured promptly rather than at interval edge.` `docs/SHUTDOWN.md` contains the exact sentence: "now `TimeoutStopSec=5` plus interruptible SIGTERM in the binary (JACK leaf client on jackd shutdown)." `native/mpe-peak-meter/mpe-peak-meter.c:319-320` calls `signal(SIGINT, ...)`/`signal(SIGTERM, ...)`. |
| 5 | Worker thread death is unobservable — no heartbeat/`is_alive()` check | ✅ Confirmed | Grepped `surge_poly_governor.py` for `is_alive`, `heartbeat`: none found outside `start()`'s reentrancy guard (`:258`, checks `is_alive()` only to skip a second `start()` call, not to detect death). No liveness signal exists anywhere in `_worker`/`stop`/`snapshot`. |

### §3 — Code Quality

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 6 | Error path now routes through the guard that cannot fire | ✅ Confirmed | `_worker` (`:394-395`): `except Exception as exc: self._journal.log_error(str(exc))` → `log_error` (`:167-168`) → `self._emit(...)`, same `_emit` gated by `LOG_SPAM_THRESHOLD_PER_S` (§1). Confirmed by diff `118a65c..3cc74a4`: previously this line was an unguarded, unprefixed `print(f"Surge poly governor tick error: {exc}", flush=True)`. |
| 7 | `_apply_limit` logs nothing when `send_polylimit` returns `False` | ✅ Confirmed | `patch_browser/surge_poly_governor.py:336-346`: `if send_polylimit(...):` — the `_effective_poly` update and `self._journal.log_transition(...)` call are both inside the `if` body; there is no `else`. |
| 8 | `send_polylimit` prints unprefixed, unguarded, per-attempt | ✅ Confirmed | `patch_browser/surge_playback.py:208-216`: `print(f"Error setting poly limit via OSC: {exc}")` — no `poly-governor:` prefix, no guard, called from `_apply_limit` on every failed tick. |
| 9 | `append_verbose_trace` docstring says "high-rate trace" but is a subset of the journal at identical rate | ✅ Confirmed | Docstring at `:201`: `"""Optional high-rate trace on tmpfs..."""`. Called only from `_emit` (post-suppression-check, `:186`) and `_flush_suppressed` (`:196`) — the same two call sites that already `print()`. No other caller exists (grepped). |
| 10 | `_proc_prev` conjured via `getattr(self, "_proc_prev", None)`, never in `__init__` | ✅ Confirmed | `:375-376` exactly as quoted. `__init__` (`:221-256`) has no `self._proc_prev` assignment. |

### §4 — Code Smells

| # | Finding | Verdict | Evidence |
|---|---------|---------|----------|
| 4.1 | Spam guard cannot fire (🔴) | ✅ Confirmed | See row 1–2. |
| 4.2 | OSC actuation failure bypasses journal (🔴) | ✅ Confirmed | See rows 7–8. Additionally verified: on the `high`/`spike` paths, `_high_since = now` is reassigned unconditionally on re-entry to the hot branch regardless of whether `_apply_limit` actually applied (`:476`, `:490`), so a failing actuation does re-arm every `cpu_high_hold_s` = 0.15s = every tick, exactly as claimed. |
| 4.3 | `stop()` unreachable under systemd (🔴) | ✅ Confirmed | See rows 3–4. `config/surge-poly-governor.service` confirmed to have `KillMode=mixed`/`TimeoutStopSec=5` (read directly), matching the claim. |
| 4.4 | Enable/disable transitions never logged (🔴) | ✅ Confirmed | `_tick()` (`:398-400`): `self._pref_check_counter += 1; if self._pref_check_counter % 4 == 0: self._enabled = governor_active()` — plain reassignment, no comparison, no `_journal` call anywhere in this block or in `__init__`. `governor_active()` = `governor_enabled_by_env() and governor_enabled_by_pref()` (`:106-107`), and `governor_enabled_by_pref()` reads `load_ui_preference("poly_governor_enabled", ...)` (`:102-103`) which reads `UI_STATE_FILE` (`Path.home() / ".patch_browser_ui.json"`) — a file a human can edit from the touch UI at any time, independent of the env var the deliverable cites. The deliverable's claim "Governor remains disabled for measurement" is unfalsifiable from the journal alone if the UI pref flips mid-soak. |
| 4.5 | Verbose trace drops the suppressed lines (🟡) | ✅ Confirmed | `_emit` (`:173-186`): the `return` on the suppression branch (`:182`) is textually before `append_verbose_trace(line)` (`:186`), which only executes after `self._window_count += 1; print(line, flush=True)`. Exactly as quoted. |
| 4.6 | Unbounded append to RAM-backed file, preserved across restarts (🟡) | ✅ Confirmed | `append_verbose_trace` (`:200-211`) opens in `"a"` mode, no size check, no truncate. `config/surge-poly-governor.service` sets `RuntimeDirectory=mpe` and `RuntimeDirectoryPreserve=yes` (both present, confirmed by direct read of the unit file). Correctly scoped 🟡 not 🔴 since gated behind opt-in `MPE_POLY_GOVERNOR_VERBOSE=1`. |
| 4.7 | Startup line omits `patch_warm_window_s` and emergency poly target (🟡) | ✅ Confirmed | `log_startup` (`:127-145`) prints `enabled, floor, poll, emergency, spike, high, warm, low, high_hold, low_hold, step_down, step_down_spike, step_down_warm, step_up` — no `patch_warm_window_s` field exists in `GovernorConfig` output, and `emergency=` is `config.cpu_emergency_threshold` (a CPU %, `:133`), not the `poly_emergency()` voice-count target actually applied at `:424,427`. The doc's "every threshold and step constant is discoverable" (`docs/measurements/...md:14`, exact quote confirmed) is contradicted by this gap. |
| 4.8 | Startup line can log the override, not the effective poll interval (🟡) | ✅ Confirmed | `self.poll_interval = self.config.poll_interval_s if poll_interval is None else poll_interval` (`:239-241`) vs `log_startup` printing `f"poll={config.poll_interval_s} "` (`:132`) — two different attributes. Confirmed latent (no caller passes `poll_interval=` in production; only tests do), as the reviewer states. |
| 4.9 | `_effective_poly` silently overwritten from disk (🟡) | ✅ Confirmed | `_refresh_patch_state` (`:296-317`): timer resets (`_high_since = None`, `_low_since = None`) happen only inside the `patch != self._last_patch` branch (`:306-311`); `self._effective_poly` reassignment at `:317` is unconditional on every mtime bump, with no journal line and no timer reset when the patch name is unchanged. |
| 4.10 | Trace path in doc isn't guaranteed by code (🟡) | ✅ Confirmed | `run_dir()` (`patch_browser/mpe_run_dir.py:9-21`) silently falls back to `${TMPDIR:-/tmp}/mpe` when `/run/mpe` isn't writable, with no log line anywhere in that fallback path. Deliverable states the trace lands at `/run/mpe/poly-governor.trace` unconditionally (`docs/...md:40-41`). |
| 4.11 | Minor bundle (🟢) | ✅ Confirmed | `_proc_prev` — see row 10. Jiffies duplication — `patch_browser/surge_cpu_monitor.py:47,199-221` independently implements `SC_CLK_TCK` + `/proc/<pid>/stat` sampling, confirmed duplicate of `_cpu_sample`'s fallback (`:366-388`). `log_startup` prints directly (`:128`) bypassing `_emit`/trace — confirmed, no `append_verbose_trace` call in `log_startup`. `_high_since` re-churn in emergency branch — confirmed at `:423` (unconditional reassignment every tick while `cpu >= emergency`), flagged in cycle 1 per its own artifact. |

### §5 — Logic & Business Rules

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 11 | Hysteresis ladder ordering (emergency → warm → spike → high → recover) and asymmetric holds are correct/legible | ✅ Confirmed | `_tick()` (`:397-507`) — emergency unconditional first (`:421-434`), warm one-shot inside window (`:436-453`), spike immediate (`:455-466`), high after `cpu_high_hold_s` (`:477-490`), recover after `cpu_low_hold_s` (`:491-504`). `DEFAULT_CPU_HIGH_HOLD_S=0.15` vs `DEFAULT_CPU_LOW_HOLD_S=5.0` — asymmetric as claimed. |
| 12 | `snapshot()` reads four fields cross-thread with no lock — "acceptable rather than correct" | ✅ Confirmed | `snapshot()` (`:282-288`) reads `_enabled, _effective_poly, _ceiling_poly, _floor_poly` with no lock. CPython attribute reads are atomic; independent fields, so benign, matching reviewer's own framing (not calling it a bug). |
| 13 | JSON `Infinity` on `ceiling`/`effective` → `OverflowError` in `_tick`, but one-shot because `_state_mtime` advances before parse | ✅ Confirmed | `read_json_dict` (`patch_browser/json_store.py:12-20`) uses bare `json.loads`, which by default accepts `Infinity`/`-Infinity`/`NaN` tokens (Python's `json` module default `parse_constant`). `_refresh_patch_state` (`:301-317`): `self._state_mtime = stat.st_mtime` (`:303`) executes before `data = read_poly_state()` (`:304`) and before the `int(ceiling)` cast (`:315`). `int(float("inf"))` raises `OverflowError`. Ordering is exactly as described and is genuinely load-bearing but has zero comment or test. |
| 14 | Calibration gap (58.9% measured vs 50.0% `MPE_POLY_CPU_HIGH` default) correctly named and not retuned | ✅ Confirmed | `docs/measurements/poly-governor-instrumentation-2026-08-21.md:171-175` states this exact comparison and explicitly defers retuning to V7. `DEFAULT_CPU_HIGH_THRESHOLD = 50.0` confirmed at `surge_poly_governor.py:29`. |

### §6 — Test Strategy & Execution

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 15 | `test_startup_log_once` leaks a live daemon thread — no `stop()`/`addCleanup` | ✅ Confirmed | `tests/test_surge_poly_governor.py:245-256` — no `addCleanup`, no `stop()` call anywhere in the test. `start()` (`surge_poly_governor.py:258-274`) spawns a real `daemon=True` thread ticking every `self.poll_interval` (unset in this test → `DEFAULT_POLL_INTERVAL_S = 0.15`). |
| 16 | `monitor = mock.Mock()` with no `check_health.return_value` → `healthy, _ = self.surge_monitor.check_health()` raises `TypeError`, caught and routed to `log_error` → `print` | ✅ Confirmed (mechanism); condition **empirically present on this checkout** | Reached only if `_limits_ready()` is already `True` (`:408-409`), which requires a parseable `POLY_STATE_FILE`. `POLY_STATE_FILE = Path.home() / ".patch_browser_poly_state.json"` (`surge_playback.py:22`) — **not patched** in this test. Direct file check on this checkout: `~/.patch_browser_poly_state.json` exists, is valid JSON, contains `{"ceiling_poly": 12, "effective_poly": 9, ...}` — exactly the shape needed to satisfy `_limits_ready()`. `~/.patch_browser_ui.json` does not exist, so `governor_active()` defaults to `True` (env default `"1"`, pref default `True`); confirmed no `MPE_POLY_*`/`MPE_` env vars set in this shell. Under these (unmodified) conditions, a real `unittest` run would very likely reach the `check_health()` unpack, and `mock.Mock()`'s auto-generated return value does not support the iterable-unpacking protocol by default, so `healthy, _ = <Mock>` raises `TypeError`. **Not executed** (no `python3` invocation per operator rule) — rated on static trace plus this file-existence evidence, same caveat the reviewer stated. |
| 17 | Alphabetical test ordering puts `test_startup_log_once` before six named tests, two of which assert `mock_print.assert_not_called()` | ✅ Confirmed | Full sorted method-name order computed from the file: `test_disabled_skips_adjustment, test_emergency_slam_at_90, test_error_log_uses_spam_guard, test_load_governor_config_defaults, test_spam_guard_emits_summary_on_window_roll, test_spam_guard_suppresses_after_threshold, test_spike_steps_down_immediately, test_startup_log_once, test_steps_down_when_cpu_high, test_stop_flushes_suppressed_summary, test_tick_without_poly_state_is_silent, test_unchanged_limit_logs_nothing, test_verbose_trace_written_on_transition, test_warm_preempt_after_patch_change` — the six named tests are exactly the ones alphabetically after `test_startup_log_once`, and `test_unchanged_limit_logs_nothing` (`:213`) / `test_tick_without_poly_state_is_silent` (`:271`) are exactly the two using `mock_print.assert_not_called()`. Python's default `unittest` test loader sorts method names alphabetically (`TestLoader.sortTestMethodsUsing`), so this ordering claim is correct for an unmodified `unittest discover` run. |
| 18 | Two tests validate machinery that cannot engage in production (`test_spam_guard_suppresses_after_threshold`, `test_error_log_uses_spam_guard` pin `time.monotonic` and fire 12 in one window; `test_stop_flushes_suppressed_summary` calls `stop()` directly) | ✅ Confirmed | `:215-234` and `:273-284` both pin `time.monotonic` to a constant via `mock.patch(...return_value=0.5)` and loop 12 times — a scenario impossible at the real 6.67 ticks/s ceiling (§4.1). `:286-294` calls `governor.stop()` directly, which is exactly the call systemd's default `SIGTERM` disposition never reaches (§4.3). |
| 19 | Monkeypatching `time.monotonic` via `mock.patch("patch_browser.surge_poly_governor.time.monotonic", ...)` is process-wide | ✅ Confirmed | `surge_poly_governor.py:7`: `import time` binds the name `time` to the real module object; `mock.patch("<module>.time.monotonic", ...)` resolves through that binding to the actual `time` module and replaces the `monotonic` attribute on the shared module object — every other consumer of `time.monotonic()` in the process sees the patched value for the duration of the `with` block. This is standard, well-documented `unittest.mock` behavior for `import module` (as opposed to `from module import name`) style imports. |
| 20 | Coverage gaps map onto the 🔴 findings (no test for `_worker` catching a raising `_tick`; no test that suppressed lines are absent from the trace; no test verbose-off-by-default; no test of `_apply_limit` on `False`; no test of enable/disable flip logged; `test_emergency_slam_at_90`/`test_spike_steps_down_immediately` don't mock `print`) | ✅ Confirmed | Grepped the full test file for each: no test calls `governor._worker()` directly or forces `_tick` to raise; `test_verbose_trace_written_on_transition` (`:296-313`) only checks a transition line lands in the trace, never checks a *suppressed* line's absence; no test asserts `verbose_trace_enabled()` default with env cleared; no test mocks `send_polylimit` to return `False`; no test asserts an `_enabled` flip produces a print; `test_emergency_slam_at_90` (`:124-139`) and `test_spike_steps_down_immediately` (`:141-156`) indeed have no `mock.patch("builtins.print")` around their `governor._tick()` calls. |

### §7 — Security & Performance

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 21 | No security surface — UDP OSC to `127.0.0.1`, no subprocess, non-root user | ✅ Confirmed | `config/surge-poly-governor.service`: `User=@MPE_PI_USER@`, no `subprocess`/shell anywhere in `surge_poly_governor.py` (grepped). `patch_browser/surge_poly_governor.py` is listed in `PERIODIC_LOOP_MODULES` in `scripts/lib/periodic_loop_lint.py:24` — mechanically enforced, confirmed by direct read. |
| 22 | Cadence/cost table — `_refresh_patch_state` stat at 6.67Hz negligible; `governor_active()`→`load_ui_preference` at 1.67Hz does `exists()+read_text()+json.loads` on a home-directory file; `poly_emergency()` at 6.67Hz negligible; `verbose_trace_enabled()` per emit negligible | ✅ Confirmed | `_pref_check_counter % 4` at `poll_interval=0.15s` → refresh every `4×0.15=0.6s` → `1/0.6=1.667`Hz, exact match. `load_ui_preference` (`ui_prefs.py:47-55`) does exactly `UI_STATE_FILE.exists()` then `.read_text()` + `json.loads` — and `UI_STATE_FILE = Path.home() / ".patch_browser_ui.json"` (`touch_ui_constants.py:7`), a real disk path (SD card on the Pi), not tmpfs. `poly_emergency()` (`surge_playback.py:51-58`) is two env reads + `min()` — negligible, confirmed no I/O. `verbose_trace_enabled()` (`surge_poly_governor.py:110-116`) is one `os.environ.get` — negligible. |
| 23 | `_pref_check_counter % 4` couples pref-refresh cadence to poll interval | ✅ Confirmed | The modulo is on tick count, not wall-clock time (`:398-400`), so lowering `MPE_POLY_POLL_INTERVAL_S` proportionally speeds up the file-read cadence — confirmed by inspection, no independent timer. |
| 24 | `CPUAffinity=0 1` fix is genuine and correctly scoped from the structural `system.conf` fix | ✅ Confirmed | `config/surge-poly-governor.service` has `CPUAffinity=0 1`. Deliverable doc (`docs/measurements/...md:67-118`) separates the applied fix from a report-only table of 20 units and a deferred structural fix — matches the claim exactly. |

### §8 — Developer Experience

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 25 | Three doc sentences are false, quoted verbatim | ✅ Confirmed | All three quotes exact-match `docs/measurements/poly-governor-instrumentation-2026-08-21.md`: "This prevents a miscalibrated threshold ... from turning a tuning bug into an I/O problem" (`:34`), "Set `MPE_POLY_GOVERNOR_VERBOSE=1` to append high-rate diagnostics" (`:40`, reviewer's "high-rate" bolding is their own emphasis on the doc's actual word), "Every threshold and step constant is discoverable from the journal without reading source" (`:14`). Falsity of each independently confirmed above (§4.1, §4.5/§4.9, §4.7). |
| 26 | `RuntimeDirectoryMode` inconsistency — `mpe-jackd.service` sets it, governor unit doesn't | ✅ Confirmed | Grepped all `config/*.service` for `RuntimeDirectoryMode`: only `config/mpe-jackd.service:34` has it (`RuntimeDirectoryMode=0755`). `config/surge-poly-governor.service` has `RuntimeDirectory=mpe`/`RuntimeDirectoryPreserve=yes` but no explicit mode (defaults to `0755` per systemd, so no functional difference — reviewer states this correctly). |

### Prior-cycle findings table

| Cycle 1 finding | Grumpy's cycle-2 verdict | My verdict | Evidence |
|---|---|---|---|
| Exception path spam | "Not fixed" — routed through unreachable guard | ✅ Confirmed | Diff `118a65c..3cc74a4` shows the raw `print(f"Surge poly governor tick error: {exc}", flush=True)` replaced with `self._journal.log_error(str(exc))` → same `_emit` gated by a guard that cannot trip at 6.67 ticks/s (§4.1). The spam is now routed to a valve, but the valve is welded open — "not fixed" is the right characterization, not "fixed." |
| Dead `append_verbose_trace` | "Partially fixed" | ✅ Confirmed | Diff confirms it's newly called from `_emit`/`_flush_suppressed` in this commit (previously unreferenced from any print path). No longer dead, but drops suppressed lines (§4.5) — "partially" is accurate. |
| Suppressed summary on stop | "Fixed in method, unreachable in production" | ✅ Confirmed | `stop()` (`:276-280`) newly calls `self._journal.flush_pending()` (confirmed added in this diff) — but `stop()` itself is only reached via `KeyboardInterrupt` (§4.3), never via systemd's `SIGTERM`. |
| Test stdout leak (P3) | "Still open" | ✅ Confirmed | `test_emergency_slam_at_90` (`:124-139`) and `test_spike_steps_down_immediately` (`:141-156`) still call `governor._tick()` with no `mock.patch("builtins.print")` around them. |

**All four rows in the prior-cycle table check out exactly as stated — no exaggeration, no missed nuance.**

---

## Severity Re-Assessment

| # | Issue | Reviewer's marker | My Rating | Delta | Reasoning |
|---|-------|--------------------|-----------|-------|-----------|
| 4.1 | Spam guard cannot fire at defaults | 🔴 | **High** | ↓ (slightly) | Real and exactly as described, but the reviewer's own verdict concedes "nothing here is a 🔴 in the 'wake someone up' sense" — the 🔴 emoji is being used as "structurally broken instrumentation," a different scale than "stop what you're doing." Defaults are unchanged, no data loss, no audible/functional regression; the actual exposure is that a persistent tick error or actuation failure could write to journald/SD at ~400 lines/min indefinitely. That's an operational hygiene risk on an SD-card appliance, not an incident. **High, not Critical.** |
| 4.2 | Actuation failure invisible to journal | 🔴 | **High** | — | Agree with reviewer. This is the finding most likely to actually bite: a real OSC send failure during a measurement window is silently absorbed, and the branch's entire purpose is to be trustworthy instrumentation. |
| 4.3 | `stop()`/`flush_pending()` unreachable under systemd | 🔴 | **High** | — | Agree. Established precedent exists in this repo (`session-snapshot-publisher.py`, `mpe-peak-meter.c`) making this a known, low-effort fix pattern that just wasn't applied here — that raises confidence this should ship before further soak, not lower urgency. |
| 4.4 | Enable/disable transitions never logged | 🔴 | **High** | — | Agree, and arguably understated in isolation: this is the one gap that directly undermines certifiability of the stated Plan V precondition ("governor off during measurement"). Combined with 4.2/4.3, a bad measurement window could pass silently. |
| 6.1 | Test thread leak / `$HOME`-dependent flakiness | 🔴 | **High** | — | Agree, and I have stronger evidence than the reviewer did: the exact trigger file (`~/.patch_browser_poly_state.json`, valid, parseable) is present on this very checkout, and no `MPE_POLY_*` env vars are set, so `_limits_ready()` and `governor_active()` both land in the state needed to reach the `Mock()`-unpacking `TypeError`. This is not a hypothetical — it is a live landmine in any developer or CI environment that has ever run the patch browser or written that file. |
| 4.5 | Verbose trace drops suppressed lines | 🟡 | **Medium** | — | Agree. Opt-in feature, but defeats its own stated purpose once 4.1 is fixed. |
| 4.6 | Unbounded tmpfs append | 🟡 | **Low-Medium** | ↓ | Agree it's real, but this is gated behind an explicit opt-in env var on a 4-core Pi with existing memory budgeting; "left on after a debugging session" is a real but self-inflicted failure mode. Low effort to fix, so priority stays but severity is a notch below the 🔴s. |
| 4.7–4.10 | Startup line gaps / stale path claim | 🟡 | **Low-Medium** | — | Agree with reviewer across the board — all four are documentation-accuracy issues rather than behavioral bugs, correctly scoped 🟡. |
| 6.2 | Tests validate unreachable machinery | 🟡 | **Medium** | — | Agree — this is a direct symptom of 4.1/4.3 rather than an independent test bug; fixing the underlying code makes these tests meaningful again rather than needing separate remediation. |
| 6.3 | Process-wide `time.monotonic` patch | 🟡 | **Low** | ↓ | Technically correct and worth fixing, but in practice only matters *because* of 6.1's leaked thread; if 6.1 is fixed (thread properly stopped before other tests run), the blast radius of this pattern shrinks to "ugly but harmless" for the current test suite. Still worth the one-line fix, but I would not block on it independently. |
| 6.4 | Coverage gaps | 🟡 | **Medium** | — | Agree — these are the direct test-side complement of the 🔴 findings and should land in the same PR as the fixes, not as separate backlog. |

---

## What the Review Missed

The review is unusually thorough — after this audit's exhaustive line-by-line pass, only minor additions surface:

1. **`main()` in `scripts/surge-poly-governor.py` has no exception handling around `cpu_monitor.start()`/`governor.start()`.** If either raises during startup, the process exits with a traceback and neither the (non-existent) SIGTERM handler nor any cleanup runs. Minor — same root cause as §4.3, not a separate defect worth a new line item.

2. **`start()`'s reentrancy guard (`if self._thread and self._thread.is_alive(): return`) is not thread-safe against concurrent callers**, though nothing in the current codebase calls `start()` from more than one thread. Purely theoretical; not worth adding to the matrix.

3. **The emergency branch's `minimum=emergency` clamp in `_apply_limit` interacts with 4.9**: if `_effective_poly` is silently overwritten from disk (4.9) to a value already below `emergency`, the `self._effective_poly > emergency` guard at `:425` will be `False` and the emergency slam simply won't fire on that tick, with no log line explaining why. This is a second-order consequence of 4.9 that the review didn't spell out, but it doesn't change 4.9's severity or fix — it's additional justification, not a new finding.

4. **Nothing security-relevant was missed.** Confirmed no path handling untrusted input differently than described, no new attack surface introduced by this branch.

The review did not manufacture anything; every 🔴/🟡/🟢 item traces to real code, and the "what the review missed" list above is genuinely thin — this is a well-executed review.

---

## What the Review Got Right (And Why It Matters)

**§4.1 (spam guard) is the load-bearing finding of the whole review**, and it deserves the emphasis the reviewer gives it in §1. The downstream chain is: cycle 1 asked for exception-path spam to stop → cycle 2 routed it through a guard → the guard is mathematically incapable of engaging at the shipped poll interval → the four tests that exercise the guard all pin `time.monotonic` to defeat the real tick ceiling, so they're green regardless → the deliverable doc asserts the guard "prevents a miscalibrated threshold from turning a tuning bug into an I/O problem," a sentence that becomes false the moment someone reads the constants next to each other. Every one of those links checks out. The compound effect is real: this is not five independent nits, it's one causal chain from a design decision (route errors through the transition guard) through an unexamined constant relationship (10 > 6.67) to a false claim in the deliverable, and the tests actively hide it rather than catching it.

**§4.4 (enable/disable not logged) matters more than its 🟡-adjacent framing in isolation would suggest**, because it's the one gap that touches the actual experiment. Plan V's premise — "governor off during measurement" — is currently a claim about environment configuration that the journal cannot corroborate if a human touches the UI toggle mid-soak. Combined with §4.2 (actuation failures also invisible), a bad measurement window looks identical to a good one from the journal. That's the exact "failure indistinguishable from success" framing the reviewer opens with, and it's correct.

**§6.1 (test thread leak) is the strongest individual catch in this review**, and my independent verification found it to be *more* certain than the reviewer's own hedge suggests. They flagged it as needing an actual run to confirm and noted the trigger file "exists on this checkout" without further comment. I checked: `~/.patch_browser_poly_state.json` is present, valid, and contains exactly the shape (`ceiling_poly`, `effective_poly` both present as numbers) needed to satisfy `_limits_ready()`, and no `MPE_POLY_*` env var is set in this environment, so `governor_active()` resolves to its enabled default. That means on any machine with that file (which the patch browser itself writes during normal use), running the test suite is likely to non-deterministically fail one of two unrelated tests depending on real-wall-clock scheduling of a leaked background thread. This is exactly the kind of "green until it isn't, for a reason nobody put in the PR description" bug this project has been burned by before.

---

## Prioritized Action Matrix

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

## Disagreements and Judgment Calls

1. **The review's own 🔴 usage is internally inconsistent with its verdict.** Five items are marked 🔴 (§4.1–4.4, §6.1), but the Verdict section explicitly says "nothing here is a 🔴 in the 'wake someone up' sense." That's not wrong, but it means the reviewer is using 🔴 as "structurally broken, fix before merge" rather than the "stop what you're doing" bar this audit's own priority scale uses for P0. I've rated all five as **High/P1** rather than treating the emoji as a literal P0 signal — which matches what the reviewer's prose actually argues, just not what the emoji visually implies. Worth flagging so a reader skimming for 🔴 counts doesn't over- or under-react.

2. **I would not gate the §6.3 (`time.monotonic` patch scope) fix on its own PR.** The reviewer lists it as a separate 🟡 finding with its own fix. I'd bundle it into the §6.1 fix (stop leaking the thread) — once nothing is running in the background during other tests, the process-wide patch's blast radius drops to "cosmetically wrong" rather than "actively dangerous," so it's reasonable to defer the clock-injection refactor to whenever `PolyGovernorJournal`'s constructor is next touched, rather than treating it as independently urgent.

3. **§4.6 (unbounded trace) — I'd rate this a notch lower than the reviewer's 🟡 given it's opt-in and Low-Medium is a more honest severity than treating it on par with §4.5.** Not a disagreement on whether to fix it (agree, quick fix, low priority is fine), just on how much anxiety the write-up should carry: "left on after a debugging session" is a real but rare, self-inflicted, easily-diagnosed failure mode (the fix is "restart the unit"), not a silent creeping problem like the journal-side issues.

4. **No disagreement on Task C (Surge engine research) or the calibration-gap framing** — both read as genuinely careful work and the reviewer's praise is proportionate, not padding.

5. **No big-team-standard-on-small-team-context complaints to raise.** Every P1 in this matrix is either a quick fix or a half-day, all localized to files already in scope, and all trace to a real gap between the branch's stated purpose (trustworthy instrumentation) and its current behavior. This isn't the review holding a measurement branch to production-service standards it doesn't need — the branch's own job is to be the thing people trust when reading the journal, so "the journal can lie by omission in five specific ways" is squarely in scope regardless of team size.

---

## Verdict

This is one of the more rigorous grumpy reviews I've audited: **every single claim I checked — all ~26 distinct assertions across ten code smells, four test-strategy findings, the logic section, the performance table, and the four prior-cycle re-verifications — held up exactly as stated, with no exaggeration, no missing context, and no incorrect quotes.** Where I found room to push back, it was on severity framing (the 🔴 vs. "wake someone up" inconsistency, §6.3's dependency on §6.1, §4.6's opt-in framing) rather than on any factual claim. I additionally strengthened one finding beyond what the reviewer could confirm: the §6.1 test-thread-leak trigger condition (`~/.patch_browser_poly_state.json` present and parseable, no `MPE_POLY_*` overrides) is not hypothetical — it is the actual state of this checkout right now, checked directly rather than inferred.

**Recommendation: fix the five P1s before further Pi soak.** They form one causal chain (a guard that can't fire, feeding a doc claim that's false, feeding tests that can't tell the difference) plus two independent observability gaps (actuation failure, enable/disable transitions) that specifically undermine the branch's stated purpose of producing a measurement the next person can trust from the journal alone. None of the five is more than a half-day; all five together are well inside a single PR. The P2/P3 backlog is real but does not block merge to `dev` once the P1s land.

**P0 count: 0. P1 count: 5. Artifact: `/home/claude-sandbox/workspace/MPE-Module/Documents/reviews/review-audit-poly-governor-instrumentation-cycle2-2026-08-22.md`.**
