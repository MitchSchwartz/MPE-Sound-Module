# Grumpy review — test suite integrity

**Branch:** `refactor/looper-ownership-2026-08-30`
**Reviewer:** fresh-context adversarial, dimension = *the tests themselves*
**Date:** 2026-08-30
**Governed by:** [`CHARTER-looper-ownership-2026-08-30.md`](CHARTER-looper-ownership-2026-08-30.md) §2
**Suite state at review:** `1597 passed, 3 skipped, 106 subtests` in 52.31 s — verified, not taken on trust.
**Scope:** 24 test files, 6,146 lines, **445 test functions**.

---

## 0. The verdict in one paragraph

The green is mostly real. This is a better suite than the charter's framing led
me to expect: `test_slot_runtime.py`, `test_apc_link.py`, `test_looper_health.py`
and `test_multiclip_workflow.py` contain genuinely adversarial tests that encode
canon, cite the incident that produced them, and would fail on the bug they are
named for. Several files openly document their own blind spots, which is the
behaviour `DECISIONS.md` asks for and I am not going to pretend is common.

But the net has **two specific holes that matter to this refactor**, and they
are not random:

1. **The one LED with two writers is the one LED the tests exclude from their
   fixture.** Two surface harnesses configure a seven-button scene column that
   production cannot produce. The eighth button — `0x59`, the note that both
   `TransportButtonLeds` and `SlotSurface` write — is absent from every
   scene-LED test. Spec §2 D2's race is invisible **by construction**, and I
   demonstrated it executably below.
2. **Nothing tests `device_facts.py`.** Zero tests import it. `Fact.refuse_with()`
   — written specifically so rule 4 would be "executable rather than
   aspirational" — has never been executed by a test. Meanwhile five production
   modules cite a fact id that **does not exist**.

Add to that: **47 commits since 2026-08-20 changed a looper production module
and its tests in the same commit.** That is nearly every commit in the window.
The suite is therefore a near-complete record of what each session decided to
write, and a very thin source of independent evidence. Mitch's framing is
correct, and understated.

---

## 1. Method

Every assertion was judged against the canon stack, never against the code:

| Rank | Source | Used for |
|---|---|---|
| 1 | `device_facts.py` @ MEASURED/OWNER | panel behaviour, LED colours, channel response |
| 2 | `DECISIONS.md` | intent-expires, absent-instrumentation, `pause_on` idempotence, CPU |
| 3 | `Documents/specs/*.md`, later date wins | timing model (2026-08-30), loop seam (2026-08-18), APC architecture (2026-08-29) |
| 4 | `README`, `AGENTS.md` | operating knowledge |
| 5 | the code | last |

Where I could make a finding executable rather than argumentative, I did. Two
scratch harnesses were written and run (lint evasion; the LED race). **No
product code or test was modified.**

### A canon conflict found on the way in, worth recording

`Documents/specs/apc-control-surface-architecture-spec.md` §3.3 lists scene and
track button colours as **UNKNOWN — VENDOR, unmeasured**. `device_facts.py`
records `apc.scene.led_observed` and `apc.track.led_observed` as **MEASURED
2026-08-29** (scene = green, track = red, velocity 2 = blink, everything else
solid). Rank 1 beats rank 3: the spec table is one day stale. The charter §6
repeats the spec's "stays UNKNOWN" line. **They are measured.** This does not
change any test verdict below, but the next reader should not be told to
re-probe something already probed in five rounds with a positive control.

---

## 2. Bucket counts

| Bucket | Count | Basis |
|---|---:|---|
| **A — pins correct behaviour** | **~380** | residual after named C/D; spot-checked against canon per file |
| **B — pins WRONG behaviour** | **3** (+1 referred to Mitch) | §7 |
| **C — vacuous** | **31** | §4, each named with file:line |
| **D — implementation-coupled** | **~48 tests / 94 private touches / 6 clock-list mocks** | §5 |
| **E — missing** | **11 items** | §8 |

C and D overlap by design in three cases (the grep-as-tests are both), counted
once, in C.

### Per-file health

| File | Tests | Verdict |
|---|---:|---|
| `test_slot_runtime.py` | 35 | **Strongest in the suite.** Canon-faithful, encodes Mitch's own words. 1 C, heavy D. |
| `test_apc_link.py` | 12 | **Excellent.** `test_a_reopen_that_lies_is_not_believed` is DECISIONS 2026-08-15 rules 3–4 executed. 0 C. |
| `test_looper_health.py` | 20 | **Excellent.** `MeterXrunCounterTests` is rule 1 ("absent must look absent") as a test class. `test_poll_does_not_fork` is a better CPU guard than the AST lint. 0 C. |
| `test_multiclip_workflow.py` | 26 | Strong intent, self-aware docstring. Undermined by a 7-note scene fixture and a quantize mismatch. |
| `test_slot_leds.py` | 14 | Good invariant work; 1 C, 1 dead-vocabulary leftover. |
| `test_apc_mode.py` | 11 | Good, provenance-carrying. |
| `test_midi_golden_apc.py` | 9 | Good, with one over-claiming name. |
| `test_loop_mix.py` | 35 | Solid; 2 truthiness-only. |
| `test_loop_model.py` | 33 | Pure-function tests, canon-aligned. 0 C. |
| `test_apc_grid.py` | 20 | Clean geometry tests. |
| `test_slot_surface.py` | 24 | **4 C findings and the headline B.** |
| `test_apc_transport.py` | 28 | **2 airtight C, heavy clock-mock D.** |
| `test_track_gesture.py` | 33 | Good coverage, 1 C, heaviest private-attr use. |
| `test_multigrid_gesture.py` | 2 | 1 of 2 is a mock-call assertion. |
| `test_mixer_controls.py` | 10 | Out of charter scope; contains a pure C. |
| `test_looper_session.py` | 15 | **~11 of 15 are `assertIn(literal, source.read_text())`.** Worst file. |
| `test_periodic_loop_lint.py` | 4 | Passes; the lint under it does not do what the test's name claims. §6. |
| `test_gesture_router.py` | 8 | Well-formed; **out of scope** (touch browser, charter §4 "Out"). |

