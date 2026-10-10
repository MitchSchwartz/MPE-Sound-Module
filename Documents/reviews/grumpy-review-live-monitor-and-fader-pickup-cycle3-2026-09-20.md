# Grumpy review — live monitor & fader pickup, cycle 3

**2026-09-20.** Branch `dev`, working tree still uncommitted. Same scope as
cycle 2. **Nothing on hardware; the Pi is unreachable.**

**Read in full:** `native/mpe-live-monitor/mpe-live-monitor.c`,
`scripts/sooperlooper/live_monitor.py`, `tests/test_live_monitor.py`,
`scripts/sooperlooper/sl_osc_session.py`, `sl_bench_listener.py`,
the `sl-watchdog.py` / `audio_engine.py` / `mpe-peak-meter.c` /
`sooperlooper-apc-bench.py` / `wire-jack-graph.sh` / `loop_mix.py` diffs,
`scripts/start-mpe-live-monitor.sh`, `scripts/restore-direct-monitor-path.sh`,
`config/mpe-live-monitor.service`, `docs/PATHS.md`, `install-units.sh`,
`tests/engine/harness.py`, `scripts/lib/audio-engine.sh`,
`scripts/lib/periodic_loop_lint.py`, both cycle-2 documents.
**Not read:** LED modules, `slot_*`, `looper_songs`, `track_gesture`,
`docs/CLASSIC-MIDI-PLAN.md`.

**Ran:**

- `python3 -m unittest discover -s tests -q` → **2219 tests, OK (skipped=23),
  121.4 s.** Matches the claim handed to me.
- All three `native/*.c` under `-O2 -Wall -Wextra -Werror -std=c11` → **clean.**
- **The real SooperLooper 1.7.9 in Docker** (`tests/engine/`), driving the
  *production* `SlOscSession` + `SlBenchStateListener` + `LiveMonitor` — to
  settle whether `ask()` actually works. See §Verified.
- `read_graph_snapshot()` executed directly against synthetic state files, in
  both the meter-off and meter-on configurations, to settle H1.
- Timed `poll_monitor_capture` at its real call rate (§G12).

Nothing touched the appliance and nothing made sound. One temporary probe was
written under `tests/engine/` and deleted after the run; the working tree is
otherwise untouched.

---

## Step 0 — the fate of the cycle-2 findings

Eight of twelve closed, and four of those closures are the best work in three
cycles. **But the finding that has now survived two cycles — G5, "the watchdog
arm does not run in the default configuration" — is still open, and this cycle
closed it in appearance with a new sensor, a new helper, a new state file, a
new PATHS.md row and five new tests, none of which the watchdog can reach.**

