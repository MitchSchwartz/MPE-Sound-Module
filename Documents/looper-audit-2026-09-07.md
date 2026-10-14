# Looper audit, 2026-09-07

**This file describes no behaviour, and it will not be updated.** It is a dated
record of one audit, kept for the same reason a lab notebook is kept. If you
want to know what the looper does, read the code; the map at the bottom says
which file answers which question.

That is deliberate. This audit started as a prose behaviour contract with a
data mirror and a test that checked the two copies agreed. Mitch, reading it:

> *"So now we have three documents that could still come out of sync. One of
> them is a long description. One of them is tests, and one of them is the
> code. And two of the three of those have caused issues in the past that have
> confused the AI. So here, what we need is the code to be the documentation."*

He was right, and the mirror was the worst of the three: it restated facts that
already lived in the code, so its test could only ever prove that two copies of
one answer matched. Both files were deleted. What replaced them is
`scripts/sooperlooper/looper_timing.py`.

---

## What the audit read

All 23 files in `Documents/specs/`, all 1,113 lines of `Documents/DECISIONS.md`,
and the eleven modules that implement the looper.

## What it found

**Eleven places where two documents state opposite things** about the same
gesture. The load-bearing one: whether clearing every clip drops the grid.
`looper-timing-model-spec.md` §3 says only a track reset drops it and quotes
Mitch in support. That quote was about **Stop All** and was applied to
**clearing**. Mitch settled it on 2026-09-06: *"If we clear all clips, then the
grid should be cleared. If we stop all clips, that's different."* The code is
right and the spec is wrong. That rule has now been written three times.

**Five places where the code had diverged from every written account of it**,
including one the decision log states as current fact and has had backwards
since 2026-08-30: `eighth_per_cycle` is not fixed at 8. It is `8 × bars`, so
the quantize unit is the whole first take, not one bar of it.

**One rule applied to half the places that needed it.** In August the rule *a
silent session needs no count-in* was settled and applied to launching a clip.
It never reached recording one. After Stop All, with everything silent,
pressing record still counts in to a boundary nobody can hear. Measured on the
appliance, press to first recorded sample:

| Grid | Swallowed |
|---|---|
| none | 0.06 s, 0.07 s |
| 3.62 s cycle | 0.56 s, 0.57 s |
| 2.18 s cycle | 2.27 s |

That is the report that a column *"only records the after loop"*. The column was
never the variable.

## What was changed

Nothing about how the instrument sounds. Two things about where decisions live.

**The timing decision was moved into one module.** It had been made in five
places that could not see each other: the engine's quantize controls, the
launch branch, a `quantized` flag on every gesture, the Stop All burst, and a
five-second fallback. `looper_timing.when()` now answers for every action, and
`tests/test_looper_timing.py` refuses a sixth place.

**A flag that recorded the wrong thing was deleted.** Every gesture carried
`quantized`, set once at bench startup from `MPE_SL_SYNC_MODE` and never
updated. That is the mode the instrument booted in, not whether a grid exists.
`looper-transport-clock-spec.md` had named tracking the setting instead of the
state as *"the recurring bug shape"* before the flag was written.

## Two bugs the centralisation surfaced

Neither was findable by reading, because neither line was wrong on its own.

`settle_stop_all` restored `mute_quantized` to 1.0 unconditionally while
restoring `quantize` from the grid. `looper_songs.stop_playback` did the same
thing in a second file. So after a Stop All in a session with no grid, every
later per-clip stop was deferred to a cycle boundary that no tempo defined,
while `set_grid_active` believed it had turned exactly that off. Both now take
the value from `looper_timing.engine_controls`, which is the only thing that
produces it.

## What was done to every other document

The audit fixed the code and left 253 markdown files that a future reader could
still mistake for truth. That was half a job. So:

- **All 23 files in `Documents/specs/` carry a HISTORY stamp** at the top saying
  they do not describe the instrument and pointing here.
- **The four statements proved false carry an inline `CORRECTION`** naming what
  the code actually does and which test pins it. A stamp alone does not stop
  somebody quoting one specific sentence.
- **`DECISIONS.md` now opens by saying it is a log, not a description** — and
  says explicitly that an *uncorrected* row is not thereby current, because it
  took a full audit to find the four.
- **`DIRECTION.md` lost the title "read before looper work"**, which is what
  sent readers there first, three weeks after it was last touched.
- **`AGENTS.md` now sends an agent to the code**, naming the two authorities,
  instead of to the two documents that were most often wrong.
- **`README.md` no longer says "builder docs and specs are the source of
  truth"** while linking a spec for a pipeline deleted in `a99cf63`.
- **`docs/CODE-MAP.md` admits it is stale** and lists `looper_timing.py`.
- **`scripts/sooperlooper/README.md` says it is orientation, not authority.**

`Documents/reviews/` and `docs/measurements/` were left alone. Every filename
there carries a date and each reads as a record of one moment, which is what
they are.

`tests/test_docs_do_not_claim_authority.py` holds this in place: a new spec file
with no stamp fails, a stripped stamp fails, and so does an `AGENTS.md` that
stops naming the authorities. All four checked by breaking them.

## What was deleted rather than corrected

Stamping a file as history keeps it in the tree, where the next reader still
finds it. Where the honest answer was "this should not exist", it was removed.
Everything below is in `716468f` and recoverable; nothing was rewritten.

**Code — 734 lines, all with zero references at the time of removal:**