---

## 3. The two flagged files, answered

### 3.1 `test_midi_golden_apc.py` — provenance is recorded, and in the wrong place

**Does it encode real device behaviour?** Yes. `tests/fixtures/apc-mini-mk2-notes-2026-08-28.jsonl`, 187 lines, timestamped, committed in `dda2b1b`. Provenance is stated in the module docstring (ALSA port 32:1, 2026-08-28, hand-played) and in `docs/CLASSIC-MIDI-PLAN.md` §5 OPEN-4, which is itself a model of honest correction. **This is not a vendor-tier fact wearing a test's clothes.**

It is also the best-defended fixture in the repo: `test_fixture_is_the_capture_we_recorded` (line 66) exists specifically so that "a silently truncated file would make every assertion below pass vacuously." Credit where due — that is the failure shape this review exists to hunt, pre-empted by the author.

**Two real problems:**

- **The device facts it discovered live only here.** The capture establishes, at
  MEASURED tier: the grid emits **velocity 127 always** (no velocity
  sensitivity), notes 36–96 on ch 1 in Notes mode, and — the interesting one —
  **the APC double-strikes: `note_on` twice and `note_off` twice for the same
  press, 7 times in 187 messages.** None of that is in `device_facts.py`. That
  is rule 1 ("recorded HERE, once") broken, in the exact way the file's preamble
  describes. Same for `test_apc_mode.py:20` `NOTES_SYSEX`, captured verbatim
  2026-08-28, also absent from `device_facts.py`.
- **One name over-claims.** `test_note_numbers_and_velocities_pass_through_unaltered`
  (line 105) asserts `src_on == dst_on` over `(note, velocity)` pairs. Every
  velocity in the fixture is 127 — the file's own guard asserts exactly that at
  line 74. So the velocity half of that assertion **cannot fail**: a translator
  that hard-coded 127 passes. The note-number half is real. Listed in C as a
  half-vacuous assertion, not a bad test.

### 3.2 `test_periodic_loop_lint.py` — the lint misses 10 of 12 evasions I tried

The test passes. The lint under it does not enforce DECISIONS 2026-08-18. I ran
twelve subprocess-in-a-loop shapes through `lint_source()`:

```
MISSED  A: import subprocess as sp
MISSED  B: from subprocess import run
MISSED  C: argv hoisted out of the call  (CMD = [...]; subprocess.run(CMD))
MISSED  D: os.system("jack_lsp -c")
MISSED  E: subprocess.run(["python3", "-c", ...])   <-- the 400 ms cost DECISIONS names
MISSED  F: for _ in itertools.count()
MISSED  G: while not self._stop:
MISSED  H: threading.Timer periodic callback (no loop node at all)
MISSED  I: Clock.schedule_interval(tick, 5.0)
MISSED  J: cross-module helper (loop here, fork in another module)
CAUGHT  K: subprocess.run(["jack_lsp","-c"]) in while True   [control]
CAUGHT  L: self._probe() -> subprocess.run(["jack_lsp"])     [intra-file]
```

Case **E** is the indictment. `DECISIONS.md` § 2026-08-18 measures the cost as
*"a `python3` fork is ~400 ms on the Pi"* — and a `python3` fork inside
`while True:` is **not flagged**, because `_subprocess_in_node` only fires when
a string literal in the call matches `jack_lsp | journalctl | jack_cpu_load |
pgrep`. Cases A and B mean **one import statement disables the lint entirely.**

H and I are theoretical here — I checked, no `schedule_interval` / `Timer` /
`after()` periodic callback exists in `scripts/` or `patch_browser/` today.
Recorded so they are known before someone adds one.

**The coverage list is the bigger hole.** `PERIODIC_LOOP_MODULES` is a
hand-maintained tuple of nine files. `scripts/sooperlooper-apc-bench.py` is
**not in it** — and its `while True:` at line 583 runs at `time.sleep(0.002)`,
making it the hottest periodic loop in the entire product. It is clean today (I
linted it directly: 0 findings) and completely unprotected tomorrow. Also
outside: `sl-health.py` (2 loops), `mpe-pressure-remap.py`, `sl_bench_listener.py`.
No test asserts the list is complete.

`test_sl_watchdog_module_passes` (line 74) is a strict subset of
`test_production_modules_have_no_jack_or_journal_forks_in_loops` — `sl-watchdog.py`
is already in `PERIODIC_LOOP_MODULES`. Harmless, but it is not the second
opinion it looks like. And `_lint_source_loop_body_only` is dead: nothing calls it.

**Contrast:** `test_looper_health.py::test_poll_does_not_fork` patches
`subprocess.Popen`/`run` and asserts `call_count == 0` over 20 real polls. That
catches every one of A–E, at runtime, with no AST. It is the pattern the lint
should follow.

---

