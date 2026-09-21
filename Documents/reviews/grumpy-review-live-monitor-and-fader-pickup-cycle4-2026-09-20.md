# Grumpy review — live monitor & fader pickup, cycle 4 (final)

**2026-09-20.** Branch `dev`, working tree still uncommitted. Same scope as
cycles 1–3. **Nothing has run on the Pi in any cycle. The Pi is unreachable.
No hardware test, no ear test, no audio has been produced at any point in this
review loop.** Every statement below about the appliance is an inference from
source; every statement marked MEASURED was measured on this laptop.

**Read in full this cycle:** `native/mpe-live-monitor/mpe-live-monitor.c`,
`scripts/sooperlooper/live_monitor.py`, `tests/test_live_monitor.py`,
`tests/test_sl_watchdog_live_path.py`, the `sl-watchdog.py` /
`audio_engine.py` / `loop_mix.py` / `sooperlooper-apc-bench.py` /
`sl_osc_session.py` / `sl_bench_listener.py` / `wire-jack-graph.sh` /
`AGENTS.md` / `docs/PATHS.md` diffs, `scripts/restore-direct-monitor-path.sh`,
`scripts/start-mpe-live-monitor.sh`, `config/mpe-live-monitor.service`,
`config/mpe-peak-meter.service`, `scripts/install-units.sh`,
`scripts/lib/audio-engine.sh`, `tests/test_loop_mix.py`, cycle-3 review.
**Not read:** LED modules, `slot_*`, `looper_songs`, `track_gesture`,
`docs/CLASSIC-MIDI-PLAN.md`, `.github/workflows/test.yml` diff, `.gitignore`.

**Ran:**

- `python3 -m unittest discover -s tests -q` → **2226 tests, OK (skipped=23),
  117.3 s.** Matches the claim handed to me.
- `mpe-live-monitor.c` and `mpe-peak-meter.c` under
  `-O2 -Wall -Wextra -Werror -std=c11` → **clean.**
- `read_graph_snapshot()` and `live_path_via_monitor_state()` executed directly
  against synthetic state files in five configurations, including the two
  clock-step directions. Results quoted verbatim in §5.

Nothing touched the appliance and nothing made sound. One probe was written
under the scratchpad, outside the repo. Working tree untouched.

---

## Step 0 — the fate of the cycle-3 findings

**Both 🔴s are genuinely closed, and I proved H1 by execution rather than by
reading.** That is the first cycle in four where the headline finding did not
survive. The count below is the real result; the new findings in §5 are
smaller than it.

