# Grumpy review — LED / control-surface output ownership

**Dimension:** who writes to the wire.
**Branch:** `refactor/looper-ownership-2026-08-30` (verified with `git branch --show-current`)
**Device:** APC mini mk2. mk1 paths are live code no hardware here exercises.
**Suite baseline at review time:** `Ran 1600 tests in 50.814s / OK (skipped=3)`.
**Method:** read every module named in the brief plus `slot_matrix`, `slot_runtime`,
`sl_bench_listener`, `device_facts`, `probe-apc-buttons.py`, `looper_session.py` and
the relevant tests; then *ran* the real classes against a recording fake `midi_out`
to reproduce five of the findings. No product code or test was modified. Nothing was
sent to hardware; nothing made a sound.

**`MPE_SL_MULTIGRID=1` is live on the appliance.** Every finding below qualified with
"under multigrid" is therefore a defect on Mitch's running instrument, not a latent one
waiting for a feature flag. I could not establish this myself — the repo's own
`config/mpe.env.example:213` has it commented out and `sooperlooper-apc-bench.py:273`
defaults it to `0`, which is what I had assumed. It was established by commit `7c57107`,
which checked `/etc/mpe/mpe.env` **and the running session's environ**. Read the whole
review with that in mind; it is the difference between "this will bite when the feature
ships" and "this is biting now."

**Capability status — stage 0 is done.** The charter's original §6 said the button
colours were UNKNOWN and the probe was pending; it was amended mid-review and the
amendment is right. `device_facts.unmeasured()` returns `[]` — verified by execution.
The buttons are MEASURED: scene column green-only, track row red-only, channel 0 and
nothing else, exactly three states each (off / on / blink), closed as a bounded
negative with a positive control. Everything below is written against that, and it
changes three things: the capability check may **raise** rather than warn (§7);
colour-carrying UI is confined to the 8x8 grid as a measured constraint (F9a); and
`blink` becomes one of only three tokens available on a button, which turns F18 from a
tidiness complaint into a real ambiguity.

---

## 1. First impressions (the gut check)

This is not vibe-coded. It is the opposite failure, and a rarer one: nearly every
module here is individually excellent — `sl_limits.py`, `apc_leds.py`,
`device_facts.py`, `led_table.py`, `slot_leds.py` each carry the reason they exist
and what it cost to learn it. `apc_leds.translate` and `sl_limits.py` were flagged
as "believed good"; both check out, and I'll say why in §8.

What is missing is the layer *above* the modules. Six things write button LEDs and
none of them knows the others exist. The word `multigrid` does not appear anywhere in
`apc_transport.py` — the module that blanket-darkens 56 of the 64 pads `SlotSurface`
owns. Each writer keeps its own private record of what it last told the device, so
"who wins" is not even decided by call order: it is decided by which writer's *private
cache* happens to disagree with its own last desired value. Call order would at least
be deterministic.

The spec's D2 says "which one you see depends on call order in the event loop."
That is the optimistic reading. It is worse than that.

Four of the defects below produce a light that is wrong and *stays* wrong until the
session restarts, and one of them crashed the session outright — that one (F2) was
fixed in the working tree by a parallel agent while this review was being written, and
I re-verified the fix. All of it passed 1600 tests.

---

## 2. Architecture & structure — the write graph

Every LED byte in the running session originates at one of these sites. Reachable from
`run_bench` (`grep -rn "send_message(\[0x90"`):

| # | Site | Owner-ish | Diff cache | Notes it writes |
|---|---|---|---|---|
| 1 | `sooperlooper-apc-bench.py:267` | bench startup | none | 0x00–0x3F (blank) |
| 2 | `sooperlooper-apc-bench.py:504` | bench reopen | none | 0x00–0x3F (blank) |
| 3 | `apc_transport.py:532` (`TransportButtonLeds._set_led`) | transport | `_last_vel` | 0x08–0x3F, scene column, stale lamp |
| 4 | `track_gesture.py:538` (`TrackGesture._set_led`) | gesture | `_led_last` | its bound pad — **suppressed under multigrid** |
| 5 | `track_gesture.py:897` (`apply_view`) | bank change | none | clip row |
| 6 | `track_gesture.py:937` (`reset_all_loops`) | reset | none | clip row |
| 7 | `slot_surface.py:335` (`poll_hold_led`) | surface | **none** | one held pad |
| 8 | `slot_surface.py:547` (`repaint`) | surface | `_painted` | full matrix |
| 9 | `slot_surface.py:567` (`repaint_scenes`) | surface | `_scene_painted` | scene column |
| 10 | `slot_surface.py:573` (`blank`) | surface | none | 0x00–0x3F |

Out of process: `probe-apc-buttons.py:105/118/125` writes raw, deliberately, with the
session stopped. That one is fine and I'll defend it in §8.

**`PacedMidiOut` is genuinely the single chokepoint — verified.** All ten sites above
receive the same object: `midi_out = PacedMidiOut(raw_midi_out)` at
`sooperlooper-apc-bench.py:159`, and `raw_midi_out` is thereafter touched only for
`close_port`/`open_port` in `reopen_apc`. `apc_label` is set at line 193, *before* the
first LED write at 266. Nothing bypasses it. That is a real, load-bearing seam and it
works.

But note what the chokepoint is and is not. `PacedMidiOut` paces and translates. It
does **not** diff, and it has no idea what state the device is in — `send_message`
is `self._queue.append(...)`, unconditionally, on an unbounded `deque`
(`apc_link.py:93`). So the one place that knows everything reaching the wire knows
nothing about what the wire currently shows, and the four things that think they know
are each wrong about three-quarters of the surface.

Its docstring at `apc_link.py:65` claims *"There are eleven `send_message([0x90, ...])`
sites"*. It is ten in-tree today (plus three in the probe). Nothing tests the count and
nothing tests the chokepoint property, so the claim is decorative — the same shape as
`apc_panel`'s Rule 2. A rule a build cannot fail is not a rule.

---

## 3. The specific question: `clear_unwired_surfaces()` vs `repaint_scenes()`

**Confirmed, and it is worse than the spec states.** It is not only the scene column;
it is 56 of the 64 grid pads as well. And "which one you see" is not decided by call
order.

```python
# scripts/sooperlooper/apc_transport.py:423-430
    def clear_unwired_surfaces(self) -> None:
        """Darken scene launch 1–7 and grid rows 1–7 (not wired until P3)."""
        from apc_grid import RESERVED_GRID_NOTES

        for note in self._scene_launch_notes:
            self._set_led(note, SCENE_LED_OFF)
        for note in RESERVED_GRID_NOTES:
            self._set_led(note, LED_OFF)
```

