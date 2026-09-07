# Why the looper bugs recur, and why 2031 tests never see them

**2026-09-06.** Mitch, after a session on the SD boot: a first take recorded as
seven bars ("it should never be an odd number"), and after clearing it, a new
first clip started late because it was still quantizing — the grid had not
dropped. His question was not "fix it". It was: *is the behaviour undocumented,
are we not checking it, or what?*

This is the answer, with what was actually read.

---

## The short version

The documentation is not the gap. It is the best part of this subsystem.

The gap is that **the test double makes loop length and loop phase INPUTS**, so
the two quantities that are actually wrong in every one of these bugs cannot
fail an assertion. The suite is enormous and it is measuring the other axis.

---

## 1. Length is an argument the test supplies, never a result the system computes

`tests/fake_sl_engine.py`:

```python
def boundary(self, *, length: float = 2.0) -> None:
def _finish_record(self, loop: int, length: float = 2.0) -> None:
    self.state[loop] = SL_STATE_PLAYING
    self.loop_len[loop] = length
```

The fake never counts elapsed boundaries. A take that spans four `boundary()`
calls is exactly as long as a take that spans one — whatever the caller typed.

Across the whole suite there are **21 `boundary()` calls**. Five pass a
`length`, and in all five it is a constant the test author chose. **Zero derive
a length from elapsed cycles.**

So "I recorded four bars and got seven" is not an uncaught assertion. It is an
*unexpressible* one. No test in this repository can be wrong about how long a
loop is.

## 2. There is no playhead, so "started late" is also unexpressible

`boundary()` is instantaneous and exact. The fake has no notion of *where in
the bar* a command arrived, so the entire class of "the command landed 0.4 of a
bar late" has no representation. `should_defer_phase_anchor` exists in
production and takes `loop_pos`; nothing in the harness can produce a wrong one.

## 3. The fidelity harness has a shape bias

`tests/test_fake_engine_fidelity.py` opens with exactly the right instinct:

> "Every entry here corresponds to a defect that reached the appliance while
> the suite stayed green, because `FakeSlEngine` could not represent the thing
> that was wrong."

That file is the correct mechanism. But look at what is in it: a dropped
`load_loop` signature, a wrap flag, a ring-out transition. **Every entry is a
discrete-state bug. There is not one duration or phase entry.**

The harness grew a memory for state bugs and never grew one for time bugs. That
is the whack-a-mole: each fix is encoded as a new transition or guard, the
fidelity file gets one row longer, and the next *time*-shaped bug walks
straight through — because the axis it travels on still has no ruler.

## 4. The one instrument that CAN see it is not a gate

`scripts/measure-loop-alignment.py` records a real loop through the real path
and asks where the audio physically landed. Its own docstring is the sharpest
statement of the problem in the repo:

> "conformance proves a reading is true and never that it is the right quantity"

It appears **nowhere in `.github/`**. It needs the hardware, so it is a manual
expedition run *after* something is already broken. It is not a check that can
fail. The only thing standing between a timing regression and the appliance is
Mitch noticing while playing.

## 5. Documentation is not the gap — it is ahead of the tests

`Documents/specs/looper-timing-model-spec.md` (231 lines) already contains his
own words from 2026-08-30 on this exact confusion:

> "A six second clip reading as 138 BPM in four bars — I don't know why it's
> inherently four bars. If it's my first clip, it should still be one bar."

and line 95 already records the open question about odd meters that bar counts
of 1/2/4/8 cannot express. `sl_grid_sync.py` and `sl_grid_state.py` carry dated
corrections with measurements attached.

**Nothing executes any of it.** The spec says what the cycle should be; the
double says whatever the test passed in. A spec with no executable counterpart
is a very well-written opinion.

---

## MEASURED, 18:29 the same evening — the journal was reachable after all

The Pi came back. `journalctl -u mpe-looper-session` has the whole session, and
it answers both symptoms exactly. Nothing below is inferred from code shape.

### The seven bars

```
18:17:45.138  loop 0: defining the grid (free-form, no count-in)
18:17:46.222  loop 0: stop + overdub the ring-out          <- 1.084 s after record
18:17:47.223  loop 0: grid established from this take —
              1.084s = 1 bar(s) @ 221.4 BPM
18:17:51.226  loop 0: -> record (state=idle)
18:17:59.053  loop 0: stop at bar + overdub the ring-out    <- 7.83 s held
18:17:59.197  slots: track 1 slot 1: take landed (7.59s)
```

**7.59 / 1.084 = 7.00.** Exactly seven cycles.

The quantizer did its job perfectly. `derive_tempo` did its job perfectly — and
as predicted above, it never saw a 7; it returned `1 bar`. Every piece of
arithmetic downstream is correct.