| # | Cycle-3 finding | Mark | Evidence now |
|---|---|---|---|
| H1 | `read_graph_snapshot()` discards the live-monitor sensor when `meter.state` is absent; arm nested under meter-only `jack_reachable` | **CLOSED — verified by execution** | `sl-watchdog.py:220` now returns `GraphSnapshot(None, None, None, "meter_stale", live)`, and the arm (`:607`) gates on `snap.surge_playback is False … and snap.jack_reachable is not False`. Ran it with no `meter.state` and a stale `live-monitor.state`: `surge_playback=False`, **arm would fire: True**. `tests/test_sl_watchdog_live_path.py` covers five snapshot cases through the real function. G5/F2, open since cycle 1, is finally shut |
| H2 | Nothing clears `live-monitor.state`; clean stop leaves `detached=1` forever | **CLOSED — with a side effect, see N2** | `main()` publishes a final `write_live_state()` after `restore_direct_path()` (`:625`), and `restore-direct-monitor-path.sh:48-53` deletes the file **only when `failures -eq 0`**. Ran the clean-stop shape: `surge_playback=None`. No permanent alarm |
| H3 | One `ask` only; a lost datagram raises the monitor +16 dB mid-take | **CLOSED, well** | `VERIFY_ATTEMPTS=3`; `needs_verification()` re-arms on timeout, `_capturing_loop_locked` drops only at `attempts >= VERIFY_ATTEMPTS`. Budget is 3.0 + 3×1.5 = 7.5 s. `test_one_lost_question_does_not_end_a_take` and `test_an_engine_that_never_answers_releases_the_monitor` are real behavioural tests with injected clocks, and the second asserts the *attempt count*, so weakening the constant fails it |
| H4 | Four answers to "is the live path up"; the default one is a proxy | **OPEN — 1 day** | `grep -n "direct=" native/mpe-live-monitor/mpe-live-monitor.c` → **nothing.** The client still does not publish what it knows. `live_path_via_monitor_state` still answers "I did not break it", not "it works" |
| H5 | Tests that cannot fail the way they claim | **CLOSED for the one that mattered; the pattern came back in the new file** | `test_the_gate_check_never_reaches_the_binary` now runs the script against a stand-in `$BIN` that `touch`es a marker, asserts the marker is absent under `--check` **and present without it** — a positive and a negative control, exactly right. But `tests/test_sl_watchdog_live_path.py::test_the_arm_is_not_nested_under_a_meter_only_field` is a `source.index()` / `assertIn(block_indent, …)` string test: it is the cycle-3 defect, re-committed in the file written to close cycle-3 |
| H6 | `PATHS.md` documents the deleted TTL with the refuted premise | **CLOSED** | Row gone; `grep -rn CAPTURE_TTL` finds it only inside the review documents. `MPE_LIVE_MONITOR`, `_CC`, `_PORT`, `_RAMP_MS`, `_VERIFY_AFTER_S`, `_VERIFY_ATTEMPTS`, `_VERIFY_TIMEOUT_S` all documented, and `live-monitor.state`'s keys are in the state-file table. Five *other* live-monitor env vars are still undocumented — see N4 |
| H7 | Unusable/negative `updated=` answers `False`; `audio_engine` ignores `MPE_RUN_DIR` | **HALF-CLOSED — and the half that was fixed is the harmless one** | Clock stepped **backwards** → `None` ✅ (ran it). Clock stepped **forwards** → still `False` → repair fires → **doubled signal**. Ran it. See **N1**, which I am rating 🔴. `MPE_RUN_DIR` is honoured in Python but resolved three different ways across three consumers — N3 |
| G5 (c2) | Watchdog arm inert in default config | **CLOSED** | Superseded by H1, closed with it. Four cycles |
| G9 (c2) | Surge client name: 5 sites, 4 tested | **OPEN — 2 days** | `wire-sooperlooper-graph.sh:18` still uncovered; `mpe-peak-meter.c` still *prefers* `MPE_PEAK_METER_SURGE_CLIENT`; neither variable is in `docs/PATHS.md` |
| G10 (c2) | Refusal path cannot fire while idle; `close()` never called | **HALF-CLOSED** | `monitor_sender.close()` is now called at `run_bench`'s normal return (`:1040`). The refusal half is unchanged: `push_monitor()` runs at ~485 Hz but `send()` dedups on value, so an idle stretch emits no datagram and `ECONNREFUSED` still cannot be observed |
| G11 (c2) | Handover order; AGENTS.md absolute | **OPEN — 2 days, partially addressed** | AGENTS.md gained a good fan-out section, and the four-stage table is a real improvement. But it still states *"both paths must never be connected at once"* as an absolute, while `ensure_wiring()` connects `out_N → playback_N` before `detach_direct_path()` removes the direct leg — true by construction, transiently, on every pass |
| G12 (c2) | Cadence × cost unstated | **CLOSED for the poll** | `push_monitor`'s docstring now carries `MEASURED 2026-09-20 … 1.51 us idle / 2.70 us capturing … 0.07–0.13 % of a core`, and the false "never on a timer" sentence is gone. The repair arm's own cadence × cost is still unstated |
| F7 (carried) | Fader law path-dependent; "converge as you play" overclaims | **CLOSED by rewrite — and it opened a new decision** | `_scaled_level()` replaces `ref + (raw - anchor)` with proportional-to-remaining-travel. Endpoints now genuinely converge (`test_fader_ends_always_reach_silence_and_unity`). The docstring is honest about the cost and even carries the measurement. That cost has never been put to Mitch — see **N5** |
| 09-07 F1 | `looper_timing` has no `binding_table` cross-check | **OPEN — 18 days** | unchanged |
| 09-07 Step0 | `fake_sl_engine.py:67` discards every non-`hit` message | **OPEN — 19 days** | unchanged, and now load-bearing: it is *why* `SlOscSession.ask()` has no in-suite integration test. The fake cannot answer a `/sl/N/get` |

**Counts: 8 CLOSED · 3 HALF-CLOSED · 5 OPEN (one 18 days, one 19).**
🔴 count: **2 → 0 carried.** Two new ones open below.

---

## 1. First impressions

Four cycles in, this is a component that has learned from itself in writing.
`live_monitor.py`'s `VERIFY_ATTEMPTS` docstring names the failure, the
direction, the magnitude (+16 dB) and the date. `restore-direct-monitor-path.sh`
warns the next editor about the invariant they are about to break.
`_scaled_level()` describes the exact bug it replaced *and* the measured cost
of its own replacement. `test_two_faders_move_independently` documents the
earlier version of itself that could not fail. That is a codebase that has
stopped losing its own history, and it is rare.