`RESERVED_GRID_NOTES` is **56 notes, 8..63** (`apc_grid.py:62`, measured by running it).
`self._scene_launch_notes` is **all eight**, 0x70–0x77 on mk2 — including the Stop All
note, despite the docstring saying "1–7". Under `MPE_SL_MULTIGRID=1`, `SlotSurface`
owns every one of those 64 controls.

`apc_transport.py` contains **zero** occurrences of the string `multigrid`
(`grep -n multigrid scripts/sooperlooper/apc_transport.py` → nothing). The module that
darkens the multigrid matrix has never heard of it.

### Three callers, and what wins today

| Caller | When | mk2? | Runs relative to `SlotSurface` |
|---|---|---|---|
| `__init__` (`:405`) | bench startup | yes | **after** `slot_surface.repaint_scenes(force=True)` (bench:378 vs bench:438) |
| `repaint()` (`:418`) | every APC re-enumeration | yes | **after** `repaint(force=True)` + `repaint_scenes(force=True)` (bench:509–511) |
| `_darken_mk1_shift_ghost_surfaces()` (`:436`) | every `poll()` while Shift is solo | mk1 only | interleaved with `repaint_scenes()` at poll rate |

At startup the clobber is harmless because nothing is lit yet. The re-enumeration path
is not harmless, and re-enumeration is not hypothetical: `apc_link.py`'s own module
docstring records *"four starts in six left the pads dead"* and `LinkHealth` re-checks
and reopens on a 2 s timer.

### 🔴 F1 — `transport_leds.repaint()` erases the matrix after a USB reconnect, permanently

`sooperlooper-apc-bench.py:502-512`:

```python
        midi_out.reset()
        for _n in range(GRID_ROWS * GRID_COLS):
            midi_out.send_message([0x90, _n, LED_OFF])
        by_note = apply_view(...)
        if slot_surface is not None:
            slot_surface.repaint(force=True)        # :509  paints all 64 correctly
            slot_surface.repaint_scenes(force=True) # :510  paints the scene column
        transport_leds.repaint()                    # :511  darkens 56 pads + 8 buttons
        return True
```

Reproduced with the real classes (three tracks each holding clips in slots 1–3, one
playing):

```
pads the surface painted and transport.repaint() then darkened:
   {'0x8': (1,0), '0x9': (1,0), '0xa': (1,0),      <- three GREEN active clips
    '0x10': (5,0),'0x11': (5,0),'0x12': (5,0),      <- six YELLOW stored takes
    '0x18': (5,0),'0x19': (5,0),'0x1a': (5,0),
    '0x74': (1,0),'0x75': (1,0),'0x76': (2,0)}      <- three scene indicators
messages sent by 50 poll cycles afterwards: 0
wire state for those pads after 50 polls:  all 0
SlotSurface._painted still believes:       {'0x8':1, ... '0x1a':5}
```

Twelve lit controls go dark and **fifty subsequent poll cycles send nothing**, because
`repaint()` diffs against `_painted`, which still describes the surface as the surface
painted it. `_scene_painted` does the same for the buttons. The panel is wrong until
each individual cell's *desired* colour happens to change.

What the player sees: after a USB glitch they cannot even perceive, their stored takes
have vanished from the matrix and the scene column has gone dead. What they do about
it: press a dark pad. `SlotSurface._acts_on_release` routes a press on an occupied,
non-active pad to launch-or-switch, and a hold to delete. So a pad that reads "empty,
record here" launches an old take — or, held, deletes one.

The one recovery path in the code is a bank change (`set_view` → `_painted = None` →
full repaint, `slot_surface.py:517-522`). On this hardware that path is unreachable —
see F4. Restarting the session is the only fix, and nothing on the surface says so.

**How you would know it was violated:** you would see it on the device. That is the
finding. The missing test is: *build `TransportButtonLeds` and `SlotSurface` against
one `midi_out`, run the exact `reopen_apc` sequence, and assert the device's final
state equals what `SlotSurface` believes it painted.* No test in the suite constructs
both objects — `tests/test_apc_transport.py` is the only file that mentions
`TransportButtonLeds` and it contains zero references to `SlotSurface`. The conflict
is untestable by construction.

### 🔴 F3 — the Stop All note has two owners, and every press of it kills scene row 0

On mk2, Stop All is 0x77. `resolve_scene_launch_notes` returns **all eight** notes
0x70–0x77, which `SlotSurface` paints as scene indicators; `TransportButtonLeds._apply`
(`:506-525`) drives that same note as the held-lamp and the clear-hold blink.

The docstring at `apc_transport.py:190` says otherwise:

```python
def resolve_scene_launch_notes(apc_label: str) -> tuple[int, ...]:
    """Scene Launch 1–7 notes (slot rows 0–6). Stop All is not included."""
```

It *is* included. `SCENE_LAUNCH_NOTES_MK2 = SCENE_COLUMN_MK2 = tuple(range(0x70, 0x78))`,
and `apc_panel.py` asserts `SCENE_COLUMN_MK2[-1] == NOTE_STOP_ALL_CLIPS_MK2`. Verified
at runtime: `stop_all note in scene_launch_notes? True`. That stale docstring is why
the overlap has been invisible to every reader since.

Reproduced — one stored, stopped clip in slot row 0, so the button correctly reads
"row 0 holds clips, press to launch" (velocity 1):

```
desired for scene row 0 (note 0x77): 1   wire now: 1
-- player taps Stop All (no Shift) --
wire 0x77 = 0    desired = 1    surface believes = 1
```

One tap of the most-used transport button on the panel, and row 0's indicator is dark
forever while `SlotSurface` believes it is lit. Not on re-enumeration, not on a rare
race — every single time. This is a wrong light a player acts on: the button that means
"this scene holds clips" is now identical to the button that means "empty, does nothing".

Note the second-order damage: `scene_row_led` can return `SCENE_LED_BLINK` (2), which
on a button is a *hardware* blink (`device_facts.apc.scene.led_observed`, MEASURED).
`TransportButtonLeds` then drives the same note with a *software* accelerating blink
using 1/0. Two blink engines, one lamp, different rates, and whichever stops last
leaves the LED wherever it last wrote.

---

## 4. Code smells (the hall of shame)

### 🔴 F2 — `SlotSurface.poll_pending()` throws `TypeError` and kills the session

> **Status: FIXED AND COMMITTED while this review was being written** — `7c57107`
> *"fix(slots): the silent-session launch killed the process on arrival"*, found
> independently by the track-state ownership reviewer. I re-ran my reproduction against
> the amended file and it completes cleanly, landing the pending launch correctly:
> `pending: None, active_slot: 3`. Suite 1602 green.
>
> Recorded in full anyway, for two reasons. First, the *reason it survived 1600 tests*
> is the finding, and that part is only half fixed (see the test gap below). Second,
> that commit is where `MPE_SL_MULTIGRID=1` was confirmed live on the appliance, which
> re-rates four of my other findings from latent to active.
>
> ```diff
> -        for track_index in self._rt.poll_grid_wait():
> -            self.repaint(self._sl_states)
> +        if self._rt.poll_grid_wait():
> +            self.repaint()
>              self.repaint_scenes()
> ```
> That is the right fix: the loop variable was never used, and the discarded
> `self._sl_states` argument confirms the reading below — someone intended `repaint` to
> take a state dict and the signature had moved on without them.