## 4. Bucket C — vacuous (31)

**Airtight, in descending order of how much they mislead:**

| # | Location | Assertion | Why it cannot fail |
|---|---|---|---|
| C1 | `test_apc_transport.py:374` | `self.assertIn(first, (True, False))` | `accelerating_hold_blink_on` returns `bool \| None`. This asserts "not None". Test is named `test_blinks_after_delay`; **it passes if the function never blinks.** |
| C2 | `test_slot_surface.py:423` | `self.assertEqual(state, self.surface.track_state(track))` | `track_states()` is literally `{i: self.track_state(i) for i in range(...)}` (`slot_surface.py:203-205`). A mathematical tautology. |
| C3 | `test_apc_transport.py:263` | `self.assertIn(self.sent[-1][2], (SCENE_LED_ON, SCENE_LED_OFF))` | `= (1, 0)`. The only other legal velocity is 2. Test is `test_mk1_both_held_blinks_stop_all_only`; **it asserts nothing about blinking** and passes with a constant-off implementation. |
| C4 | `test_track_gesture.py:474` | `self.assertIn(calls[-1], (0, 3))` | `LED_OFF, LED_RED` — both blink phases. `test_hold_blink_starts_after_blink_start_s` accepts either. Same shape as C1/C3. |
| C5 | `test_mixer_controls.py:118-121` | `assertEqual(_handle_y(...), bottom)` | `_handle_y` (line 27) is a **verbatim copy** of `patch_browser/touch_browser_mixer.py:41-46 _value_to_handle_y`. The test asserts a test-local function against test-local arithmetic. **Production code is never called.** Would pass if the production fader were deleted. |
| C6 | `test_slot_leds.py:170-179` | `test_a_bank_change_forces_a_full_repaint` | Calls `matrix_messages(moved, ..., previous=None)` — so of course 64 messages. Its docstring describes diffing against the **old bank**, which it never does. Duplicates `test_first_paint_covers_every_visible_pad`. `assertNotEqual(state, {})` is filler. |
| C7 | `test_slot_surface.py:210` | `assertGreaterEqual(len(self.out.sent) - before, 64)` | Counts messages after `set_view`. A bank change that repainted all 64 pads **with the wrong bank's colours** passes. Bank changes are a priority-E area and this is their only caller-level test. |
| C8 | `test_slot_surface.py:215` | `assertGreaterEqual(len(dark), 64)` | Counts LED_OFF messages, not the **set of notes**. 64 dark writes to one note passes. |
| C9 | `test_slot_surface.py:407` | `self._sl_states_is_stale = True` | Sets a dead attribute **on the TestCase**. `surface._sl_states` is never written anywhere in the file. `OneStateSourceTests`, whose whole purpose is "a stale cache must not win", **never constructs a stale cache.** It catches a total regression only by accident of the `SL_STATE_OFF` default. |
| C10 | `test_apc_bench.py:110-116` | `assertIn("poll_track_gestures(gestures, multigrid=multigrid)", source)` | **Grep-as-a-test.** Passes if the string is in a comment; fails on a variable rename with identical behaviour. The only test of bench idle-loop wiring. |
| C11 | `test_slot_runtime.py:340-349` | `assertNotIn('["mute_off"]', source)` | Grep-as-a-test, evaded by `('mute_off',)` or a variable. Also uses **relative** `Path("scripts/sooperlooper/slot_runtime.py")` — raises if pytest runs from any other cwd. |
| C12–C22 | `test_looper_session.py` (~11 of 15 tests) | `assertIn("threading.Thread", mod)`, `assertIn("daemon=False", mod)`, `assertIn("os._exit(1)", mod)`, `assertNotIn("bench_argv or None", text)`, … | **21 assertions whose haystack is production source text.** None execute the code. `assertIn("daemon=False", mod)` passes if the string is in a docstring. Worst file in the suite by this measure. |
| C23-24 | `test_loop_mix.py` `test_master_is_not_gated_by_column_pickup`, `test_master_is_exempt_from_pickup` | `assertTrue(LoopMix().messages_for(MASTER, N))` | Two near-identical tests asserting only non-emptiness. No value is checked. |
| C25-26 | `test_multigrid_gesture.py:22,27` | `fs.poll_hold.assert_not_called()` / `fs.poll_led.assert_called_once()` | Both real methods replaced by `MagicMock`. Asserts a dispatcher calls methods; cannot see whether any LED stayed unpainted. |
| C27 | `test_slot_surface.py:~340` | `self.assertTrue(sink, "…produced no engine command at all")` | Truthiness only — a tap sending the *wrong* command passes. Credited: it does catch the specific 200 ms debounce regression it was written for. |
| C28 | `test_apc_transport.py:~284` | `assertEqual(blinks[-1][2], SCENE_LED_ON, "held = lit")` | `test_the_clear_hold_blinks_stop_all_on_both_models` never advances the clock or calls `poll()`, so no blink phase is computed. Its real content duplicates `test_mk1_stop_all_alone_lights_scene_green`. |
| C29 | `test_apc_leds.py:47-57` | `assertEqual(translate([...], "mk2")[2], apc_leds.MK2_GREEN)` | Asserts the module's output equals the module's own constants. The palette **index** is not in `device_facts` at MEASURED tier, so nothing anchors it to the device. If `MK2_GREEN` were blue, this passes. |
| C30 | `test_midi_golden_apc.py:105-113` | velocity half of `assertEqual(src_on, dst_on)` | Every fixture velocity is 127 (asserted at line 74), so "velocities pass through unaltered" is unfalsifiable. Note-number half is real. |
| C31 | `test_multiclip_workflow.py:~305` | `if destroyers: assertLess(...)` | **Conditional assertion.** The ordering invariant only runs when a destroyer command happens to appear; it currently does not. Honestly documented in-place, which is why it is listed last — but structurally the guarantee is unchecked. |