The seam problem named in cycles 1–3 is *half* solved. `tests/test_sl_watchdog_live_path.py`
is the first test in this feature that crosses a module boundary, and it is the
test that closed the finding that survived three cycles. But it stops one seam
short: it proves the snapshot carries the answer and then asserts the *shape of
the source text* rather than running the arm. Everything downstream of
`snap.surge_playback is False` — the subprocess, the delete, the wait, the
success report — has never been executed by anything, in any cycle, on any
machine. §5 N2 is what lives in that gap.

## 2. Architecture & structure

The supervision path is now coherent end to end and I can draw it without a
"discarded here" box:

```
mpe-live-monitor ──2 s──> {MPE_RUN_DIR}/live-monitor.state
                                    │
                    live_path_via_monitor_state()   (staleness IS the alarm)
                                    │
                    read_graph_snapshot() ── surge_playback ──┐
                                                              ▼
                        if surge_playback is False → restore-direct-monitor-path.sh
                                                              │
                                        ...which DELETES live-monitor.state
                                                              │
                                        wait_for_live_path() reads the file ← N2
```

The last two boxes were designed in different cycles by different reasoning and
nobody drew them together. That is N2.

`run_bench` grows a fourth closure and still has zero tests that construct it
(2026-09-07 F5, open). `poll_monitor_capture()` is called **only from the idle
branch** (`packet is None`, `:990`), so under dense MPE traffic verification is
deferred — harmless in direction (a take is held, not dropped), but the "one
datagram per `VERIFY_AFTER_S`" claim in its docstring is a best case, not a
bound.

## 3. Single authority

### Q1 "Is the live monitor on?" — **one owner, four readers, all agreeing.** Closed.
`live_monitor.enabled()`; the shell matches via `--check`; the watchdog and the
bench import it. The `--check` agreement is now enforced by a test that runs the
real shell over 17 values *and* proves `--check` does not reach the binary.

### Q2 "Is this track still capturing?" — **one owner, and it asks three times.** Closed.

### Q3 "What is Surge's JACK client name?" — **five sites, two variables, four tested.** Unchanged from cycle 3.
`wire-sooperlooper-graph.sh:18` still uncovered; the meter still prefers the
legacy `MPE_PEAK_METER_SURGE_CLIENT`; neither variable is in `docs/PATHS.md`.

### Q4 "Does Surge reach playback?" — **four implementations, still.** H4 open.
The client still publishes `carrying=`/`detached=` and not `direct=`, though
`port_connected_to()` is already in the file and already called for exactly
that question in `detach_direct_path()`. One `fprintf` closes it.

### Q5 — NEW. "Where does `live-monitor.state` live?" — **three answers.**

| Site | Resolves to | Notes |
|---|---|---|
| `scripts/start-mpe-live-monitor.sh:59-60` | `mpe_run_dir()` — `$MPE_RUN_DIR`, else `/run/mpe`, **else `$TMPDIR/mpe`** | the writer |
| `scripts/restore-direct-monitor-path.sh:48` | `${MPE_RUN_DIR:-/run/mpe}` — **no fallback leg** | the eraser |
| `patch_browser/audio_engine.py:30-32` | `os.environ.get("MPE_RUN_DIR") or /run/mpe`, **evaluated at import** | the reader |

They agree on the appliance, because `RuntimeDirectory=mpe` makes `/run/mpe`
writable. They diverge the moment `mpe_run_dir()` takes its fallback leg: the
client writes to `$TMPDIR/mpe`, the reader looks in `/run/mpe` and sees nothing
(safety net silently off, which is the fail-safe direction), and `ExecStopPost`
deletes a path that was never written. Also note `ExecStopPost` gets the unit's
environment, **not** the runtime-exported `MPE_RUN_DIR` `ExecStart` computed —
so even the exported value does not reach the eraser.

**Owner:** `mpe_run_dir()` is the law; the other two restate it. Either put the
resolved directory in the unit's `Environment=` so all three read one value, or
have the client publish its own path.

## 4. Code quality

- `push_monitor`'s docstring is now true and carries its measurement. Fixed.
- The per-sample NULL guard in `process()` (`mpe-live-monitor.c:148`) was **not
  removed** — it gained the comment *"Both buffers were checked above, before
  the loop."* Documenting a dead branch is not deleting it, but it is harmless
  and the comment is honest. Downgrade to cosmetic.