As found (`scripts/sooperlooper/slot_surface.py:353-356`, pre-fix):

```python
        for track_index in self._rt.poll_grid_wait():
            self.repaint(self._sl_states)
            self.repaint_scenes()
```

```python
# scripts/sooperlooper/slot_surface.py:532
    def repaint(self, *, force: bool = False) -> None:
```

`force` is keyword-only. The call is positional.

```
TypeError: SlotSurface.repaint() takes 1 positional argument but 2 were given
```

Reproduced end to end through the real path, with the runtime wired exactly as the
bench wires it (`grid_boundary=lambda: grid.next_boundary(...)`,
`sooperlooper-apc-bench.py:361`):

```
slots: track 1 slot 4: launch slot 3 onto a silent track
deferred? True  grid_wait: {0: 481623.269053382}
--- poll_pending() ---
slots: track 1: queued launch lands on the grid bar line (nothing was playing to sync to)
!! TypeError: SlotSurface.repaint() takes 1 positional argument but 2 were given
  File ".../slot_surface.py", line 355, in poll_pending
    self.repaint(self._sl_states)
```

**Trigger:** launch a stored clip onto a silent track while any other track is playing,
with the grid established. `_defer_launch` (`slot_runtime.py:403-422`) parks it in
`_grid_wait`; a silent track produces no `loop_pos` and therefore no wrap, so
`poll_grid_wait` is the *only* thing that can land it — exactly as its docstring says.
That is not an edge case, it is the core multi-clip gesture.

**Blast radius:** `poll_pending` ← `poll_led_repaint` ← `poll_holds` ← the bare
`while True` in `run_bench`, on the main thread, with no handler. `looper_session.py:153`
returns `bench.run_bench(...)` directly, so the exception exits the process. The unit
restarts (the SIGTERM comment at `looper_session.py:75` documents `Restart=always`),
which means: pads dark, faders re-anchored, HUD dropped, and the APC reopened in the
same second the old process is being killed — the precise race
`_install_sigterm_handler` was written to avoid.

**Why no test caught it:** every `SlotSurface` test builds `SlotRuntime` without
`grid_boundary`, so `_grid_wait` is always empty and line 355 never executes.
`poll_grid_wait` returning non-empty is asserted only at the runtime level
(`tests/test_slot_runtime.py:579`), never through the surface. This is the identical
shape to the bug `synthesised_tap`'s own docstring describes — *"every harness used
debounce_ms=0 — the one value at which that bug is invisible."* Same lesson, same file,
one method apart.

### 🔴 F4 — the mk2 bank arrows are the mk2 scene buttons

```python
# scripts/sooperlooper/apc_transport.py:95-96
ARROW_NOTES_MK2 = (0x70, 0x71, 0x72, 0x73)  # up, down, left, right
ARROW_NOTES_MK1 = (0x40, 0x41, 0x42, 0x43)  # up, down, left, right
```

```python
# scripts/sooperlooper/apc_panel.py
SCENE_COLUMN_MK2: tuple[int, ...] = tuple(range(0x70, 0x78))   # 0x70..0x77
```

0x70–0x73 cannot be both. Canon settles it: `probe-apc-buttons.py` paints
`SCENE_COLUMN_MK2` and `device_facts.apc.scene.led_observed` (**MEASURED**, 2026-08-29)
records Mitch reading eight scene buttons lighting from that paint, top to bottom.
Tier 1 outranks the recalled constant. `ARROW_NOTES_MK2` is wrong.

The consequence is in the event loop's routing order:

```python
# sooperlooper-apc-bench.py:686-696
        if scene_row is not None:
            if slot_surface is not None and down:
                slot_surface.scene_press(scene_row)
            ...
            continue                       # <- never reaches handle_arrow
...
# :722
        if down and handle_arrow(n):
```

The scene branch `continue`s first, in **both** multigrid and single-clip mode. So on
mk2 `handle_arrow` is dead code, banking is unreachable, and what the code calls
"up/down/left/right" launches scene rows 7/6/5/4. Meanwhile the real arrows (the first
four of the horizontal row, which this same repo elsewhere treats as 0x64–0x6B — see
`resolve_stale_lamp_note` returning 0x6B, and `probe-apc-buttons.py:69`) fall through
every branch and are silently ignored.

Why this is an LED finding and not just a routing one: (a) `set_view` is the *only*
path that resets `SlotSurface._painted` to `None` and forces a full matrix repaint, so
F4 removes the sole recovery from F1; (b) `scripts/sooperlooper/README.md` names "the
mk2 arrows have LEDs" as the next candidate for a bank indicator — building that on
0x70–0x73 would light scene buttons and add a *fifth* writer to a column that already
has two.

I am not proposing a replacement constant. The right answer is `--dump-midi` and press
each arrow, which the code has been telling us to do since it was written
(`apc_transport.py:92`) and which nobody has done.

*Independently confirmed by another reviewer on this branch, by execution, while this
review was in progress. It does change my read of the scene column: those four buttons
have a third claimant, on the **input** side, and it is the same four notes whose
**output** is already contested between `SlotSurface` and `TransportButtonLeds`. Notes
0x70–0x73 are therefore the most over-claimed controls on the panel — two writers and
two readers each — which is why they head the ownership table in §10.*

### 🟡 F5 — one writer has no cache at all, and it floods the wire

```python
# scripts/sooperlooper/slot_surface.py:323-335
    def poll_hold_led(self) -> None:
        ...
        vel = LED_RED if int(elapsed * 4) % 2 == 0 else LED_OFF
        self._midi_out.send_message([0x90, note, vel])
```

No diff, no rate limit. Measured: **87,174 messages to a single note in 0.30 s** in a
free-running loop. At the bench's actual cadence (`time.sleep(0.002)`, plus a call to
`poll_holds()` after every MIDI event) that is roughly 400 messages/second, against
`PacedMidiOut`'s budget of one per `DEFAULT_GAP_S = 0.0015` ≈ 666/s. So a 1.5-second
hold spends ~60% of the entire LED bandwidth re-asserting the same two velocities ~200
times each, and every other repaint queues behind it in an unbounded deque.

