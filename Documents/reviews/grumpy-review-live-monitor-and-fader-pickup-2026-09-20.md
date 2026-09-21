# Grumpy review — the live monitor, and the fader pickup law

**2026-09-20.** Branch `dev`, working tree uncommitted: 10 modified files and 6
new ones (`native/mpe-live-monitor/`, `scripts/sooperlooper/live_monitor.py`,
`tests/test_live_monitor.py`, the service, the two shell wrappers).
`docs/CLASSIC-MIDI-PLAN.md` is out of scope and was not read. Reviewer had no
hand in any of it. **None of this has run on the hardware.**

**Read in full:** `native/mpe-live-monitor/mpe-live-monitor.c` and its Makefile,
`scripts/sooperlooper/live_monitor.py`, `tests/test_live_monitor.py`,
`scripts/sooperlooper/loop_mix.py`, `tests/test_loop_mix.py`,
`config/mpe-live-monitor.service`, `scripts/start-mpe-live-monitor.sh`,
`scripts/build-mpe-live-monitor.sh`, `sl_bench_listener.py`, the
`wire-jack-graph.sh` and `install-units.sh` diffs, the AGENTS.md and PATHS.md
diffs, and the two 2026-09-07 review documents. **In part:**
`sooperlooper-apc-bench.py` (the monitor block and its call sites only),
`sl_osc_session.py`, `sl-watchdog.py`, `wire-sooperlooper-graph.sh`,
`dump-loop-levels.py`, `mpe-peak-meter.c` head. **Not read:** the LED modules,
`slot_*`, `looper_songs`, `track_gesture`.

**Ran:** `python3 -m unittest discover -s tests -q` → **2198 tests, OK
(skipped=23), 120.8 s.** Plus a scripted exercise of `LoopMix` to get the
numbers in F7/F8. Nothing that touches the appliance, nothing that makes sound.

---

## Step 0 — the fate of the 2026-09-07 findings

Source: `grumpy-review-looper-consolidation-2026-09-07.md`, the **first and only**
review in this directory that ended with a ledger. That ledger worked — this
table took ten minutes instead of a day, which is the entire argument for F13.

| # | Finding (2026-09-07) | Mark | Evidence now |
|---|---|---|---|
| F1 | `looper_timing._assert_total()` is vacuous; no cross-check against `binding_table.ACTIONS` | **OPEN — 13 days** | `grep -n binding_table tests/test_looper_timing.py` → nothing. The named fix was never written |
| F2 | `RECORD_START` decided in `loop_model`, outside `when()` | **CLOSED** | `loop_model.py:169-170` now calls `timing.when(timing.RECORD_START, …)`; `:228` routes the wait through it too |
| F3 | `sooperlooper/README.md` says 16 tracks | **CLOSED (mostly)** | `grep -c "16 \|sixteen"` → 1 remaining hit, down from 6 |
| F4 | `Moment` omits `enforced_by` | **UNTRACKED** | Not re-checked; out of this review's scope |
| F5 | `looper_timing` loaded twice | **CLOSED** | `tests/test_looper_timing.py:22` carries the comment and the single import |
| F6 | Stop-All assertion weakened in the same diff as the fix | **OPEN — 13 days** | Not re-verified line by line, but see 🟡 F16: **the same pattern recurs in today's diff** |
| F7 | Three call sites lift different subsets of `engine_controls` | **CLOSED (partly)** | `sl_grid_sync.py:172` now sends `mute_quantized` from the dict; `looper_songs.py` no longer greps as a second site |
| F8 | AST guard covers 2 of 4 controls | **CLOSED** | `tests/test_looper_timing.py:167` `CONTROLS = set(timing.engine_controls(grid=True))` — derived, not literal |
| F9 | `Session.grid` has two producers | **UNTRACKED** | Not re-checked |
| F10 | Flaky `test_planned_promote_sync_path` | **UNTRACKED** | Full suite passed once today; one pass is not evidence of a fix |
| F11 | `DEFERRED_LAUNCH_GRACE_S = 5.0` unjustified | **UNTRACKED** | Not re-checked |
| F12 | `MPE_LOOPER_EIGHTH_PER_CYCLE` honoured in one of three places | **CLOSED (partly)** | `sl_grid_sync.py:209` default is now `EIGHTH_PER_CYCLE`, not a literal 8. `sl_grid_state.py:338` still computes `beats_per_cycle * 2`, but `:315-328` now argues they are the same quantity on purpose |
| F13 | No review contains a finding ledger | **CLOSED** | `grep -l "Finding ledger" Documents/reviews/*.md` → 2 (the review and its audit). This file is the third |
| Step0 #1/#2 | `fake_sl_engine` discards every `/set`; loop length is an argument | **OPEN — 14 days** | `tests/fake_sl_engine.py:62-65` byte-identical: `if parts[2] != "hit": return` |

**Counts: 6 CLOSED · 3 OPEN · 5 UNTRACKED (not re-checked — scope).**