- **`config/mpe-peak-meter.service:6` still says "install-units.sh lists this in
  DISABLED"; `install-units.sh:45` says "Do NOT add mpe-peak-meter or
  mpe-live-monitor here." Third cycle.** Two files, opposite claims, about what
  a deploy does to a unit.
- `osc_session.ask()` is still called from `poll_monitor_capture` with no
  `try`. An `OSError` out of `send_message` propagates through the bench's main
  `while` and ends the control surface mid-set. Third cycle for this one too,
  and it is three lines.
- `push_monitor()` computes `target_amp` twice and `capturing_loop` once more
  when `fader_log` is on — three lock acquisitions, and the logged value can
  differ from the sent one. Cosmetic.

## 5. Code smells — the hall of shame

### 🔴 N1 — A forward clock step (or a 6-second writer stall) makes the watchdog reconnect the direct path underneath a live insert

Cycle 3 asked for "an unusable or negative age answers `None`". That landed —
and it is the direction that was already safe. The dangerous direction is
unchanged. Run, not read:

```
=== CLOCK STEPPED FORWARD 1h under a healthy live insert ===
   (carrying=1 detached=1 updated=now, evaluated at now+3600)
surge_playback= False

=== CLOCK STEPPED BACK (updated in the future) ===
surge_playback= None          # the case that was fixed

=== writer alive & detached, last write 7 s ago (3 missed passes) ===
surge_playback= False
```

`False` means the arm fires: `bash restore-direct-monitor-path.sh` →
`jack_connect Surge XT:out_N → system:playback_N` succeeds → **direct *and*
insert are both connected**. That is two copies of the live signal, which
AGENTS.md names as the one fault this feature must never produce, into
headphones that may be on his head. The G4 fix means the client re-detaches on
its next 2 s wiring pass, so it is bounded — roughly 2 s of +6 dB, not
permanent — but it is not zero and it is reachable:

- The Pi has **no battery-backed RTC**. It boots on `fake-hwclock`'s saved time
  and NTP steps it forward. Every state file on the box looks ancient for the
  instant after the step.
- A `jack_disconnect`/`jack_port_by_name` in `ensure_wiring()` blocking across
  three 2 s passes under xrun load produces the same reading with a perfectly
  healthy client.

The root cause is that "stale" is computed from a wall-clock difference between
two processes, on a board whose wall clock steps. **Fix:** make staleness mean
*the number stopped moving* rather than *the number is old* — have the reader
remember the previous `updated=` value and require it to be **unchanged across
two of its own ticks** before answering `False`. That is clock-step immune and
stall-tolerant, costs one field of state in the watchdog, and keeps the
crash case (the file genuinely stops changing) answering `False` within 20 s.
**Enforced by:** a test that feeds `read_graph_snapshot` a fresh file evaluated
at `now + 3600` and asserts `surge_playback is not False`.

### 🔴 N2 — The H2 fix and the H1 success check collide: every genuine repair reports as a failure

`restore-direct-monitor-path.sh` now deletes `live-monitor.state` on full
success (the H2 fix). `wait_for_live_path()` (`sl-watchdog.py:244-261`) asks
`live_path_via_monitor_state()` — which reads that file — for 4 seconds to
decide whether the repair took. Cycle 3 wrote these two in the same pass and
never ran them together.

Trace the case the arm exists for — the insert segfaulted holding the detach,
and `ExecStopPost` could not or did not restore:

1. `surge_playback is False` → `problems.append("nothing reaches system:playback…")`
2. `subprocess.run(["bash", restore])` → both channels reconnect → **script
   deletes `live-monitor.state`**
3. `wait_for_live_path()` polls for 4 s. Meter off → `surge_playback_via_meter()`
   is `None`. File gone → `live_path_via_monitor_state()` is `None`. Never `True`.
4. Returns `None` → `log("repair did not take: restore-direct-monitor-path.sh
   exited 0")`, followed by the script's own **successful** output, and the
   problem stays in `problems`.

The audio is fine. The report is a lie in the one log line an operator would
read at 2 a.m., it is the exact symptom cycle 2 filed as G6 and cycle 3 closed,
and it costs a 4 s block in a 10 s watchdog loop every time a real repair
happens. It self-clears on the next tick (no file → `None` → no alarm), so it
is one false line per repair, or one per cycle of a crash-looping unit.

The mirror case is worse for reasons that are not about logs: when N1 fires on
a **healthy** insert, the script reconnects the direct path, deletes the file,
the live client rewrites it fresh 2 s later, `wait_for_live_path()` sees `True`
and the watchdog logs **`repaired: reconnected Surge -> playback`** — claiming
a successful repair of a fault it created, with the doubling it caused
unmentioned.