| File | Why |
|---|---|
| `slot_matrix_spike.py` | Finished experiment. Its two results were already quoted in `looper_songs.py`, which is where they belong. It was also the sole entry in the centralization guard's allowlist — deleting it deleted the exception. |
| `spike-load-halt.py` | Finished experiment, question answered. |
| `measure_midi_osc_latency.py` | Second instrument for a measurement the bench already takes (`--measure-latency`). |
| `set-input-latency.sh` | No reference in code, tests, config, or CLI. |

**Dead knobs inside live files:** `MPE_SL_SONGS_PORT` / `looper_songs.LISTEN_PORT`
(assigned, then overwritten by an ephemeral port — the env var never did
anything); `sl_grid_sync.set_count_in` (self-labelled deprecated alias, no
callers); `slot_matrix.NUM_TRACKS` (a second name for `MAX_USABLE_LOOPS`, kept
alive only by tests asserting the alias equalled its source).

**Specs — 1,391 lines.** Four were queues: `next-work-order-2026-08-19`,
`next-tasks-2026-08-20`, `rerun-order-2026-08-19`, and
`queue-2026-08-21-evening`, whose successor already opened with *"Supersedes"*.
The fifth, `looper-loop-seam-spec.md`, described the pipeline `a99cf63` deleted.

Links to them from *live* documents were repointed at the code. Links from dated
records — `Documents/reviews/`, `docs/measurements/` — were left dangling on
purpose: those files correctly cite what existed on their own date, and editing
them would falsify the record. A dangling link there means "read this file's
date", which is the right instruction.

**Still standing, deliberately:** `sl-hud-monitor.py`, a deprecated shim that
`install-units.sh` retires but `mpe-cli`'s `looper.sh:325` still launches,
starting a second process that contends for the OSC port the merged session
binds. The removal belongs in the CLI repo.

## Cycle 1 of the review loop, 2026-09-08

Three independent contexts: builder, reviewer, auditor. Fifteen findings, all
fixed. The ones worth remembering:

**The reviews had already found most of it.** Step 0 of the rewritten grumpy
skill checked 24 prior findings: 9 closed, 13 open, 1 declined, 1 untracked.
Three were 8 days old *with file and line numbers*. Detection was never the
problem — 47 review files, 1.4 MB, and no file recorded whether a finding was
closed. The skill now opens with that table and closes with a ledger.

**The consolidation's own guard was vacuous.** `_assert_total()` compared
`ACTIONS` with `RULES` — two literals in one file, written by the same hand.
It could not fail. `ACTIONS` is now `tuple(RULES)` and the real question, *is
this row ever reached*, is asked of the call sites by `ReachabilityTests`.
`SCENE_LAUNCH`/`SCENE_STOP` were deleted: their stated "many launches sharing
one boundary" was false, since `scene_press` dispatches one `when()` per track.

**`looper_timing` was being loaded twice** — bare and package-qualified, two
`RULES` dicts. Production used the bare copy; the tests asserted against the
other. No behaviour differed, and that is the point: the guard was guarding a
module nothing ran.

**Two facts were sharing one name.** `Session.grid` meant "a grid is
established" in `loop_model` and "a boundary is computable" in `slot_runtime`.
`slot_runtime` cannot answer the first — it is handed a boundary callable by
design — so it now passes `None`, and `when()` refuses rather than reading a
guess. Same for `sounding`, which a per-loop gesture cannot answer either.

**The flake had a mechanism.** `test_health_source_liveness.py` wrote
`MPE_METER_STATE` and never restored it; `_bash_env` handed the whole
environment to the shell under test, and `audio-engine.sh:89` reads
`MPE_JACK_BUFFER`. The env is now built deliberately instead of inherited.

Every new guard was falsified by breaking it. F9 was caught only on the second
attempt: the first fix had no enforcement at all and would have rotted.

## Still open, and yours

- **D0.** Should recording into a silent session fire at once, the way
  launching already does? The rule is in `looper_timing.RULES[RECORD_START]`,
  and the comment there says what the two-line fix is.
- **D1.** Stop All is immediate: 20 to 21 ms at five bar phases against a
  2003 ms bar. The delay you feel is that it fires on the release of the chord,
  because that same chord held three seconds means Clear All. Accept it, or
  move Clear All to another control.
- **D5.** A queued slot switch that sees no wrap for five seconds currently
  launches off-grid rather than stay stranded.
- **The count-in itself.** Whether swallowing what you play before the boundary
  is right at all is a musical question, not a code one.

## What still cannot catch a regression

- **The engine harness cannot hear.** `tests/engine/` runs real SooperLooper in
  a container with no audio source, so every take in it is silent by
  construction. No test in this repository can tell a recorded take from
  silence. This is the largest gap.
- **Nothing exercises the surface end to end.** The APC is driven by hand.
- **Timing is asserted, never felt.**

---

## Map: which file answers which question

| Question | File |
|---|---|
| When does an action take effect? | `looper_timing.py` |
| Which control does what, on which edge? | `binding_table.py` |
| What verbs does a pad press send? | `loop_model.plan_gesture` |
| What does a press mean in the matrix? | `slot_matrix.plan_slot_press` |
| What is the grid, and what drops it? | `sl_grid_state.py` |
| How is the grid told to the engine? | `sl_grid_sync.py` |
| How does a take end? | `tail_phase.py` |
| What does a lamp show? | `led_compositor.py` |

Each of those owns its answer and refuses to share it. `binding_table` refuses
a second claim on a control at import; `looper_timing` refuses a second opinion
on timing in the test suite. That is the pattern, and it is the only one here
that has ever held.
