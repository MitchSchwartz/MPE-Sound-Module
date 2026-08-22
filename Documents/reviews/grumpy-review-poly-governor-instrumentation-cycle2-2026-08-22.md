# Grumpy review — poly-governor-instrumentation, cycle 2 (2026-08-22)

**Scope:** `poly-governor-instrumentation`
**Branch:** `yolo/poly-governor-instrumentation` (post PR-review fixes, `3cc74a4`)
**Cycle:** 2
**Reviewer stance:** grumpy senior dev who would have to keep this appliance alive

**Files read in full:** `patch_browser/surge_poly_governor.py` (507 lines),
`config/surge-poly-governor.service`, `tests/test_surge_poly_governor.py` (317),
`docs/measurements/poly-governor-instrumentation-2026-08-21.md` (181),
`scripts/surge-poly-governor.py`, `patch_browser/mpe_run_dir.py`,
`patch_browser/json_store.py`, plus the poly helpers in `patch_browser/surge_playback.py`,
`load_ui_preference` in `patch_browser/ui_prefs.py`, the `SurgeCpuMonitor` lifecycle,
`scripts/lib/periodic_loop_lint.py`, and the five sibling units that share `/run/mpe`.

**Not read:** the rest of `surge_playback.py`, the touch UI, anything looper. Out of scope.

**Not executed:** the unit suite. Direct `python3` invocation is prohibited by the operator
rules in force for this session and there is no `mpe` subcommand that runs the suite. Every
test finding below is static reading, and I flag where execution is required to confirm.
**Finding 6 in particular needs a real run to confirm — but the trigger file it depends on
exists on this checkout.**

---

## 1. First Impressions (The Gut Check)

This looks like professionals work here. The journal-vs-tmpfs split is a real design decision,
not a log statement someone dropped in; the transition line is a single grep-able record with
old, new, reason, CPU, patch and hold time; the deliverable doc quantifies the I/O harm it is
trying to avoid instead of hand-waving. Task C is genuinely good work — it reads Surge source,
cites `softkillVoice()`/`uber_release()`, corrects a claim in the class docstring, and refuses
to retune anything before V7. That last part is the discipline that keeps measurement projects
honest, and it is rarer than it should be.

Then I did the arithmetic on the spam guard and my mood changed.

The poll interval is 0.15 s. That is 6.67 ticks per second, and the loop emits at most one line
per tick. The spam guard trips at 10 lines per second. **The guard cannot fire at shipped
defaults.** Not "rarely fires" — cannot. Which means the four tests covering it, the
suppressed-count summary, the `stop()` flush that cycle 1 asked for and this cycle added, and
the paragraph in the deliverable claiming the guard "prevents a miscalibrated threshold from
turning a tuning bug into an I/O problem" are all describing machinery that is unreachable in
production. The doc even cites the correct harm number — "~400 unbuffered syscalls/min" — which
is 6.67/s, the exact rate that sails through the guard untouched.

This is the failure mode `AGENTS.md` warns about in its own words: *the failure is
indistinguishable from the success.* The tests are green. The instrument is not connected.

## 2. Architecture & Structure

The module boundaries are sensible. `PolyGovernorJournal` is a separate class with a narrow
surface (`log_startup`, `log_transition`, `log_error`, `flush_pending`), injectable via the
constructor, and the tests exercise it standalone. `GovernorConfig` is a frozen dataclass built
by one `load_governor_config()` reader, so there is exactly one place where environment meets
policy. `Reason` as a `Literal` keeps the transition vocabulary closed. All good.

Dependencies are unchanged — no new third-party anything, `pythonosc` imported lazily in the
entrypoint with a clean error path. `patch_browser/surge_poly_governor.py` is already in
`PERIODIC_LOOP_MODULES` in `scripts/lib/periodic_loop_lint.py`, so the no-forks-in-loops rule is
mechanically enforced on this file rather than trusted. That is the right way to hold a rule.

Two structural complaints.