**Also found, not counted as C:** three files place
`if __name__ == "__main__": unittest.main()` **mid-file** —
`test_track_gesture.py:510` (before `OverdubOnePassTests`),
`test_apc_grid.py:104` (before `CellGeometryTests`),
`test_multiclip_workflow.py:422` (before `StopAllThenLaunchTests`). pytest
collects them fine (verified); direct `python tests/test_x.py` silently skips
every class below the guard. Cosmetic today, a trap for anyone debugging one file.

---

## 5. Bucket D — implementation-coupled (the refactor tax, priced)

These are correct today and will break on an honest refactor for reasons
unrelated to behaviour. Enumerated so the cost is known before Stage 1 starts.

| Cost | Measure | Which stage collects it |
|---|---|---|
| **Private-attribute assertions** | **94 touches across 11 files** — `_pad_down_at` (25×), `_tracks[i] = …` (direct model assignment), `_stop_queued`, `_pending`, `_pending_since`, `_led_transition`, `_wait_since`, `_osc`, `_midi_out`, `_tail`, `_note`, `_painted`, `_scene_painted`, `_last_vel`, `_send`, `_save_timeout_s`, `_hold_s`, `_clear_loop()`, `_launch()`, `_waiting_for_quantize()` | **4, 5** — any ownership move renames these |
| Worst offenders | `test_slot_runtime.py` 27, `test_track_gesture.py` 25, `test_slot_surface.py` 14, `test_looper_songs.py` 8, `test_apc_bench.py` 6 | |
| **Exact-length clock `side_effect` lists** | **6** in `test_apc_transport.py` (e.g. `side_effect=[10.0, 12.9, 13.0, 14.0]` at `ShiftHoldComboTests`) | **5** — adding or removing one `time.monotonic()` call raises `StopIteration`, failing a behaviourally-correct change. `test_gesture_against_engine.py` shows the right pattern: an injected `now=lambda:` clock. |
| **Note literals imported from `apc_transport`** | `test_apc_transport.py:6-22` imports `NOTE_TRACK8_MK2`, `MK1_TRACK_OVERLAP_NOTES`, `SCENE_LAUNCH_NOTES_MK1`, `NOTE_SHIFT_MK1/MK2`, `NOTE_STOP_ALL_CLIPS_MK1/MK2` | **1** — spec D1 says these three should never have been in `apc_transport`. The test cements the violation. Repoint at the registry. |
| **Direct `midi_out.send_message` assertions** | `test_slot_surface.py`, `test_slot_leds.py`, `test_apc_transport.py`, `test_apc_leds.py` — ~25 tests read the wire directly | **2** — after the compositor, owners submit *desired state*; nothing writes the wire but the compositor. Every one of these needs repointing. |
| **Diff-cache assertions** | `test_slot_surface.py:196` (`test_repaint_is_quiet_when_nothing_changed`), `test_slot_leds.py` `MatrixMessageTests` (4 tests, `previous=`/`state` threading) | **2** — spec §5.3 explicitly deletes `_last_vel` and `_scene_painted` "in favour of one diff." The *behaviour* (no redundant writes) should survive; the *plumbing* these assert will not. |
| **Grep-as-tests** | C10, C11, C12–C22 — **~13 tests** | **all stages** — they fail on renames and reformats and pass on comments. Highest breakage-per-value in the suite. |
| **Wall-clock timing** | `test_slot_runtime.py` `test_the_press_does_not_sleep` — real `time.monotonic()`, 0.05 s budget | flake risk on a loaded machine or the Pi; intent is sound, the instrument is not |

**Total D-flavoured tests: ~48.** None should be deleted. All will need touching,
and every touch needs the charter §2 comment naming the canon it now conforms to.

---

## 6. What `e0ffeae` fixed, and the siblings it missed

`e0ffeae test(slots): make the harness able to see the bugs that shipped` fixed
four real blind spots — `load_loop`/`save_loop` invisible to `FakeSlEngine`
including the arity rule, `quantized=False` in the shared gesture harness, no
Stop All + launch coverage in either direction, and dead `ACT_STOP` /
`ACT_CLOSE` / `PENDING_STOP` vocabulary. It verified its own launch test honestly
("fails when `LAUNCH_COMMANDS` is reverted to `mute_off`") and labelled the
companion test as the *gesture* path so it could not be mistaken for launch
coverage. That is exemplary work and I want it on the record before the
criticism.

**Three siblings it missed:**

1. **Dead vocabulary survived in `test_slot_leds.py:120`** —
   `Pending("stop", from_slot=1)`, in `ColumnInvariantTests`' four-way pending
   cross-product. `PENDING_STOP` was deleted from `slot_matrix.py` by this very
   commit (only `PENDING_LAUNCH = "launch"` and `PENDING_SWITCH = "switch"`
   remain, lines 42-43). `Pending` accepts any string, so it still passes —
   **a quarter of that cross-product exercises a state the system cannot enter.**