| # | Cycle-2 finding | Mark | Evidence now |
|---|---|---|---|
| G1 | Capture TTL rests on a premise the repo refutes | **CLOSED, and verified against the engine** | `CAPTURE_TTL_S` is gone. `needs_verification()` → `SlOscSession.ask()` → reply on `/sl/bench/state` → `note_state`. I ran the **production objects against the real engine**: a held take produced **1 state datagram in 5 s** (change-only, as canon says), and the `ask` was **answered in 1 ms** through the bench path. The mechanism is right and it is the correct shape of fix. Residual → **H3** |
| G2 | Meter's `surge_playback_wired()` is the presence-not-path law | **CLOSED** | Now `direct \|\| through_insert`, both legs, both channels, exact names, with a comment naming the fault it replaced. Correct. The *duplication* got worse → **H4** |
| G3 | The enable-law test `exec`s the real client | **CLOSED** | `--check` answers and exits before `exec`; `test_python_and_shell_agree_on_what_switches_it_on` uses it. The guard on the guard is weak → **H5** |
| G4 | `!g_direct_detached` blocks re-detach forever | **CLOSED, well** | Guard dropped; the announcement is edge-triggered instead; `restore-direct-monitor-path.sh`'s comment is corrected *and* carries a warning to whoever edits `ensure_wiring` next. That is a fix that defends itself |
| G5 | The watchdog arm is inert unless `MPE_PEAK_METER=1` | **OPEN — 1 day. Still inert, now with more machinery in front of it** | `read_graph_snapshot()` computes `live` from the new sensor and then **throws it away** whenever the meter is off, and the arm is nested under `snap.jack_reachable`, which is also meter-only. Proven by running it → **H1** |
| G6 | A successful repair reports as a failed one | **CLOSED in form** | `wait_for_live_path()` polls, mirroring the sibling helper. But in the default config the arm never runs (H1), and where it does run the sensor can be permanently stuck at False → **H2** |
| G7 | `restore_direct_path()` ignores failures, clears the flag anyway | **CLOSED** | Counts per channel, logs the channel, `g_direct_detached` cleared only on `failures == 0`, with the reason in the comment |
| G8 | TTL tests are handed the cadence they measure | **CLOSED** | Replaced by `AskingRatherThanAssuming`, which tests ask/answer/no-answer as behaviour with injected clocks. Real tests. Two *new* weak ones appeared → **H5** |
| G9 | Surge client name: 2 of 4 sites tested | **HALF-CLOSED** | The meter now falls back to `MPE_SL_SURGE_CLIENT` and the test covers it. **`wire-sooperlooper-graph.sh:18` is still uncovered**, and the meter still *prefers* `MPE_PEAK_METER_SURGE_CLIENT`, so a box that ever set the legacy variable still diverges. Neither variable is in `PATHS.md` |
| G10 | Nothing pushes the monitor on a timer | **MOVED, not closed** | `poll_monitor_capture()` now calls `push_monitor()` ~485× a second — but `send()` dedups on value, so during an idle stretch **no datagram leaves and the refusal path still cannot fire**. `monitor_sender.close()` is still never called. And `push_monitor`'s own docstring 40 lines above still says it is "never on a timer … the idle branch runs at roughly 485 Hz and this would be pure repetition there" |
| G11 | AGENTS.md invariant not amended; handover order unchanged | **OPEN — 1 day** | `ensure_wiring()` still connects `out_N → playback_N` (line 322) before checking and detaching (line 353). AGENTS.md still says "both paths must never be connected at once" as an absolute |
| G12 | Repair arm's cadence × cost unstated | **OPEN, and joined by a second one** | The arm's cost is moot while H1 holds. The *new* 485 Hz poll went in with no number either. I measured it: **1.5 µs/pass idle, 2.7 µs capturing → 0.07–0.13 % of a laptop core.** Cheap. It should still have been in the PR, per AGENTS.md |
| F7 (carried) | Fader law path-dependent; docstring overclaims | **OPEN — 1 day, still undecided** | `loop_mix.py:33-35` still says "the two positions converge as you play". No decision recorded anywhere |
| 09-07 F1 | `looper_timing` has no `binding_table` cross-check | **OPEN — 17 days** | unchanged |
| 09-07 Step0 | `fake_sl_engine.py:67` discards every non-`hit` message | **OPEN — 18 days** | unchanged |

**Counts: 8 CLOSED · 1 HALF-CLOSED · 1 MOVED · 5 OPEN (one of them 17 days, one 18).**

**The headline.** Cycle 2's five 🔴 are down to one — but that one is G5, which
is now on its third cycle, and the way it failed this time is the worst of the
three. Cycle 1 shipped it missing with a header comment admitting the hole.
Cycle 2 shipped it coupled to an off-by-default service. Cycle 3 built the
right sensor, in the right process, published on the right cadence, with the
staleness-is-the-alarm reasoning written out properly — and then wired it into
a function that discards it. Every piece is correct except the one line that
connects them, and **the five tests written for it all pass**, because they
test the sensor and nobody tested the wiring.

---

## 1. First impressions

The best diff of the three cycles, and it is not close. The TTL is not merely
deleted — the reason it was wrong is in the source, with the date and the
measurement, next to the mechanism that replaced it. `ask()` is a genuinely
good piece of design: it reuses the return path the bench already listens on,
so the answer arrives through the same door as every other state update and no
second cache exists. I pointed it at a real SooperLooper and it answered in a
millisecond.

`restore-direct-monitor-path.sh`'s corrected comment is the single best line in
the diff, because it does not just state the true thing — it says *"that second
half was false for one revision … if you are reading this after changing
`ensure_wiring`, the guarantee this sentence makes is the one you have to
keep."* That is a comment that fixes the bug and then stands guard over it.

What has not changed across three cycles is *where* the failures live. All the
hard parts of this component are the seams between processes — the graph, the
engine, the state files — and the tests still stop at the module boundary on
every one of them. H1 is that sentence made concrete: a sensor with five
passing tests that the watchdog cannot see.

## 2. Architecture & structure

The supervision path is now the right shape on paper and the wrong shape in
`read_graph_snapshot`:

```
mpe-live-monitor ──> /run/mpe/live-monitor.state ──> live_path_via_monitor_state()
                                                             │  (correct: returns False)
                                                             ▼
                        read_graph_snapshot()  ──── DISCARDS IT unless meter.state is fresh
                                                             │
                                          GraphSnapshot(None, None, None, "meter_stale")
                                                             │
                             `if snap.jack_reachable and …:`  ← also meter-only, also None
                                                             ▼
                                                    (arm never entered)
```