**The daemon has no shutdown path.** `scripts/surge-poly-governor.py` sleeps in
`time.sleep(3600)` and catches only `KeyboardInterrupt`. systemd sends `SIGTERM`, which Python
does not translate into an exception, so `governor.stop()` and `cpu_monitor.stop()` never run
under the service manager. This repo already solved this problem twice and wrote it down:
`scripts/session-snapshot-publisher.py` installs handlers for `SIGTERM`/`SIGINT`/`SIGHUP` and
sleeps in slices with the comment *"Sleep in slices so SIGTERM is honoured promptly rather than
at interval edge"*, and `docs/SHUTDOWN.md` records that `mpe-peak-meter.service` got
"`TimeoutStopSec=5` **plus interruptible SIGTERM in the binary**." The governor unit copied the
`TimeoutStopSec=5` half and skipped the half that does the work.

**The worker thread's death is unobservable.** `_worker` is a daemon thread. If it ever exits,
the main thread keeps sleeping, systemd keeps reporting `active (running)`, and `Restart=on-failure`
has nothing to react to. There is no heartbeat, no liveness line, no `is_alive()` check. For a
service whose entire purpose this branch is *instrumentation*, the one thing it cannot report is
its own absence.

## 3. Code Quality

Naming is clear throughout — `_high_since`, `_low_since`, `_warm_preempt_done`, `step_down_spike`
all say what they are, and the `Reason` literals match the strings that land in the journal, so a
journal line greps back to a code branch with no translation layer. `_env_float`/`_env_int`
swallow `ValueError` and fall back to the default, which is correct for an appliance that must
boot with a fat-fingered `mpe.env`.

Error handling is where it thins out.

- `_worker`'s `except Exception` routes to `log_error`, which routes to the guard that cannot
  fire (§1). Cycle 1 flagged "exception path spam"; this cycle routed it through a guard that
  does not engage at the shipped cadence. The pipe is connected to a valve that is welded open.
- `_apply_limit` (line 336) logs **nothing** when `send_polylimit` returns `False`. The governor
  silently fails to actuate, retries next tick, and the journal shows a gap.
- `send_polylimit` in `surge_playback.py` prints its own unprefixed
  `"Error setting poly limit via OSC: …"` on every failure — no `poly-governor:` prefix, no
  guard, no trace, once per tick.

Dead-ish code: nothing outright dead this cycle — cycle 1's `append_verbose_trace` is now wired
into `_emit` and `_flush_suppressed`. But its docstring still says "high-rate trace," which is
false: it is called only from the two places that already printed to the journal, so at defaults
it is a strict subset of the journal at identical rate, and it never contains anything the
journal lacks. See §4 for the part that actually matters.

Type usage is good — `Literal` for reasons, `frozen=True` on the config, `| None` where genuinely
optional. One exception: `_proc_prev` is conjured at line 375–376 via
`getattr(self, "_proc_prev", None)` and never declared in `__init__`, so the one piece of mutable
state in the fallback CPU sampler is invisible to anyone reading the constructor.

## 4. Code Smells (The Hall of Shame)

### 🔴 4.1 The spam guard cannot fire at shipped defaults

`patch_browser/surge_poly_governor.py:39,173-186` and `DEFAULT_POLL_INTERVAL_S = 0.15`:

```python
LOG_SPAM_THRESHOLD_PER_S = 10
...
if self._window_count >= LOG_SPAM_THRESHOLD_PER_S:
    self._suppressed += 1
    return
```

`_worker` ticks every `poll_interval` (0.15 s → 6.67 ticks/s) and `_tick` reaches at most one
`_apply_limit` per tick, so a maximum of ~7 emits can land in any one-second window. `7 < 10`.
The guard, `_suppressed`, `_flush_suppressed`, `flush_pending`, and the `stop()` flush added this
cycle are all unreachable unless someone sets `MPE_POLY_POLL_INTERVAL_S` below `0.1`.

Meanwhile the real worst case — one journal line per tick, forever, from a persistently failing
tick or actuation — passes through at 6.67 lines/s ≈ 400/min ≈ 576k lines/day into journald and
onto the SD card. That is precisely the number the deliverable cites as the harm it prevents.

**Fix:** the threshold must sit *below* the tick rate to mean anything. Either set it from the
poll interval (e.g. `max(2, int(0.5 / poll_interval_s))`) or, better for the error path
specifically, dedupe on message identity with exponential backoff — a repeated identical tick
error should log once, then at 1 s, 2 s, 4 s … capped. And assert the invariant in a test:
`LOG_SPAM_THRESHOLD_PER_S < 1.0 / DEFAULT_POLL_INTERVAL_S`.