2. **`debounce_ms=0` is still the suite-wide default.** The commit flipped
   `quantized`; it did not touch debounce. `test_slot_surface.py`'s shared
   `build_track_gestures` (line ~55) still passes `debounce_ms=0`, and the
   appliance runs `MPE_APC_DEBOUNCE_MS=200`
   (`sooperlooper-apc-bench.py:111`). `SceneLaunchSurvivesTheRealDebounce`
   names this precisely — *"Every other harness in this suite uses
   debounce_ms=0, the single value at which the defect cannot appear, so it
   shipped green"* — and then fixes exactly two tests. **The other ~50 tests
   built on that harness, including all 26 in `test_multiclip_workflow.py`,
   still run at the one value that hides the bug class.**
3. **A quantize mismatch was introduced, not removed.**
   `test_multiclip_workflow.py:59` builds `FakeSlEngine(quantized=False)` while
   its gestures come from `test_slot_surface.build_track_gestures`, which sets
   `quantized=True`. Production has only two states: pre-grid
   (`quantized=grid_active` → False on **both** sides) and post-grid (True on
   both). **Gesture-quantized + engine-free is not a configuration the appliance
   can be in.** `QuantizedSessionTests` flips the engine later and is correct;
   every class above it runs the impossible combination.

---

## 7. Bucket B — tests that must change

Three findings. Each is airtight: canon citation, contradicting artefact,
executable proof where possible. A fourth is **referred to Mitch, not
prescribed**, and is deliberately kept out of B.

### B1 — the seven-button scene column *(headline)*

**Location:** `tests/test_slot_surface.py:288-293` and `tests/test_multiclip_workflow.py:81`

```python
# test_slot_surface.py:288
self.surface._scene_launch_notes = tuple(range(0x52, 0x59))
# Row 0 has NO scene button — Stop All Clips (0x59) occupies that
# position on the panel. The lowest scene launcher, 0x58, is beside
# row 1, so that is the row these tests drive.
self.scene_row = 1
self.scene_note = 0x58
```

```python
# test_multiclip_workflow.py:81
scene_launch_notes=tuple(range(0x52, 0x59)),
```

**Canon violated — three sources, all agreeing against it:**

- **`scripts/sooperlooper/apc_panel.py:70-85`**, with *import-time assertions*:
  `SCENE_COLUMN_MK1 = tuple(range(0x52, 0x5A))` (**eight** notes),
  `assert len(SCENE_COLUMN_MK1) == GRID_ROWS, "one scene button per grid row"`,
  `assert SCENE_COLUMN_MK1[-1] == NOTE_STOP_ALL_CLIPS_MK1`.
- **`a2da7a7` "canonical panel map; bottom scene button restored"** — the *later*
  of two same-day commits, which explicitly retracts the earlier one:
  *"The scene column has eight buttons (0x52..0x59), not seven. The bottom one
  was omitted because it doubles as Stop All Clips, **which silenced scene row 0
  outright** — and the off-by-one that produced was rediscovered three separate
  times."* The comment at line 289 is verbatim the reasoning of the retracted
  commit `8c4aee6` (*"row 0 has none"*).
- **`apc-control-surface-architecture-spec.md` §5.2**, which lists as a binding
  row: `Binding(control="stop_all", layer=BASE, action="scene_launch_row_0")`.

**Direct contradiction inside the suite:** `tests/test_apc_transport.py:59-63`
asserts the opposite and is right —

```python
self.assertIn(NOTE_STOP_ALL_CLIPS_MK1, resolve_scene_launch_notes("mk1"))
self.assertEqual(len(resolve_scene_launch_notes("mk1")), 8)
```

**Why this is the most dangerous test in the suite.** Production wires
`SlotSurface(scene_launch_notes=resolve_scene_launch_notes(label))` →
**8 notes** (`sooperlooper-apc-bench.py:367-377`). Both surface harnesses
override that to **7**. Note `0x59` is therefore in **no** scene-LED test — and
`0x59` is precisely the note that `TransportButtonLeds` writes as
`self._stop_all_note` **and** `SlotSurface.repaint_scenes` writes as scene row 0.
It is the single most contested LED on the panel, and it is the one the fixture
removes.

**The race, demonstrated executably** (scratch script, product code untouched):

```
scene notes: ['0x52' … '0x59'] count: 8
after transport init, device shows note 0x55 = 0
surface paints it ON -> 1
transport clear_unwired_surfaces emitted 0 messages for that note: []
DEVICE STILL SHOWS: 1   <-- transport wanted 0
transport's private cache thinks it is: 0
```

`TransportButtonLeds._last_vel[0x55] == 0` while the device shows `1`, and
transport will **never** correct it, because its own cache suppresses the write
(`apc_transport.py:527-529`). `SlotSurface._scene_painted` is a separate cache
that knows nothing of it. This is spec §2 **D2** exactly — *"Two writers, one
LED, and which one you see depends on call order"* — made **sticky** by two
independent diffs. It applies to **all eight** scene notes, not just `0x59`.

**What it should assert instead.** Delete both overrides; build the fixture from
`resolve_scene_launch_notes(label)` so the harness cannot drift from production.
Then `scene_row = 0`, `scene_note = 0x59`, and add the two-writer test in E1.

---

### B2 — the ring-out threshold the suite cannot see

**Location:** `scripts/sooperlooper/tail_phase.py:52` — and **no test asserts it**.

```python
TAIL_RATIO = float(os.environ.get("MPE_SL_TAIL_RATIO", "0.032"))
```

