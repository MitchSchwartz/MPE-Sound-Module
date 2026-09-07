# Grumpy review — why the looper keeps re-breaking

**2026-09-07, 00:30.** Scope: the looper subsystem of MPE-Module — `scripts/sooperlooper/`
(14,672 lines across 30 modules), `scripts/sooperlooper-apc-bench.py`, `scripts/looper-session.py`,
their tests, CI, the deploy path, and the 46 review documents already in `Documents/reviews/`.
Read in full: `fake_sl_engine.py`, `sl_grid_state.py`, `sl_grid_sync.py`, `track_gesture.py`
(stop-all, grid, tail), `loop_mix.py` (fader path), `slot_runtime.py` (record/clear paths),
`test.yml`, `looper-deploy.sh`, `bootstrap-pi5-looper.sh`. Sampled: `slot_surface.py`,
`control_registry.py`, `device_facts.py`, `apc_transport.py`, the bench, and the prior
test-integrity review. Not read: `looper_songs.py`, `sl-watchdog.py`, the LED modules, the
spikes.

The brief, in Mitch's words tonight: *"I refuse to accept a tiny answer of this code just needs
to be fixed. I want to know how the fuck we are going to continue working on the looper without
constantly re-breaking the looper."* And: *"adding more tests probably isn't the answer."*

He is right on both counts, and the second one is provable.

---

## 1. First impressions (the gut check)

This does not look like a hackathon. It looks like something rarer and in some ways worse: a
codebase being maintained with enormous care by an author who cannot run the thing it controls.

The care is visible everywhere. `device_facts.py` tiers every hardware claim by how it was
established and refuses to let a vendor PDF call something impossible. `control_registry.py` has
a test that walks the AST of every APC module so no note number can be typed twice. `sl_limits.py`
is genuinely the standard the charter says it is. `verify_stop_all` and `measure-loop-alignment.py`
are real instruments with honest docstrings. `DECISIONS.md` exists and is mostly telling the truth.
`.gitleaks.toml`, shellcheck in CI, a public-repo banner at the top of `AGENTS.md`. These are the
habits of people who have been burned and learned.

And yet: **215 commits touched the looper in the last 30 days. 116 of them — 54% — say fix,
revert, invert, correct, supersede or wrong in the subject line.** The bench file alone took 79
commits. Forty-six review documents were written in 24 days, eleven of them on a single day, twice.
The last one before tonight, `grumpy-review-test-integrity-2026-08-30.md`, is 671 lines long and
its verdict paragraph already says what this review is about to say. Nothing in git suggests it was
acted on.

So the gut check is: professionals work here, and they are on a treadmill. The question is what
the treadmill is made of.

## 2. Architecture & structure

**The shape is right and the seams are in the wrong place.** The looper is SooperLooper (a C++
engine, driven over OSC) plus a Python control layer that owns the pads, LEDs, grid lifecycle,
slot matrix and faders. That split is correct. What is wrong is where the *knowledge about the
engine* lives.

Every fact about how SooperLooper behaves — that `pause` is a toggle, that `trigger` lifts a
pause, that `load_loop` needs three arguments or is silently dropped, that `smart_eighths`
doubles the cycle under 60 BPM, that a muted empty loop reports state 20 — was learned on the
appliance with Mitch present, then written into a *comment* next to the code that depends on it.
There are 59 lines saying `MEASURED`, 131 dated fact lines, 38 lines quoting Mitch, and 69
saying `used to`, across the looper modules. That is not documentation. That is the engine's
behaviour specification, stored as prose, in the one place a test cannot read it.

**`run_bench` is an 848-line function containing 34 nested closures** (`sooperlooper-apc-bench.py:90-938`).
Every handler — pad, scene, fader, transport, stop-all verify, slot surface, LED compositor,
grid callbacks — is a closure over the same bag of locals. `tests/test_apc_bench.py` has 13
tests and none of them call `run_bench`. The one place all the modules actually meet is the one
place nothing can be tested.

**Engine state is cached in three modules** — `track_gesture.py` (20 references to `sl_state`),
`slot_surface.py` (11, in `_sl_states`), `looper_songs.py` (3). `slot_surface.py:211-218` says
so itself: *"two sources for one fact is how the paths drifted apart in every other respect."*
It then keeps both.