### 🔴 4.2 OSC actuation failure bypasses the entire journal design

`patch_browser/surge_poly_governor.py:336`:

```python
if send_polylimit(self.osc_client, new_limit):
    self._effective_poly = new_limit
    self._journal.log_transition(...)
```

and `patch_browser/surge_playback.py:208-216`:

```python
def send_polylimit(osc_client, voice_count) -> bool:
    ...
    except Exception as exc:
        print(f"Error setting poly limit via OSC: {exc}")
        return False
```

On failure the governor logs nothing, `_effective_poly` stays put, and the same branch retries
next tick (the high path resets `_high_since = now`, so it re-arms every `cpu_high_hold_s` =
0.15 s = every tick). The only evidence is an unprefixed line from another module, emitted at
tick rate, outside the guard and outside the trace. A `poly-governor:`-filtered journal read —
which is how the deliverable tells you to read this — shows a governor that has gone quiet, not
one that is failing to actuate 6.67 times a second.

**Fix:** log a guarded `poly-governor: send failed target=<n> reason=<r>` from `_apply_limit`
on the `False` branch, once per state change rather than per attempt.

### 🔴 4.3 `stop()` is unreachable under systemd — cycle 1's fix is test-only

`scripts/surge-poly-governor.py`:

```python
    try:
        while True:
            import time

            time.sleep(3600)
    except KeyboardInterrupt:
        governor.stop()
        cpu_monitor.stop()
```

`SIGTERM` does not raise `KeyboardInterrupt`. Under `surge-poly-governor.service`
(`KillMode=mixed`, `TimeoutStopSec=5`) the process dies at default disposition and neither
`governor.stop()` nor `cpu_monitor.stop()` runs. `test_stop_flushes_suppressed_summary` calls
`governor.stop()` directly, so it passes while proving nothing about the production path.

**Fix:** copy the pattern from `scripts/session-snapshot-publisher.py` — install handlers for
`SIGTERM`/`SIGINT`, set a `threading.Event`, wait on it instead of `time.sleep(3600)`, and call
`stop()` on the way out. Also hoist `import time` to module scope while you are in there.

### 🔴 4.4 Enable/disable transitions are never logged

`patch_browser/surge_poly_governor.py:399-401`:

```python
self._pref_check_counter += 1
if self._pref_check_counter % 4 == 0:
    self._enabled = governor_active()
```

`enabled=` appears exactly once, in the startup line, from the value sampled at construction.
After that the flag is re-read every 4 ticks (~0.6 s) and reassigned with no comparison and no
journal line. The deliverable states "**Governor remains disabled** for measurement
(`MPE_POLY_GOVERNOR=0` on the Pi during Plan V)" — but `governor_active()` is `env AND
UI preference`, and the UI preference is a separate file a human can flip from the touch screen.
If it flips mid-soak, the governor starts actuating and the journal never says so.

That is not a logging nit. It means a Plan V measurement window cannot be shown, from the
journal alone, to have run with the governor off. The instrumentation cannot certify the
condition the measurement depends on.

**Fix:** compare before assigning and emit `poly-governor: enabled 0 -> 1 (env=… pref=…)` on
change. It is a state transition, which is exactly what this journal is for.

### 🟡 4.5 The verbose trace drops precisely the lines it exists to capture

`patch_browser/surge_poly_governor.py:173-190` — `_emit` returns on the suppression branch
*before* reaching `append_verbose_trace(line)`, which is only called on the paths that already
printed:

```python
if self._window_count >= LOG_SPAM_THRESHOLD_PER_S:
    self._suppressed += 1
    return          # <-- no append_verbose_trace() below this