This directly contradicts the design premise `apc_link.py` was built on: *"`PacedMidiOut`
spreads writes so the burst cannot happen; the steady-state diffing repaint was never
the problem."* True — because everything else diffs. This one doesn't. It is also the
only writer whose output `_painted` does not know about, so when the blink ends on the
`LED_OFF` phase and the press resolves to the same colour the cell already had (a
cancel, or `ACT_NOOP`), `repaint()` sends nothing and the pad stays dark. Fix: it wants
the same seq/phase treatment `TrackGesture._led_transition` already has, not its own
timer.

### 🟡 F6 — four caches of one fact, none of them at the wire

`SlotSurface._painted` (`:84`), `SlotSurface._scene_painted` (`:85`),
`TransportButtonLeds._last_vel` (`:404`), `TrackGesture._led_last` (`:179`). Plus
`poll_hold_led` with none, plus `PacedMidiOut` with none.

They *can* disagree, and F1/F3 are the constructed sequences where they do. The general
mechanism: writer A sends note N and records it; writer B sends note N and records it;
neither reads the other; both then suppress the correction that would fix the panel.
Two of the four are private to objects that were never told the other exists.

**The `force=` flags are the tell.** Every one of them is a manual cache invalidation
issued because the cache's owner does not own the wire:

- `SlotSurface.repaint(force=)` and `repaint_scenes(force=)` — used at `set_view`,
  `reset`, bench startup, and `reopen_apc`, i.e. "the device is not what I think".
- `TransportButtonLeds.repaint()` — `self._last_vel.clear()` (`:417`), same thing by
  another name, with the docstring saying so outright: *"it then comes back dark while
  the cache still says lit."*
- `TrackGesture._set_led(force=True)` — passed unconditionally by `_sync_led` (`:591`),
  so `_led_last` is only ever a dedup for `poll_led`.

The proof that these are papering over ownership rather than solving anything: at bench
startup, `slot_surface.repaint_scenes(force=True)` (`:378`) invalidates its cache to
paint the truth, and 60 lines later `TransportButtonLeds.__init__` undoes it. A force
flag defeated by a different writer's force flag. In a compositor there is one cache,
one invalidation point, and no caller needs a flag.

### 🟡 F7 — the scene LED and the scene press read different state

`slot_surface.py:207-219` is unusually clear about this being the defect:

> `track_state` — *"The ONE answer to 'what is this track's engine state'. `press` read
> `fs.sl_state` while `scene_press` and `dispatch` read the surface's own `_sl_states`
> cache … two sources for one fact is how the paths drifted apart in every other
> respect."*

The press path was fixed. The LED path was not:

```python
# :557 — repaint_scenes
            desired[note] = scene_row_led(
                self._rt.tracks(), row, sl_states=self._sl_states
            )
# :171 — scene_press
        plans = plan_scene_press(
            self._rt.tracks(), row, sl_states=self.track_states()
        )
```

`scene_row_led` and `plan_scene_press` both branch on `row_is_fully_playing`
(`slot_matrix.py:299`, `:356`) — the same predicate deciding *what the light says* and
*what the press does*. Fed from two different sources. `repaint` has the same problem
at `:542`.

Today they mostly agree, because `SlBenchStateListener.on_update` (`:60-66`) feeds
`fs.sync_from_sl` and `surface.on_state` from the same message. The one place they
provably diverge is `reset()` (`:505-513`), which does `self._sl_states.clear()` and
never touches the gestures — so for the window between a track reset and the next
engine report, the light is computed from an empty dict and the press from the
gestures' retained states. "Mostly agree" is how every other pair in this file started.

### 🟡 F8 — mk1: a refuted ghost is still eating the matrix

```python
# :432-436
    def _darken_mk1_shift_ghost_surfaces(self) -> None:
        """Re-assert dark on mk1 surfaces that ghost-glow when Shift is solo."""
        if self._apc_label != "mk1" or not self._shift_down or self._stop_down:
            return
        self.clear_unwired_surfaces()
```

Called from `note_event` on Shift-down and from `poll()` (`:490`) on **every poll
cycle** while Shift is held alone. Under multigrid that blanks 56 matrix pads and the
scene column every time the player reaches for Shift — and `_scene_painted`/`_painted`
then suppress the repair, exactly as in F1.

It is compensating for a phenomenon the same file records as disproven, twice, on
hardware (`apc_transport.py:57-72`), with `MK1_GHOST_SHIFT_S = 0.0` disabling the
*filter* — but not this darkening, which is gated only on `apc_label == "mk1"`.
mk1 is unexercised here, so this is not a live defect on Mitch's device; it is live
code that would fail the moment a mk1 is plugged in, and it should not survive stage 3.

### 🟡 F9 — the anti-drift mechanism has drifted: every fact citation in the LED stack is broken

`device_facts` rule 1 is *"Other modules cite the fact id in a comment. They do not
restate the claim."* Four modules cite ids that **do not exist**:

```
scripts/sooperlooper/led_table.py:45      device_facts.apc.scene.led_colours
scripts/sooperlooper/led_table.py:46      .apc.track.led_colours
scripts/sooperlooper/slot_matrix.py:330   device_facts.apc.scene.led_colours
scripts/sooperlooper/apc_leds.py:31       device_facts.apc.scene.led_colours
scripts/sooperlooper/apc_transport.py:368 device_facts.apc.scene.led_colours
scripts/probe-apc-buttons.py:10           device_facts.apc.scene.led_colours
```

The real ids are `apc.scene.led_observed`, `apc.track.led_observed`,
`apc.buttons.single_colour`. `fact("apc.scene.led_colours")` raises `KeyError`. Nothing
checks, because the citations are comments.

This is the sharper form of the spec's D3. D3 says *"hardware capability lives in prose,
not in code."* That was true when it was written and is no longer the whole story: the
code now **does** cite the fact base, in five places, and every citation is broken. A
citation that raises is worse than no citation, because it reads as diligence.

Worse than dangling: **stale**, and in one case actively dangerous.

### 🟡 F9a — a standing instruction to ship something the hardware cannot do

```python
# scripts/sooperlooper/slot_matrix.py:328-332  (scene_row_led docstring)
    Mitch asked for yellow here and got blink instead, on the grounds that the
    scene buttons are green-only. That ground is not solid — see
    `device_facts.apc.scene.led_colours`, which is vendor-tier and unmeasured.
    If the probe shows these buttons can do yellow, this should become yellow,
    which is what was asked for in the first place.
```

The probe has run. The answer is no. `apc.buttons.single_colour` (**MEASURED**,
2026-08-29) closes it as a bounded negative across channel, velocity and SysEx, with a
positive control (`apc.probe.positive_control`: the same message aimed at known-RGB
grid pads turned them blue while the buttons stayed dark). `apc.scene.led_observed`
records that the RGB palette indices are simply not honoured — velocity 13 ("yellow" on
the grid) is green on a scene button.