**Canon violated:** `Documents/specs/looper-timing-model-spec.md` §6, dated
**2026-08-30**, `Status: Intended behaviour, agreed with Mitch 2026-08-30.
Implemented.`:

> **decay** — the input has fallen to `MPE_SL_TAIL_RATIO` (**0.01, −40 dB**) of *this tail's own peak*

`looper-loop-seam-spec.md` says `0.032` / −30 dB, but is dated **2026-08-18**.
Charter §2 rank 3: *"Where two specs disagree, the later date wins and the older
one gets corrected."* The later spec says `0.01`; the code says `0.032`; the
later spec claims it is **implemented**, and it is not.

**Why this is a test finding and not just a code finding:**
`tests/test_tail_phase.py` injects `ratio=0.1` explicitly at lines 33, 143, 147,
175, 185 — **every single time**. The module default is never exercised. A
constant that §6 says was chosen from measured trace data
(`MPE_SL_TAIL_TRACE`, a real ring-out peaking at 0.0487) is a free variable the
suite has no opinion about, and a 3.2× discrepancy between spec and code
produced no red.

**What should be asserted:** one test pinning
`tail_phase.TAIL_RATIO == 0.01` with a comment citing
`looper-timing-model-spec.md` §6 — *after* someone rules on which number is
right. If `0.032` is correct, §6 is the defect and must be corrected in place
with both dates visible (`device_facts` rule 5 discipline applied to specs).
**Do not silently change the code to match the spec, or the spec to match the
code.** This one needs a human.

---

### B3 — five production modules cite a fact that does not exist

**Location:** `led_table.py:45-46`, `apc_leds.py:31`, `slot_matrix.py:330`,
`apc_transport.py:368`, `probe-apc-buttons.py:10` — and **no test guards it**.

```python
# scripts/sooperlooper/led_table.py:44-47
# What we currently SEND to the side buttons. Not a statement about what
# they can show: see `device_facts.apc.scene.led_colours` and
# `.apc.track.led_colours`, both still resting on a vendor document that has
# already been wrong once about this panel. Measure before promising a colour.
```

Verified by execution:

```
IDS: ['apc.buttons.all_have_leds', 'apc.buttons.channel_response',
      'apc.buttons.single_colour', 'apc.grid.mk2_encoding',
      'apc.probe.positive_control', 'apc.scene.led_observed',
      'apc.scene_column.bottom_is_0x59', 'apc.shift.led', 'apc.track.led_observed']
KeyError -> apc.scene.led_colours
KeyError -> apc.track.led_colours
```

**Two failures, both canon violations:**

1. **The ids do not resolve.** `device_facts.fact("apc.scene.led_colours")`
   raises `KeyError`. Five citations, all dead.
2. **The claim attached to them is wrong at the tier that matters.** All five say
   "vendor-tier and unmeasured." The real facts —
   `apc.scene.led_observed` and `apc.track.led_observed` — are **MEASURED
   2026-08-29**, from five probe rounds with a positive control
   (`apc.probe.positive_control`). `device_facts` rule 3: *"MEASURED and OWNER
   outrank VENDOR and INFERRED. Always."*

This is `device_facts` rule 1 broken exactly as the module's own preamble warns:
*"Other modules cite its id in a comment. They do not restate it — **five
restatements is how this happened**."* Five files. Again.

**What should exist:** a test that extracts every `device_facts.<id>` citation
from `scripts/**/*.py` and asserts each resolves in `FACTS` — the cheapest
possible guard, and one the build can fail. See E2.

---

### Referred to Mitch, deliberately NOT in Bucket B — the scene-button blink

`tests/test_slot_matrix.py:242-252`:

```python
self.assertEqual(scene_row_led(playing, 0, sl_states=states), SCENE_LED_BLINK)
idle = {i: SL_STATE_OFF for i in playing}
self.assertEqual(scene_row_led(playing, 0, sl_states=idle), SCENE_LED_ON)
```

A **fully playing** (confirmed, settled) row **blinks**; an **idle** row is
**solid**. On the pads, `led_table.py`'s central rule and `DECISIONS.md`
2026-08-15 rule 6 are the inverse: *"Solid means confirmed; blinking means
requested."* So a blink means two opposite things depending on which region of
the surface you look at.

**I am not calling this B.** §L's rule is stated about the pad and framed around
`pending` vs `sl_state`; the scene button has no `pending` concept, so the rule
is arguably out of scope rather than violated. And charter §6 and spec §8 both
say plainly that whether a cue is *good UI* is Mitch's eye, not ours. The
hardware permits a consistent alternative (off = empty, **solid = fully
playing**, blink = partial), so it is a free choice, not a constraint.

**Flagged, not prescribed.** Worth thirty seconds of Mitch's attention; not
worth a refactor changing a test on my say-so.

---

## 8. Bucket E — missing coverage, prioritised

**E1 — the two-writer LED race. Nothing tests it; the fixture prevents it. (P0)**
Spec §2 D2, §5.3. Proven live in B1. Two writers, two independent diff caches
(`_last_vel`, `_scene_painted`), and the loser's cache guarantees it never
re-asserts. **Test:** instantiate `TransportButtonLeds` *and* `SlotSurface` over
the **same 8-note** `resolve_scene_launch_notes(label)` and one shared MIDI
sink; interleave `clear_unwired_surfaces()` and `repaint_scenes()`; assert the
sink's final value per note matches the declared owner's intent **in both
orders**. This test must fail today. It is the single most valuable test that
does not exist, and after Stage 2/3 it becomes the compositor's acceptance test.