self._window_count += 1
print(line, flush=True)
append_verbose_trace(line)
```

The stated purpose of a tmpfs trace is to hold detail the journal must not take. Instead the
trace holds a copy of what the journal already has and discards the suppressed lines — the only
lines that were ever unique to it. If §4.1 is fixed so the guard actually fires, this becomes
the bug that eats the evidence.

**Fix:** call `append_verbose_trace(line)` before the suppression check, so the trace is complete
and the journal is the throttled view.

### 🟡 4.6 Unbounded append to a RAM-backed file, preserved across restarts

`append_verbose_trace` opens `run_dir() / "poly-governor.trace"` in `"a"` mode with no size cap,
no rotation, and no truncate-at-startup. `config/surge-poly-governor.service` sets
`RuntimeDirectory=mpe` **and** `RuntimeDirectoryPreserve=yes`, so the file survives unit restarts
and keeps growing. `/run` is tmpfs — this is RAM on a 4-core Pi that is already CPU- and
memory-budgeted. Gated behind opt-in `MPE_POLY_GOVERNOR_VERBOSE=1`, which is the only reason this
is 🟡 and not 🔴, but "opt-in" and "left on after a debugging session" are the same state.

**Fix:** truncate on startup (`"w"` for the first write) and stop appending past a byte cap
(a few hundred KB is plenty), logging once when the cap is hit.

### 🟡 4.7 The startup line under-reports the config it claims to make discoverable

`log_startup` (line 127) prints `poll`, `emergency`, `spike`, `high`, `warm`, `low`, `high_hold`,
`low_hold`, `step_down`, `step_down_spike`, `step_down_warm`, `step_up`, `floor`, `enabled`. It
omits `patch_warm_window_s` — which is in `GovernorConfig` and gates the `warm` reason — and the
`poly_emergency()` voice target the emergency slam actually sets. The deliverable claims "Every
threshold and step constant is discoverable from the journal without reading source." It is not:
a `reason=warm` line cannot be interpreted without the window, and `reason=emergency` does not
reveal what limit it slammed to beyond the transition's `new_limit`.

**Fix:** add `warm_window=` and `emergency_poly=` to the startup line, or drop the "every" claim.

### 🟡 4.8 The startup line can lie about the poll interval

`patch_browser/surge_poly_governor.py:239` vs `:132`:

```python
self.poll_interval = (
    self.config.poll_interval_s if poll_interval is None else poll_interval
)
...
f"poll={config.poll_interval_s} "        # in log_startup
```

Pass `poll_interval=` explicitly and the journal reports the config value while the loop runs at
the override. Production does not pass it, so this is latent — but it is a lie in the
instrumentation, in the branch whose deliverable is the instrumentation.

**Fix:** log `self.poll_interval`, not `config.poll_interval_s`.

### 🟡 4.9 `_effective_poly` is silently overwritten from disk

`_refresh_patch_state` reassigns `self._effective_poly` from the state file whenever its mtime
advances, with no journal line. The governor never writes the file back after actuating, so the
limit it believes in can be replaced by whatever the UI last wrote. The hysteresis timers reset
only on *patch name* change, so an effective-poly change under the same patch name leaves
`_high_since` intact. Net effect: a `12 -> 10` transition line whose `old_limit` came from an
unlogged file write, and a journal from which the limit timeline cannot be reconstructed.

**Fix:** emit a guarded line when `_effective_poly` changes from a source other than
`_apply_limit`, and reset the timers when it does.

### 🟡 4.10 The trace path in the doc is not the path the code guarantees

`run_dir()` (`patch_browser/mpe_run_dir.py`) falls back to `$TMPDIR/mpe` when `/run/mpe` is not
writable, silently. The deliverable states the trace lands at `/run/mpe/poly-governor.trace`.
All five units sharing `/run/mpe` run as `@MPE_PI_USER@`, so ownership is consistent and the
fallback is unlikely — but if it ever triggers, you enable verbose mode, find
`/run/mpe/poly-governor.trace` absent, and conclude no transitions occurred.

**Fix:** log the resolved trace path in the startup line when verbose is enabled.

### 🟢 4.11 Minor

- `_proc_prev` created via `getattr` at lines 375–376, never declared in `__init__`.
- The `/proc/<pid>/stat` fallback in `_cpu_sample` duplicates sampling logic `SurgeCpuMonitor`
  already owns (including its own `SC_CLK_TCK` handling) — two copies of the same jiffies math.
- `log_startup` prints directly rather than through `_emit`, so the trace file has no config
  header.
- The emergency branch reassigns `_high_since = now` every tick while hot. Harmless, pre-existing,
  flagged in cycle 1, still there.
- Stray double blank line before `test_tick_without_poly_state_is_silent`.

## 5. Logic & Business Rules

The hysteresis ladder is legible and the ordering is right: emergency first and unconditional,
then the one-shot warm preempt inside the post-patch window, then spike (immediate on arrival,
`step_down_spike`), then sustained high (`step_down` after `cpu_high_hold_s`), then recovery
(`step_up` after a much longer `cpu_low_hold_s` = 5 s). Asymmetric hold times are the correct
shape for a load governor — fast down, slow up. `_warm_preempt_done` makes the warm step
genuinely one-shot per patch change. `_limits_ready()` gates every arithmetic path, so the
`self._effective_poly > self._floor_poly` comparisons cannot hit `None`. Clamping runs through
one `clamp_poly_limit` with an explicit `minimum=emergency` for the slam, so the emergency path
can legitimately go below the normal floor and nothing else can.

The comment at `_cpu_sample` — "Prefer raw proc/OSC sample — smoothed meter lags on rising load"
— is exactly the kind of comment worth having: it states a constraint the code cannot show.

Two logic notes beyond the smells above.

**Threading.** All mutable governor state is touched only by the worker thread, so the missing
locks are fine — except `snapshot()`, which reads four fields from a caller's thread. Individual
attribute reads are atomic under CPython and the fields are independent, so this is acceptable
rather than correct; worth a one-line comment saying so deliberately.

**Corrupt state file.** `read_json_dict` returns `{}` on `OSError`/`JSONDecodeError` and coerces
non-dict JSON to `{}`, so the common corruption cases are absorbed. `int(ceiling)` on a JSON
`Infinity` (which `json.loads` accepts by default) would raise `OverflowError` out of `_tick` —
but `_state_mtime` is advanced *before* the parse, so it is a one-shot error rather than
permanent spam. That ordering is load-bearing and undocumented; it deserves a comment before
someone "cleans it up" by moving the assignment after the parse and converts a single log line
into 6.67/s forever.

**Calibration.** The doc's own open question is the biggest logical issue on the branch and it is
handled correctly: measured baseline ≈ 58.9% against a `MPE_POLY_CPU_HIGH` default of 50.0 means
the governor would sit in near-permanent step-down during ordinary playing. Naming that, and
explicitly refusing to retune before the V7 capacity curve, is the right call.

## 6. Test Strategy & Execution

Fourteen tests. What they cover well: every reason branch (`high`, `spike`, `warm`, `emergency`,
`recover` by omission), the disabled path, the unchanged-limit silence, the missing-state-file
silence, startup-logged-once, and — the standout — `test_load_governor_config_defaults` under
`mock.patch.dict(os.environ, {}, clear=True)`, which is the correct way to prove "defaults =
shipped constants" and is the single most valuable test here given the branch promises no
behaviour change. `test_unchanged_limit_logs_nothing` asserting `print` was never called is the
right way to test a negative.

Now the problems.

### 🔴 6.1 `test_startup_log_once` leaks a live worker thread into the rest of the suite

```python
governor = SurgePolyGovernor(osc, surge_monitor=monitor, journal=journal)
with mock.patch("builtins.print") as mock_print:
    governor.start()
    governor.start()