So this docstring is a conditional instruction whose condition has been evaluated and
come back false, pointing at a fact id that raises `KeyError` so the next reader cannot
check, in the one function whose output feeds a button. It is a shipped-regression
waiting for someone diligent. Delete the paragraph, replace it with the measured
constraint, and make the capability check enforce it.

The other four citations are inert but equally wrong-by-tier — all still describing a
settled MEASURED fact as vendor-tier and unmeasured, which stopped being true the day
before this branch opened.

**No live violation today**, and that is worth saying: I traced every velocity that can
reach a button note. `TransportButtonLeds` only ever sends `SCENE_LED_OFF/ON` and
`TRACK_LED_OFF` (`apc_transport.py:407-524`), and `scene_row_led` only ever returns
`SCENE_LED_OFF/ON/BLINK` (`slot_matrix.py:334-338`) — all within {0, 1, 2}. The button
writers *do* honour the capability today. The capability check is therefore a guard
against the next change, not a fix for this one, which is exactly when it is cheapest
to add.

The invariant tests this wants are both small: every `device_facts.<id>` string
appearing in a comment must resolve in `FACTS`; and every velocity submitted for a
control declared `kind=SCENE|TRACK` must be in {off, on, blink} — **raising**, per
charter §5, because the governing facts are MEASURED.

### 🟢 F12 — a dead parameter advertising a second source of truth

```python
def matrix_messages(view, tracks, sl_states, *, previous=None, gesture_leds=None):
```

`sl_states` is never read in the body (`slot_leds.py:88-131`). `static_cell_led`'s
docstring explains at length why it deliberately dropped the parameter — *"An unused
parameter here would be an open invitation to derive a colour from it again"* — and
then `matrix_messages` keeps one. Delete it.

### 🟢 F13 — three mutually exclusive claims about the mk1 horizontal row

| Claim | Where |
|---|---|
| 0x64–0x6B | `apc_panel.py` `TRACK_BUTTON_NOTES_MK1` — in the file that declares itself canonical |
| 0x30–0x37 | `apc_transport.py:51` `MK1_TRACK_OVERLAP_NOTES`, *"mk1 Track Select 1–8 share notes with grid row 6"* |
| 0x40–0x43 | implied by `ARROW_NOTES_MK1` being the first four of that row |

All three unmeasured, none reconcilable. This is D1 in a form the spec did not catch.
Do not delete the mk1 paths on this basis — but do not let a registry launder these
into looking settled either.

### 🟢 F14 — five things animate, none of them coordinate

| Animator | Clock | Notes it can drive |
|---|---|---|
| `TrackGesture._led_transition` / `poll_led` (`:797-801`) | `int(t / TRANSITION_BLINK_S) % len(seq)` | its bound pad (write suppressed under multigrid) |
| `TrackGesture.current_led()` (`:564-567`) | same expression, evaluated by the *reader* | its bound pad, via `SlotSurface.repaint` |
| `SlotSurface.poll_hold_led` (`:334`) | `int(elapsed * 4) % 2` | any held non-active pad |
| `TransportButtonLeds._apply` (`:506-518`) | `accelerating_hold_blink_on` | Stop All, scene column |
| the device itself | firmware | any pad at `MK2_BLINK`, any button at velocity 2 |

Two of these can address the same note. `poll_holds()` calls them in a fixed order —
`poll_hold_led()` at `sooperlooper-apc-bench.py:530`, then `poll_led_repaint()` at
`:532` — so within one iteration **the repaint pre-empts the hold-warning blink**
whenever the repaint has anything to send. The delete-confirmation blink is therefore
suppressed non-deterministically by unrelated engine traffic. That is the answer to
"what happens when two owners animate the same note": the later caller wins the
iteration, and which caller has something to say depends on a cache neither of them
shares.

The hardware blink is a third clock nobody owns. A pad going from `TAIL_CAPTURE`
(software red/green) to `(LED_GREEN_BLINK,)` (firmware blink at `MK2_BLINK`) has no
defined phase relationship — cosmetic, but it is the same missing owner.

### 🟡 F18 — blink means three different things on one column of eight buttons

The colour policy is stated twice, identically, and it is a good rule:

> `scripts/sooperlooper/README.md` — *"a solid colour always comes from the engine; a
> blink is something asked for and not yet confirmed."*
> `led_table.py` module docstring — *"A solid colour is only ever painted from
> `sl_state`. Anything we have asked for but not seen confirmed blinks."*

On the 64 grid pads that rule holds, and `led_for` enforces it structurally. On the
eight scene buttons it is violated twice, in opposite directions:

| Writer | Note(s) | Blink means | Mechanism |
|---|---|---|---|
| `slot_matrix.scene_row_led` (`:336-337`) | 0x70–0x77 | **"every clip here is already playing — press to STOP"** — confirmed engine truth, the *inverse* of the rule | firmware blink, velocity 2 |
| `TransportButtonLeds._apply` (`:506-518`) | 0x77 | **"you have been holding Shift+StopAll for N seconds"** — a hold timer, neither engine truth nor queued intent | software 1/0 toggle, accelerating |
| the adjacent grid | 0x00–0x3F | **"queued, lands on the next bar"** — the actual rule | firmware blink at `MK2_BLINK` |

Note 0x77 carries the first two simultaneously (see F3), directly beside 64 pads
carrying the third.

This was survivable while it was one visual token among seven. It is not survivable now
that `apc.buttons.single_colour` is MEASURED and CLOSED: a scene button has **exactly
three states**, so blink is one third of the entire vocabulary available on that
control, and it currently means two mutually exclusive things on it. A player reading
a blinking scene button cannot know whether it is reporting the engine or their own
finger.

Two sub-points the compositor must carry:

- `TransportButtonLeds` imports only `SCENE_LED_OFF/ON` (`apc_transport.py:20-23`) and
  never `SCENE_LED_BLINK`. It hand-rolls its blink from on/off. That is *defensible* —
  the device's blink rate is firmware-owned and cannot accelerate, and the accelerating
  warning is the point — but it means a control can be in firmware-blink or
  software-blink mode and **the two do not compose**: a firmware blink left running is
  not stopped by writing `on`, it is replaced, and whichever writer stops first leaves
  the lamp wherever it last wrote. The registry needs `blink` to be a mode with an
  owner, not a velocity anyone can send.
- Deciding which meaning wins is a UI judgement, not an architecture one. Per charter
  §6 that is Mitch's eye. The architecture's job is to make it one line — which, today,
  it is nowhere near.

### 🟡 F17 — Shift+Scene belongs to the firmware, and the bench claims it too