**Two boot images, one deploy path, no record of which is which.** Tonight the Pi was on SD
(`/dev/mmcblk0p2`) running `46aafbb` from 2026-09-03 with `mpe-sooperlooper` and
`mpe-looper-session` both `disabled`. Nothing changed today reached it. The volume regression
Mitch hit last night is therefore in code at least four days old — which nobody could have
known without `findmnt` and `git log` on the box.

Dependencies are fine. `python-osc`, `numpy`, `rtmidi`. No complaints.

## 3. Code quality

**Naming is good.** `settle_stop_all`, `note_loop_content`, `_holds_audio`, `derive_tempo`,
`grid_silent_reason` — you can read the call sites. Credit where due.

**The comments are the problem, and not because they are bad.** They are excellent. They are
also the changelog, the incident log, the hardware spec and the decision record, all at once,
interleaved with the logic. `track_gesture.py` is 1,332 lines of which roughly 300 are comment
or docstring, and the ratio is *worse* in the parts that matter: `stop_all_loops` is 22 lines
of sends wrapped in 45 lines of history. A reader who wants to know what Stop All *does* has to
read what it *used to do* on 08-19, 08-30 and 09-06 first.

DECISIONS.md has 25 dated entries. The code has 131 dated facts. The decision log is the
summary; the source is the record. That is backwards, and it has a concrete cost: the grid
policy flipped on 2026-08-30 (`b4446a3`) and DECISIONS.md was never updated. For a week the
log said "No clips, no grid" while the code did the opposite. The log was right.

**Dead code is kept on purpose and labelled.** `apc_panel.scene_press_row` — *"Superseded
2026-08-30 and deliberately kept."* `slot_matrix_spike.py` and `spike-load-halt.py` live in
the production tree. `track_gesture.py:329` records a method *deleted* because it had zero
callers, as a comment where the method was. This is the same instinct as the comments: nothing
is allowed to be forgotten, so nothing is allowed to be removed.

**Error handling is honest.** `try_jack` in `wire-jack-graph.sh` is a small masterpiece of
"do not abort under set -e" scar tissue, and it says so. `_close_inputs` in the router
documents an ALSA client leak by measured count. Nothing swallows exceptions silently that I
found.

**DRY:** `resolve_apc_transport_notes`, `resolve_arrow_notes` and `resolve_fader_ccs` are the
same eleven lines three times, and `apc_faders.py:59` says *"mirrors
resolve_apc_transport_notes() deliberately."* Deliberate duplication is still duplication.

## 4. Code smells (the hall of shame)

### 🔴 The test double throws away every control the looper is built on

`tests/fake_sl_engine.py:58-67`:

```python
def send_message(self, path: str, arg) -> None:
    self.sent.append((path, arg))
    parts = path.strip("/").split("/")
    if len(parts) != 3 or parts[0] != "sl":
        return  # /set and global paths do not move loop state here
    if parts[2] in ("load_loop", "save_loop"):
        self._buffer_op(int(parts[1]), parts[2], arg)
        return
    if parts[2] != "hit":
        return
```

Production sends thirteen distinct `/set` controls: `mute_quantized` (7 sites), `quantize` (6),
`wet` (3), `tempo` (3), `sync_source` (3), `sync`, `smart_eighths`, `round`, `relative_sync`,
`playback_sync`, `input_latency`, `fade_samples`, `eighth_per_cycle`. **The fake models none of
them.** It handles all eight `hit` verbs the code sends, and zero of the thirteen settings.

Quantize, sync and tempo *are* the grid. `wet` *is* the volume. Every bug Mitch reported tonight
— seven bars, the clear that didn't clear, Stop All resuming, volume breaking a track — lives in
a control that this line discards. The suite is not porous. It is looking at a different axis.

**Fix:** either the double models the controls that matter (quantize deferral, sync, tempo →
cycle length, wet), validated against the real engine — or it is retired in favour of the real
engine. See §6.

### 🔴 Loop length is an argument, not a result

`tests/fake_sl_engine.py:176-181`:

```python
def _finish_record(self, loop: int, length: float = 2.0) -> None:
    self.state[loop] = SL_STATE_PLAYING
    self.loop_len[loop] = length

def boundary(self, *, length: float = 2.0) -> None:
```