**The bar was 1.084 seconds.** That is the whole defect. A first take barely
longer than a second was allowed to define the session's base unit, and
everything after it is faithfully measured against a bar that is not a bar.

The only guard on a defining take is `20 <= bpm <= 300` in `derive_tempo`. At
1.084 s the one candidate in range is `1 bar @ 221.4 BPM`, so it is accepted
without complaint. There is no minimum take length, no "this is implausibly
fast for a first loop" check, nothing that treats 221 BPM as the alarm it is.

Five grids were established in four minutes that evening:

| time | defining take | derived |
|---|---|---|
| 18:16:48 | 1.285 s | 1 bar @ **186.7 BPM** |
| 18:17:47 | 1.084 s | 1 bar @ **221.4 BPM** |
| 18:19:12 | 1.802 s | 1 bar @ **133.1 BPM** |
| 18:20:01 | 1.225 s | 1 bar @ **195.9 BPM** |
| 18:20:16 | 3.697 s | 2 bars @ **129.8 BPM** |

`sl_grid_state.note_loop_content`'s own docstring names this exact failure
shape from a month ago — "how a stable tempo turned into 73.7, then 34.6, then
54.9, then 179.3 BPM across four consecutive takes." It is happening again,
from a different cause, and nothing detects it.

### The clear that did not clear

```
18:17:48.222  loop 0: -> hold clear
18:17:48.222  loop 0: -> undo_all (state=playing)
18:17:48.229  loop 0: SL sync sl=0 bench=idle
```

The loop went to OFF. The line `last clip cleared — grid dropped` is **absent**.
The next take at 18:17:51 has no `defining the grid (free-form, no count-in)`
line and stops with `stop at bar` — counted in and quantized, exactly as Mitch
described it.

This one is **not a bug**. `GridState.note_loop_content` always returns `False`,
deliberately, and its docstring is Mitch's own instruction from 2026-08-30:

> "Even if we stop all clips ... we still need to reinitialize with those
> original settings. They should never be cleared away."

The docstring even states the tradeoff in advance: *"the take after you clear
everything is now counted in and length-quantized rather than free-form.
Redefining the tempo takes an explicit track reset."*

So a pad-hold clear intentionally keeps the grid. Only Shift+StopAll **long**
(track reset) drops it — and he did use that at 18:18:57, 18:19:54, 18:20:08
and 18:20:36, each time correctly getting a fresh free take.

The design is behaving as specified. It only *reads* as broken because the grid
it is loyally preserving was garbage — a 221 BPM bar of 1.08 s. Symptom 2 is
symptom 1 wearing a costume.

### The real single defect

**Nothing sanity-checks the take that defines the grid.** One rule — a minimum
plausible defining length, or a plausible-tempo band far tighter than 20-300 —
and the seven bars, the 221 BPM, and the "clear didn't work" all disappear
together, because they are one fault seen three ways.

### A third thing, unrelated and also real

Firing on nearly every take:

```
slots: track 1: engine reached PLAYING but the take was NOT registered —
slot 1 already holds a take, so the buffer's binding never moved to the new
slot. The pad will read empty and the next press will record again.
```

That is a multiclip slot-binding defect, separate from the timing story, and it
is already printing its own diagnosis. Not chased here.

## What would close it

Not more tests of the kind there are 2031 of. One change of shape:

1. **Give the fake a clock.** `boundary()` increments a cycle counter; a take's
   length becomes `cycles_elapsed * cycle_len`, computed, not passed. The day
   that lands, "recorded N cycles when it should have been M" becomes a
   sentence the suite can say. Every existing `boundary(length=...)` call site
   becomes a fixture that has to justify itself.
2. **Give it a position.** A command carries a phase within the cycle, so
   "arrived late" and "arrived on the boundary" stop being the same event.
3. **Add the first duration/phase rows to `test_fake_engine_fidelity.py`** —
   seven bars, and a clear that leaves the grid up — so the harness grows the
   memory it is missing rather than another state row.
4. **Make `measure-loop-alignment.py` a gate that runs on the appliance** on a
   schedule, with its result written where a green suite cannot contradict it.

Steps 1-3 are pure Python and need no hardware. Step 4 is the only one that
needs the Pi.

## What is still unknown

* Why the defining takes were so short. The pad-down/pad-down gap says 1.084 s,
  so the engine recorded what it was told — but whether Mitch intended a
  1-second first loop, or the second press was meant as something else, is not
  in the log. That is the one question the journal cannot answer.
* What the right guard is. A minimum defining length in seconds, a tempo band,
  or a confirmation prompt are three different instruments and the choice is
  his, not the code's.
* The multiclip slot-binding failure above.
* Whether the SD image's looper units differ from the USB image's in any way
  beyond being `disabled` (they were started by hand this session).
