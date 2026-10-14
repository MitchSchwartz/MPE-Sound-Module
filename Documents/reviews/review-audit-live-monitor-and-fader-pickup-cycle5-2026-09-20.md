# Review audit — live monitor & fader pickup, cycle 5 (final)

**2026-09-20.** Audits `Documents/reviews/grumpy-review-live-monitor-and-fader-pickup-cycle4-2026-09-20.md`
against the working tree at commit `2dc3857` (uncommitted changes on top, `git status`
shows 29 changed/untracked paths, all matching this feature). This is the fifth and
last cycle of the loop. **Nothing has run on the Pi in this audit either.** Every claim
below is either read from source or executed on this laptop; nothing here is an ear
test, a hardware test, or a claim about the appliance.

**Verified by execution, this cycle:**

- `python3 -m unittest discover -s tests -q` → **2236 tests, OK (skipped=23), 117.4 s.**
  Matches the number handed to me.
- `gcc -O2 -Wall -Wextra -Werror -std=c11 -c` on both `mpe-live-monitor.c` and
  `mpe-peak-meter.c` → clean, both.
- `LiveMonitorWatcher.poll()` executed directly against synthetic state files: a
  healthy 12-second run with a real 2 s writer cadence, a forward wall-clock step of
  1 hour mid-run, a genuine 7 s writer stall, and a file missing the `detached=` key.
- `test_the_repair_script_leaves_an_answer_the_watchdog_can_read` and
  `test_a_clock_step_does_not_condemn_a_healthy_insert` (both in `tests/test_live_monitor.py`)
  and all 13 tests in `tests/test_sl_watchdog_live_path.py` run individually — all pass.
- The real `scripts/restore-direct-monitor-path.sh` run against a stubbed
  `jack_connect` that always succeeds, then the resulting state file re-read through a
  fresh `LiveMonitorWatcher`.
- One scenario the cycle-4 review did not run: a **freshly constructed watcher**
  reading a file whose writer died long ago but whose last write said
  `carrying=1 detached=1`. Reported below as a new, low-severity finding.

---

## 1. N1 — clock-step / stall staleness

**Claim:** staleness is no longer "the timestamp is old"; `LiveMonitorWatcher` treats
`updated=` as an opaque token and calls the file stale only when the token hasn't
changed for `LIVE_MONITOR_STATE_MAX_AGE_S` of the *reader's* monotonic clock. The
watchdog passes `time.monotonic()`.

**Verdict: ✅ Confirmed, closed.** Read `patch_browser/audio_engine.py:338-397`:

```python
token = state.get("updated")
if token != self._token:
    self._token = token
    self._token_seen_at = t
still_for = t - (self._token_seen_at if self._token_seen_at is not None else t)
...
if still_for <= self._max_still_s:
    if _meter_flag(state, "carrying") is True:
        return True
    return detached is not True
return False if detached is True else None
```

`t` comes from `time.monotonic()` (the caller supplies it; `sl-watchdog.py:219` passes
`time.monotonic()` explicitly, `wait_for_live_path()` at `:282` lets it default, which
resolves to `time.monotonic()` internally — both paths use the reader's own monotonic
clock, never wall time). Executed both clock-step directions plus a long healthy run:

```
Scenario A: healthy run, 6 ticks, updated= advancing normally  → True every time
Scenario B: wall clock steps forward 1h mid-run (token changes)  → True before and after
Scenario C: writer stalls (token frozen) for >6s of reader's monotonic clock → False
Scenario E: `detached=` key absent                                → None
```

This closes the exact failure the cycle-4 review demonstrated (`False` after a forward
step under a healthy insert, firing the repair arm on top of a live signal). The fix is
mechanically sound: Linux `CLOCK_MONOTONIC` is not affected by `settimeofday`/NTP steps,
so comparing the reader's own elapsed monotonic time against "has the token changed" is
immune to the exact hazard (no RTC, NTP steps at boot) the finding was about.