Twenty-one `boundary()` calls in the suite. Five pass a `length`; all five are constants the
test author chose. Zero derive a length from elapsed cycles. "Recorded seven cycles when it
should have been four" is not an uncaught assertion in this suite. It is an unexpressible one.

**Fix:** the fake gets a clock. `boundary()` increments a cycle counter; a take's length is
`cycles_elapsed × cycle_len`, computed. Every `boundary(length=...)` call site then has to
justify itself.

### 🔴 Engine semantics by send-order guess

`scripts/sooperlooper/track_gesture.py`, `stop_all_loops`, as it stood until tonight:

```python
osc.send_message("/sl/-1/set", ["mute_quantized", 0.0])
osc.send_message("/sl/-1/set", ["quantize", 0.0])
osc.send_message("/sl/-1/hit", "mute_on")
osc.send_message("/sl/-1/hit", "trigger")
osc.send_message("/sl/-1/hit", "pause_on")
osc.send_message("/sl/-1/set", ["quantize", 1.0 if grid_active else 0.0])
osc.send_message("/sl/-1/set", ["mute_quantized", 1.0])
```

Surrounded by 45 lines explaining that `trigger` rewinds, that `trigger` lifts a pause
(measured 08-30), that a quantized trigger is deferred, and that *"SL drains its non-realtime
queue in order, so the restore cannot overtake the mute."* That last clause is the load-bearing
one and it is asserted, not measured. If `set` is applied on the OSC thread while `hit` is
queued for the audio thread, the restore *can* overtake the trigger, the trigger is deferred,
and it fires after the pause — which is the symptom Mitch reported at 23:58 and the instrument
caught at 23:59:05 (`1 loop(s) did NOT stop -- loop 0 state=4`, with no intermediate state
change logged).

I moved the restore a second later tonight. **I also do not know that this is the mechanism.**
I wrote that into the source. The point of this entry is not the bug; it is that a seven-line
OSC sequence whose correctness depends on the engine's thread model has been edited by four
commits, has a paragraph of reasoning attached, and has never once been executed against the
engine in a test.

**Fix:** §6. A real-engine test that sends this sequence and asserts `state == PAUSED` after
one cycle, a hundred times, is a twenty-line test and would have settled the thread-model
question on the first run.

### 🔴 Policy is pinned by tests that get inverted

`tests/test_sl_grid_state.py`, the grid-drop class, in its three lives:

- 2026-08-15: `test_clearing_last_clip_drops_grid` — asserts the drop.
- 2026-08-30: renamed `GridSurvivesEmptyPadsTests`, *"Four tests inverted rather than deleted —
  they pinned the old policy honestly and now pin the new one."*
- 2026-09-06 (me): inverted back, with both quotes kept.

`b4446a3` reported *"Three of the four new repro tests verified red against the pre-fix code."*
That is true and it is worthless: a policy test goes red against the opposite policy by
definition. The test-integrity review said this on 08-30: *"47 commits since 2026-08-20 changed
a looper production module and its tests in the same commit... The suite is therefore a
near-complete record of what each session decided to write, and a very thin source of
independent evidence."* Since that review: 8 looper commits, 7 of them also edited `tests/`.

**Fix:** a behaviour rule lives in one table in DECISIONS.md with the engine-backed test that
proves it. A test may be *replaced* by a spec change plus a new test. It may not be "inverted".

### 🟡 `run_bench` — 848 lines, 34 closures, 0 tests

`scripts/sooperlooper-apc-bench.py:90-938`. The fader handler, the stop-all verify, the grid
callbacks, the slot surface wiring and the LED compositor all share one scope. `test_apc_bench.py`
tests the helpers around it. Nothing instantiates the thing that runs on the Pi.

**Fix:** extract the handlers into a `BenchSession` object with explicit dependencies, so a test
can construct one over a fake OSC and a recorded MIDI stream and drive it. The closures are
already shaped like methods.

### 🟡 `loop_len` is a latch that cannot say "empty"

`scripts/sooperlooper/slot_surface.py:427-429`:

```python
def on_loop_len(self, track: int, loop_len: float) -> None:
    if loop_len > 0:
        self._loop_lens[track] = float(loop_len)
```