**E2 — `device_facts.py` has no test at all. (P0)**
Zero tests import it. Three cheap, high-value tests:
(a) every `device_facts.<id>` citation in `scripts/**/*.py` resolves in `FACTS`
— fails today, five times (B3);
(b) `Fact.refuse_with()` raises `NotMeasured` for VENDOR/INFERRED and is silent
for MEASURED/OWNER — rule 4 is currently aspirational, which is the exact word
the spec uses for what it was meant to stop being;
(c) every `Fact` carries a non-empty `tier` in the known set and a parseable ISO
`established` — rule 2 ("a fact with no tier and no date is not a fact").

**E3 — reconnect / reset state consistency. (P0)**
`sooperlooper-apc-bench.py:495-512` `reopen_apc()` is the whole
recovery path — `midi_out.reset()`, blank 64 pads, `apply_view`,
`slot_surface.repaint(force=True)`, `repaint_scenes(force=True)`,
`transport_leds.repaint()`. **None of it is tested.** `test_apc_link.py` tests
`LinkHealth` against a lambda `on_lost`; nothing asserts a recovered link
produces a full repaint of pads **and** scenes **and** transport. The failure
mode is silent and known: the device returns dark while three caches say lit, so
every subsequent write is suppressed as a no-op. `TransportButtonLeds.repaint()`
documents this in its own docstring; nothing proves the caller invokes it.

**E4 — the shipped defaults of the tail constants. (P1)**
B2. `TAIL_RATIO`, `TAIL_FLOOR`, `TAIL_HOLD_S`, `SILENT_GRACE_S`, `cap` are all
injected in tests and never asserted at their module defaults. Spec §6 names
four exit conditions (decay / cap / wrap / silent); pin the constants against it.

**E5 — spec §5.5's three invariant tests. All three absent. (P1)**
Nothing enforces (a) no note literal outside the registry — `apc_transport.py`
still defines `NOTE_TRACK8_MK2` etc. in violation of `apc_panel`'s Rule 2, which
is D1 and the reason the charter says *"a rule a build cannot fail is not a
rule"*; (b) no button LED write outside the compositor; (c) every colour request
within the control's declared capability. (c) is now cheap and grounded:
`apc.buttons.single_colour` is MEASURED — scene/track buttons have exactly three
states, `0`/`1`/`2`. A test asserting no scene or track note is ever sent a
velocity outside `{0,1,2}` is writable **today**, before the registry lands.

**E6 — expiry of intent, only half-covered. (P1)**
`test_gesture_against_engine.py:150` covers `PENDING_TIMEOUT_S` for one gesture,
and `test_slot_runtime.py` `StrandedSwitchTests` covers
`DEFERRED_LAUNCH_GRACE_S` well. Not covered: what happens when **both** expire
in the same poll; what expires on a **bank change** while intent is pending on
an off-screen track; and whether `QUANTIZE_WAIT_TIMEOUT_S`,
`PENDING_TIMEOUT_S` and `DEFERRED_LAUNCH_GRACE_S` are ordered such that they
cannot fire in a contradictory sequence.

**E7 — Stop All → phase zero. (P1)**
Timing spec §5: Stop All *"pauses every loop, **resets the grid phase to zero**,
and keeps the grid."* `test_track_gesture.py` `StopAllIsImmediateTests` asserts
the OSC hits (`mute_on`, `pause_on`) and the `mute_quantized` lift/restore —
correctly. **Nothing asserts the phase reset.** `mark_phase_zero` is tested only
on the start-into-silence path (`test_slot_runtime.py`
`StartingIntoSilenceTests`), never on the Stop All path.

**E8 — phase re-anchor beyond the happy path. (P2)**
`test_track_gesture.py` `test_grid_anchor_defers_until_loop_wrap` covers the
late-PLAYING case well. Not covered: a re-anchor arriving while a **second**
track is mid-take; a re-anchor when `loop_pos` updates stop entirely (the
`StrandedSwitchTests` premise, applied to phase rather than to switching).

**E9 — bank changes assert counts, never content. (P2)**
C6 + C7. Neither the caller-level (`set_view`) nor the function-level
(`matrix_messages`) bank test checks that the 64 repainted pads carry the **new
bank's** colours. Add one that banks with distinguishable per-track state and
asserts the resulting note→colour map equals the expected map for the new
offset. Also missing: a bank change while a switch is **pending** on an
off-screen track.

**E10 — the debounce blind spot, closed for two tests out of ~50. (P2)**
§6 item 2. Either flip the shared `build_track_gestures` to `debounce_ms=200`
(the appliance value) and fix what falls over, or parametrise the harness across
`{0, 200}`. Leaving one class as the sole 200 ms citizen means the next
debounce-window bug ships exactly the way the last one did.

**E11 — the lint's own coverage list. (P2)**
§3.2. A test asserting `PERIODIC_LOOP_MODULES` contains every module under
`scripts/` and `patch_browser/` that has a loop `_is_periodic_loop` recognises.
Today `sooperlooper-apc-bench.py` — the hottest loop in the product, 2 ms
cadence — is outside the list with nothing to keep it there. Separately, widen
`_subprocess_in_node` to flag **any** fork (not four hard-coded needles) and
resolve aliased/`from`-imported `subprocess`, or replace the AST lint with the
runtime-patch pattern that `test_looper_health.py::test_poll_does_not_fork`
already proves works.

---

## 9. Independence of evidence