`apc_mode.py`'s docstring records that the mk2 enters Notes mode on **Shift+Scene 7**,
which silences the grid on the Control port entirely and cost a debugging session on
2026-08-28. `apc_panel.scene_press_row` special-cases Shift only for Stop All, so every
other Shift+Scene chord is dispatched as a scene launch (`bench:676-688`). The player
gets a scene launch *and* a dead grid, and the `grid_silent_reason` print is the only
thing that says why. Whoever owns the binding table in stage 5 has to record that the
device firmware owns this chord and we may not.

### 🟢 F16 — outside my dimension, flagging once

`sooperlooper-apc-bench.py:113` — `num_loops = int(os.environ.get("MPE_SL_LOOPS", str(NUM_LOOPS)))`
bypasses `sl_limits.resolve_num_loops()`, the clamp written specifically to stop
`MPE_SL_LOOPS=16` manufacturing a phantom track. It is LED-adjacent (`GridView(num_loops=…)`
decides which columns get painted, so a phantom gets a pad), but it belongs to the
state-ownership reviewer. Charter §3's headline row, still live.

---

## 5. Logic & state — answering the charter's three questions

For every control I examined, the answers are in §10. The pattern:

1. **Who owns it?** For 64 of the 73 controls the honest answer today is "two modules,
   and they do not know it."
2. **Who may write it, through what seam?** Anyone holding `midi_out`. `PacedMidiOut`
   enforces pacing and per-model encoding, which is real, but it enforces nothing about
   *authority*. Six objects hold it.
3. **How would you know?** **You would see it on the device**, for every single one.
   That is the finding, and it is uniform: there is no test anywhere that asserts the
   device's resulting state rather than one writer's outgoing messages.

The one place ownership is genuinely enforced, and it works:

```python
# scripts/sooperlooper/track_gesture.py:529-531
    def _set_led(self, velocity: int, *, force: bool = False) -> None:
        if self._multigrid:
            return
```

Under multigrid the gesture computes colour (`current_led()`) and writes nothing;
`SlotSurface` reads and paints. Policy separated from the wire, one writer. That is
exactly the shape §5.3 wants, already built, already working. It is the model — the
problem is that it was applied to one writer out of six.

---

## 6. Test strategy

The suite is 1600 tests and green, and every defect above survives it. Specifically:

**Untested entirely:** `clear_unwired_surfaces` (zero references in `tests/`),
`_scene_painted`, `_last_vel`, `poll_hold_led`, the bench's LED call order, the
`PacedMidiOut` chokepoint property, the resulting device state after any sequence.

**Tests that lock in behaviour that is itself the bug** — charter §2 applies, and each
would need a commit-body line naming the canon it now conforms to:

1. `tests/test_apc_transport.py:325` `test_mk1_shift_clears_scene_and_upper_grid`
   asserts that holding Shift darkens every scene note **and every grid note 8–63**.
   That is F8 written down as a requirement. It contradicts
   `apc-control-surface-architecture-spec` §5.3 and stage 3's "delete
   `clear_unwired_surfaces`". It cannot survive the refactor and should not be
   preserved.

2. `tests/test_slot_surface.py:288` — `SceneRowTests.setUp` overrides
   `self.surface._scene_launch_notes = tuple(range(0x52, 0x59))` — **seven** notes —
   with the comment *"Row 0 has NO scene button — Stop All Clips (0x59) occupies that
   position on the panel."* That directly contradicts `apc_panel.py`, which is
   MEASURED 2026-08-27 and states all eight are scene launchers with Stop All as a
   Shift layer. The test asserts the pre-fix world, and by excluding 0x59/0x77 it makes
   F3 structurally invisible to the suite.

3. Both `repaint_scenes` tests (`:300`, `:306`) pass `force=True` — the one value at
   which the diff-cache staleness cannot appear. Same shape as the `debounce_ms=0`
   blindness `synthesised_tap` documents.

4. `tests/test_apc_transport.py:196-212` `_leds()` builds `TransportButtonLeds` with a
   bare `FakeOut` and nothing else. A test that cannot see a second writer cannot fail
   for a two-writer bug.

**The three tests that should exist, and would each have caught a 🔴:**

- *One wire, two writers.* Build `TransportButtonLeds` and `SlotSurface` on one
  recording `midi_out`, run the `reopen_apc` sequence, assert final device state ==
  `SlotSurface`'s belief. Catches F1 and F3.
- *Every control has exactly one writer.* Enumerate note→writer at import and assert
  the sets are disjoint. Catches F1, F3, F4, F8 and prevents the next one.
- *`SlotSurface` under a wired `grid_boundary`.* **Half-closed by `7c57107`**, which
  added `GridWaitLaunchTests` — a good test, and its author checked that it fails
  against the reverted code, which is more than most of this suite can say. But it
  reaches in and sets `self.rt._grid_wait[3] = 0.0` directly. That proves the branch no
  longer raises; it does not prove the *feature* works, because `SurfaceCase.setUp`
  still builds `SlotRuntime` with no `grid_boundary` (`grep grid_boundary
  tests/test_slot_surface.py` → nothing). The end-to-end path the bench actually runs —
  press → `_defer_launch` → `_grid_wait` populated from the real callback → due → land
  → repaint — is still never exercised. Bluntly: a `SlotRuntime` constructed without
  the callback the bench always supplies is not the object under test, and injecting
  its private state is a workaround for that, not a fix.

---

## 7. Risk: does a §5.3 compositor refactor silently change what the panel shows?

**Ranked HIGH — but the risk is inverted from the usual case, and that is the important
part.**

The compositor will *change what the panel shows*, and mostly by fixing it. F1, F3, F5
and F8 are all "the compositor's absence is why the panel is wrong", so a correct
compositor makes 56 pads and 8 buttons start behaving differently — visibly, and for
the better. The danger is that a reviewer sees the diff, sees "no behaviour change
intended", and treats a corrected panel as a regression.

Concretely ranked:

| Risk | Level | Why |
|---|---|---|
| A control silently loses its writer during migration | **HIGH** | 10 write sites, 4 caches, 0 tests asserting device state. Nothing would fail. |
| Blink phase/rate changes | **HIGH** | Five animators on three clocks (§F14); collapsing them to one re-times every blink. Compounded by F18: `blink` currently means three things, and firmware-blink and software-blink do not compose. Whether the new timing reads right is Mitch's eye, not a test. |
| Priority resolution differs from today's accidental order | **MEDIUM-HIGH** | Today's resolution is not call order, it is *cache history* (§F6). There is no order to preserve — a declared priority is a new behaviour by definition. Do not describe it as "preserving current behaviour"; it isn't, and can't be. |
| mk1 regressions | **MEDIUM** | F8's darkening is asserted by a test and exercised by no hardware here. Any change is unprovable until a mk1 is plugged in. |
| Grid (8x8) regression | **LOW** | `apc_leds.translate` is pure, exhaustively tested, and stage 5 defers it. Leave it last, as the spec says. |
| Colour/capability regression | **LOW — and this one gets cheaper, not riskier** | Stage 0 is done. `unmeasured()` is `[]`, so §5.4 may be implemented as a **hard raise** for button colour requests, not a warning: charter §5's "warn on unmeasured tiers" hedge has no button-side facts left to apply to. Declare `kind=SCENE → colours=(GREEN,), modes=(OFF, ON, BLINK)` and `kind=TRACK → colours=(RED,)`, and let `scene_row_led`'s yellow suggestion (F9a) fail in the suite in seconds instead of surviving a deploy. |