**Fix:** the success check must not consult the file the repair deletes. Either
(a) treat a deleted/absent state file as success inside `wait_for_live_path`
when the pre-repair answer was `False`, or (b) don't delete — have the script
write `detached=0 updated=<now>` instead of `rm`, which satisfies H2 and leaves
`wait_for_live_path` a fact to read. (b) is one line and keeps one writer
format. **Enforced by:** a test that runs the arm with a stubbed `subprocess.run`
and a script that deletes the file, and asserts the watchdog reports repaired,
not "did not take".

### 🟡 N3 — Three resolutions of `MPE_RUN_DIR` (see §3 Q5)

Fail-safe today on the appliance, and the eraser can never see the exported
value. **Fix:** resolve once in the unit's `Environment=`. **Enforced by:** a
test that all three sites read one helper.

### 🟡 N4 — The string-order test pattern came back in the file written to close it

```python
def test_the_arm_is_not_nested_under_a_meter_only_field(self):
    arm  = source.index("if (\n            snap.surge_playback is False")
    gate = source.index("if snap.jack_reachable and not orphan and not stopped:")
    block_indent = "\n        if (\n            snap.surge_playback is False"
    self.assertIn(block_indent, source, ...)
    self.assertGreater(arm, gate, ...)
```

This asserts an **indentation width and a literal source substring**. It breaks
on any reformat and it passes on any refactor that keeps the text while
changing the behaviour — and it is standing in for the only part of this
feature nothing has ever executed (N2 is what was hiding there). Cycle 3
condemned exactly this shape; the file written to answer cycle 3 contains it.

**Fix:** call `main()`'s loop body once with a stubbed `subprocess.run` and a
`False` sensor, and assert the script was invoked. That is the test that finds
N2, and it replaces this one. Also: the four other 🟢-to-🟡 leftovers below all
share the property that **nothing fails if they regress**.

### 🟡 N5 — The fader law's unbounded single-CC move has never been put to Mitch

`_scaled_level()` is a better law than what it replaced and its docstring is
admirably honest:

> *"a single CC can still move the level a long way once a fader is moving, and
> the further the fader is from its track's level, the further. From an anchor
> at 2, one CC to 60 is a 15 dB move (MEASURED against this module,
> 2026-09-20 …). The output ramp (FADER_SMOOTH_MS) is what keeps that from
> clicking; it is not bounded here."*

`test_misaligned_fader_does_not_jump_on_grab` pins the consequence: anchor at
10, one CC to 5, **level 127 → 64**. The law is deliberately asymmetric near the
bottom of travel — small physical moves there are enormous level moves. That is
defensible (it is what makes the ends converge) and it is upward-bounded by
unity, so it cannot exceed what the patch already sends. But since cycle 3 it
also drives the **live monitor** while capturing, ramped over 80 ms, in the
phones. This is a feel decision and only he can make it. It is written down in
a docstring and has never been asked.

**Options:** (1) ship as is — bounded by unity, ramped; (2) clamp the per-CC
level delta (e.g. ≤ 16 CC per message), which softens fast drags and makes the
ends take two passes to reach; (3) ship as is but log a line when a single CC
moves a level more than N. **Recommendation:** (1), and put the measurement in
the PR so it is his call and not a default.

### 🟢 Minor / carried

- `config/mpe-peak-meter.service:6` vs `install-units.sh:45` — **third cycle.**
- `osc_session.ask()` with no `try` in the bench main loop — third cycle.
- `monitor_sender.close()` only on the normal return; an exception out of
  `run_bench` skips it. The process is ending anyway.
- `live-monitor.state` publishes `surge_client=`; nothing reads it. Second cycle.
- Refusal reporting still cannot fire during an idle stretch (`send()` dedups).
- Dead per-sample NULL guard — now commented rather than deleted.
- `docs/PATHS.md` still omits `MPE_SL_SURGE_CLIENT`, `MPE_PEAK_METER_SURGE_CLIENT`,
  `MPE_RUN_DIR`, `MPE_LIVE_MONITOR_REFUSAL_LOG_S`, `MPE_LIVE_MONITOR_RECOVERY_STREAK`.

## 6. Logic & business rules

The rule is still one sentence and the code is still a transcription of it:
*while a track is capturing you hear yourself at that track's level, and only
the engine — or three unanswered questions — ends a capture.*