The engine's own answer to "is there audio in this buffer" is discarded whenever it says no.
Tonight I wanted to use `loop_len > 0` as the occupancy predicate for the grid rule and
couldn't, because the one consumer of the signal already distrusts half of it. Nobody wrote
down why.

**Fix:** find out whether the engine sends spurious zeros (a real-engine test would tell you in
a minute) and either trust the signal or document the exact case it lies in.

### 🟡 The fader path: a feedback loop with a heuristic, unobservable, tested by shape

`scripts/sooperlooper/loop_mix.py:225-262` — the wet-echo adopter. The bench sends `wet`; the
engine echoes `wet`; the adopter decides whether the echo is ours (via `echo_probe` → the
sender's emitted-history ring, tolerance `1e-4`) or foreign, and if foreign it *rewrites the
user's fader position* from the engine value. The docstring records this drifting levels
`0.9959 → 0.9604 → 0.9262 → 0.8604` over four cycles on 08-31 before the "a ramp is an echo
too" patch. That is a control loop with a classifier in it, and its failure mode is exactly
"I touched a fader and the level went somewhere and stayed there."

Around it: 12 commits in 30 days, 9 of them on 2026-08-16 alone, all `fix(looper)`.
`test_loop_mix.py` has 35 tests; **zero** mention a loop state, `FakeSlEngine`, or whether a
track still plays after a move — they assert lists of `("/sl/N/set", [...])` tuples.
`note_active_loops` has zero callers, so `auto_law` is dead code guarding a multiply.
`faders.seed_current(path, mix.wet_for(loop))` in the bench seeds the ramp "from engine truth"
with the *model's* value. And the bench logs nothing on a fader move: **three hours of tonight's
journal contain zero fader lines.** The regression Mitch reported at midnight left no trace
anywhere by construction.

**Fix:** log every fader emit and every adoption at INFO with the loop, the value sent, the
echo received and the decision. Then one real-engine test: move fader, wait, assert state is
still PLAYING and `wet` is what was sent.

### 🟡 An instrument that flattered itself

`track_gesture.py`, `verify_stop_all`, until tonight: `STOPPED_STATES` contains `OFF` and
`OFF_MUTED`, so fourteen empty pads can never fail the check. *"all 15 loops stopped"* was
*"loop 0 stopped"* on every single-clip session since 08-30. Fixed tonight to say
`all 1 loop(s) with audio stopped (14 empty pad(s) not checked)`. Listed here because it is the
shape, not the instance: an instrument built to distinguish two outcomes, whose scope was never
checked against the fixture it runs on.

### 🟢 Minor

- `git log` subject `Dev (#122)` for a PR merge into dev. Say what it was.
- `apc_panel.scene_press_row` kept "deliberately" after being superseded. Delete it; git
  remembers.
- Three copies of the variant-resolution ladder (`resolve_apc_transport_notes`,
  `resolve_arrow_notes`, `resolve_fader_ccs`).
- `slot_matrix_spike.py` and `spike-load-halt.py` in `scripts/sooperlooper/`. Move to
  `scripts/research/` where the others live.

## 5. Logic & business rules

**The rules are expressed clearly and then contradicted by the code, on a schedule.** The grid
lifecycle has been: no-clips-no-grid (08-15) → grid-outlives-clips (08-30) → stop-keeps /
clear-drops (09-06). Each version was stated in a docstring with a quote from Mitch. Two of the
three were built from a sentence about a *different gesture*. The rule Mitch actually holds —
"no clips playing, grid remains; all clips cleared or deleted, grid is deleted" — is one line
and was never written anywhere until tonight.

**The state machine is predictable in each module and unpredictable in aggregate.** `TrackGesture`
holds `sl_state` + `_pending` and derives `state`. `SlotSurface` holds `_sl_states`. `GridState`
holds `_occupied`, `_pending`, `established`. `SlotRuntime` holds per-track active slots and
deferred launches. Each is internally coherent. The bugs — every one tonight — were in the
seams: a slot re-record passing through `OFF` looks like a clear to the grid; a Stop All that
mutes globally makes empty pads report a state the grid rule didn't know about; a fader echo
looks foreign to the mix. No test composes two of these modules against a moving engine.

**Race conditions are documented rather than removed.** `midi_subscription.py` exists because
systemd restarts race rtmidi's `open_port`. `restart-looper-session.sh` exists because
`systemctl restart` races the ALSA subscription. `stop_all_loops` races its own restore. The
pattern is: find the race on the Pi, add a settle, write a paragraph. Fine for the systemd
ones. Not fine for the OSC one — that race is inside a process we own and can test.

**The wildcard and the loop are both used for "all loops."** `stop_all_loops` and
`looper_songs.stop_playback` send `/sl/-1/hit`; `reset_all_loops`, `clear_all_loops`, and
`slot_runtime` iterate `range(num_loops)`. One of these conventions is the intermittent one.

## 6. Test strategy & execution

This is the section that answers the question.

**What the 2,036 tests test.** 23 test files drive production code with `MagicMock` OSC
clients — they assert which messages were sent. 4 use `FakeSlEngine` — they assert state
transitions against a hand-written model. 10 spawn subprocesses, all of them shell scripts or
audio-device detection. **Zero tests run SooperLooper.** CI (`.github/workflows/test.yml`)
installs `python-osc` and `numpy` and runs `unittest discover`; `sooperlooper`, `jackd` and
`oscsend` are not installed on the laptop, and the Pi's engine is a hand-built 1.7.9 from
`~/src` with a liblo patch (`bootstrap-pi5-looper.sh:7,71`).

**Why adding tests cannot help.** A test needs an oracle. Here the oracle for "what does the
engine do when I send this" is either a `MagicMock` (agrees with anything) or `FakeSlEngine`
(agrees with whatever the author believed when they wrote it, and discards every `/set`). Both
oracles are written by the same author as the code, in the same commit — the test-integrity
review measured 47 of 47 commits doing so over ten days; I measured 7 of 8 since. A test whose
oracle is the author's belief can detect a typo. It cannot detect that the belief is wrong,
because the belief is what it checks against.

Every one of the 59 `MEASURED` facts in the comments is a moment when the belief was found
wrong on the appliance. Each was then encoded into the fake or the code by hand, from memory
of what the Pi did. The fake is a *mirror* — its own docstring in `test_fake_engine_fidelity.py`
uses that word — and it has been polished to reflect exactly the bugs that have already
happened. It cannot reflect the next one. That is what "whack-a-mole" means mechanically.

**What actually catches the bugs.** Reading the journal. Every diagnosis tonight — the 7.59 s
take that was seven cycles of a 1.084 s bar, the missing `grid dropped` line, the missing
`sl=14` line, the fourteen `state 20` reports at Stop All — came from `journalctl` on the
appliance, not from a test. The instruments that exist (`verify_stop_all`, `measure-loop-alignment.py`,
`smoke-16-loops.sh`, `sl-health.py`) are the only things that have ever seen a real regression,
and none of them run automatically: `measure-loop-alignment.py` appears nowhere in CI,
`smoke-16-loops.sh` is not called by `looper-deploy.sh`, and `verify_stop_all` only *logged*
until tonight.

**What a test that could catch this looks like.** The real engine, in a container:

```
jackd -d dummy -r 48000 -p 256 &
sooperlooper -l 15 -p 9951 -D -t 40 &          # the appliance's flags
```

driven by the real `SlOscSession`, with assertions on `state`, `loop_len` and `loop_pos`
*over time*. First suite: the 59 measured facts, as tests. `pause` is a toggle. `trigger`
lifts a pause. `load_loop` with one arg is dropped. Empty loop after `mute_on` reports 20.
Record for N boundaries yields `loop_len == N × cycle`. Stop All sequence → `PAUSED` within
one cycle, a hundred times. Fader move → `wet` echo equals sent, state unchanged. None of these
is more than twenty lines. All of them are currently prose.

Cost: SooperLooper 1.7.9 builds from source on Debian bookworm (the Pi is bookworm arm64; the
laptop has Docker 29). `jackd2` and `liblo-tools` are packaged. Call it a day to get the
container and the first ten tests. The appliance has consumed 215 commits, 46 review documents,
and roughly every one of Mitch's evenings for three weeks on the alternative.

**And `FakeSlEngine`?** Keep it only if it can be *validated*: record the real engine's
responses to the bench's message log and diff the fake against the recording. A fake that
cannot be checked against the thing it fakes is a second implementation of the bug.

**The good tests, to be fair.** `test_slot_runtime.py`, `test_multiclip_workflow.py`,
`test_apc_link.py`, `test_looper_health.py`, `test_fake_engine_fidelity.py`'s *idea* — these
are adversarial, cite incidents, and would catch a regression of the bug they name. The
control-registry AST walk. `test_periodic_loop_lint.py`. These are worth keeping regardless of
what happens to the double.

## 7. Security & performance

Nothing that would keep a security reviewer up. The public-repo discipline is real: `gitleaks`
config, redaction commits (`7d5fd0b`, `0ffa90a`), a banner in `AGENTS.md`, hardware and host
addresses kept out. The Pi is declared a read-only deploy target and the deploy scripts respect
it. SSH keys are per-host. The Supabase default password is out of scope here and is a recorded
decision elsewhere.

Performance doctrine is unusually good for a Python control plane: the fork-cost rule in
`AGENTS.md`, `periodic_loop_lint.py`, the measured `~16.5 µs / iteration` note in
`poll_transport_leds`. The ~485 Hz idle loop is the right shape.

Operational, not code, and it matters more than it looks: **the appliance's journal was
volatile.** After this morning's reboot `/var/log/journal` was a 4 KB empty directory and
`journalctl --list-boots` showed one boot — last night's entire looper session, the Stop All
failure at 23:59:05, the fader regression window, and both screen crashes are gone. The only
instrument that has ever caught a looper regression was being erased on every reboot. Enabled
`Storage=persistent` (200 MB cap) this morning and verified it writes. Also: `raspberrypi5.local`
timed out three times in twelve hours (mDNS returning link-local v6); the SD image's host key
differs from the entry `known_hosts:35` holds for that IP (two images, one address); and the
looper units are `disabled` on the SD image, so a reboot does not bring the looper back. Mitch
reports the screen has crashed twice since yesterday — new behaviour, and now there will be a
record of the next one.

## 8. Developer experience

**A new developer cannot run the product.** Not the looper, not the engine, not the bench
against anything real. They can run 2,036 tests that will pass, read 23 specs and 46 reviews,
and then discover that the truth is in comments in `track_gesture.py` and in `journalctl` on a
box they cannot reach. Onboarding is not a day. It is a week of archaeology, and the
archaeology is *good* — every scar is labelled — which is what makes it seductive and slow.

**The docs are honest and scattered.** `DECISIONS.md`, the specs directory, `AGENTS.md`, and
the reviews all tell the truth as of when they were written. They disagree with each other on
timing because nothing updates them together; the grid rule proved that. The `mpe` CLI is a
real convenience. `docs/GIT-WORKFLOW.md` and the Pi-as-read-only rule are clear.

**The build/deploy pipeline has no gate that exercises the looper.** `looper-deploy.sh` →
`bootstrap-pi5-looper.sh` → `sl-health.py` (a round-trip `set`) → restart. No smoke, no
Stop All verify, no "record one loop and check its length." `smoke-16-loops.sh` exists, loads
16 clips, triggers, measures load, pauses, prints PASS — and nothing calls it.

**The review process is running hot and converting cold.** Eleven review documents on 08-22,
eleven on 08-30. The test-integrity review's verdict from a week ago is the verdict here. The
charter's *"tests are evidence, not intention"* section is correct and has not changed what a
commit looks like. Reviews that do not change the next commit's shape are a cost, not a
control.

**One author.** Every one of the 136 co-author trailers on looper commits in 30 days is the
same agent. Sessions do not share memory except through the repo, so every session re-derives
the engine's behaviour from the comments the last one left — and adds its own. That is the
mechanism by which the code became the changelog. It is also why "the process" here means "what
each session is instructed to do before it may touch the looper."

---

## The good, the bad, and what smells

**Good.** `device_facts.py` and its tiers. The control-registry AST walk. `sl_limits.py`.
`verify_stop_all` as an instrument. `measure-loop-alignment.py` and its docstring. The honesty
of the comments, individually. Public-repo hygiene. The fork-cost doctrine. `try_jack`.
The *idea* of `test_fake_engine_fidelity.py`. Naming, throughout.

**Bad.** The test double discards every `/set` control and takes loop length as an argument,
so the suite is structurally blind to the axis every looper bug lives on. No test has ever run
SooperLooper. Tests and code are written together, so the oracle is the author. Policy tests get
inverted. The engine's behaviour spec lives in 131 dated comments and lags into DECISIONS.md by
a week. `run_bench` is untestable by construction. Deploy has no looper gate. The fader path is
a feedback loop nobody can see in the journal.

**Smells.** Three caches of engine state. `loop_len` distrusted without a reason on file. Dead
code kept "deliberately." Spikes in the production tree. Two "all loops" conventions. Three
copies of the variant ladder. `Dev (#122)`.

---

## Verdict

The looper keeps re-breaking because the process for changing it is: observe a symptom on the
appliance with Mitch present → infer what SooperLooper did → write the inference into the code,
into a comment beside it, and into a test against a double that cannot see the inference → ship
→ repeat. The suite is 2,036 assertions about a *model* of the engine, written by the code's
author in the code's commit, and the model discards the thirteen controls the looper's
behaviour is made of. Regressions do not slip past the tests. They travel on a dimension the
tests do not have. Mitch is right that more of these tests cannot help, and right that the
problem is the model, not the count. I followed the same process tonight — 318 lines, four new
guards, three inverted tests, sixty lines of history in comments — and it produced fixes I
cannot prove. The way to keep working on the looper without re-breaking it is not a better
double. It is to stop guessing what the engine does: run the real one, in a container, in CI,
and make every "MEASURED" sentence in the code a test with a name — then gate every deploy on
the instruments that already exist and are never run.

## Priority backlog (🔴 only)

1. **Real-engine harness.** SooperLooper 1.7.9 + `jackd -d dummy` in a Debian bookworm
   container; a CI job; the real `SlOscSession` driving it. First ten tests are the ten most
   expensive `MEASURED` facts, executed. This is the one change that alters what a regression
   *is*. Everything else is downstream.
   *Done the same day:* `tests/engine/` (Debian trixie, like the Pi, with the liblo 0.32
   patch), the `engine` CI job, ten claims executed and green, and the production grid code
   (`GridState.establish`, `apply_established_grid`, `stop_all_loops`) driving the engine
   directly. First contact already corrected one comment: a `record` hit while WAIT_START is
   ignored by the engine, not a cancel (`test_gesture_against_engine.py` said cancel).
2. **No engine claim without an executable.** A comment saying `MEASURED` or `used to` about
   SooperLooper must name the test that proves it, or it is a hypothesis and says so. Enforce it
   the way `control_registry` enforces note numbers — a test that greps.
3. **Deploy gate on the appliance.** `looper-deploy.sh` runs, before and after restart:
   `sl-health`, `smoke-16-loops`, one record-one-cycle-check-length, one Stop All + verify, one
   fader move + still-playing. Red blocks the deploy. These scripts mostly exist.
   *Partly done the same day:* the gate that exists is on **CI**, not on the appliance —
   `scripts/ci_gate.py` refuses any commit without a green `unittest` + `engine` + `shell-tests`
   run, from `looper-deploy.sh` after the reset and from `mpe looper deploy` before it
   (mpe-cli branch `feat/deploy-ci-gate`, unmerged). The on-appliance smoke run listed here
   is still to do.
4. **Make the record survive.** Persistent journal on every image (done on SD this morning;
   put it in `bootstrap-pi5-looper.sh` so it cannot be forgotten), and log every fader emit and
   every echo adoption with values and the decision. Last night's regression was invisible in
   three hours of journal and the journal itself was then erased by a reboot; between them that
   forbids diagnosing anything.
   *Done the same day:* persistent journal in `bootstrap-pi5-looper.sh`; every fader move and
   every echo adoption logged by the bench (`MPE_APC_FADER_LOG=0` to silence);
   `LoopMix.seed_from_engine` returns its verdict; one real-engine test on `wet`.
5. **One rule, one table, one test.** Grid lifecycle and Stop All each get a table in
   DECISIONS.md (gesture → engine state → outcome) and an engine-backed test that pins it. Tests
   are never inverted again; a policy change is a spec edit plus a new test, and the old one is
   deleted.

Not on the list, deliberately: the volume bug. It should be found by item 4 and fixed under
items 1 and 3, or it will be fixed the way the last 116 were.