**Provable without hardware tonight:** every write site's *intent* (which module wants
which note at which velocity); that exactly one writer owns each control; that the
device's simulated final state equals the compositor's model (a recording fake is a
sufficient oracle for this — it is how F1, F2, F3 and F5 above were established); that
no note literal exists outside the registry; that every colour request is inside the
declared capability — **fully provable now that stage 0 is closed**, and enforceable by
raising rather than warning; that every `device_facts` citation resolves; that the whole
suite stays green.

**Not provable without eyes on the panel, and must be recorded as unproven:**

- Whether a corrected panel *reads* right — blink vs solid, the accelerating clear
  warning, whether `SCENE_LED_BLINK` at the firmware rate is legible beside the grid's
  software blinks, and which of F18's three meanings of blink should win on the scene
  column. Charter §6: that is Mitch's eye. Note that the measured three-state limit
  *narrows* the options he has to choose between, which is the useful half of a
  constraint.
- Which physical buttons the mk2 arrows actually are (F4). `--dump-midi`, four presses.
  Two minutes of Mitch's morning, and it unblocks banking *and* F1's recovery path.
- Whether F1 and F3 reproduce on the device as they do in process. I am confident in
  the mechanism because I ran the real classes, but the fake `midi_out` cannot tell you
  the APC honours the last write it received. It should — and the last three times this
  project assumed "should", it cost an evening.
- Anything at all about mk1.

**One structural note for the migration:** F2 is a live crash sitting *inside*
`SlotSurface.poll_pending`, which stage 2 will move. Fix it as its own commit, before
the compositor lands, or the bisect that finds it in the morning will land on the
compositor commit and blame the wrong change.

---

## 8. Credit where it is due

- **`apc_leds.translate` — verified good.** Pure, total, never raises, identity for
  every model it does not understand, refuses to invent a colour for an unmapped
  velocity, never rewrites the note. 14 tests in `tests/test_apc_leds.py` cover
  identity, palette indices, blink preservation, hue consistency, malformed input, and
  the pacer dropping a backlog encoded for the previous model. Its claims now agree
  with `device_facts` at MEASURED tier. This is the one layer in the LED stack I would
  put in front of an audio engineer as-is.
- **`sl_limits.py` — verified good.** One constant, one home, the measurement table,
  the phantom's failure mode, and what it cost, all travelling together; plus
  `resolve_num_loops` making the clamp executable rather than aspirational. It is the
  standard the charter says it is. It is also bypassed at exactly one call site (F16),
  which is not the module's fault.
- **`PacedMidiOut` is a real chokepoint**, verified by tracing every construction site.
  The `apc_label` setter dropping a backlog encoded under the old model is a subtle
  correctness point most people would miss.
- **`TrackGesture._set_led`'s multigrid early-return** is the correct ownership seam,
  built and working. It is the template for everything else.
- **`led_table.led_for`** — one pure function, returns a *sequence* so callers never
  have to know which states animate, and the docstring on why there is deliberately no
  "launch queued" flag is the kind of reasoning that stops a bug class rather than a bug.
- **`slot_leds.static_cell_led`** — deliberately narrowed, `sl_state` deliberately
  removed, and the reason written down. Exactly right.
- **`device_facts.py` + `probe-apc-buttons.py`** — `refuse_with()` makes rule 4
  executable; the probe steps one class at a time, records what Mitch types, and
  round 5 carried a **positive control** (the same message aimed at known-RGB pads).
  That is AGENTS.md Rule -1 done properly, and it is why `apc.buttons.single_colour`
  is evidence instead of another vendor claim. The probe writing raw, unpaced, with
  the session stopped is the right call for an instrument.
- **`apc_panel.py`'s vertical-flip explanation** and **`apc_mode.py`'s refusal to guess
  what modes 0x00/0x02 mean** are both the repo at its best.

The bad: six writers, four caches, zero device-state assertions.
The smell: every `force=` flag in the codebase — each one is a note saying *"I do not
trust my own record of the device, because I am not the only one writing to it."*

---

## 9. Verdict and priority backlog

The modules are better than the system. Every individual file here was written by
someone thinking hard about one problem; nobody was ever assigned the problem of *the
surface as a whole*, and the result is a control panel with six mayors. The spec's D2
is correct and understates the scope — it is 64 pads, not just the scene column, and
the resolution mechanism is worse than call order because it depends on private cache
history. Stage 2 and stage 3 are the right treatment and should be done together;
splitting them leaves a compositor that still has `clear_unwired_surfaces` shooting
through it.

Do not let "1600 tests green" stand in for "the panel is right". It was green through
all four 🔴s, and it is still green now that one of them is fixed — which tells you the
suite is not watching this dimension at all, in either direction.

**Priority backlog**

1. **🔴 F2 — done (`7c57107`), and correctly staged as its own revertible commit.**
   What remains: wire `grid_boundary` into `SurfaceCase.setUp` so the launch path is
   tested end to end rather than by injecting `_grid_wait`. The crash is gone; the
   blindness that hid it is not.
2. **🔴 F1 — delete `clear_unwired_surfaces` and its three call sites (stage 3), or
   gate it on multigrid today.** Until then, every APC re-enumeration silently erases
   the matrix with no recovery.
3. **🔴 F3 — give the Stop All note one owner.** Cheapest correct answer: stop passing
   the full eight-note column to `TransportButtonLeds`, and fix
   `resolve_scene_launch_notes`' docstring, which currently states the opposite of what
   the function does.
4. **🔴 F4 — measure the mk2 arrow notes** (`--dump-midi`, four presses, Mitch's
   morning, two minutes). Until then banking is dead and F1 has no recovery path.
5. **🟡 F5 / F6 — one diff, at the wire.** Move diffing into the compositor, delete
   `_painted`, `_scene_painted`, `_last_vel`, `_led_last` and every `force=` flag they
   forced into existence. Then write the "one wire, two writers" test so the class
   cannot come back.
6. **🟡 F9 / F9a — fix the five broken `device_facts` citations, and delete the yellow
   suggestion in `scene_row_led`.** Cheap, in scope per charter §6, and F9a is a
   standing instruction to ship something now provably impossible. Add the
   citation-resolves invariant so it cannot rot again — the mechanism built to stop
   restatement drift has itself drifted, which is the failure worth pinning.