**The one thing that will make this instrument silent is in the rule itself,
and it is still undecided after four cycles.** `target_amp()` returns
`wet_for(loop)`, and `test_a_silent_column_monitors_silent` /
`test_giving_up_falls_back_to_the_live_level` both pin `0.0` as correct.
Arm-record into a column whose fader is down and **you cannot hear yourself
play**, with no log line, no LED, and nothing anywhere in the repo that says so
out loud. It is consistent with the rule and with every DAW, and it is also
precisely the shape this project calls its own named failure: a broken
instrument and a working one producing identical output. Cycle 1 handed it to
Mitch. He has not been asked since. **Recommendation:** ship the behaviour,
add one bench log line (`live monitor -> 0.0000 (loop N) — you will not hear
yourself`) the first time a capture targets a silent column. One line, no
policy change.

**Settings read once**, re-checked: `VERIFY_*`, `ENABLED`, `REFUSAL_COMPLAIN_S`,
`RECOVERY_STREAK`, `resolve_live_cc()`, `load_env()` — all correct for an
appliance where env changes mean a restart. **New exception:**
`LIVE_MONITOR_STATE_FILE` is computed at `audio_engine` **import** time
(`:30-32`), so it is a boot-captured setting; it describes a directory that
`mpe_run_dir()` can choose at runtime. See N3.

## 7. Test strategy & execution

2226 OK in 117 s — verified, not taken on trust. The additions are the best of
the four cycles:

- `test_one_lost_question_does_not_end_a_take` / `test_an_engine_that_never_answers…`
  assert the **attempt count**, so the constant cannot be quietly weakened.
- `test_the_gate_check_never_reaches_the_binary` now has a positive and a
  negative control. Textbook, and it directly answers the project's own Rule −1.
- `test_two_faders_move_independently` documents the version of itself that
  could not fail, in its own docstring. Do more of this.
- `test_a_drag_does_not_compound` asserts a **property** (13 small steps land
  where 1 big step lands) rather than the formula. It survives a rewrite of the
  law. This is the right shape.

**The doubles, as instruments** — one axis moved, two did not:

| What the suite measures | How |
|---|---|
| the level Python decides | computed — real |
| whether an unanswered question drops a capture | injected clock — real |
| whether a lost question does **not** | injected clock — real (new) |
| **whether the watchdog can see the sensor** | **real, through `read_graph_snapshot` — new, and it closed a four-cycle finding** |
| whether the engine answers `ask` at all | **not expressed** — `fake_sl_engine.py:67` drops every non-`hit` message (19 days), so the fake *cannot* answer a `/sl/N/get`. `tests/engine/` can, and still has no case from this feature |
| **whether the repair arm works** | **not expressed** — a source-string test stands where it should be (N4). N2 is what was hiding there |
| whether the state file's lifetime is right | partially — the script's delete is untested end to end |
| whether the C client behaves | not expressed, in any cycle |

Three of this cycle's four findings are in that table's "not expressed" rows.
That is not a coincidence and it is the single sentence worth carrying forward:
**the suite has learned to cross one seam and there are three more.**

## 8. Security & performance

Unchanged and fine. Loopback bind, strict ASCII parser, clamp-to-attenuation,
`JackUseExactName`. No hostnames, addresses, usernames or infrastructure in any
new file — I checked the state file's contents and `surge_client=` is a JACK
client name. Both native clients compile clean at `-Werror`.

| Addition | Cadence | Cost |
|---|---|---|
| `poll_monitor_capture` (idle) | ~485 Hz | 1.51 µs → **0.07 %** of a laptop core (cycle-3 measurement, now in the docstring) |
| `ask` datagram during a held take | 1 per 3 s | negligible |
| `write_live_state` | 1 per 2 s | one `fopen`/`rename` |
| **watchdog repair arm** | **3 forks + a 4 s block per 10 s tick while alarming** | **still unstated in the PR, and N2 guarantees it fires on every successful repair** |

The last row is the only one that matters and it is the only one without a
number. A 4 s block in a 10 s loop is 40 % of the watchdog's duty cycle.

## 9. Documentation vs. reality