```

No `stop()`, no `addCleanup`, no `tearDown`. `start()` spawns a real daemon thread ticking every
0.15 s for the remainder of the process. `monitor` is a bare `mock.Mock()` with no
`check_health.return_value`, so `healthy, _ = self.surge_monitor.check_health()` unpacks a plain
`Mock` and raises `TypeError`, which `_worker` catches and routes to `log_error` → `print`.

For that to be reached, `_limits_ready()` must be true, which needs a parseable poly state file.
`_refresh_patch_state` reads the module global `POLY_STATE_FILE`, which is
`Path.home() / ".patch_browser_poly_state.json"` by default — **present on this checkout** — and
is monkeypatched to a *valid* temp state file by six later tests. Alphabetical ordering puts
`test_startup_log_once` before `test_steps_down_when_cpu_high`,
`test_stop_flushes_suppressed_summary`, `test_tick_without_poly_state_is_silent`,
`test_unchanged_limit_logs_nothing`, `test_verbose_trace_written_on_transition` and
`test_warm_preempt_after_patch_change`. Two of those assert `mock_print.assert_not_called()`.

So a leaked thread emitting `poly-governor: tick error …` at 6.67/s lands inside another test's
mocked `print` and fails an unrelated assertion, non-deterministically, depending on whether a
file in the developer's home directory exists and parses. This needs an actual run to confirm the
race lands, which I could not perform — but the mechanism and the trigger file are both present
here, and a suite whose result depends on `$HOME` is broken regardless of which way the coin
lands today.

**Fix:** `self.addCleanup(governor.stop)`, and patch `POLY_STATE_FILE` in every test that
constructs a governor so no test ever touches the real home directory.

### 🟡 6.2 Two tests validate machinery that cannot engage in production

`test_spam_guard_suppresses_after_threshold` and `test_error_log_uses_spam_guard` both pin
`time.monotonic` to a constant and fire 12 emits "within" one window. At the shipped 0.15 s poll
the loop can produce at most ~7. `test_stop_flushes_suppressed_summary` calls `stop()` directly,
which systemd never does (§4.3). Three green tests, three unreachable paths — and per `AGENTS.md`
that is the shape to be most suspicious of.

**Fix:** after §4.1 and §4.3, add a test asserting
`LOG_SPAM_THRESHOLD_PER_S < 1.0 / DEFAULT_POLL_INTERVAL_S` and a test that the entrypoint's
`SIGTERM` handler flushes.

### 🟡 6.3 Monkeypatching `time.monotonic` is process-wide, not module-scoped

`mock.patch("patch_browser.surge_poly_governor.time.monotonic", …)` resolves
`…surge_poly_governor.time` to the *real* `time` module and patches the attribute globally for the
duration. Combined with 6.1's leaked thread, the suite is patching a clock out from under a live
worker.

**Fix:** patch a module-level indirection, or inject a clock into `PolyGovernorJournal`.

### 🟡 6.4 Coverage gaps that map exactly onto the 🔴 findings

- No test that `_worker` catches a raising `_tick` — `log_error` is only tested directly.
- No test that suppressed lines are *absent* from the trace (§4.5), so the defect is
  untested by construction.
- No test that verbose mode is off by default.
- No test of `_apply_limit` when `send_polylimit` returns `False` (§4.2) — the actuation-failure
  path has zero coverage.
- No test that an enable/disable flip is logged (§4.4), because it isn't.
- `test_emergency_slam_at_90` and `test_spike_steps_down_immediately` still do not mock `print`,
  so the suite spews governor lines. Cycle 1 flagged this as P3; the cycle-1 audit dispositioned
  it "mock print optional." It is still open.

## 7. Security & Performance

No security surface worth losing sleep over: no network listener, no secrets, UDP OSC to
`127.0.0.1`, env vars read as scalars with typed fallbacks, no shell, no `subprocess` anywhere in
the loop (mechanically enforced by `periodic_loop_lint.py`). The unit runs as `@MPE_PI_USER@`, not
root. Fine.

Performance is where `AGENTS.md` sets a specific bar — *"Before adding any polling loop, watchdog
tick, or timer … Compute cost × cadence and put it in the PR"* — and the deliverable's
Verification section answers only "No subprocess forks added to 0.15 s loop." That is the fork
rule, not the cadence budget. The unaccounted per-second costs at defaults:

| Path | Cadence | Cost per call |
|---|---|---|
| `_refresh_patch_state` → `POLY_STATE_FILE.stat()` | 6.67 Hz | one `stat(2)`, VFS-cached — negligible |
| `governor_active()` → `load_ui_preference` | 1.67 Hz | `exists()` + `read_text()` + `json.loads` on a **home-directory** file |
| `poly_emergency()` while CPU ≥ 90 | 6.67 Hz | two env reads + clamp — negligible |
| `verbose_trace_enabled()` per emit | per emit | one env read — negligible |

The one that deserves a number is `load_ui_preference`: ~1.67 file reads + JSON parses per second,
forever, against a file on the SD card rather than tmpfs. Not a fork, not remotely 400 ms, and the
page cache will absorb the reads — but this is exactly the "compute cost × cadence" the rule asks
you to state rather than leave for a reviewer to derive. `_pref_check_counter % 4` also silently
couples the pref-refresh cadence to the poll interval: lower `MPE_POLY_POLL_INTERVAL_S` for a
measurement and the file read speeds up with it.

`CPUAffinity=0 1` on the unit is a genuine performance fix and correctly separated in the doc as a
pre-existing defect found by the same survey. Pinning a `SCHED_OTHER` helper off CPU2–3 where
`mpe-jackd` (FF 70) and `surge-xt-cli` (FF 65) live is right, and the cache-competition rationale
is the correct reason rather than a preemption hand-wave. The report-only table of the twenty
units with no affinity, plus the deferred `system.conf` structural fix gated on a touch-browser
responsiveness check, is how this should be handled.

## 8. Developer Experience

A new dev could onboard onto this module in an afternoon. `AGENTS.md` routes correctly, the
deliverable doc explains what each journal line means and lists every env var against its default,
and Task C means the next person to touch the actuation layer does not have to re-read Surge
source. `docs/CODE-MAP.md` and `docs/SHUTDOWN.md` both already reference the governor.

But the documentation is lying in three specific places, and this branch's product *is*
documentation plus journal lines:

1. "This prevents a miscalibrated threshold … from turning a tuning bug into an I/O problem" —
   the guard cannot fire at the shipped poll interval (§4.1).
2. "Set `MPE_POLY_GOVERNOR_VERBOSE=1` to append **high-rate** diagnostics" — the trace is written
   only on state change, at exactly journal rate, and is a strict subset of the journal (§4.5).
3. "Every threshold and step constant is discoverable from the journal without reading source" —
   `patch_warm_window_s` and the emergency poly target are not printed (§4.7).

A doc that overstates the instrument is worse than no doc, because the next person budgets their
attention against it. Fix the code and these three sentences become true; fix nothing and they
become the stale claims `AGENTS.md` spends a whole section warning about.

Deploy story is unchanged and fine — `install-units.sh` picks up the unit, `configure-pi-paths.sh`
applies it, `TimeoutStopSec=5` matches the `SHUTDOWN.md` convention. One gap: the unit sets
`RuntimeDirectory=mpe` without `RuntimeDirectoryMode=0755`, while `mpe-jackd.service` sets it
explicitly. Same default, same user, so no functional difference — but the inconsistency will make
someone check.

---

## Prior-cycle findings — verification

| Cycle 1 finding | Status | Evidence |
|---|---|---|
| Exception path spam | ❌ **Not fixed** | Routed through `LOG_SPAM_THRESHOLD_PER_S = 10`, which cannot trip at 6.67 ticks/s. A persistent tick error still emits ~400 journal lines/min (§4.1) |
| Dead `append_verbose_trace` | ⚠️ **Partially fixed** | Now called from `_emit` and `_flush_suppressed`, so no longer dead — but it drops the suppressed lines (§4.5) and the "high-rate" claim is still false (§8) |
| Suppressed summary on stop | ⚠️ **Fixed in method, unreachable in production** | `stop()` calls `flush_pending()`, but no `SIGTERM` handler exists so systemd never reaches it (§4.3). `test_stop_flushes_suppressed_summary` passes without exercising the real path |
| Test stdout leak (P3) | ❌ **Still open** | `test_emergency_slam_at_90` and `test_spike_steps_down_immediately` do not mock `print` |

---

## Verdict

The design instincts here are right and Task C is the best piece of work on the branch — it reads
the engine source, corrects a docstring claim it found to be misleading, and refuses to retune
thresholds before the capacity curve exists. The state-change-only journal, the frozen config
dataclass, the defaults-under-cleared-environ test and the `CPUAffinity` fix are all things I
would keep. But this is an instrumentation PR, and the instrumentation has the specific defect
this project has been burned by before: **the safety mechanism cannot engage at the shipped
cadence, the shutdown flush cannot be reached under systemd, and the tests are green for both.**
Cycle 1 asked for the exception path to stop spamming; cycle 2 routed it through a valve welded
open, and the deliverable now documents a protection that does not exist. Add to that a governor
that cannot report its own enable/disable transitions during the very measurement window it was
built to instrument, and an actuation failure that is invisible to a `poly-governor:`-filtered
journal. None of it is a data-loss or security problem, and defaults are genuinely unchanged, so
nothing here is a 🔴 in the "wake someone up" sense — but merging as-is ships an instrument that
reports success while disconnected, which is the one bug class `AGENTS.md` says to stop and fix.
Fix the four 🔴s and the thread leak; the rest can follow.

## Priority backlog

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