**47 commits between 2026-08-20 and 2026-08-30 modified both a looper production
module and a test in `tests/`.** That is close to every commit touching this
subsystem in the window. Full list captured; the ones most load-bearing for this
review:

| Commit | Date | Note |
|---|---|---|
| `d06fb08` | 08-30 | "the cycle is the first take" — 3 prod, 1 test |
| `4ceca61` | 08-30 | "a clip started into silence is the downbeat" — 3 prod, 1 test |
| `b4446a3` | 08-30 | "the grid owns the clock, and outlives the clips" — 5 prod, 4 tests |
| `399c71e` | 08-29 | "end the ring-out relative to its own peak" — the commit that set `TAIL_RATIO`; see **B2** |
| `568f459` | 08-29 | "put the clear blink back on Stop All" — the commit that wrote **C3** and **C28** |
| `8c4aee6` → `a2da7a7` | 08-27 | **the same day, in opposite directions**, on the scene column; see **B1** |
| `2fc657e` | 08-28 | `LoopFootswitch` → `TrackGesture` rename — 10 prod, **15 tests** |
| `0f967c9` | 08-27 | "15 loops, not 16" — 21 prod, 5 tests |

The `8c4aee6` body is the most useful artefact I found all night, because it
names the failure mode outright: *"I got this wrong once already today, in the
opposite direction, by **'fixing' the tests to match the function's own
docstring**."* That is precisely how the seven-note fixture in **B1** was
created, and it is still in the tree three days later, in two files.

**Practical consequence for this refactor:** treat no assertion in these files as
independent corroboration of the code beside it. Where a test and its module
were born in the same commit, the test records a decision, not a verification.
The exceptions — tests that fail when their fix is reverted, and say so in the
commit body — are `e0ffeae`'s launch test and a handful in `test_slot_runtime.py`.
That habit is worth making universal: **a new test should state, in the commit
body, what it does when the fix is reverted.**

---

## 10. Credit, specifically

Not padding — these are the parts of the net that are load-bearing and should
survive the refactor unchanged in intent:

- `tests/fake_sl_engine.py` — holds quantized actions until `boundary()`, carries
  MEASURED provenance inline (`# MEASURED on the Pi, 2026-08-28, engine 9951,
  loop_len 0.803 s`), and its `_hit`/`boundary` comments explain what the fake
  used to get wrong and what that hid. It is the best artefact in the test tree.
- `test_slot_runtime.py::StartingIntoSilenceTests` — timing spec §4, with Mitch's
  words in the docstring, including the "other half of the bug" (session
  sounding, not track sounding).
- `test_apc_link.py::test_a_reopen_that_lies_is_not_believed` — DECISIONS
  2026-08-15 rules 3–4, executed.
- `test_looper_health.py::MeterXrunCounterTests` — five tests whose whole content
  is "absent must not read as zero." Rule 1, as a class.
- `test_slot_leds.py::ColumnInvariantTests` — a real cross-product invariant
  (≤1 green per column), not an example.
- `test_apc_bench.py::ViewAgreementTests` — names its own reason for existing:
  *"Every other test here checks one layer alone, so the bench forgetting to call
  `mix.set_view()` … passes all of them."*
- `test_multiclip_workflow.py`'s module docstring and its `deliver()` —
  change-only delivery, because *"a harness that pushes the current state every
  round is strictly more generous than the appliance."* Right, and rare.
- `test_midi_golden_apc.py::test_fixture_is_the_capture_we_recorded` — a fixture
  that guards itself against silent truncation.
- `test_apc_mode.py::test_undecoded_modes_are_reported_as_unknown_not_guessed` —
  refuses to label 0x00/0x02, because a wrong label *"would tell Mitch the grid
  should work when it cannot."*
- `test_slot_surface.py::SceneLaunchSurvivesTheRealDebounce` — the only test in
  the suite that indicts the rest of the suite. It should have gone further
  (§6.2), but it was right.

Only 3 of 24 files use call-count/`assert_called` assertions at all
(`test_looper_health.py`, `test_looper_songs.py`, `test_multigrid_gesture.py`),
and two of those uses are legitimate. For a suite of this size that is a good
result and the direct product of the `FakeSlEngine` decision in DECISIONS
2026-08-15.

---

## 11. Recommended order for the refactor

1. **Before Stage 1** — write E2(a). It fails immediately, costs ten lines, and
   closes B3. Write E5(c) too: `apc.buttons.single_colour` is MEASURED, so the
   capability test is grounded today and does not need the registry.
2. **Before Stage 2** — write E1 as a **failing** test. It is the acceptance
   criterion for the compositor, and having it red first is what proves the
   compositor did something. Fix B1's fixtures in the same commit; they are what
   is hiding it.
3. **During Stage 2** — expect the ~25 direct-`midi_out` assertions and the
   two diff-cache test groups to need repointing. Budgeted in §5.
4. **Before Stage 4** — settle B2 with Mitch (0.01 vs 0.032), then pin it.
5. **Any time** — E10. Flipping the shared harness to `debounce_ms=200` is a
   one-line change that will either pass or find a real bug, and either outcome
   is worth more than the current state.
6. **Do not** delete a Bucket-D test to make a stage green. Charter §2: no
   weakened assertion, no loosened tolerance, no skip. Repoint them, and name
   the canon in the commit body.

---

*Every claim in this review was checked against the tree at
`refactor/looper-ownership-2026-08-30`. Two findings (§3.2, §7 B1) were verified
by running scratch scripts against the real modules. No product code or test was
modified.*