Moving the sensor into the process that causes the fault was the right call and
it removed the `jack_lsp` fork the lint forbids. The mistake is that
`read_graph_snapshot` is a single all-or-nothing gate over four independent
questions; one sensor being absent zeroes the other three. That is the
structural fix worth making, not a patch to the `if`.

`run_bench` grows a fourth closure (`poll_monitor_capture`) and still has zero
tests that construct it (2026-09-07 F5, open).

## 3. Single authority

### Q1 "Is the live monitor on?" — **one owner, now shared with a fourth reader.** Credit.

`live_monitor.enabled()`; the shell matches via `--check`; `sl-watchdog.py`
*imports* it rather than restating it, with a comment saying why. Correct.

### Q2 "Is this track still capturing?" — **one owner, and the owner now asks.** Credit.

`LiveMonitor._capturing`, dropped only on the engine's word or on an
**unanswered** question. Verified against the real engine.

### Q3 "What is Surge's JACK client name?" — **five sites, two variables, four tested.**

| Site | Reads | Tested? |
|---|---|---|
| `mpe-live-monitor.c:504` | `MPE_SL_SURGE_CLIENT` | authority |
| `wire-jack-graph.sh:23` | `MPE_SL_SURGE_CLIENT` | ✅ |
| `restore-direct-monitor-path.sh:21` | `MPE_SL_SURGE_CLIENT` | ✅ |
| `mpe-peak-meter.c:403-405` | `MPE_PEAK_METER_SURGE_CLIENT` **then** `MPE_SL_SURGE_CLIENT` | ✅ (fallback only) |
| `wire-sooperlooper-graph.sh:18` | `MPE_SL_SURGE_CLIENT` | ❌ |

Better. Still not closed, and the meter still prefers the legacy variable, so
"set the legacy one once, years ago" is a live divergence path. Neither
variable is in `docs/PATHS.md`.

### Q4 "Does Surge reach playback / is the insert carrying?" — **four implementations, up from three.**

| Site | Language | Law |
|---|---|---|
| `mpe-live-monitor.c:monitor_path_live()` | C | both legs, both channels, insert only |
| `wire-jack-graph.sh:live_monitor_carrying()` | shell + `jack_lsp -c` | both legs, both channels, insert only |
| `mpe-peak-meter.c:surge_playback_wired()` | C | direct OR insert, both legs, both channels |
| `audio_engine.live_path_via_monitor_state()` | Python, over a file | `carrying` **or** "it never said it detached" |

The first three now agree on the hard part, which is the G2 fix and is real
progress. Nothing cross-checks any pair of them, and the fourth is a *proxy*
with different semantics: it answers "did the live monitor break it", not "is
it broken". In the configuration it was built for it is the only sensor — so
any breakage of `Surge → playback` by anything other than the insert reads as
healthy. The comment on the arm asks "Can Mitch hear himself play?"; this
sensor cannot answer that question. → **H4**

**Owner:** the meter's predicate is now the correct one. Either the watchdog
gets it from a process that always runs, or `live-monitor.state` should publish
`direct=0/1` too so the Python side stops inferring.

## 4. Code quality

- **`push_monitor`'s docstring is now false in its own file.** It says "never
  on a timer" and explains at length why the 485 Hz idle branch must not call
  it; `poll_monitor_capture`, defined 20 lines below, calls it unconditionally
  from exactly that branch. One of the two has to go.
- **The per-sample NULL guard in `process()` is dead for the second cycle
  running** (`mpe-live-monitor.c:149-151`), under a hoisted check that already
  handles it and a comment explaining why the hoist exists.
- **`config/mpe-peak-meter.service:6` still says "install-units.sh lists this
  in DISABLED"**; `install-units.sh:45` still says the opposite, and now names
  both units while doing it. Second cycle.
- `monitor_sender.close()` is still never called on bench exit.

## 5. Code smells — the hall of shame

### 🔴 H1 — The new sensor is discarded before the watchdog can act on it

```python
    live = surge_playback_via_meter(now=t)
    if live is None and live_monitor_enabled():
        live = live_path_via_monitor_state(now=t)      # correct, and computed
    if jack is not None and looper is not None and playback is not None:
        return GraphSnapshot(jack, looper, playback, "meter", live)

    return GraphSnapshot(None, None, None, "meter_stale")   # <-- `live` dropped
```