---

## 10. Ownership table

Every control I examined. "Claimants" = modules that write its LED today, verified by
reading and by running the code. "Recommended owner" = exactly one, per charter §3.

### Buttons — scene launch column (mk2 0x70–0x77 / mk1 0x52–0x59)

Capability for every row below, MEASURED 2026-08-29: **green only, three states —
off (0) / on (1) / blink (2), channel 0x90 only.** No other colour, no other channel.
The registry row is `Led(colours=(GREEN,), modes=(OFF, ON, BLINK))` and the check
**raises**.

| Control | Note (mk2 / mk1) | Claimants today | Recommended single owner | Seam |
|---|---|---|---|---|
| Scene launch row 7 | 0x70 / 0x52 | `SlotSurface.repaint_scenes`, `TransportButtonLeds.clear_unwired_surfaces` — **plus a third claimant on the input side: `ARROW_NOTES_MK2` "up"** (F4) | **`SlotSurface`** (policy: `slot_matrix.scene_row_led`) | compositor submits desired; transport may not name it. Arrow claim is a bug, not a layer — delete it once the real arrow notes are measured |
| Scene launch row 6 | 0x71 / 0x53 | same + `ARROW_NOTES_MK2` "down" | **`SlotSurface`** | same |
| Scene launch row 5 | 0x72 / 0x54 | same + `ARROW_NOTES_MK2` "left" | **`SlotSurface`** | same |
| Scene launch row 4 | 0x73 / 0x55 | same + `ARROW_NOTES_MK2` "right" | **`SlotSurface`** | same |
| Scene launch row 3 | 0x74 / 0x56 | `SlotSurface.repaint_scenes`, `TransportButtonLeds.clear_unwired_surfaces` | **`SlotSurface`** | same |
| Scene launch row 2 | 0x75 / 0x57 | same | **`SlotSurface`** | same |
| Scene launch row 1 | 0x76 / 0x58 | same | **`SlotSurface`** | same |
| **Scene row 0 / Stop All** | **0x77 / 0x59** | `SlotSurface.repaint_scenes`, `TransportButtonLeds._apply`, `.clear_unwired_surfaces`, `.on_reset_fired` | **`SlotSurface`**, with the transport submitting a higher-priority *transient* (held / clear-hold blink) that the compositor resolves and then releases back | one control, two layers, declared priority — never two writers (**F3**). The two layers must also agree what blink means (**F18**) |

### Buttons — transport and track row

| Control | Note | Claimants today | Recommended single owner | Seam |
|---|---|---|---|---|
| Shift | 0x7A / 0x62 | **none** (correct) | **nobody** — `device_facts.apc.shift.led` is OPEN; firmware-owned hypothesis | registry marks it unwritable; a write is a test failure, not a runtime surprise |
| Track Select 8 ("stale lamp") | 0x6B / — | `TransportButtonLeds.__init__`, `.repaint`, `.note_event`, `._apply` — all writing OFF | **compositor**, as a one-shot startup clear | a legacy-clear belongs in the compositor's init, not in four methods of a live writer. Capability: **red only**, off/on/blink (MEASURED) |
| Track Select 1–7 | 0x64–0x6A / ? | none | **unassigned** — reserve in the registry | mk1 numbering is contested three ways (**F13**). Capability: **red only**, off/on/blink (MEASURED) — so these can never carry a per-track colour code |
| Bank arrows | **unmeasured** | none (unreachable, **F4**) | **bench viewport owner**, once measured | must be measured before a registry row is written, or the registry launders a guess |

### Grid — 8x8 (notes 0x00–0x3F)

| Control | Notes | Claimants today | Recommended single owner | Seam |
|---|---|---|---|---|
| Clip row (row 0) | 0x00–0x07 | `SlotSurface.repaint`, `TrackGesture._set_led` (single-clip only), `apply_view`, `reset_all_loops`, bench startup/reopen blanks, `SlotSurface.blank` | **`SlotSurface`** under multigrid; **`TrackGesture`** in single-clip mode — mode selects the owner, never both | `TrackGesture.current_led()` is already the correct read-only seam; extend it to the blank paths |
| Matrix rows 1–7 | 0x08–0x3F | `SlotSurface.repaint`, **`TransportButtonLeds.clear_unwired_surfaces`** (56 pads), bench blanks, `SlotSurface.blank` | **`SlotSurface`** | delete the transport's claim (**F1**) |
| Held-pad hold warning | any held pad | `SlotSurface.poll_hold_led` (uncached), racing `SlotSurface.repaint` | **`SlotSurface`**, as a priority layer inside its own desired-state model | one submitter, one diff — not a second timer (**F5**) |
| Startup / reopen blank | 0x00–0x3F | bench:267, bench:504, `SlotSurface.blank`, `apply_view`, `reset_all_loops` | **compositor** | "make the device match an all-off model", then flush |

### Non-LED state that decides an LED

| Thing | Claimants today | Recommended single owner | How you would know it was violated |
|---|---|---|---|
| "What the device currently shows" | `_painted`, `_scene_painted`, `_last_vel`, `_led_last`, and nothing at `PacedMidiOut` | **compositor**, one cache | assert simulated device state == compositor model after any sequence (**F6**) |
| Engine state feeding scene colour | `SlotSurface._sl_states` (LED) vs `SlotSurface.track_states()` (press) | **`track_state()`**, as its own docstring already says | a test that drives them apart via `reset()` and asserts the light matches the action (**F7**) |
| Blink phase | 4 software animators + firmware | **compositor** owns the frame clock; owners submit sequences | assert two controls asked for the same sequence are in phase (**F14**) |
| **What `blink` means** | `scene_row_led` (engine truth), `TransportButtonLeds._apply` (hold timer), `led_for` (unconfirmed intent) | **one policy, stated once** — Mitch picks which; the code must express it in one place | a test asserting the token has one meaning per control class; today it has three on one column (**F18**) |
| Firmware-blink vs software-blink | `scene_row_led` sends velocity 2; `TransportButtonLeds` hand-rolls 1/0 | **compositor**, as an explicit mode on the control | they do not compose — assert a control is never handed off between modes without an explicit stop (**F18**) |
| Hardware facts behind colour choices | 5 modules citing 2 non-existent fact ids, all still labelled vendor-tier | **`device_facts.py`** — settled at MEASURED since 2026-08-29 | import-time check that every cited `device_facts.<id>` resolves; capability check that **raises** on a button colour request (**F9**, **F9a**) |

---

*Reproduction scripts used for F1/F2/F3/F5 were written to the session scratchpad, not
to the repo. No product code or test was modified by this review.*