| Claim | Verdict |
|---|---|
| `docs/PATHS.md`: "`sl-watchdog.py` repairs it from `live-monitor.state`" | ✅ **True for the first time.** Verified by execution |
| `mpe-live-monitor.c` header: "`sl-watchdog.py` asserts that Surge reaches playback by one route or the other" | ✅ **True for the first time.** Fourth cycle for this sentence |
| `live_monitor.py`: "five seconds inside a held take produced one datagram" | ✅ verified against the real engine in cycle 3 |
| `restore-direct-monitor-path.sh`: "a healthy client re-detaches within its next wiring pass (2 s)" | ✅ True — and it is the *only* thing bounding N1 |
| `AGENTS.md`: "both paths must never be connected at once" | ❌ Still an absolute; `ensure_wiring()` still violates it transiently by construction, and N1 makes it violable for ~2 s from outside. **Fourth cycle** |
| `config/mpe-peak-meter.service:6` vs `install-units.sh:45` | ❌ Direct contradiction. Third cycle |
| `push_monitor` docstring | ✅ Fixed, with the measurement |
| `loop_mix.py`: "the two positions converge as you play" | ✅ **Now true** — `test_fader_ends_always_reach_silence_and_unity` |
| `_scaled_level` docstring: the 15 dB single-CC move | ✅ True and admirably self-incriminating — see N5 |
| `poll_monitor_capture`: "Costs one datagram per VERIFY_AFTER_S" | ⚠️ Best case. It runs only in the idle branch, so under dense MIDI it is slower than that |

Build/deploy: the `native` CI job is real; I compiled both clients under its
flags, clean. A new dev could onboard on this feature in an afternoon — the
source carries its own history now, which is more than most of this repo.

---

## Verdict

**The two 🔴s that have defined this review loop are closed, and I proved the
important one by running it rather than reading it.** `read_graph_snapshot()`
now hands the watchdog a real answer in the configuration the appliance
actually runs, the repair arm sits outside the meter-only gate, and the state
file finally has a lifetime. G5/F2 — open since cycle 1 — is shut. The verify
protocol is right and now survives a lost datagram. The `--check` test grew a
positive and a negative control. The fader law was rewritten into something
whose endpoints actually converge, with tests that assert properties instead of
formulas. Four cycles of pressure produced a component that documents its own
failures, and that is worth saying plainly.

What it did not produce is a component that has been **run**. Not one line of
this feature has executed on the Pi, no audio has been produced in any cycle,
and no ear has been near it. The repair arm — the whole point of the exercise —
has never been executed by a test or by a machine, and when I traced it by hand
I found N2 waiting there: the script deletes the file that the success check
reads, so every genuine repair reports as a failure and every *spurious* repair
reports as a success. That is two cycles' fixes colliding in a path nothing
exercises, which is the exact failure mode this project named for itself.

And the H7 fix closed the harmless half. A forward clock step — ordinary on a
Pi with no RTC, at every boot — still reads a healthy insert as dead and makes
the watchdog reconnect the direct path underneath it. Two copies of the live
signal, +6 dB, for up to two seconds, into headphones. Bounded, self-healing,
and the one thing AGENTS.md says must never happen.

## Go / no-go

**NO-GO for unattended use on the instrument with `MPE_LIVE_MONITOR=1`.
CONDITIONAL GO for a supervised bench session.**

Reasoning, separated by configuration, because they are not the same risk:

- **`MPE_LIVE_MONITOR=0` (the default, and what is on the Pi today).** The
  client never starts, no file is written, `live_path_via_monitor_state()`
  answers `None`, the arm never fires. The only reachable change is the fader
  law (N5) and the `push_monitor` no-ops. **Safe to deploy.** The fader rewrite
  is the part he should actually play.
- **`MPE_LIVE_MONITOR=1`, supervised, speakers not headphones, at low level.**
  Acceptable, and the right next step — this needs to be *heard*, and four
  cycles of static review cannot substitute. Fix N2 first (one line: have the
  restore script rewrite the file rather than delete it) so the logs tell the
  truth during the session.
- **`MPE_LIVE_MONITOR=1` unattended, or with headphones on.** No. N1 is a
  doubled-signal path reachable at every boot, it has never been observed on
  hardware, and the thing that bounds it to ~2 s — the client's 2 s re-detach —
  has also never been observed on hardware. AGENTS.md is unambiguous that this
  is the failure that cannot be rolled back.

**What has NOT been verified, in full:** anything on the Pi; that
`mpe-live-monitor` starts, registers, or carries audio; that `ExecStopPost`
runs the restore script; that `jack_connect` succeeds from that context; that
the client re-detaches within 2 s; that the watchdog's repair arm has ever
executed; that `SlOscSession.ask()` works against the engine *through the bench
as deployed* (it was verified in cycle 3 against a container, with production
objects, but not on the appliance); that the 485 Hz poll costs 0.07 % **on the
Pi** rather than on a laptop; that the C client's ramp sounds like a ramp; any
level, at any stage, as heard by a human.

## Priority backlog (🔴 only)