`jack`, `looper` and `playback` all come from `meter.state`. With
`MPE_PEAK_METER=0` — **the default**, `docs/PATHS.md:43` — all three are
`None`, so the early return never fires and `surge_playback` falls back to its
`None` default. And even if it did not, the arm is nested under
`if snap.jack_reachable and not orphan and not stopped:` (`sl-watchdog.py:578`),
which is `None` for the same reason.

Two gates, both meter-only, in front of a sensor whose entire purpose was to
stop depending on the meter. Run, not read:

```
$ MPE_LIVE_MONITOR=1  (meter absent; live-monitor.state stale, detached=1)
live_path_via_monitor_state (the sensor) -> False          # correct answer
read_graph_snapshot -> GraphSnapshot(jack_reachable=None, looper_client=None,
                       looper_playback=None, source='meter_stale',
                       surge_playback=None)
repair arm fires? False | audio-path block entered? False
```

With the meter **on** and a fresh `meter.state`, the same inputs give
`surge_playback=False` and the arm works. So the mechanism is fine; the wiring
is the bug, and `MPE_PEAK_METER=1` is still the only configuration in which
cycle-1's F2 is answered — which is precisely what cycle 2's G5 said, and
cycle 1's F2 before that.

The five tests in `TheSensorTheWatchdogRepairsFrom` all pass. Not one of them
calls `read_graph_snapshot`.

**Fix:** stop making `GraphSnapshot` all-or-nothing — return the fields that
*are* known and let each arm gate on its own field. Then gate the live-path arm
on `snap.surge_playback is False` alone, not on `snap.jack_reachable`, since
the insert's own state file already implies a live graph. **Enforced by:** a
test that calls `read_graph_snapshot()` with no `meter.state` and a stale
`live-monitor.state` and asserts `surge_playback is False`.

### 🔴 H2 — Nothing ever clears `live-monitor.state`, so stopping the unit alarms forever

`connect_thread` writes one last line on the way out, then `main()` calls
`restore_direct_path()` — and never writes the file again:

```c
    write_live_state(0, atomic_load_explicit(&g_direct_detached, ...), ...);
    return NULL;                       /* connect_thread ends here */
    ...
    if (!g_jack_shutdown) {
        restore_direct_path();         /* succeeds, clears the flag, publishes nothing */
    }
```