**New, low-severity finding (🟡, not blocking):** a *freshly constructed* watcher —
which is what exists after `sl-watchdog.py` itself restarts — has no memory of the
token's prior value. Its first poll always treats the token as "just changed" (`still_for
= 0`), so a file left behind by an insert that died an arbitrary amount of time ago,
still saying `carrying=1 detached=1`, reads as **True** on that first poll:

```
Fresh watcher, first poll against an insert that died long ago:  True
Same watcher, second poll 10 s later, file unchanged:             False
```

Before the N1 fix, a wall-clock comparison would have caught this on the very first
read (the file's `updated=` would already be old). The token-based fix trades that away:
detecting a genuinely-dead insert across a watchdog restart now costs one extra poll
cycle (~10 s at the watchdog's own cadence) rather than being immediate. This is
**fail-safe in direction** — it delays raising an alarm, it does not create or extend a
doubled-signal window — so it is not a P0/P1, and it is a narrow window (watchdog
restart coinciding with an already-dead insert). Worth a one-line note in the
docstring and a test (`fresh watcher + old token + carrying=1` → should probably answer
`None`, not `True`, until the watcher has watched for `max_still_s`), but it does not
undermine the N1 closure.

## 2. N2 — repair deletes the file its own success check reads

**Claim:** `restore-direct-monitor-path.sh` rewrites the file with `detached=0
updated=<now>` on full success instead of deleting it.

**Verdict: ✅ Confirmed, closed, and executed end-to-end.** Read
`scripts/restore-direct-monitor-path.sh:54-77` — on `failures -eq 0` it writes
`carrying=0`, `detached=0`, `restored_by=...`, `updated=$(date +%s)` via a
tmp-file-then-`mv` (atomic), and does **not** delete the file on any path. Ran it for
real:

```
$ bash restore-direct-monitor-path.sh   # jack_connect stubbed to exit 0
$ cat $MPE_RUN_DIR/live-monitor.state
carrying=0
detached=0
restored_by=restore-direct-monitor-path
updated=1758...
$ python3 -c "LiveMonitorWatcher(path=...).poll(now=0.0)"  →  True
```

`tests/test_live_monitor.py::test_the_repair_script_leaves_an_answer_the_watchdog_can_read`
does exactly this (real script, faked `jack_connect`, asserts the file exists and a
fresh watcher reads it `True`) and passes in isolation.

**Gap that survives (🟡, carried, not new):** this test proves the *file contract* is
now consistent — the thing `wait_for_live_path()` reads is no longer deleted by the
thing whose success it's meant to confirm. It does **not** exercise
`sl-watchdog.py:main()`'s own arm (`:645-660` — the `subprocess.run` call, the
`wait_for_live_path()` call, and the two log-line branches "repaired:" vs "repair did
not take"). No test in the suite calls that code with a stubbed `subprocess.run`. The
cycle-4 review's own priority-backlog item 3 — "run the arm once, anywhere" — was not
acted on. The fix is correct at the layer it was tested at; the orchestration layer
around it (the exact log line an operator reads at 2 a.m.) is still unexecuted by
anything. This is the same "not expressed" gap cycle 4 named in §7 of its own review,
one layer higher than N2 sat, and it is the same shape as N1's fresh-watcher gap above:
correct fix, thinner test than the finding deserved.

## 3. N3 — three resolutions of `MPE_RUN_DIR`

**Claim:** the restore script now sources `lib/paths.sh` + `lib/audio-engine.sh` and
uses `mpe_run_dir()`.

**Verdict: ✅ Confirmed for the stated fix.** `scripts/restore-direct-monitor-path.sh:21-33`:

```bash
source "$SCRIPT_DIR/lib/paths.sh"
source "$SCRIPT_DIR/lib/audio-engine.sh"
...
RUN_DIR="$(mpe_run_dir)"
```

`mpe_run_dir()` (`scripts/lib/audio-engine.sh:212-223`) is the same function
`scripts/start-mpe-live-monitor.sh:59-60` calls and exports. Writer and eraser now
share one Bash implementation, including its `/run/mpe` → `$TMPDIR/mpe` fallback leg.

**Unchanged (🟡, as cycle 4 rated it, not re-opened by me):** the Python reader
(`patch_browser/audio_engine.py:33-35`) still restates the logic independently —
`os.environ.get("MPE_RUN_DIR") or METER_STATE_FILE.parent`, evaluated at import, with
no "not writable → fallback" leg (Bash functions aren't callable from Python, so full
unification would need the resolved directory published somewhere, e.g. the unit's
`Environment=`, which cycle 4 already recommended and which remains undone). On the
appliance, where `RuntimeDirectory=mpe` guarantees `/run/mpe` is writable, writer,
eraser, and reader agree in practice. This was rated 🟡 not 🔴 in cycle 4 and I agree
with that — the divergence is real but only reachable off the appliance's own
guarantee.

## 4. N4 — repair arm asserted only as source text

**Claim:** the decision is now `sl-watchdog.live_path_needs_repair(snap, orphan=,
stopped=)` with six executable tests in `tests/test_sl_watchdog_live_path.py`.

**Verdict: ✅ Confirmed, closed.** `sl-watchdog.py:246-265` is a pure function taking a
`GraphSnapshot` plus `orphan`/`stopped` flags and returning a bool — no I/O, directly
callable. `tests/test_sl_watchdog_live_path.py::TheRepairDecision` has exactly six test
methods calling it directly (`test_the_default_configuration_repairs`,
`test_a_healthy_live_path_is_left_alone`, `test_not_knowing_is_not_a_reason_to_rewire`,
`test_nothing_is_repaired_while_orphaned_or_stopped`,
`test_nothing_is_repaired_when_jack_is_known_to_be_down`,
`test_an_unknown_jack_does_not_block_the_repair`) — ran the file in isolation, all 13
tests pass (7 in `LivePathReachesTheSnapshot`, 6 in `TheRepairDecision`).

Also confirmed: `grep -n "source.index\|block_indent" tests/test_sl_watchdog_live_path.py`
returns nothing. The condemned string-order test cycle 4 flagged as "the pattern came
back in the file written to close it" (`test_the_arm_is_not_nested_under_a_meter_only_field`)
is **gone from the current file** — it's not in the working tree I audited. Either it
was removed as part of this pass or cycle 4 read a version that no longer matches disk;
either way, the defect it named does not exist in what I read.

---

## 5. Carried findings — spot-checked, not re-derived

Not part of the four assigned fixes, but touched by the "whole change" verdict below.
Each confirmed unchanged from cycle 4's rating by direct inspection:

| # | Finding | Check | Result |
|---|---|---|---|
| D1 | `mpe-peak-meter.service:6` vs `install-units.sh:55-57` contradict each other about DISABLED-list membership | `grep` both files | ❌ Still contradictory. 4th cycle |
| D2 | `osc_session.ask("state", loop)` in `poll_monitor_capture` (`sooperlooper-apc-bench.py:484`) has no `try` | read the call site | Still uncaught. 4th cycle, still three lines |
| G9 | `wire-sooperlooper-graph.sh:18` sets `SURGE_CLIENT` from `MPE_SL_SURGE_CLIENT`, untested; `docs/PATHS.md` still lacks `MPE_SL_SURGE_CLIENT`/`MPE_PEAK_METER_SURGE_CLIENT`/`MPE_RUN_DIR` | `grep docs/PATHS.md` | Confirmed absent, still open |
| G11 | AGENTS.md states "both paths must never be connected at once" as an absolute | `grep AGENTS.md:87` | Sentence unchanged, still present |

None of these are P0/P1 in a small-appliance context; all were already correctly filed
as 🟡 by cycle 4 and none regressed or improved this cycle.

---

## 6. What the cycle-4 review missed

One thing, found by running code it didn't run: the fresh-watcher gap in §1 above. It
is real, execution-confirmed, and low severity — I would not have blocked cycle 4 on it
had I found it then, and I'm not blocking cycle 5 on it now. Everything else the
review claimed to have verified by execution, I re-ran and got the same answers.

---

## Final verdict — all five cycles

### State of the change (two-minute read)

Five cycles turned a component with an inert safety net (the repair arm couldn't fire
in the configuration the appliance actually runs — G5/F2, cycles 1–3) into one where
the arm fires correctly, decides correctly, and — as of this cycle — reports correctly
when it does. The two 🔴s cycle 4 found by actually running the code (N1: a clock step
could make the watchdog double the live signal; N2: a successful repair reported itself
as a failure and a spurious repair reported itself as a success) are both closed, and I
re-executed both fixes myself rather than re-reading cycle 4's transcript. The fader law
was rewritten into something that actually converges at its endpoints, with property
tests instead of formula tests. Nothing regressed.

What is still true after five cycles: **no line of this feature has ever run on the Pi,
and no ear has been near it.** That has not changed and cannot change from a laptop.

### 1. Remaining P0/P1 — can the instrument end up silent or doubled?

**No open P0.** The two doubled-signal paths cycle 4 identified (N1, N2) are both
closed and verified by execution. The one remaining doubled-signal window in the
system — the ~2 s transient at `ensure_wiring()`'s own startup/repair pass, where the
insert's output reaches playback before the direct leg is confirmed detached — is not
new, was already priced into cycle 4's "bounded ~2 s" acceptance for supervised use,
and is unchanged this cycle (still filed as G11, a documentation gap in AGENTS.md's
"never both" absolute, not an uncontained fault).

**No P1 from this cycle's fixes.** The one new observation (the fresh-watcher gap,
§1) is P2: it delays detecting a genuinely dead insert by one watchdog tick after a
watchdog restart, in the safe direction, and does not enable a doubled-signal window.

The carried 🟡s (D1, D2, G9, G11, N5, C1) are documentation/coverage debt, not live
safety risks, and none of them got worse.

### 2. Did any fix move a problem rather than close it?

**Partially, and it's worth naming precisely.** N2's fix is correct at the layer it was
tested at (the file contract between the script and the reader), but the specific code
path that consumes that contract inside `sl-watchdog.py:main()` — the actual
`subprocess.run` call and the two log branches an operator reads — is *still* not
exercised by any test, exactly as it wasn't before N2 landed. The bug that lived there
(N2) is fixed; the untested seam that let it hide for two cycles is not. If another bug
is introduced in that 15-line block tomorrow, nothing in the suite will catch it,
same as before. That's a gap that persisted through the fix, not one the fix created —
but it is the literal thing cycle 4's own priority-backlog item 3 asked for and it
still isn't there.

Nothing else moved. N1, N3, and N4 close cleanly at the layer they were meant to.

### 3. Does the cycle-4 go/no-go still stand?

**Yes, and one of its own conditions is now satisfied.** Re-quoting cycle 4:

> `MPE_LIVE_MONITOR=1`, supervised, speakers not headphones, at low level.
> Acceptable... **Fix N2 first** (one line: have the restore script rewrite the file
> rather than delete it) so the logs tell the truth during the session.

N2 is fixed and verified by execution (§2 above). The condition cycle 4 attached to the
supervised-session recommendation is met. The go/no-go itself is otherwise unchanged
and should stand as written:

- **`MPE_LIVE_MONITOR=0` (default, what's on the Pi today):** safe to deploy, unchanged.
- **`MPE_LIVE_MONITOR=1`, supervised, speakers, low level:** the right next step, and
  its stated precondition (N2) is now satisfied. Still nothing has run it.
- **`MPE_LIVE_MONITOR=1` unattended, or headphones:** still no. The 2 s startup
  transient (G11) is bounded but has never been observed on hardware, same as cycle 4
  said.

### 4. What remains unverified because nothing has run on the Pi

Unchanged from cycle 4, and it cannot be shortened by more reading:

- That `mpe-live-monitor` starts, registers on the real JACK graph, and carries audio.
- That `ExecStopPost` actually runs the restore script under systemd on a real crash
  (segfault / OOM / `systemctl kill -s KILL`), as opposed to under a shell harness with
  a stubbed `jack_connect`.
- That `jack_connect` succeeds from the `ExecStopPost` context specifically (different
  cgroup, different environment than the direct-invocation test).
- That the client's 2 s re-detach — the thing that bounds N1's residual scenario and
  every G11 startup transient to "seconds, not permanent" — has ever been observed to
  actually happen in that time on the appliance.
- That the watchdog's repair arm, end to end through `main()`, has ever executed
  outside a test harness (see "moved, not closed" above — it hasn't even executed
  *inside* a test harness at the orchestration layer).
- That the 485 Hz poll and the repair arm's cost figures hold on Pi CPU, not laptop CPU.
- Any level, any ramp, any click, as heard by a human being.

---

## Prioritized Action Matrix

| Priority | Issue | Verdict | Effort | Depends on |
|---|---|---|---|---|
| P2 | `sl-watchdog.py:main()`'s repair-arm orchestration (subprocess call + both log branches) still has no test with a stubbed `subprocess.run` | ✅ confirmed gap | half-day | — |
| P2 | Fresh `LiveMonitorWatcher` reads a long-dead insert as healthy for one poll cycle after a watchdog restart | ✅ confirmed, execution-verified | quick fix (seed `_token_seen_at` conservatively, or return `None` until watched `max_still_s`) + one test | — |
| P2 | `config/mpe-peak-meter.service:6` contradicts `install-units.sh:55-57` about DISABLED membership | ✅ confirmed, unchanged, 4th cycle | quick fix (delete one sentence) | — |
| P2 | `osc_session.ask()` uncaught in `poll_monitor_capture`; an `OSError` ends the control surface mid-set | ✅ confirmed, unchanged, 4th cycle | quick fix (three lines) | — |
| P2 | AGENTS.md's "both paths must never be connected at once" still stated as an absolute; true only outside the ~2 s startup/repair transient | ✅ confirmed, unchanged, 4th cycle | quick fix (doc wording) | — |
| P3 | `wire-sooperlooper-graph.sh:18` untested; `docs/PATHS.md` missing 5 live-monitor/meter env vars | ✅ confirmed, unchanged | half-day | — |
| P3 | Python reader (`audio_engine.py`) restates `MPE_RUN_DIR` resolution instead of reading one published value | ✅ confirmed, unchanged (🟡, not 🔴) | multi-day (needs a publishing mechanism, e.g. unit `Environment=`) | — |
| — | Fader law's unbounded single-CC move (N5) and silent-column monitoring (C1) | handed to Mitch, undecided 1–4 cycles | n/a — decision, not code | — |

No P0 or P1 items remain open as of this cycle.

---

## Bottom line

**P0: 0. P1: 0.** Both 🔴s from cycle 4 (N1, N2) are closed and I verified both by
running the actual code — the clock-step and stall directions for N1, and the real
shell script plus a real state-file re-read for N2 — not by re-reading cycle 4's
transcript. N3 and N4 are also confirmed as described. One new low-severity, fail-safe
finding surfaced by running code cycle 4 didn't run (a freshly restarted watchdog
briefly misreads a long-dead insert as healthy), and one gap survived the N2 fix rather
than being created by it (the repair arm's own orchestration in `sl-watchdog.py:main()`
is still never executed by any test, at the same seam cycle 4's own priority list named
and did not close). The cycle-4 go/no-go stands as written — default off is safe to
deploy, `MPE_LIVE_MONITOR=1` is a conditional go for a supervised bench session on
speakers at low level, and unattended or headphone use remains a no — and its one
attached precondition (fix N2 first) is now satisfied. Nothing in this feature has run
on the Pi at any point across five cycles, and that gap is unchanged by anything either
review could do from a laptop.