**The headline:** the ledger did its job. Six closures are verifiable in one
grep each, and F2/F8 were closed *properly* — by deriving a value instead of
re-typing it, which is the only kind of closure that stays closed. Against that,
**F1 is thirteen days old with its fix written out in the ledger and still
unwritten**, and the test double is fourteen days into the same condition. The
pattern is legible now: findings that can be closed by editing the file under
discussion get closed; findings that require touching *test infrastructure* do
not. Today's diff is a clean instance of that same shape — see section 7.

---

## 1. First impressions

The live monitor is the best-reasoned new component I have read in this tree.
The C header comment is not decoration: it names the fan-out, states the two
safety properties as *properties* rather than intentions, and — remarkably —
**writes down its own unfixed hole** ("A crash still leaves the direct path
gone — that case needs a watchdog, and does not have one yet"). The
`live_monitor.py` docstring correctly refuses to compose a level of its own and
says why drift would be the hardest kind of wrong to hear. `WAIT_STOP` in
`CAPTURE_STATES` and `WAIT_START` out of it is a genuinely careful distinction
that most people get wrong. `atomic_is_lock_free` as a startup refusal, `exp()`
hoisted out of the RT callback into `on_samplerate`, clamp-to-1.0 as an
architectural property rather than a defensive `if` — this is someone who has
written audio software before.

And the `loop_mix` fix is a real fix for a real symptom, with the old law's
failure quantified in the code ("13 steps down took a loop from 127 to 36").

So the criticism below is not that this is sloppy. It is that **a component
that writes down its own unfixed hole and ships anyway has converted a bug into
a documented bug, which on a live instrument is the same bug.** Every hard
finding here is downstream of one decision: this client performs irreversible
surgery on the only fail-open audio path in the system, and nothing anywhere
checks whether the surgery worked.

## 2. Architecture & structure

The split is right and I would keep it. Policy in Python (`live_monitor.py`),
mechanism in C (`mpe-live-monitor.c`), and the C client holds no opinion about
level — told an amplitude, applies it. The UDP text protocol is the same shape
as `remote_fader.py`, deliberately, and the reuse of that precedent is correct.
Putting the gain stage on the monitor branch rather than upstream of the
fan-out is the right call and the AGENTS.md paragraph explaining it is the best
new prose in the diff.

The structural problem is **ownership of the graph**. Three things now write the
Surge→playback connection: `wire-jack-graph.sh:connect_graph()`, the C client's
`ensure_wiring()`/`restore_direct_path()`, and (by omission) whatever runs
neither. There is no single module that answers "does Surge reach playback right
now, and by which route." `sl-watchdog.py` looks like the obvious home and does
not know this component exists.

`run_bench` grows three more closures and is now the place where the monitor,
the mix, the sender and two threads meet — still with zero tests that construct
it (2026-09-07 F5, still open).

## 3. Single authority

### Q1 — "Is the live monitor on?"

**Four sites answer it, with three different laws.**

| Site | Law |
|---|---|
| `live_monitor.py:79` | `os.environ.get("MPE_LIVE_MONITOR","0").strip() not in ("","0","off","false")` — case-sensitive *blacklist* |
| `start-mpe-live-monitor.sh:16` | `case … 1\|true\|yes\|on\|TRUE\|YES\|ON` — an *allowlist* |
| `wire-jack-graph.sh:live_monitor_present()` | `jack_lsp \| grep -q '^mpe-live-monitor:'` — the graph, not the flag |
| systemd | `systemctl enable mpe-live-monitor`, recorded in neither list in `install-units.sh` |

They disagree today. Demonstrated:

```
MPE_LIVE_MONITOR=OFF   → python: ENABLED=True   shell: disabled
MPE_LIVE_MONITOR=no    → python: ENABLED=True   shell: disabled
MPE_LIVE_MONITOR=False → python: ENABLED=True   shell: disabled
MPE_LIVE_MONITOR=2     → python: ENABLED=True   shell: disabled
```

The dangerous direction (client running, Python not driving it) is *not*
reachable from this pair — I checked, the sets do not intersect that way — so
this is 🟡 not 🔴. The reachable failure is: the bench prints nothing, opens a
socket successfully, and fires datagrams into a port nobody is bound to, for the
whole session. **Owner:** `live_monitor.py` should export the predicate and the
shell should call `python3 -c` on it, or both should read one `mpe_flag()`
helper. Today there is no owner.

### Q2 — "What is the control port?"

`mpe-live-monitor.c:64` `CONTROL_PORT_DEFAULT 9957`; `live_monitor.py:72`
`DEFAULT_PORT = 9957`; `docs/PATHS.md` says `9957`. Three literals. The test
that claims to reconcile them (`test_the_control_port_matches_the_client`) reads
only the Python one — see F4. **Owner:** the test should grep the `.c`.

### Q3 — "What is Surge's JACK client name?"

`mpe-live-monitor.c:62` + `getenv("MPE_SL_SURGE_CLIENT")`;
`wire-jack-graph.sh:23`; `wire-sooperlooper-graph.sh:18`; and
`mpe-peak-meter.c:27` — which reads a **different env var**,
`MPE_PEAK_METER_SURGE_CLIENT`. So setting `MPE_SL_SURGE_CLIENT` re-points three
of the four and silently leaves the meter pointed at "Surge XT". That divergence
pre-dates this diff; this diff added the fourth site rather than fixing it.

### Q4 — "Does Surge reach playback?"

`wire-jack-graph.sh` answers it by *presence of a client*; the C client answers
it by `monitor_path_live()` (output leg only); `sl-watchdog.py:526` answers a
different question entirely (`common_out → playback`). Nobody answers the real
one. This is F1/F2 below.

## 4. Code quality

Naming is good throughout; `_scaled_level`, `capturing_loop`, `monitor_path_live`
all say what they do. Error handling in the C is the honest kind — every
`jack_connect` return is either checked or explicitly `(void)`-cast with a
reason. `parse_gain` rejects trailing junk and non-finite floats properly.

Three quality problems:

- **`_picked_up` is dead state.** `loop_mix.py:176` declares it, `:212` clears
  it, `:272` discards from it, `:342` adds to it. Nothing ever *reads* it. It
  is a second, unused answer to "has this fader picked up?", and the real answer
  is `fader in self._pickup_anchor` (`:339`). A reader will believe it matters.
- **`_accept`'s docstring is now wrong.** `:338` still says *"anchor on first
  touch, no jump; then delta applies."* There is no delta any more.
- **`push_monitor` computes `target_amp` twice** when logging
  (`sooperlooper-apc-bench.py:455, 459`). Cosmetic, but it runs per fader CC.

## 5. Code smells — the hall of shame

### 🔴 F1 — The graph guard keys on the client existing, not on audio arriving

```bash
# wire-jack-graph.sh
live_monitor_present() {
  jack_lsp 2>/dev/null | grep -q '^mpe-live-monitor:'
}
…
  if live_monitor_present; then
    log "live monitor on the bus — leaving Surge -> playback to it"
```

`jack_lsp` lists ports the instant `jack_port_register` returns — before
`ensure_wiring()` has connected anything. And the C side's own gate is no
stronger:

```c
/* Is our own output actually reaching playback? */
static int monitor_path_live(void)
{
    …  if (!port_connected_to(g_out_ports[ch], playback)) return 0;
```

It checks the **output** leg only. It never checks that `Surge XT:out_N` is
connected to `in_N`. So the reachable state is: the client's outputs are wired
to playback, its inputs are wired to nothing, `monitor_path_live()` returns
true, `detach_direct_path()` removes Surge→playback, and `wire-jack-graph.sh`
declines to put it back because the client is "present". **Result: total
silence, no error in any log, and every rewiring pass reaffirms it.** The
2 s `connect_thread` heals the common case (Surge came up late), but not a
wrong `MPE_SL_SURGE_CLIENT`, a renamed Surge client, or a Surge that never
starts — the exact cases where you most want the fail-open path.

**Fix:** make both gates test the *path*. C: require `Surge→in_N` **and**
`out_N→playback_N` on every channel before detaching. Shell:
`jack_lsp -c mpe-live-monitor:out_1 | grep -Fq system:playback_1` and the same
for the input leg, instead of `grep '^mpe-live-monitor:'`.

### 🔴 F2 — A crash leaves the instrument silent, and nothing notices

The header comment says it plainly: *"A crash still leaves the direct path gone
— that case needs a watchdog, and does not have one yet."* `docs/PATHS.md`
repeats it. Nothing implements it.

- `config/mpe-live-monitor.service` has **no `ExecStopPost`**. SIGTERM is handled
  in-process; SIGKILL, a segfault, an OOM kill, or `systemctl kill -s KILL` are
  not.
- `Restart=on-failure` + `RestartSec=3` means a crash is ≥3 s of silence
  mid-performance, and a crash *loop* that hits systemd's start limit is
  permanent silence.
- `sl-watchdog.py` watches `common_out → system:playback` (`:526`) and nothing
  else. **No health check anywhere in this repository asserts that Surge reaches
  playback.** That was survivable when the connection was made once and never
  removed. It is not survivable now that a process removes it on purpose.

**Fix, in order of cost:** (a) two `ExecStopPost=-/usr/bin/jack_connect "…"`
lines in the unit — covers everything but power loss, costs nothing; (b) a
`problems.append("neither Surge->playback nor mpe-live-monitor->playback")` arm
in `sl-watchdog.py` with the existing repair pattern.

### 🔴 F3 — Two instances would double the live signal

```c
g_client = jack_client_open(CLIENT_NAME, JackNoStartServer, NULL);
```

No `JackUseExactName`, no `jack_status_t` out-parameter. On a name collision
JACK does not fail — it *renames* the client to `mpe-live-monitor-01` and
returns success. A second instance (systemd restart racing a lingering process,
or someone running the binary by hand during a soak) then registers its own
ports, connects `Surge→in`, connects `out→playback`, and **both inserts feed
playback**. That is two copies of the live signal, one insert-latency apart —
the precise fault AGENTS.md's new paragraph says must never happen, and the same
fault `dump-loop-levels.py:45` documents as "heard as random volume swells".
Meanwhile the second instance's `bind()` fails, so it sits at unity forever: the
loud copy is the one nothing can turn down.

**Fix:** `jack_client_open(CLIENT_NAME, JackNoStartServer | JackUseExactName, &status)`
and exit non-zero on `JackNameNotUnique`.

### 🔴 F4 — Nothing executable checks any of the C client's safety properties

The header declares two safety properties. Neither has a test. Nor does
`clamp_gain`, nor `parse_gain`, nor the ramp, nor the detach ordering. There is
no C test target in the Makefile, `build-mpe-live-monitor.sh` is called from
nowhere but the start script, and the binary is gitignored — so CI never even
*compiles* it. A `-Wall -Wextra` regression would reach the Pi first.

And the one test that claims to span the boundary does not:

```python
def test_the_control_port_matches_the_client(self):
    """Both sides of the UDP protocol, one number. The C default is 9957."""
    self.assertEqual(live_monitor.DEFAULT_PORT, 9957)
```

It reads the Python constant and a literal. Change `CONTROL_PORT_DEFAULT` in the
`.c` and this passes, green, while the two sides no longer speak. The docstring
asserts the thing the code does not check — which is worse than no test, because
someone will read the name and believe the boundary is covered.

**Fix:** one test that greps `mpe-live-monitor.c` for
`#define CONTROL_PORT_DEFAULT (\d+)` and compares; a `make check`-style compile
in `ci_gate.py`; and a tiny harness that pipes datagrams at `parse_gain`/
`clamp_gain` (or extract them into a header and unit-test in C).

### 🟡 F5 — Four answers to "is the monitor on" (see §3 Q1)

### 🟡 F6 — Three answers to the port and the Surge client name (see §3 Q2/Q3)

### 🟡 F7 — The new fader law is position-dependent, and the docstring claims it converges

The docstring says *"the two positions converge as you play."* They do, but
slowly, and in the meantime **the same fader position produces a different level
on every pass.** Measured, wiggling one fader between raw 32 and 64:

```
raw 32 → level 64      raw 64 → level 85
raw 32 → level 42      raw 64 → level 71
raw 32 → level 35      raw 64 → level 66
```

Three full sweeps and the fader at 64 still means 66, not 64. And the route
matters as much as the destination:

```
anchor 64 → 100        level 127   (unity)
anchor 64 → 10 → 100   level 102
```

Same final position, 2 dB apart. For a player this means a fader has no
readable value: you cannot set it and know what you will get, and a slow
fade-out with a correction in it **ratchets the level downward**. That may be an
acceptable price for jump-free pickup — it is the classic tradeoff — but it is a
decision, and the docstring currently presents it as a non-issue.

**Handed to the user.** Options: (a) keep it, and fix the docstring to say "the
level is path-dependent until the fader has made a full sweep"; (b) snap to
absolute once `|raw - level| <= PICKUP_TOLERANCE_CC`, which is what every
hardware surface with pickup mode does and which makes the fader readable within
one pass; (c) LED/HUD feedback instead. **Recommend (b)** — the machinery
(`PICKUP_TOLERANCE_CC`) already exists and is used for exactly this comparison
in `seed_from_engine`.

### 🟡 F8 — "it never jumps" is false, by 15 dB

```c
 * A fader at 10 with its track at unity halves the level by 5 and silences it
 * at 0; it never jumps.
```

The halving claim checks out. "It never jumps" does not. Measured, with the
column near silence and one sparse CC on a fast upward drag:

```
fader 10 → 2    level 25   (-32.1 dB)
fader 2  → 60   level 73   (-17.0 dB)   ← one CC message, +15.1 dB
fader 60 → 127  level 127  (  0.0 dB)   ← one CC message, +17.0 dB
```

A physical fader dragged quickly emits sparse CC values; this is the normal
case, not a dropped-message edge case. The step is smoothed over
`FADER_SMOOTH_MS` (45 ms), so it is a fast swell rather than a click, and the
old law had a comparable worst case — so this is **not a regression**. But it is
a live claim in a file about audio safety on an appliance that may have
headphones on someone's head, and it is wrong. Either bound the per-message
level change, or delete the sentence.

### 🟡 F9 — The monitor's state is mutated from OSC threads with no lock

`sl_osc_session.py:87` uses `ThreadingOSCUDPServer` — a thread per datagram.
`on_state` → `LiveMonitor.note_state` → `self._capturing.append/remove`, and
`push_monitor` → `LiveMonitorSender.send` are now called from those threads
**and** from the main MIDI loop (`sooperlooper-apc-bench.py:736`). No lock
anywhere in the bench.

Two consequences. `note_state` does `if loop in self._capturing: … .remove(loop)`
— a classic check-then-act; a concurrent remove raises `ValueError` inside the
OSC handler thread. And `send()`'s dedup is a read-modify-write on `_last`: two
threads can both pass the tolerance check and reach `sendto` in either order, so
**the gain stage can land on the older of two levels and stay there** — audible,
sticky, and indistinguishable from a hardware fault.

This extends a pre-existing unguarded pattern (`on_wet` already mutates `mix`
from these threads), so I am not calling it 🔴. But the new writer is the one
whose stale value you can hear. **Fix:** one `threading.Lock` around
`note_state`/`target_amp`/`send`, or marshal state updates onto the main loop's
queue.

### 🟡 F10 — A capture that never closes pins the monitor forever

`_capturing` is only emptied by a non-capture state arriving for that loop. If
the engine restarts, the OSC subscription lapses, the loop is deleted, or a
`state` datagram is simply lost, the loop stays in the list and
`target_amp` returns that track's `wet` forever. If that track's fader is down,
you are **silent in the phones while playing**, the take is still recording
correctly, and the instrument reads as dead.

Worse, the recovery is not obviously available: the fader that fixes it is the
column fader for that loop, which is pickup-gated and may not be in the current
bank. There is no reset, no timeout, no "nothing has been capturing for N
seconds" floor.

**Fix:** treat a full `state` sweep with no capture state as authoritative
(SL streams state continuously, so a sweep arrives ~10×/s), or bound
`_capturing` entries with a last-seen timestamp.

### 🟡 F11 — The send path cannot report its own failure

```python
except OSError as exc:
    if exc.errno not in (errno.ECONNREFUSED, errno.EAGAIN, errno.EWOULDBLOCK):
        self.error = f"{exc}"
    return False
```

`ECONNREFUSED` is exactly what loopback returns when the gain stage is not
running — and it is swallowed without setting `.error` and without a counter.
The only WARN in the bench fires on `socket()` creation failure, which
essentially never happens. So "the monitor is working" and "every datagram has
been rejected for the last hour" produce identical output: nothing.

This is the repo's own named failure mode — AGENTS.md, *"the failure is
indistinguishable from the success"*, Rule −1. **Fix:** count consecutive
refusals and print one line on the first and every Nth.

### 🟡 F12 — The client is silent in the journal, and its RuntimeDirectory is unused

`mpe-live-monitor` prints only on startup errors. It never logs the gain it
applied, never logs that it detached the direct path, never logs restoring it.
`start-mpe-live-monitor.sh` exports `MPE_RUN_DIR` and the binary never reads it,
so unlike `mpe-peak-meter` there is no state file for `mpe diagnose` to read.

The 2026-09-07 ledger closed F7 ("fader path unobservable in the journal") by
adding `MPE_APC_FADER_LOG`, because a level regression on 09-06 left no trace.
This component is the same shape and arrived without the lesson.

**Fix:** one line on detach, one on restore, and rate-limited gain logging behind
`MPE_LIVE_MONITOR_LOG`.

### 🟡 F13 — `ensure_wiring` disconnects an already-absent connection every 2 s, forever

```c
    if (monitor_path_live()) {
        detach_direct_path();
    }
```

`detach_direct_path()` has no idempotence guard: every pass issues two
`jack_disconnect` calls for connections that were removed on the first pass,
forever, for the life of the process. Two futile JACK IPC round-trips every 2 s
(and jackd logs a failure for each). The helper to avoid it already exists —
`port_connected_to()` — and is used two functions up.

Cost is small (≈1 round-trip/s), but AGENTS.md is explicit that cadence × cost
belongs in the PR and this one is not stated anywhere.

### 🟡 F14 — Both paths are live during the handover, and a half-failed detach is not detected

`ensure_wiring` connects `out_1→playback_1` and `out_2→playback_2`, *then*
checks, *then* disconnects both direct legs. Between the first connect and the
last disconnect — two to four JACK IPC round-trips — Surge reaches playback by
both routes. That is the doubling AGENTS.md says must never happen; it is
milliseconds at startup, so 🟡 not 🔴, but it is a stated invariant that the
implementation violates by construction.

Separately, `detach_direct_path` ignores both `jack_disconnect` return values.
If channel 1 succeeds and channel 2 fails you get left-through-insert,
right-direct: a channel imbalance that moves when the monitor level moves.

### 🟡 F15 — The RT callback's NULL check leaves the output buffer unwritten

```c
for (jack_nframes_t i = 0; i < nframes; i++) {
    …
    for (int ch = 0; ch < CHANNELS; ch++) {
        if (in[ch] == NULL || out[ch] == NULL) { continue; }
        out[ch][i] = in[ch][i] * gain;
    }
}
```

`jack_port_get_buffer` does not return NULL for a registered port, so this is
dead defence — but if it ever did fire, the output buffer is left holding
whatever JACK last put there rather than silence. A defensive branch whose
failure mode is "emit stale audio" is worse than no branch. It also costs a
compare per sample per channel inside the hot loop for nothing.

**Fix:** hoist the check above the sample loop; on NULL, `memset` the output and
return.

### 🟡 F16 — New tests that cannot fail

Three of them, and the pattern is the 2026-09-07 F4/F6 finding recurring.

```python
def test_off_unless_asked_for(self):
    self.assertIn(live_monitor.ENABLED, (True, False))
```

This asserts that a boolean is a boolean. It cannot fail. Its name promises the
default-off behaviour, which is the actual safety property and is not checked
anywhere.

```python
def test_two_faders_move_independently(self):
    …
    self.assertEqual(mix.user_gain[col1], CC_MAX)
```

Fader 1 is driven *upward* from an anchor whose ref is already `CC_MAX`, so
`_scaled_level`'s upward branch computes `127 + 0 * …` — a no-op by arithmetic.
**This test passes unchanged if fader 1 is ignored entirely**, which is the
exact failure "independently" is supposed to catch.

```python
self.assertEqual(mix.user_gain[0], round(127 * 51 / 64))
```

That is `_scaled_level`'s downward branch transcribed into the assertion. It
pins the formula, not the behaviour — if the law is rewritten the test is
rewritten in the same commit, which is precisely the 09-07 F4 finding. The
behavioural claim in the docstring ("13 steps down from 64 took the level from
127 to 36") is the thing worth asserting: **assert the level is far from 36**,
or assert the invariant (`level/raw` is preserved under a downward drag).

### 🟢 Minor

- `install-units.sh:45` — "Do NOT add mpe-peak-meter or mpe-live-monitor here.
  **It** gates on MPE_PEAK_METER…". The pronoun no longer has a referent.
- `docs/PATHS.md` — the `MPE_LIVE_MONITOR_RAMP_MS` row is missing its closing
  `|`.
- `CHANNELS 2` is hardcoded while `docs/USB-MULTICHANNEL-STEMS.md` describes a
  wider future. Fine for now; worth a comment saying so.
- `loop_mix.py` uses `round()`, which is banker's rounding — `round(63.5)` is 64
  but `round(64.5)` is 64 too. Irrelevant at CC resolution, surprising in a diff.

## 6. Logic & business rules

The monitor's rule is stated in one sentence at the top of `live_monitor.py` and
the code is a direct transcription of it. That is the right way round and rare
here.

Two rules are buried rather than stated:

- **Recording into a silent track makes you deaf.** It follows correctly from
  the stated rule (`test_a_silent_column_monitors_silent` pins it), and it is
  DAW-correct — but combined with the new fader law, where the bottom of travel
  is *always* exact silence and reaching it is easy, the instrument now has a
  reachable state where playing produces no sound at all while everything is
  working. Nothing warns. **Handed to the user:** accept it, floor the monitor
  at some minimum while capturing, or log one line when the monitor target hits
  zero. Recommend the log line at minimum.
- **Settings read once:** `live_monitor.ENABLED` (`:79`), `resolve_live_cc()` via
  `field(default_factory=…)`, and the C client's `load_env()` are all captured at
  start. That is correct for this appliance (env changes require a restart), and
  I am flagging it only so the next reviewer does not re-derive it.

`push_monitor`'s docstring claims it is *"never on a timer"* because the idle
branch runs at ~485 Hz. True of the fader path. But `on_state` fires from SL's
continuous state stream — 15 loops × the stream rate — so the real cadence is
tens to hundreds of calls per second, each running `target_amp` and a dedup
compare. The cost is genuinely small; the *stated reason* is wrong, and AGENTS.md
asks for the number.

## 7. Test strategy & execution

2198 tests, all green, in 121 s. `test_live_monitor.py`'s first class is good
work: `test_waiting_to_start_is_not_yet_a_capture`,
`test_a_take_waiting_for_the_boundary_still_holds_it` and
`test_releasing_one_of_two_falls_back_to_the_other` are real behavioural claims
that would fail if the policy changed. `test_the_target_never_boosts` passing a
`lambda _loop: 4.0` is exactly the right instrument.

**Reading the doubles as instruments, per the skill's rule.** There is only one
double in the new code: `LiveMonitorSender(send=list.append)`. What it *computes*
is nothing; what it is *handed* is the fully-formed payload. So the suite
measures **the format of the datagram and the dedup decision** and nothing else.
It cannot express: whether anything is listening, whether datagrams arrive in
order, whether the C side parses what Python formats, whether the gain actually
changed. `test_a_dead_socket_is_not_an_exception` gestures at the first of these
but sends a single datagram to port 1 — on UDP the first `sendto` succeeds, so
the `ECONNREFUSED` branch (F11) is never entered.

**The axis the suite measures is "did Python decide the right number." The axis
the reported bugs travel on is "did the number reach the audio, and what happened
to the graph while it did."** Those do not intersect at any point. There is not
one test in this repository that exercises `mpe-live-monitor.c` — not a compile,
not a datagram, not a graph assertion. The existing `tests/engine/` harness runs
a real SooperLooper on a JACK dummy backend and is the obvious place to put a
real one: start the binary, assert the ports appear, assert the direct path
leaves, `kill -TERM`, assert it comes back.

Same shape on the fader side: the new `loop_mix` tests are all of one form
(drive N CCs, assert the arithmetic), so the suite has grown a memory for
arithmetic regressions and none for the class the bug actually belonged to —
*what a player experiences over a sequence of moves*. F7's ratcheting and F8's
15 dB step are both reachable in the existing test harness and neither is
asserted.

## 8. Security & performance

Nothing here would keep a security reviewer up. The control socket binds
`INADDR_LOOPBACK` explicitly (not `INADDR_ANY` — correct and deliberate), the
parser rejects anything that is not `gain <float>` with optional trailing
whitespace, and the gain is clamped to attenuation, so the worst a hostile or
buggy local process can do is make monitoring quiet. Worth noting in one line
somewhere that it is therefore **unauthenticated by design**: any local process
can silence the monitor, and per F12 nothing would log it.

No secrets, no hostnames, no addresses in the new files — I checked against the
public-repo banner. `.gitignore` correctly excludes the arm64 binary with the
reason written down.

Performance: the RT callback is a multiply and a one-pole per sample per channel
— negligible on a Pi, and `exp()` is correctly kept out of it. The 2 s connect
poll is cheap but undocumented (F13). `CPUAffinity=2 3` on the audio cores is the
right call and the comment justifying it is correct.

## 9. Documentation vs. reality

The AGENTS.md rewrite is the strongest part of the diff and mostly earns its
claims. Checked:

| Claim | Verdict |
|---|---|
| "four gain stages, and they are not all in series" | ✅ True, and the new *Reaches* column is the thing that was missing |
| "`Surge XT:out_N` goes to `system:playback_N` and to every `mpe-looper:loopM_in_N` from the same port" | ✅ `wire-jack-graph.sh:126-133`; `mpe-looper` is the default `MPE_SL_JACK_CLIENT` (`:22`) |
| "**both paths must never be connected at once**" | ⚠️ Stated as an invariant, violated by construction for the handover window — F14. Say "must not be left connected at once" or fix the ordering |
| "`MPE_LIVE_MONITOR=1` inserts `mpe-live-monitor`" | ⚠️ Half true. The C binary does not read that flag at all; `start-mpe-live-monitor.sh` does, with a different law than Python's — F5 |
| PATHS.md: "restores it on clean stop; a crash leaves it out (no watchdog yet)" | ✅ Accurate, and unusually honest. It is also the 🔴 — F2 |
| `live_monitor.py`: "the only thing that decides that amplitude" | ✅ True today. `clamp_gain` is a second (safety) opinion and correctly framed as a bound, not a policy |
| `loop_mix.py`: "the two positions converge as you play" | ⚠️ True but slow, and misleading about path-dependence — F7 |
| `loop_mix.py`: "it never jumps" | ❌ False — F8 |
| `loop_mix.py:338`: "then delta applies" | ❌ Stale from the old law |

A new dev could onboard on this component in an afternoon, which is more than I
can say for most of this tree. Build/deploy: `build-mpe-live-monitor.sh` mirrors
the peak-meter script correctly, `install-units.sh` handles the ghost-unit trap
with the reason written down, and the unit's `PartOf=mpe-jackd.service` +
`TimeoutStopSec=5` are right. The gap is CI: nothing compiles this.

---

## Verdict

This is good work with one structural hole in it. The design is right, the
reasoning is written down, the safety properties are real properties rather than
defensive `if`s, and the fader fix addresses a genuine symptom with the old law's
failure quantified in the source. What it does not have is any mechanism that
notices when it has broken the thing it took responsibility for. It removes the
only fail-open audio path on the instrument, it knows it removes it, it says in
its own header that a crash strands it — and then it ships with no
`ExecStopPost`, no watchdog arm, no journal line, no test, and a shell guard that
declines to restore the path based on a client merely *existing*. Every one of
those is a ten-line fix. On a device that may have headphones on someone's head
and no screen to tell them why it went quiet, doing three of them before this
reaches the Pi is not optional. The fader law is a smaller matter: it fixes a
real compounding bug, it is honestly documented apart from two sentences, and
its remaining rough edge (path-dependence) is a legitimate design choice that
should be *chosen* rather than inherited.

## Priority backlog (🔴 only)

1. **`ExecStopPost` in `mpe-live-monitor.service`** (F2). Two `jack_connect`
   lines, `-` prefixed. Covers SIGKILL and segfault, which is everything but
   power loss. Cheapest fix in this review by a wide margin — do it first.
2. **Make both graph guards test the path, not the client** (F1).
   `monitor_path_live()` must require the input leg; `live_monitor_present()`
   must be `jack_lsp -c`. Today "the client exists" and "you can hear yourself"
   are the same check and they are not the same thing.
3. **`JackUseExactName`** (F3). One flag; turns a silent doubled-signal fault
   into a refusal to start.
4. **A watchdog arm for Surge→playback** (F2). `sl-watchdog.py` already has the
   snapshot/problem/repair shape; add one condition: neither route reaches
   playback → problem, then repair. This is the thing the C header says is
   missing.
5. **Compile the C in CI and pin the port across the boundary** (F4). Add
   `build-mpe-live-monitor.sh` to `ci_gate.py`, and make
   `test_the_control_port_matches_the_client` actually read the `.c`.

## Finding ledger

| # | Finding | Severity | Fate | Enforced by | Fails today if it regresses? |
|---|---------|----------|------|-------------|------------------------------|
| F1 | `wire-jack-graph.sh:live_monitor_present()` and C `monitor_path_live()` both key on presence/output-leg, not on audio reaching playback; reachable permanent silence | 🔴 | Fixed now + Enforced | *to write* — a `tests/engine/` case that starts the binary with no Surge and asserts the direct path survives | **No** |
| F2 | A crash strands the graph: no `ExecStopPost`, no watchdog arm; `sl-watchdog.py:526` watches only `common_out` | 🔴 | Fixed now | *to write* — `config/mpe-live-monitor.service` `ExecStopPost`; `sl-watchdog.py` problem arm | **No** |
| F3 | `jack_client_open` without `JackUseExactName` → a second instance doubles the live signal into playback | 🔴 | Fixed now | *to write* — startup refusal is itself the enforcement | **No** |
| F4 | No executable check of any C safety property; CI never compiles it; `test_the_control_port_matches_the_client` reads only the Python constant | 🔴 | Enforced | *to write* — `ci_gate.py` compile step + a `.c`-grepping port test | **No** |
| F5 | Four answers to "is the live monitor on", three different laws; `MPE_LIVE_MONITOR=OFF` → Python on, shell off | 🟡 | Enforced | *to write* — one shared predicate + a test over the value table | **No** |
| F6 | Port `9957` and client name `"Surge XT"` each duplicated across C and shell; `mpe-peak-meter.c` reads a different env var | 🟡 | Enforced | *to write* — cross-file constant test | **No** |
| F7 | Fader law is path-dependent; same position gives 85/71/66 on successive passes; docstring says it converges | 🟡 | Handed to user | — | **No** |
| F8 | `loop_mix.py:350` "it never jumps" — measured +15.1 dB in one CC | 🟡 | Fixed now (docstring) or Enforced (bound the step) | *to write* — `tests/test_loop_mix.py` max-step-per-CC assertion | **No** |
| F9 | `LiveMonitor._capturing` / `LiveMonitorSender._last` mutated from `ThreadingOSCUDPServer` threads and the main loop, no lock | 🟡 | Fixed now | *to write* — a `threading.Lock` | **No** |
| F10 | A capture state that never closes pins the monitor forever; silent-while-playing with no reset | 🟡 | Handed to user | — | **No** |
| F11 | `ECONNREFUSED` swallowed without setting `.error`; "working" and "every datagram rejected" look identical | 🟡 | Fixed now | *to write* — a test that asserts a refusal counter increments | **No** |
| F12 | Client logs nothing after startup — no gain, no detach, no restore; `MPE_RUN_DIR` exported and unused | 🟡 | Fixed now | — | **No** |
| F13 | `detach_direct_path()` re-issues `jack_disconnect` on absent connections every 2 s forever; cadence×cost unstated | 🟡 | Fixed now | — | **No** |
| F14 | Both paths live during handover (contradicts the AGENTS.md invariant); `jack_disconnect` return values ignored → channel imbalance reachable | 🟡 | Fixed now + doc amendment | — | **No** |
| F15 | RT NULL check sits inside the per-sample loop and leaves the output buffer unwritten on NULL | 🟡 | Fixed now | — | **No** |
| F16 | Three new tests that cannot fail: `test_off_unless_asked_for` (bool is bool), `test_two_faders_move_independently` (fader 1 path is a no-op by arithmetic), `test_a_drag_does_not_compound` (asserts the implementation formula) | 🟡 | Fixed now | `tests/test_live_monitor.py`, `tests/test_loop_mix.py` | **No** |
| F17 | `loop_mix._picked_up` is written in four places and read in none — a dead second answer to "has this fader picked up?" | 🟡 | Deleted | — | **No** |
| F18 | `_accept` docstring says "then delta applies"; there is no delta | 🟡 | Fixed now | — | **No** |

*Eighteen findings — four 🔴, fourteen 🟡. Every row is checkable against the
tree as of this file's date; where I could run the check I ran it and quoted the
numbers. **"Fails today if it regresses?" is "No" on all eighteen** — that is not
eighteen coincidences, it is the finding: this component has no executable
surface at all. Next reviewer: this table is your Step 0, along with the three
rows still open from 2026-09-07 (F1 timing totality, F6 weakened assertion, and
the fake engine's discarded `/set`). Mark each row rather than re-deriving it,
and leave one behind.*