So after a clean `systemctl stop mpe-live-monitor`, `/run/mpe/live-monitor.state`
is left saying `detached=1` and stops being updated — while the direct path is,
in fact, restored. Six seconds later it is stale, and
`live_path_via_monitor_state()` returns **False** by design ("the process that
owed us a restore is gone, and what it removed is still removed"). Nothing will
ever rewrite that file. `MPE_LIVE_MONITOR` is still `1` in `/etc/mpe/mpe.env`,
because stopping a unit is not the same as disabling the feature.

Result, once H1 is fixed (or today, with the meter on): every 10 s tick appends
`nothing reaches system:playback from Surge or the live monitor`, forks `bash`
plus two `jack_connect`s, **blocks for the full 4 s of `wait_for_live_path`**,
then logs `repair did not take` followed by the restore script's *successful*
output. Forever. That is cycle-2's G6 symptom reborn from a different cause,
and it burns 40 % of the watchdog's duty cycle doing it.

**Fix:** the state file's lifetime must end with the thing it describes.
Cheapest correct version: `restore-direct-monitor-path.sh` removes
`$(mpe_run_dir)/live-monitor.state` **only when every channel reconnected** —
which covers the clean stop (ExecStopPost) and the crash (ExecStopPost) with
one line, and correctly leaves the alarm standing when the restore failed.
`main()` should also publish one final `detached=0` after a successful restore.
**Enforced by:** a test that the restore script deletes the file on success and
leaves it on failure.

### 🟡 H3 — One lost datagram still raises the monitor mid-take

The verify protocol is right, and I confirmed the happy path against the real
engine. The unhappy path is a single point of failure with an audio-safety
consequence:

```python
VERIFY_AFTER_S = 3.0       # ask once
VERIFY_TIMEOUT_S = 1.5     # then drop the capture
```

`needs_verification()` hands out a loop **once** and records `asked`. There is
no retry. If that one `/sl/N/get` datagram is lost, or the reply is, or the
engine's OSC thread is stalled past 1.5 s, the capture is dropped and
`target_amp` returns to `live_amp()` — which is **unity by default**. Measured
in cycle 2 on the same code path: +16 dB. The C client ramps it over 80 ms, so
it is a swell rather than a click, and it is still an unbounded upward move on
a lost UDP packet, on an appliance AGENTS.md describes as possibly having
headphones on someone's head.

The module's own docstring argues the direction is safe ("you hear yourself
play, rather than being silent in the phones"). That is a fair argument for
*silence*, and this is not the silent case: the common case is a column fader
pulled down, so giving up means going **loud**.

**Fix:** ask two or three times before giving up (cost: two extra datagrams per
lost one), and/or fall back to `min(live_amp(), last_known_capture_amp)` rather
than straight to live. Either keeps the "engine is dead, let him hear himself"
property without making a dropped packet a 16 dB event. **Enforced by:** a
`tests/engine/` case that kills the engine mid-take and asserts the monitor
lands at the live level, plus a unit test that one lost question does not.

### 🟡 H4 — Four answers to "is the live path up", and the one the watchdog uses is a proxy

Listed in §3 Q4. The specific hazard: `live_path_via_monitor_state` returns
`True` when the insert says it "has not taken the direct path away". That is
"I did not break it", not "it works". Anything else that breaks
`Surge → playback` — a hand-run `jack_disconnect`, a wiring script race, the
`common_out` repair arm firing — reads as healthy through the only sensor the
default configuration has. The arm's own comment claims it answers "Can Mitch
hear himself play?" It does not.

**Fix:** have the client publish `direct=0/1` (it already calls
`port_connected_to` for exactly this) so the Python side reports a fact instead
of inferring one.

### 🟡 H5 — Two new tests that cannot fail the way they claim, and a five-test class that tests the wrong seam

```python
def test_the_gate_check_never_reaches_the_binary(self):
    """The guard above is only safe while --check exits before `exec`."""
    check_exit = text.index('if [ "$CHECK_ONLY" -eq 1 ]; then')
    self.assertLess(check_exit, text.index('exec "$BIN"'), ...)
```

This asserts that one string appears before another in a file. Delete the
`exit 0` inside that block and the test still passes while the script runs the
gain stage — which is the entire G3 hazard, restored. The honest version costs
three lines: point `MPE_MODULE_REPO` at a fixture whose `$BIN` writes a marker
file, run `--check` with `MPE_LIVE_MONITOR=1`, assert the marker does not exist.

```python
    self.assertNotIn("CAPTURE_TTL_S", source)
```

Pins a *name*. `EXPIRE_AFTER_S` reintroduces the bug with the test green. It is
paired with a real behavioural assertion (an hour of silence, still recording),
which carries the class — so this line is noise rather than harm, but it is the
line a reader will trust.

And the pattern that matters: **`TheSensorTheWatchdogRepairsFrom` has five
tests, all correct, all passing, for a sensor H1 shows the watchdog never
reads.** Cycle 1 said it, cycle 2 said it, and it is still true — the suite
measures what each module computes and never the seam between two of them. Of
this cycle's two 🔴, both live on a seam, and both are invisible to 2219 tests.

### 🟡 H6 — `PATHS.md` still documents the deleted TTL, with the refuted claim attached

```
| `MPE_LIVE_MONITOR_CAPTURE_TTL_S` | `2.0` | … SooperLooper streams state every
100 ms, so a live take never expires; …
```

`docs/PATHS.md:48`, in the table AGENTS.md sends every agent to for env vars.
The variable no longer exists in any source file — `grep` finds it only here
and in the test asserting it is gone. The sentence is the exact false premise
this whole cycle was spent removing, now in the document with the longest
reach, while `live_monitor.py` three files away carries the measurement
disproving it.

The two variables that **do** exist — `MPE_LIVE_MONITOR_VERIFY_AFTER_S` and
`MPE_LIVE_MONITOR_VERIFY_TIMEOUT_S` — appear in no document at all.

**Fix:** delete the row, add the two real ones. **Enforced by:** the test that
greps for `CAPTURE_TTL_S` should grep `docs/PATHS.md` too — it is one line and
it would have caught this.

### 🟡 H7 — A clock step, or a `/run/mpe` fallback, breaks the sensor in the dangerous direction

Two ways the file-based sensor lies, both of which the test class's own
docstring says to look for:

1. **Clock.** `meter_state_age_s()` returns `None` on a *negative* age.
   `live_path_via_monitor_state` then falls through to
   `if detached is True: return False`. So a healthy, actively-writing insert
   whose `updated=` is in the future — an NTP step backwards — reports **False**
   and the watchdog reconnects the direct path underneath it. A step *forwards*
   makes a fresh file look stale, with the same result. The Pi keeps time by
   NTP (`docs/measurements/README.md:74`) and steps are ordinary at boot on a
   board without a battery-backed RTC. Thanks to the G4 fix the client then
   re-detaches within 2 s, so this is no longer permanent — it is an
   **oscillation**: up to 2 s of doubled live signal every 10 s, plus a
   `repair did not take` line each time. *(Reachability on the actual hardware
   is unverified — the Pi is unreachable. The code-level asymmetry is not:
   an unusable timestamp should answer `None`, not `False`.)*
2. **Path.** `mpe_run_dir()` (`scripts/lib/audio-engine.sh:212-224`) silently
   falls back to `$TMPDIR/mpe` when `/run/mpe` is not writable, warning once on
   stderr — *"state may split across processes"*. `audio_engine.py:30` hardcodes
   `/run/mpe/live-monitor.state`. When that fallback happens the watchdog sees
   no file, answers `None`, and the safety net is off with nothing said.

**Fix (1):** treat an unusable or negative age as `None`. **Fix (2):** have
`audio_engine` honour `MPE_RUN_DIR` like the shell does.

### 🟢 Minor

- Dead per-sample NULL guard in `process()` — second cycle.
- `config/mpe-peak-meter.service:6` vs `install-units.sh:45` — second cycle.
- `push_monitor` docstring vs `poll_monitor_capture` (see §4).
- `monitor_sender.close()` never called.
- `live-monitor.state` publishes `surge_client=`; nothing reads it.
- `osc_session.ask()` is called from the bench's main loop with no `try`. A
  `send_message` that raises takes the bench down. Cheap belt.

## 6. Logic & business rules

The rule is back to one sentence with no exception, which is the real win of
this cycle: *while a track is capturing you hear yourself at that track's
level*, and the only thing that ends a capture is the engine saying so — or
refusing to answer when asked. That is a rule you can hold in your head, and
the code is a transcription of it again.

**Settings read once**, re-checked: `VERIFY_AFTER_S`, `VERIFY_TIMEOUT_S`,
`ENABLED`, `REFUSAL_COMPLAIN_S`, `RECOVERY_STREAK`, `resolve_live_cc()`, the
C's `load_env()`. All correct for an appliance where env changes mean a
restart. `live_monitor_enabled()` in the watchdog reads the flag at call time,
not at import — correct, since the watchdog outlives config edits.

**Recording into a silent track still makes you deaf**, still handed to the
user in cycle 1, still no decision and no log line.

## 7. Test strategy & execution

2219 OK in 121 s — verified. The new `AskingRatherThanAssuming` class is a real
improvement: injected clocks, ask/answer/no-answer as three distinct
behaviours, and a docstring that records *why* the previous design was wrong
with the measurement attached. `test_a_silent_engine_does_not_end_a_take` is
the assertion cycle 2 asked for and it can fail.

**The doubles, as instruments.** Unchanged in kind, and the axis has moved
again:

| What the suite measures | How it gets it |
|---|---|
| the level Python decides | computed — real |
| whether a refusal is reported | real closed port — real |
| whether an unanswered question drops a capture | computed from an injected clock — **real** |
| whether the engine answers `ask` at all | **not expressed** (I ran the engine; the suite cannot) |
| whether the watchdog can see the sensor | **not expressed** — H1 |
| whether the state file's lifetime is right | **not expressed** — H2 |
| whether the C client behaves | not expressed |

Both 🔴s this cycle are seam findings, and both would be caught by a single
test each that crosses one module boundary. `tests/engine/` is now proven twice
over (cycle-2 audit, and this cycle's `ask` probe) and still has no case in it
from this feature.

## 8. Security & performance

Unchanged and fine. Loopback bind, strict parser, clamp-to-attenuation,
`JackUseExactName`. No secrets, hostnames or addresses in any new file — I
checked the state file's contents against the public-repo banner; `surge_client=`
is a JACK client name, not infrastructure.

Performance, with numbers this time, since AGENTS.md asks for them:

| Addition | Cadence | Measured cost |
|---|---|---|
| `poll_monitor_capture` (idle) | ~485 Hz | 1.51 µs/pass → **0.07 %** of a laptop core |
| `poll_monitor_capture` (capturing) | ~485 Hz | 2.70 µs/pass → **0.13 %** |
| `ask` datagram while a take is held | 1 per 3 s | negligible |
| `write_live_state` | 1 per 2 s | one `fopen`/`rename` |
| watchdog repair arm | 3 forks + a 4 s block per 10 s tick **while alarming** | see H2 |

Nothing here is a problem except the last row, which is a problem because H2
makes it permanent.

## 9. Documentation vs. reality

| Claim | Verdict |
|---|---|
| `live_monitor.py`: "`register_auto_update` delivers on change, not on a timer … five seconds produced one datagram" | ✅ **True — I re-ran it. 1 datagram in 5 s.** Citing the measurement in the source is exactly right |
| `live_monitor.py`: "the bench sends a `get` whose reply arrives on the same path as any other state update" | ✅ True — verified against the engine, answered in 1 ms through `/sl/bench/state` |
| `restore-direct-monitor-path.sh`: "a healthy client re-detaches within its next wiring pass (2 s)" | ✅ True now, and it says so was false once. Best comment in the diff |
| `mpe-live-monitor.c` header: "`sl-watchdog.py` asserts that Surge reaches playback by one route or the other" | ❌ **Still false in the default configuration** — H1. Third cycle for this sentence |
| `docs/PATHS.md` `MPE_LIVE_MONITOR`: "`sl-watchdog.py` repairs it from `live-monitor.state`" | ❌ Same — H1. At least the two documents now agree with each other, which is progress of a sort |
| `docs/PATHS.md` `MPE_LIVE_MONITOR_CAPTURE_TTL_S`: "SooperLooper streams state every 100 ms" | ❌ **Dead variable, refuted claim, still in the env table** — H6 |
| `push_monitor` docstring: "never on a timer" | ❌ Called ~485× a second, 20 lines below |
| `mpe-peak-meter.c`: "an insert wired to playback with nothing feeding it satisfied it perfectly … a sensor that can be satisfied by the failure it watches for is worse than no sensor" | ✅ True, fixed, and the comment earns its length |
| `AGENTS.md`: "both paths must never be connected at once" | ❌ Still violated transiently by construction (G11, third cycle) |
| `loop_mix.py`: "the two positions converge as you play" | ⚠️ F7, still undecided |

Build/deploy: the `native` CI job is real — I compiled all three clients under
its exact flags, clean.

---

## Verified against the real engine

Cycle 2 asked the next reviewer to settle the delivery question; the cycle-2
audit did. This cycle the open question was the *replacement*, so I ran the
production `SlOscSession`, `SlBenchStateListener` and `LiveMonitor` against
SooperLooper 1.7.9 in the `tests/engine/` container:

```
state datagrams in 5s of a held take: 5 -> [4 loops at t=0 (reg), loop 0 -> RECORDING at t+0.5]
capturing_loop after 5s silence: 0
ASK ANSWERED in 0.001s -> [(loop 0, state 2)]
ask on idle loop 1 -> [(loop 1, state 0)]
after stopping the take, capturing_loop=None
```

So: `/sl/N/get` with a retpath of `/sl/bench/state` **is** right for
SooperLooper; the reply carries `(loop, control, value)` and lands on
`_on_bench_state` → `on_update` → `on_state` → `note_state` with no special
casing anywhere. The "what if the reply never routes there" case is real but is
a lost-datagram/stalled-engine question rather than a protocol question, and it
is H3.

---

## Verdict

This is the cycle where the hard finding got fixed properly. G1 was not patched
— it was re-founded on asking the engine instead of guessing at it, the reason
is in the source with a date and a number, and when I pointed the production
objects at a real SooperLooper the mechanism worked on the first try. G4 and G7
are clean closures with comments that will stop the next person re-breaking
them. G2's predicate is now correct in the place it matters.

Against that, **G5 is open for the third cycle, and it failed in the most
expensive way available**: everything needed to close it was built — the right
sensor in the right process on the right cadence, published as a file, with the
`jack_lsp` ban respected and the staleness-is-the-alarm reasoning written out —
and then `read_graph_snapshot` throws the answer away whenever the meter is
off, which is the default. Five tests cover the sensor. None covers the
function that reads it. That is one line to fix and one test to stop it
recurring, and until both exist, enabling the live monitor the documented way
still removes the fail-open path with no supervisor behind it, exactly as it
did in cycle 1.

The second 🔴 is the mirror image: the sensor's *lifetime* was never designed.
Stop the unit cleanly and it leaves a file asserting a fault that has already
been repaired, which nothing will ever rewrite — so the day H1 is fixed, the
watchdog starts alarming forever on a healthy instrument and spends four
seconds of every ten-second tick doing it. Fix them together or the first fix
lands straight into the second.

The fader side is unchanged and remains close to done; F7 is a decision Mitch
has not been asked for in two cycles.

## Priority backlog (🔴 only)

1. **H1 — stop `read_graph_snapshot` discarding `surge_playback`,** and stop
   the live-path arm gating on `snap.jack_reachable`. Return per-field answers
   instead of an all-or-nothing snapshot. One test: no `meter.state`, stale
   `live-monitor.state`, assert `surge_playback is False`. Without this the
   entire cycle-3 sensor is unreachable and F2 is open for a third cycle.
2. **H2 — give `live-monitor.state` a lifetime.** Have
   `restore-direct-monitor-path.sh` delete it when every channel reconnected,
   and have `main()` publish `detached=0` after a successful restore. Land it
   with H1, because H1 is what makes H2 audible.

## Finding ledger

| # | Finding | Severity | Fate | Enforced by | Fails today if it regresses? |
|---|---------|----------|------|-------------|------------------------------|
| H1 | `read_graph_snapshot()` drops the live-monitor sensor whenever `meter.state` is absent, and the arm is gated on a second meter-only field — so the cycle-3 sensor never reaches the watchdog in the default config (proven by execution) | 🔴 | Fixed now + Enforced | *to write* — `test_the_watchdog_sees_the_insert_without_the_meter` | **No** |
| H2 | Nothing ever clears `live-monitor.state`; a clean `systemctl stop` leaves `detached=1` stale forever → permanent false alarm, 3 forks and a 4 s block every 10 s | 🔴 | Fixed now + Enforced | *to write* — restore script deletes the file on full success, keeps it on failure | **No** |
| H3 | One `ask` only, no retry: a lost datagram or a 1.5 s engine stall drops a live capture and returns the monitor to unity (+16 dB measured) | 🟡 | Fixed now | *to write* — retry count, and a `tests/engine/` kill-mid-take case | **No** |
| H4 | Four implementations of "is the live path up"; the one the default config uses is a proxy that answers "I did not break it" | 🟡 | Fixed now | *to write* — publish `direct=` from the client; cross-check test | **No** |
| H5 | `test_the_gate_check_never_reaches_the_binary` asserts string order; `assertNotIn("CAPTURE_TTL_S")` pins a name; the five sensor tests pass while H1 makes the sensor unreachable | 🟡 | Fixed now | `tests/test_live_monitor.py` — the tests are the defect | **No** |
| H6 | `docs/PATHS.md:48` documents the deleted `MPE_LIVE_MONITOR_CAPTURE_TTL_S` with the refuted "streams state every 100 ms"; the two real VERIFY vars are undocumented | 🟡 | Fixed now + Enforced | extend the existing `CAPTURE_TTL_S` grep to `docs/PATHS.md` | **No** |
| H7 | Unusable/negative `updated=` answers `False` (repair) instead of `None`; `audio_engine` ignores `MPE_RUN_DIR` while the writer honours it | 🟡 | Fixed now | *to write* — a future-timestamped state file must read `None` | **No** |
| G5 (cycle 2) | Watchdog arm inert in the default configuration | 🔴 | **OPEN — 1 day, 3rd cycle** | superseded by H1 | **No** |
| G9 (cycle 2) | `wire-sooperlooper-graph.sh` still untested; meter still prefers `MPE_PEAK_METER_SURGE_CLIENT` | 🟡 | Enforced | extend `test_the_surge_client_name_matches_the_client` | Partly — 4 of 5 sites |
| G10 (cycle 2) | Refusal reporting still cannot fire while idle (`send()` dedups); `monitor_sender.close()` never called | 🟡 | Fixed now | *to write* — periodic forced re-send | **No** |
| G11 (cycle 2) | Handover order unchanged; AGENTS.md "never connected at once" not amended | 🟡 | Fixed now + doc amendment | — | **No** |
| G12 (cycle 2) | Cadence × cost unstated for the new 485 Hz poll (measured here: 0.07–0.13 % of a core) | 🟡 | Handed to user — state it in the PR | — | **No** |
| F7 (carried) | Fader law path-dependent; `loop_mix.py:33-35` still claims convergence | 🟡 | Handed to user — **undecided, 2 cycles** | — | **No** |
| 09-07 F1 | `looper_timing._assert_total()` has no `binding_table` cross-check | 🟡 | Enforced | *to write* — **OPEN 17 days** | **No** |
| 09-07 Step0 | `tests/fake_sl_engine.py:67` discards every non-`hit` message | 🟡 | Fixed now | *to write* — **OPEN 18 days** | **No** |

*Fifteen rows — two 🔴 (down from five), eleven 🟡, two carried from 2026-09-07.
**"Fails today if it regresses?" is still "No" on almost every row**, and the
reason has narrowed to one sentence: the suite tests what each module computes
and never the seam between two of them. Both of this cycle's 🔴s are seam bugs,
both are one small test away from being permanent, and `tests/engine/` — now
proven twice — still has no case from this feature in it. Next reviewer: check
H1 first. If `read_graph_snapshot` still returns `surge_playback=None` with the
meter off, F2 is open for a fourth cycle and no amount of sensor quality is
going to close it.*