1. **N2 — stop the repair from deleting the file its own success check reads.**
   Have `restore-direct-monitor-path.sh` write `carrying=0 detached=0
   updated=<now>` instead of `rm` on full success. Satisfies H2 identically,
   makes `wait_for_live_path()` readable, and removes the false report in both
   directions. One line, plus the arm test that should have existed.
2. **N1 — make staleness mean "the number stopped moving", not "the number is
   old".** Two consecutive watchdog ticks with an unchanged `updated=` before
   answering `False`. Closes the forward clock step and the stalled-writer
   case, which are the only two doubled-signal paths left.
3. **Run the arm once, anywhere.** A single test that calls the watchdog loop
   body with a `False` sensor and a stubbed `subprocess.run` replaces N4's
   string test and would have found N2 before I did.

## Finding ledger

| # | Finding | Severity | Fate | Enforced by | Fails today if it regresses? |
|---|---------|----------|------|-------------|------------------------------|
| N1 | A forward clock step or a 6 s writer stall reads a healthy insert as dead → watchdog reconnects the direct path → two copies of the live signal for ~2 s. The H7 fix closed only the backward direction (proven by execution) | 🔴 | Fixed now + Enforced | *to write* — fresh file evaluated at `now+3600` must not read `False` | **No** |
| N2 | `restore-direct-monitor-path.sh` deletes the file `wait_for_live_path()` reads → every genuine repair logs "repair did not take"; every spurious repair logs success | 🔴 | Fixed now + Enforced | *to write* — arm test with a stubbed `subprocess.run` | **No** |
| N3 | Three resolutions of `MPE_RUN_DIR` (writer / eraser / reader); `ExecStopPost` cannot see the exported value | 🟡 | Fixed now | *to write* — one helper, three call sites | **No** |
| N4 | `test_the_arm_is_not_nested_under_a_meter_only_field` asserts source text and indentation, standing in for the only untested path in the feature | 🟡 | Fixed now | replaced by the N2 test | **No** |
| N5 | Fader law: one CC can move a level 15 dB (measured, documented, never decided); now also drives the live monitor mid-take | 🟡 | **Handed to user** — ship as is / clamp per-CC delta / log it. Rec: ship as is, state it in the PR | — | n/a |
| C1 (cycle 1) | Arm-recording into a silent column makes you deaf, silently | 🟡 | **Handed to user, 4 cycles undecided** — rec: ship the behaviour, add one bench log line | — | **No** |
| H4 (c3) | Client still publishes no `direct=`; the default sensor is a proxy for "I did not break it" | 🟡 | Fixed now | *to write* — one `fprintf`, one cross-check test | **No** |
| G9 (c2) | `wire-sooperlooper-graph.sh:18` untested; meter prefers the legacy var; neither var in `PATHS.md` | 🟡 | Enforced | extend `test_the_surge_client_name_matches_the_client` — **OPEN 2 days** | Partly — 4 of 5 sites |
| G10 (c2) | Refusal path still cannot fire while idle (`send()` dedups) | 🟡 | Fixed now | *to write* — forced re-send every N seconds | **No** |
| G11 (c2) | AGENTS.md "never connected at once" still absolute; handover order unchanged | 🟡 | Doc amendment | — **OPEN 2 days, 4th cycle** | **No** |
| D1 | `config/mpe-peak-meter.service:6` contradicts `install-units.sh:45` | 🟡 | Fixed now — delete one sentence | *to write* — grep both files | **No** |
| D2 | `osc_session.ask()` uncaught in the bench main loop; an `OSError` ends the control surface mid-set | 🟡 | Fixed now — three lines | *to write* | **No** |
| 09-07 F1 | `looper_timing._assert_total()` has no `binding_table` cross-check | 🟡 | Enforced | *to write* — **OPEN 18 days** | **No** |
| 09-07 Step0 | `fake_sl_engine.py:67` drops every non-`hit` message, so no in-suite test can exercise `ask()` | 🟡 | Fixed now | *to write* — **OPEN 19 days** | **No** |

*Fourteen rows — two 🔴 (both new, both on the path nothing executes), ten 🟡,
two carried from 2026-09-07 and now 18–19 days old. **"Fails today if it
regresses?" is "No" on every row but one**, and after four cycles the reason has
narrowed to a single sentence that is no longer about the suite's size: the
watchdog's repair arm — the entire point of this feature — has never been run
by a test, by a machine, or by a person, and both of this cycle's 🔴s were
sitting in it. Next reviewer, or next implementer: **run the arm once.** Then
put it on the instrument, supervised, at low level, on speakers.*
