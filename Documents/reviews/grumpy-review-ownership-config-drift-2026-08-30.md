# Grumpy review — cross-cutting constant/config drift, and the second consumer

**Branch:** `refactor/looper-ownership-2026-08-30`
**Reviewer:** fresh context, read-only. No product code or test touched.
**Charter:** [`CHARTER-looper-ownership-2026-08-30.md`](CHARTER-looper-ownership-2026-08-30.md).
Tests are **not** canon (charter §2). `sl_limits.py` is the standard (charter §3).
**Dimension:** constant/config drift, environment variables, docs-vs-code, and
`looper_songs` / `touch_browser_looper_songs` as the second consumer of looper state.

---

## Verdict in one paragraph

`sl_limits.py` is genuinely excellent and the modules that import it are honest.
The problem is that **the clamp it exists to enforce is bypassed on both of the
paths that actually run in production**, and the value it clamps is written into
the appliance's live env file by three different scripts with three different
opinions. `config/platform/player-env-parity.pi5.env` sets `MPE_SL_LOOPS=16` *and*
reinstates `MPE_SL_SCRATCH_LOOP=14` — the two keys `bootstrap-pi5-looper.sh` was
written to delete, in a comment that says deleting them cost real damage. The
health check written specifically to catch the phantom uses the clamped value and
therefore cannot see the fault. And a test named for the phantom asserts a constant
that no runtime code path reads. This is not a documentation problem; it is a
number with five homes, one of which is a loaded gun, and every instrument pointed
at it reads "fine" either way.

Second finding of equal weight: `loop_mix`'s central claim — *"nothing else ever
writes `wet`"* — is **false**, and the second writer lives in the other process.

Third, and the most on-the-nose: `device_facts.py` was built to end the failure where
one unmeasured sentence became load-bearing in five docstrings. Five modules now cite
two of its fact ids that **do not exist**, and all five describe a question as open
that closed MEASURED the day before. Nothing detects this, because `fact()`,
`refuse_with()` and `unmeasured()` have zero callers anywhere in the repo. The fact
base has a home and provenance; it still has no way to be wrong.

---

## Section 1 — the confirmed instance, verified and corrected

The charter's "how many loops" row is **confirmed with one correction**.

| Claimant | Value | Evidence |
|---|---|---|
| `sl_limits.MAX_USABLE_LOOPS` | **15** | `scripts/sooperlooper/sl_limits.py:43` |
| `scripts/sooperlooper/README.md` | **16** (7 places) | `:25, :29, :32, :39, :70, :86, :88, :143, :154` |
| `config/platform/player-env-parity.pi5.env` | **16** | `:24` |
| `scripts/measure-soak.sh` / `measure-latency-run.sh` | **16**, written into `/etc/mpe/mpe.env` | `measure-soak.sh:23,111-114`; `measure-latency-run.sh:724-728` |
| `scripts/bootstrap-pi5-looper.sh` | **15**, written into `/etc/mpe/mpe.env` | `:46` |
| every other shell script | **15** default | `run-sooperlooper.sh:20`, `restart-sooperlooper.sh:13`, `wire-jack-graph.sh:19`, `stop-all-loops.sh:7`, `reset-all-loops.sh:8`, `wire-sooperlooper-graph.sh:17`, `diagnose-16loop-crackle.sh:11` |

**Correction to the charter — `smoke-16-loops.sh` does not pass `-l 16`.** The file has
exactly one `-l`, at `:42`, and it passes `"${LOOPS}"`, which `:12` defaults to **15**.
The hardcoded `-l 16` was removed in `0e9987c` (2026-08-27) — the same day `sl_limits.py`
was written. The charter's row, and the coordinator's restatement of it, are stale by
three days and appear to be sourced from the **filename**.

That correction does not rescue the script. It is still broken, in a quieter and more
interesting way — it now *inherits* the bad configuration instead of hardcoding it, and
it independently exercises an index that does not exist while printing PASS. See 🟡 6.
The coordinator's ranking instinct ("a test tool that reproduces a known-bad config is
worse than no tool") is right; the mechanism is just not the one named.

**Correction to the charter (2):** "the live service runs `-l 15`" is true **today** —
the coordinator confirmed the live process is `sooperlooper -q -D yes -l 15 -c 2 -t 40
-p 9951 -j mpe-looper` and the journal logs `bench state updates for loops 0..14`. That
tells us something precise: the Pi's `/etc/mpe/mpe.env` currently has `MPE_SL_LOOPS=15`,
i.e. **`bootstrap-pi5-looper.sh` was the last writer**. The divergence in 🔴 1 is
therefore **latent, not currently active** — one run of `apply-player-env-parity.sh` or
`apply-external-state.sh` arms it. `run-sooperlooper.sh:20` reads that file
(`EnvironmentFile=-/etc/mpe/mpe.env`, `config/mpe-sooperlooper.service:20`) and passes
the value through **unclamped**.

---

## Section 2 — the Hall of Shame, ranked by what breaks

### 🔴 1. `player-env-parity.pi5.env` reinstates the phantom loop *and* the deleted scratch loop

`config/platform/player-env-parity.pi5.env:24-28`:

```
MPE_SL_LOOPS=16
MPE_SL_TAIL_CAPTURE=1
MPE_SL_SEAM_WELD=1
MPE_SL_SCRATCH_LOOP=14
MPE_SL_SEAM_MERGE_SAMPLES=2048
```

`scripts/bootstrap-pi5-looper.sh:36-46`:

```bash
# Keys from the offline seam-weld pipeline, deleted 2026-08-26 when a single
# native `overdub` replaced it. They are removed rather than ignored because
# /etc/mpe/mpe.env persists across deploys: MPE_SL_SCRATCH_LOOP=14 was still
# live on the Pi a day after the code stopped wanting a scratch loop, and it
# does real damage — looper_songs skips that index and sl_hud_monitor hides it,
# so track 15 silently disappears from a 16-track instrument.
_remove_env_key MPE_SL_TAIL_MODE
_remove_env_key MPE_SL_TAIL_CAPTURE
_remove_env_key MPE_SL_SEAM_WELD
_remove_env_key MPE_SL_SCRATCH_LOOP
_ensure_env_kv MPE_SL_LOOPS 15
```

Two provisioning scripts with **opposite intentions on the same four keys**, and
no defined ordering between them:

- `scripts/looper-deploy.sh:24` runs `bootstrap-pi5-looper.sh` (sets 15, removes scratch).
- `scripts/provision/first-boot.sh:55` and `scripts/provision/apply-external-state.sh:51,55`
  run `apply-player-env-parity.sh`, which **rewrites `/etc/mpe/mpe.env` wholesale**
  from the parity file, carrying forward only path keys and five buffer keys
  (`apply-player-env-parity.sh:113-114`, `PATH_KEY_RE`/`PRESERVE_KEY_RE` at `:34-35`).
  `MPE_SL_LOOPS` and `MPE_SL_SCRATCH_LOOP` are in neither preserve list.

So `apply-external-state.sh` — the state-restore path, runnable at any time — silently
undoes the bootstrap and hands the appliance back the exact configuration the
bootstrap comment says did real damage.

**What breaks, concretely, with `MPE_SL_LOOPS=16` live:**

1. `run-sooperlooper.sh:20,52` starts the engine with `-l 16`. Per `sl_limits.py:9-19`,
   index 15 is a phantom that answers `get` and discards `set`.
2. `sooperlooper-apc-bench.py:113` builds a track 15 (see 🔴 2).
3. `apply_grid_sync` / `set_grid_active` are sent to `/sl/15/*` — the engine drops
   them. That is verbatim the failure `sl_limits.py:22-24` describes: *"one track
   unquantized among fifteen quantized ones — a pad that records and stops unlike
   all its neighbours."*

**And with `MPE_SL_SCRATCH_LOOP=14` live — a data-loss path:**

- `looper_songs.py:63-66` `musical_loop_indices()` excludes index 14, so `save_song`
  (`:531`) skips it and `load_song` (`:662`) skips it.
- `sl_hud_monitor.py:127` hides it from the HUD.
- **Nothing on the APC path reads `SCRATCH` at all** — grep confirms the only two
  readers are `looper_songs.py:27` and `sl_hud_monitor.py:35`. `apc_grid`, `slot_matrix`,
  `slot_runtime`, `slot_surface`, `track_gesture` and the bench are unaware of it.

Net effect: the player records track 14 on the APC, it plays, it shows a solid green
pad — and "Save Song" returns `SongResult(ok=True, …)` with that track's audio
silently absent from the manifest. Recorded audio lost, success toast shown.

**Fix direction:** delete all five `MPE_SL_*` lines from `player-env-parity.pi5.env`
(and `MPE_SL_TAIL_MODE=extend` from `.pi4.env:19`, which nothing reads). Loop count
belongs to `sl_limits.py`, not to a board tuning file. If a measurement run needs a
lower count it should pass it, not persist it.

---

### 🔴 2. The bench is the one Python reader that bypasses `resolve_num_loops()`

`scripts/sooperlooper-apc-bench.py:113`:

```python
num_loops = int(os.environ.get("MPE_SL_LOOPS", str(NUM_LOOPS)))
```

Every other Python consumer goes through the clamp:

| Module | Line | Call |
|---|---|---|
| `sl_osc_session.py` | 42 | `NUM_LOOPS = resolve_num_loops()` |
| `looper_songs.py` | 23 | `NUM_LOOPS = resolve_num_loops()` |
| `sl_hud_monitor.py` | 43 | `NUM_LOOPS = resolve_num_loops()` |
| `sl-health.py` | 138 | `want_loops = resolve_num_loops()` |
| `sl_grid_sync.py` | 262 | `num_loops = resolve_num_loops()` |
| `dump-loop-levels.py` | 84 | `default=resolve_num_loops()` |
| `slot_matrix_spike.py` | 41 | `NUM_LOOPS = resolve_num_loops()` |

The bench's raw `int()` is the one that matters, because `num_loops` from line 113
is the value that flows into **everything the player touches**:

```
:218/:221  apply_freeform / apply_grid_sync(num_loops=)
:236/:254  set_grid_active(num_loops=)
:260       GridView(num_loops=)          → which tracks the pads address
:295       build_track_gestures(num_loops=)
:337       LoopMix(num_loops=)           → which loops the faders write
:356/:372  SlotRuntime(num_tracks=) / SlotSurface(num_tracks=)
:389       state_listener.register(num_loops=)  → OSC auto-update subscriptions
```

`sl_limits.resolve_num_loops` exists precisely so that *"a request for more is
clamped here rather than passed through"* (`sl_limits.py:52-53`). The one call site
that drives the instrument opts out. It also raises a bare `ValueError` on a typo in
`/etc/mpe/mpe.env` — the same complaint filed in
`Documents/reviews/grumpy-review-looper-2026-08-14.md:45`, still open.

**Fix direction:** `num_loops = resolve_num_loops()`. One line. Delete the env read.

---

### 🔴 3. `sl-health` cannot detect the phantom in the configuration that produces it

`sl-health.py:129-145` is the check written for exactly this bug, with a docstring
that names it. But:

```python
want_loops = resolve_num_loops()          # :138  → clamped to 15
loop_verdict, phantoms, loop_detail = check_loops_writable(..., num_loops=want_loops)
```

With `MPE_SL_LOOPS=16`, the engine has 16 loops (index 15 phantom), the bench drives
16, and `sl-health` probes indices **0..14** and prints `PASS loop count`.

This is the measurement-integrity shape: an instrument that reads the same whether
the fault is present or absent. The probe should assert against **what the engine was
actually started with** (`/ping`'s reported count, or the raw env value), not against
the clamp — the clamp is the thing under test.

**Fix direction:** probe `max(raw_env, resolve_num_loops())`, or better, read the
engine's reported loop count and prove *every index it claims* takes a write. That
is what the docstring at `:132-133` already says the check is for.

---

### 🔴 4. `loop_mix.wet_for()`'s sole-writer claim is false — `looper_songs` writes `wet` from another process

The claim, in two places:

`scripts/sooperlooper/README.md:102` — *"the point of the seam is that nothing else
ever writes `wet`."*

`scripts/sooperlooper/loop_mix.py:313-319`:

```python
def wet_for(self, loop: int) -> float:
    """The single point where every contribution to a loop's level meets.
    ...  Everything is recomputed
    in full from state we own, so nothing here compounds and no two
    writers of `wet` can fight.
    """
```

The exhaustive search across `scripts/` and `patch_browser/` finds exactly one other
writer, and it is live:

`scripts/sooperlooper/looper_songs.py:677`:

```python
probe.send(f"/sl/{loop}/set", ["wet", float(entry.wet)])
```

Reached from the touch UI at `patch_browser/touch_browser_looper_songs.py:448, 689, 704`.

**Why this is a bug and not just a docstring error.** The value written back is the
*composed* wet, because `save_song` captured it from the engine:

```python
wet = probe.get("wet", loop)              # looper_songs.py:534 — composed value
...
wet=float(wet) if wet is not None else 1.0,   # :560 — stored in the manifest
```

On load, the bench sees a `wet` it did not ask for and back-derives a fader position
by dividing out master and auto-law:

```python
if abs(wet - self.wet_for(loop)) <= WET_ECHO_TOLERANCE:   # loop_mix.py:219
    return
cc = self._user_cc_from_composed_wet(loop, wet)           # :221 → wet / (master × law)
self.user_gain[loop] = cc                                 # :224
```

So `user_gain` becomes `saved_composed_wet / (master_now × law_now)`. If the master
sat at unity when the song was saved and at unity when loaded, this happens to be
correct. If the master moved between save and load — or `MPE_SL_LOOP_GAIN_LAW=1` and
the active-loop count differs — the level is wrong by that factor, and wrong silently,
in the direction of loud. That is the compounding `loop_mix.py:26-28` says cannot happen.

The tests lock this in rather than catching it: `tests/test_looper_songs.py:135, 181,
377, 404` all assert the raw round-trip. Per charter §2, those tests are evidence of
what a session decided, not intention.

**Fix direction:** two options, both cheap. (a) Store the *user gain CC*, not the
composed wet — the manifest becomes state `loop_mix` owns, and load routes through
`LoopMix` rather than the wire. (b) If the manifest format must stay, `load_song` must
not touch `/set wet` at all; it should hand the level to the session and let
`wet_for()` emit it. Either way, correct the README sentence and the `wet_for`
docstring in the same commit — a claim of sole ownership that a grep disproves in
five seconds is worse than no claim.

---

### 🔴 5. mk2 arrow notes collide with mk2 scene-launch notes; the arrows are unreachable

Two constants claim the same four note numbers:

`scripts/sooperlooper/apc_panel.py:78`:
```python
SCENE_COLUMN_MK2: tuple[int, ...] = tuple(range(0x70, 0x78))   # 0x70..0x77
```

`scripts/sooperlooper/apc_transport.py:94`:
```python
ARROW_NOTES_MK2 = (0x70, 0x71, 0x72, 0x73)  # up, down, left, right
```

The charter (§5) states the connected device **is** an APC mini mk2. And the scene
column is not a guess: `device_facts.py` `apc.scene.led_observed` (MEASURED,
2026-08-29) records all eight scene buttons being lit and read back by Mitch — those
notes are exercised. `ARROW_NOTES_MK2` carries a self-declared "⚠️ UNVERIFIED"
banner (`apc_transport.py:86`).

The dispatch order settles it in favour of the scene column:

`scripts/sooperlooper-apc-bench.py:676-696` runs `scene_press_row(...)` and
`continue`s on a hit. `handle_arrow(n)` is not reached until `:722`. So on mk2,
notes 0x70–0x73 are consumed as scene launches for rows 7, 6, 5, 4 and **the arrow
handler is dead code for those notes**.

Either way the outcome is the same: **banking does not work on the only hardware
present.** The README's table row *"Up / Down | arrows | Page the viewport by 8"*
and *"Shift + Left / Right | Nudge the viewport by 1"* describe behaviour that
cannot occur on mk2. With `MPE_SL_MULTIGRID=0` (the appliance default —
`sooperlooper-apc-bench.py:273`, and the key is absent from both parity files) the
press lands at `:690` and is discarded with a message only visible under `--dump-midi`.

Related, same file: `apc_transport.resolve_scene_launch_notes` at `:183-187` has a
docstring saying *"Scene Launch 1–7 notes (slot rows 0–6). Stop All is not included."*
It returns `SCENE_LAUNCH_NOTES_MK2` = all **eight**, including Stop All at 0x77. The
docstring contradicts the return in the same function.

**Fix direction:** this is the morning-eyes item. `--dump-midi` and press each arrow.
Whatever comes back, the note numbers go into `apc_panel.py` with a `device_facts`
entry at MEASURED, and `ARROW_NOTES_MK*` is deleted from `apc_transport.py` — it is a
note literal outside the registry, which `apc_panel.py:29` rule 2 forbids and the
charter §5 makes a hard rule. Until then, an invariant test that
`set(ARROW_NOTES_MK2) & set(SCENE_COLUMN_MK2) == set()` would have caught this the
day it was written.

---

### 🔴 5b. Five `device_facts` citations point at ids that do not exist — and the fact base has zero callers, so nothing can tell

Five modules cite two fact ids. **Neither id is in `FACTS`.** Verified by executing
the module:

```
$ python3 -c "import device_facts as d; print(sorted(d.FACTS)); print(d.unmeasured())"
['apc.buttons.all_have_leds', 'apc.buttons.channel_response', 'apc.buttons.single_colour',
 'apc.grid.mk2_encoding', 'apc.probe.positive_control', 'apc.scene.led_observed',
 'apc.scene_column.bottom_is_0x59', 'apc.shift.led', 'apc.track.led_observed']
[]
```

| Site | Cites | Real id |
|---|---|---|
| `led_table.py:45-46` | `apc.scene.led_colours`, `.apc.track.led_colours` | `apc.scene.led_observed:109`, `apc.track.led_observed:121` |
| `apc_leds.py:31` | `apc.scene.led_colours` | same |
| `apc_transport.py:368` | `apc.scene.led_colours` | same |
| `slot_matrix.py:330` | `apc.scene.led_colours` | same |
| `probe-apc-buttons.py:10` | `apc.scene.led_colours`, `.track.led_colours` | same |

The dead link is the smaller half. **The prose at all five sites is now wrong against
tier 1**, and in the same direction: it tells the reader an open question is open, one
day after it closed MEASURED.

`scripts/sooperlooper/led_table.py:44-47`:
```python
# What we currently SEND to the side buttons. Not a statement about what
# they can show: see `device_facts.apc.scene.led_colours` and
# `.apc.track.led_colours`, both still resting on a vendor document that has
# already been wrong once about this panel. Measure before promising a colour.
```
It **was** measured — five probe rounds on 2026-08-29, with a positive control
(`apc.probe.positive_control`).

`scripts/sooperlooper/apc_leds.py:30-31`:
```python
This used to say the button LEDs ARE single-colour, as established fact. It is
not established — see `device_facts.apc.scene.led_colours`.
```
It **is** established: `apc.buttons.single_colour` (MEASURED, 2026-08-29) records it
CLOSED as a bounded negative across three exhausted axes. The comment now states the
exact inverse of the fact base.

`scripts/sooperlooper/apc_transport.py:367-368`:
```python
It is green on both models because the
scene-launch LEDs are driven green here. Whether they CAN show red is
unmeasured — `device_facts.apc.scene.led_colours`.
```
Answered: `apc.scene.led_observed` = green only; `apc.track.led_observed` = red on the
track row. Scene-red is refuted, not unmeasured.

**The dangerous one** — `scripts/sooperlooper/slot_matrix.py:328-332`:
```python
Mitch asked for yellow here and got blink instead, on the grounds that the
scene buttons are green-only. That ground is not solid — see
`device_facts.apc.scene.led_colours`, which is vendor-tier and unmeasured.
If the probe shows these buttons can do yellow, this should become yellow,
which is what was asked for in the first place.
```
The probe ran. `apc.scene.led_observed` states velocities *"13 yellow, 21 green are not
honoured — they are just green."* Yellow on the scene column is **measured impossible**.
So this is a standing TODO, addressed to the next session, pointing at a nonexistent
fact id, instructing it to implement a colour the hardware has been measured not to
produce — and framed as restoring something Mitch asked for. That is a trap, and it is
the highest-consequence of the five.

**The structural finding underneath, which is worse than the five instances.**
`device_facts.fact()`, `Fact.refuse_with()`, `AUTHORITATIVE` and `unmeasured()` have
**zero callers anywhere in `scripts/`, `patch_browser/` or `tests/`**. Grep confirms
every reference outside `device_facts.py` itself is prose in a comment. Consequences:

1. A broken fact id cannot raise, cannot fail a test, and cannot be noticed. That is
   why five of them accumulated in one day.
2. `Documents/specs/apc-control-surface-architecture-spec.md:102` claims
   *"`Fact.refuse_with()` raises if this is attempted, so the rule is executable rather
   than aspirational."* It is aspirational. Nothing calls it.
3. The charter's §5 hard rule — *"capability violations raise on authoritative tiers and
   warn on unmeasured ones"* — has no implementation to attach to.
4. `unmeasured()` returns `[]`. Its docstring calls it *"the work queue for the
   capability probe… meant to be read before promising Mitch anything about the panel."*
   An empty work queue that nobody reads is indistinguishable from a finished one.

`device_facts.py` was built to end the failure where *"a claim about physical hardware
had no home, no provenance, and no way to be wrong"* (`:17-18`), and where one sentence
became load-bearing in five docstrings. It now has a home and provenance. It still has
**no way to be wrong** — and it has produced five restatements of its own.

**Fix direction, and it is small:**
- Correct the five ids and rewrite the five prose blocks against the MEASURED facts.
  `slot_matrix.py:328-332` should say yellow is refuted and why, not invite it.
- Add `def cite(fact_id: str) -> str` and make the citations executable, or at minimum
  a test that scans the tree for `device_facts\.([a-z_.]+)` and asserts every captured
  id is in `FACTS`. Twenty lines. It would have caught all five the day they landed,
  and it is the charter's own standard: *"A rule a build cannot fail is not a rule."*

**Also stale against tier 1 — `apc-control-surface-architecture-spec.md:130-136`:**

```
| Scene launch colours | mk1 0x52-0x59, mk2 0x70-0x77 | UNKNOWN | VENDOR — unmeasured |
| Track select colours | 0x64-0x6B                    | UNKNOWN | VENDOR — unmeasured |

The two UNKNOWN rows are what `scripts/probe-apc-buttons.py` exists to settle.
```

Both rows are settled MEASURED 2026-08-29. Under the charter's canon table
`device_facts` at MEASURED is tier 1 and a spec is tier 3, so **the spec table is the
defect**. This matters more than an ordinary stale doc: the charter §1 instructs every
agent on this branch to read that spec *"before anything else"* and says it *"is already
correct."* In §3.3 it is not, and it is stale in the direction that re-opens a closed
question — the same shape as the five citations, one tier up.

*(This finding was flagged by the coordinator and independently verified here; the
zero-callers structure, the `slot_matrix.py` yellow trap, and the spec §3.3 row are
additions from this pass. The coordinator's "raise KeyError" framing is not quite
right — nothing calls `fact()`, so nothing raises. Silence is the problem.)*

---

### 🟡 6. `smoke-16-loops.sh` prints PASS after loading a loop that does not exist

`smoke-16-loops.sh:12` correctly defaults `LOOPS=15`. Lines 55, 66, 72 do not:

```bash
55:  for i in $(seq 0 15); do          # ensure_clips — requires 16 fixture files
66:  for i in $(seq 0 15); do          # load_loop  → index 15 goes nowhere
72:  for i in $(seq 0 15); do          # hit trigger → index 15 goes nowhere
75:  log "triggered all 16 loops"
101: log "PASS — 16 clips loaded, triggered, measured, paused"
```

Every other shell script iterates `$(seq 0 $((LOOPS - 1)))` — `stop-all-loops.sh:28`,
`reset-all-loops.sh:16`, `diagnose-16loop-crackle.sh:138`. This one is the exception,
so the smoke test loads 15 clips and reports 16, on a script whose entire purpose is
to verify the 16th. `mpe looper sl-smoke`'s own help still says *"Restart SooperLooper
-l 16"* (`mpe-cli/commands/looper.sh:96`), as does `README.md:143,154` and
`AGENTS.md:68`.

**On "does it still reproduce the known-bad config?"** — the coordinator's framing.
Answer: **not on its own any more, but it is the one script that will.** The `-l 16`
literal is gone (`0e9987c`, 2026-08-27), so with a clean env it starts the engine at 15.
But it is also the only looper script that *restarts the engine from the ambient env*
(`:38-47`) while iterating a hardcoded 16, so with `MPE_SL_LOOPS=16` present — which
`player-env-parity.pi5.env:24` ships — `sl-smoke` becomes the fastest way to put the
appliance into the phantom configuration and get a PASS for it. Ranked 🟡 rather than
🔴 only because the config that arms it is already ranked 🔴 1.

**Fix direction:** `$(seq 0 $((LOOPS - 1)))` in all three places; fix the two log
strings to interpolate `${LOOPS}`; rename the file and the CLI text. A script whose
*filename* asserts a count is how the number 16 keeps propagating — it is where both
the charter and the coordinator picked the stale claim up.

---

### 🟡 7. The second consumer runs in a different process and keeps a weaker model

`looper_songs.py` + `patch_browser/touch_browser_looper_songs.py` execute inside
`touch-patch-browser.service`; the bench state lives in `mpe-looper-session.service`.
They share no memory, only the engine. Three concrete consequences:

**(a) The multi-clip matrix is discarded on every save from the touch UI.**
`looper_songs.save_song:505-509`:

```python
"""``active_slots`` maps track -> the slot its buffer currently holds.

The matrix lives in the bench, not here, so it has to be handed in. Absent,
every track saves to slot 0 — which is exactly today's one-clip-per-track
behaviour, expressed in the v2 shape.
"""
```

`touch_browser_looper_songs.py:687` calls `save_song(p, name, overwrite=overwrite)` —
**no `active_slots`**. It cannot supply one: `SlotRuntime` is in another process. So
the seam is real, correctly documented, and permanently unwired. Every save from the
only UI that reaches it collapses the matrix to slot 0.

**(b) A song load rewrites the grid behind the bench's back.**
`load_song` calls `establish_grid_clock(send, bpm)` and `set_grid_active(...)`
(`looper_songs.py:649-652`) from the touch process. In the bench, `GridState.bpm` is
set only inside `GridState.establish()` (`sl_grid_state.py:163-172`), which requires
`arm()` first — i.e. a pad press. There is **no method to adopt an engine tempo from
outside**. `on_grid_established` / `on_phase_reanchor` (`sooperlooper-apc-bench.py:226,
243`) are the sole callers of `grid.mark_phase_zero`. So after a touch-UI song load
the engine's tempo and phase have changed and the bench's `GridState` still holds the
old ones — which is what `SlotRuntime`'s `grid_boundary` callback uses to decide when
a queued clip fires.

**(c) The song probe drops loop identity from its reply cache.**
`sl_osc_session._cache_key` keys correctly on both (`sl_osc_session.py:47`):

```python
return f"{loop if loop >= 0 else -2}:{ctrl}"
```

`LooperSongProbe` keys on the control name alone (`looper_songs.py:385, 408, 414`):

```python
self.last[str(args[1])] = float(args[2])     # :385 — args[0] (the loop) discarded
...
self.last.pop(ctrl, None)                     # :408
if ctrl in self.last:  return self.last[ctrl] # :414
```

A late reply for loop *N−1* satisfies the 1.5 s wait for loop *N*. In `save_song`'s
per-loop sweep (`:531-534`) that means a track can be written into the manifest with
its neighbour's `loop_len`, `state` or `wet`. Two OSC reply caches in one repo, one
keyed right and one keyed wrong.

**(d) Dead knob.** `LISTEN_PORT = int(os.environ.get("MPE_SL_SONGS_PORT", "9955"))`
(`:20`) is assigned at `:380` and then immediately overwritten by an ephemeral bind
at `:395-396`. The env var controls nothing.

**On `SCRATCH` / `musical_loop_indices` — is it still live, still correct, still
meaning what it meant?** Three separate answers, and they do not agree.

**Still live?** Yes, on all three counts: the constant, the function, and the env key.
`SCRATCH` is read at `looper_songs.py:27` and `sl_hud_monitor.py:35`;
`musical_loop_indices` is called at `looper_songs.py:456, 531`; and the key is *shipped
set to `14`* in `player-env-parity.pi5.env:27` and documented as `14` in
`mpe.env.example:225, 234`.

**Still correct?** The *code* is. Both defaults are `-1` and they agree, and each
carries its reason (`looper_songs.py:24-26`, `sl_hud_monitor.py:33-34`, which even says
*"Must match looper_songs.SCRATCH"*). `musical_loop_indices` short-circuits cleanly at
`:64-65`. The *configuration* is not: it still asserts the workaround
`sl_limits.py:30-32` says *"outlived the explanation."*

**Still means what it meant?** **No — and this is the sharp end.** It used to mean
"loop 14 is the seam-weld capture buffer, reserved by the engine's own workflow." The
seam-weld pipeline was deleted 2026-08-26 (`sl_grid_sync.py:35-38`), so today it means
only "hide loop 14 from two of the six modules that address loops." The other four —
`apc_grid`, `slot_matrix`, `slot_runtime`, `slot_surface`/`track_gesture` and the bench —
have **never** read `SCRATCH`; grep across `scripts/` and `patch_browser/` returns
exactly two readers. So the name still says "reserved" while the behaviour is now
"recordable on the surface, invisible to save and to the HUD." That is the data-loss
path in 🔴 1, and it is a semantic drift, not a bug in either module.

Note the symmetry with `sl_limits.py`'s own history: the scratch workaround outlived its
explanation once and cost a morning; the explanation has now been written down properly
in two modules, and the **configuration** outlived the removal instead. Same failure,
moved one layer down.

**Fix direction:** delete `MPE_SL_SCRATCH_LOOP` from `player-env-parity.pi5.env:27` and
`mpe.env.example:225,234-236`. Keep `SCRATCH`/`musical_loop_indices` — the seam is
cheap and honestly documented — but either make all six loop-addressing modules honour
it or add a test that fails when a reserved index is reachable from the surface.
"Two of six" is the two-writer shape wearing a config hat.

One stale comment inside otherwise good code: `looper_songs.py:25` — *"so all 16
loops are musical"* — sits directly under `NUM_LOOPS = resolve_num_loops()` which is 15.

---

### 🟡 8. `MPE_LOOPER_EIGHTH_PER_CYCLE` and `MPE_LOOPER_BEATS_PER_BAR` are read into constants that half the code ignores

`sl_grid_sync.py:107` reads the env var into `EIGHTH_PER_CYCLE`. `establish_grid_clock`
uses it (`:234`). `apply_grid_sync` does **not** — it takes a hardcoded default and
sends that to the same engine control:

```python
178:    eighth_per_cycle: int = 8,
198:    send("/set", ["eighth_per_cycle", float(eighth_per_cycle)])
```

Set `MPE_LOOPER_EIGHTH_PER_CYCLE=16` and startup writes 8 while grid establishment
writes 16. Two writers of one engine control, disagreeing by configuration.

Same shape for beats-per-bar, three homes:

| Home | Line | Value |
|---|---|---|
| `sl_grid_state.BEATS_PER_BAR` | `:24` | `int(env("MPE_LOOPER_BEATS_PER_BAR", "4"))` |
| `tail_phase.BEATS_PER_BAR` | `:84` | hard `4` |
| `sl_hud_monitor.bar_beat(..., beats_per_bar=4)` | `:56` | hard `4` |

Set 3/4 time and the grid changes; the ring-out length (`tail_phase.py:97,106`) and
the HUD's bar counter (`sl_hud_monitor.py:68`) do not.

**Fix direction:** `apply_grid_sync` should default to `EIGHTH_PER_CYCLE`;
`tail_phase` and `sl_hud_monitor` should import `BEATS_PER_BAR` from `sl_grid_state`.

---

### 🟡 9. `slot_matrix.NUM_TRACKS` is a decoy constant, and a test guards it

`slot_matrix.py:37-38`:
```python
# 15 — the engine ceiling, not a layout choice. See sl_limits.py.
NUM_TRACKS = MAX_USABLE_LOOPS
```

Repo-wide, `NUM_TRACKS` is referenced by **nothing but tests**
(`tests/test_slot_matrix.py:18, 63, 277-280`). `SlotRuntime` and `SlotSurface` take
`num_tracks` from the caller (`slot_runtime.py:82`, `slot_surface.py:53`), which is
the bench's unclamped env value (🔴 2).

`tests/test_slot_matrix.py:57-63`:

```python
def test_fifteen_contiguous_tracks_the_engine_ceiling(self) -> None:
    """15, not 16. SooperLooper 1.7.9 stops at index 14 — index 15 answers
    reads with defaults and discards writes, so a 16th track looks present
    and behaves unlike every other one. Measured 2026-08-27; see
    sl_limits.py."""
    self.assertEqual(NUM_TRACKS, 15)
```

This is charter §2's exact case: *"a test whose failure would not have caught the bug
it is named for."* It is green today and would stay green through the entire
configuration described in 🔴 1. Nothing in `tests/` reads `config/platform/*.env` at all.

**Fix direction:** the assertion belongs on the value the runtime actually uses. A
test that boots the bench's `num_loops` resolution with `MPE_SL_LOOPS=16` and asserts
`15` would have caught 🔴 1 and 🔴 2 together.

---

### 🟢 10. Duplicated dimension constants that currently agree

Not urgent, but each is a future 🔴 the first time someone edits one copy.

- `GRID_COLS` / `GRID_ROWS`: `apc_grid.py:33-34` and `apc_panel.py:60-61`. Both 8/8.
  The bench imports from `apc_grid`; `apc_panel` uses its own to assert its scene-column
  invariants (`:83-85`).
- `NUM_SLOTS = 8`: three homes — `apc_grid.py:37` (`= GRID_ROWS`), `slot_matrix.py:39`
  (literal `8`), `looper_songs.py:35` (literal `8`). `slot_runtime` imports from
  `slot_matrix`; `looper_songs` keeps its own. If the matrix ever stops being 8 rows,
  the manifest format silently disagrees with the surface.
- Grid note range: `apc_panel.GRID_NOTE_MIN/MAX = 0/63` (`:65-66`),
  `apc_leds.PAD_NOTE_MIN/MAX = 0x00/0x3F` (`:50-51`), and `apc_grid.note_to_row_col`
  inlines `0 <= note <= 63` (`:67`). Three homes.
- Fader CCs: `apc_faders.py:29-35` defines mk1 and mk2 separately (48–55 / 56 for both),
  so `resolve_fader_ccs` (`:41-64`) is a branch that cannot change its answer. Honest
  about being unverified; just note the variant machinery is currently inert.
- Stale comment: `apc_grid.py:45` — *"How far the viewport can travel: 16 tracks in an
  8-wide window"* — eleven lines below `NUM_LOOPS = MAX_USABLE_LOOPS` (15).

**Agreed and clean, for the record:** OSC port `9951` is `MPE_SL_OSC_PORT` with default
`"9951"` in all ten readers, no divergence. Bench listen `9953`, watchdog `9961`,
health `9954` are each single-homed. Sample rate `48000` appears only in
`generate-test-clips.sh:25` and `slot_matrix_spike.py:43` (both experiment tooling).

---

## Section 3 — docs that lie

Systematic pass over `scripts/sooperlooper/README.md` against the code.

| # | README claim | Line | Verdict |
|---|---|---|---|
| 1 | "APC **16-track** clip row" | 25 | **FALSE** — 15 (`apc_grid.py:32`) |
| 2 | "8 visible of **16**" | 29 | **FALSE** — 8 of 15 |
| 3 | "Master fader \| CC 56 \| **all 16**" | 32 | **FALSE** — 15 |
| 4 | "viewport onto **sixteen** tracks" | 39 | **FALSE** — fifteen |
| 5 | "eight of **sixteen** showing" | 70 | **FALSE** |
| 6 | "moves **all 16 loops** over per-loop `wet`" | 86 | **FALSE** — `_all_loop_messages` iterates `range(self.num_loops)` (`loop_mix.py:309`) |
| 7 | "scaling **all 16** … One master move is **16 OSC messages**" | 88 | **FALSE** — 15 |
| 8 | "restart **-l 16**" (sl-smoke) | 143 | **FALSE** — `smoke-16-loops.sh:12` defaults 15 |
| 9 | "Restarts SooperLooper with **-l 16**" | 154 | **FALSE** — same |
| 10 | "**nothing else ever writes `wet`**" | 102 | **FALSE** — `looper_songs.py:677`. See 🔴 4 |
| 11 | "Rows 1–7 — **Reserved** (per-track controllers, scenes — future)" | 30 | **FALSE** — rows 1–7 are the implemented multigrid slot rows (`slot_matrix.py`, `slot_surface.py`, `apc_grid.py:37`). The README describes a design two revisions old and never mentions multigrid |
| 12 | "**Up / Down** … Page the viewport by 8" | 33 | **FALSE on mk2** — note collision, see 🔴 5 |
| 13 | "**Shift + Left / Right** … Nudge by 1" | 34 | **FALSE on mk2** — same |
| 14 | "The `loop_gain/N` backstop … is **off by default** (`MPE_SL_LOOP_GAIN_LAW=1`)" | 100-101 | **MISLEADING** — the default is `"0"` (`loop_mix.py:74`); `=1` is how you turn it *on*, printed as if it were the default |
| 15 | "Free-form throughout: `MPE_SL_SYNC_MODE=freeform`" | 105-106 | **MISLEADING** — the default is `"grid"` (`sooperlooper-apc-bench.py:118`) and neither parity file sets it, so the appliance runs grid mode. Reads as a statement of current config; it is an instruction, and it contradicts the sentence before it |
| 16 | "Faders 1–8 \| **CC 48–55**" | 31 | **TRUE** (`apc_faders.py:29-30`) |
| 17 | "Master fader \| **CC 56**" | 32 | **TRUE** |
| 18 | "hold **3 s** = clear all" | 111 | **TRUE** — `MPE_APC_TRACK_RESET_HOLD_MS` default `"3000"` (`bench:117`), wired at `:436, :444` |
| 19 | "hold ~2 s = clear loop" (module docstring) | bench:6 | **TRUE** — `MPE_APC_HOLD_MS` default `"2000"` (`bench:110`) |
| 20 | "OSC state listener on port **9953**" | 120 | **TRUE** (`sl_osc_session.py:26`) |
| 21 | "`mpe-looper-session.service` (HUD thread) → `~/.mpe_sl_hud_state.json`" | 116-117 | **TRUE** (`patch_browser/sl_hud_state.py:10-11`) |
| 22 | "Debug: `looper-session.py --bench-only`" | 120 | **TRUE** (`looper_session.py:105`) |
| 23 | Service names `mpe-looper-session`, `mpe-sooperlooper` | 116, 120 | **TRUE** |
| 24 | `mpe looper` subcommands (`sl-health`, `sl-watchdog`, `sl-rewire`, `sl-restart`, `sl-bench restart`, `deploy`, `sl-clips`, `sl-smoke`, `sl-diagnose`) | 125-155 | **ALL EXIST** (`mpe-cli/commands/looper.sh:21-72`). `sl-stop` exists but is undocumented in the README |
| 25 | "~45 ms default, `MPE_APC_FADER_SMOOTH_MS`" | 94 | **TRUE** (`loop_mix.py:68`) |
| 26 | "arrow-button notes are UNVERIFIED" | 63 | **TRUE and honest** — but see 🔴 5: unverified is not the same as *contradicted by a measured constant in the next file* |

**`AGENTS.md`:** `:44` "16 playing at once"; `:67` "generate 16 fixture WAVs";
`:68` "16-loop load/trigger smoke" — three more stale 16s. `:32` lists the looper's
entire env surface as `MPE_SL_LOOP_GAIN`, `MPE_SL_LOOP_GAIN_LAW` — two of 94.

**`docs/CODE-MAP.md`:** `:13`, `:22`, `:239` all say "16 loops". Worse, the §3.4
function table lists a module that does not exist:

```
263 | `LoopFootswitch` | apc_footswitch.py | apc-bench | OSC `/hit`, `/undo_all` | Tap/hold/clear |
264 | `build_footswitches()` | apc_footswitch.py:392 | apc-bench main | — | 16 loop controllers |
```

`scripts/sooperlooper/apc_footswitch.py` does not exist and is referenced nowhere in
`scripts/` or `tests/`; the live module is `track_gesture.py`. Also stale line numbers:
`:276` says `main()` at `sooperlooper-apc-bench.py:74` (actual: `run_bench` at `:87`,
`main` at `:768`); `:278` says `midi_note_down()` at `:48` (actual `:61`). `:30` says
"~28 `scripts/sooperlooper/*`" — actual count is 54. `:237` lists ports 9951/9961/9953
and omits 9954 (health) and the songs probe.

**`mpe-cli/commands/looper.sh`:** `:95` "generate 16 fixture WAVs", `:96` "Restart
SooperLooper -l 16", `:109` "APC 16-pad bench".

**`Documents/specs/apc-control-surface-architecture-spec.md`:** `:130-136` — two rows
marked UNKNOWN / VENDOR-unmeasured that `device_facts` settled MEASURED on 2026-08-29;
`:102` claims `Fact.refuse_with()` makes rule 4 "executable rather than aspirational"
when nothing calls it. See 🔴 5b. This is the one that matters most, because the
charter sends every agent to this spec first and vouches for it.

**Five source files** — `led_table.py:45-46`, `apc_leds.py:31`, `apc_transport.py:368`,
`slot_matrix.py:330`, `probe-apc-buttons.py:10` — cite two fact ids that do not exist
and describe a closed question as open. See 🔴 5b.

The pattern: **16 appears as fact in five documents and three help strings**, all
downstream of a constant that has said 15 since 2026-08-27. `sl_limits.py`'s own
history section (`:26-31`) describes precisely this — a fact that lived in one place,
read as stale, and got deleted. The inverse is happening now: a corrected fact that
lives in one place while nine stale copies read as canon.

### The velocity of this, measured

Three independent data points from this session alone, worth stating because they
change what the fix has to be:

1. The **charter**, written ~6 h ago, carried a stale `smoke-16-loops.sh` claim
   (fixed in git 3 days earlier) and — per the coordinator — a stale §6 on button
   colours (closed 1 day earlier). Both have now been corrected.
2. **Five source files** drifted from `device_facts` within **one day** of the facts
   being recorded (🔴 5b).
3. This review picked the stale smoke claim up from the charter and would have
   repeated it, had the `-l` line not been read directly.

A doc-freshness convention cannot survive drift at this rate; three careful readers in
one night all inherited a stale fact from a document rather than from the source. The
only durable fixes are the executable ones: a citation-resolution test for
`device_facts` ids, a generated `mpe.env.example`, and constants that have exactly one
importable home. Everything in this review that is *prose* will be stale again by the
weekend.

---

## Section 4 — dead code and abandoned experiments

Method: reference search across `scripts/`, `tests/`, `config/`, `docs/`,
`Documents/`, and the `mpe-cli` command surface.

| File | Referenced by | Status |
|---|---|---|
| `slot_matrix_spike.py` (477 L) | `docs/measurements/multi-clip-slot-spike-2026-08-26.md`, `multi-clip-per-track-spec.md`, `multi-clip-integration-plan.md` — **docs only, no code, no CLI, no test** | Finished experiment. Its results (SP1 save/load latency, SP2 6.8 ms p95 swap) are already quoted verbatim in `looper_songs.py:73-78` and `:654-658`, which is exactly where they belong |
| `spike-load-halt.py` (153 L) | `docs/measurements/PI5-LOOPER-SEAM-WRAP.md` only | Finished experiment, question answered |
| `synthpad.py` (67 L) | archived measurement + `Documents/specs/rerun-order-2026-08-19.md` | **Live tool** — it is the "never ask Mitch to run a test you could have run yourself" instrument, and it is the only way to drive the bench without hands. Keep |
| `measure_midi_osc_latency.py` (93 L) | `docs/CLASSIC-MIDI-PLAN.md` only | Overlaps `--measure-latency N` built into the bench (`sooperlooper-apc-bench.py:88-93, :207`). Two instruments for one measurement |
| `diagnose-16loop-crackle.sh` (213 L) | `mpe looper sl-diagnose` (`looper.sh:200, 210`), `smoke-16-loops.sh` comment, `docs/CODE-MAP.md` | **Live tool.** Name is wrong (defaults to 15 at `:11`) but it is wired and used |
| `set-input-latency.sh` (11 L) | **nothing, anywhere** | Dead. Zero references in code, docs, CLI or tests |
| `sl-hud-monitor.py` (17 L) | `mpe looper` (`looper.sh:325`), `install-units.sh`, `tests/test_systemd_units.py` | Self-declared *"Deprecated entry point"* (`:4`) that the CLI still launches. It starts a **second** process that will contend for the same OSC listen port the merged session already binds (`sl_osc_session.py:86-96` exits hard on that collision). Either the shim goes or the CLI stops calling it |
| `reset-all-loops.sh` | `mpe looper` (`looper.sh:426, 429`) | Live |
| `stop-all-loops.sh`, `wire-jack-graph.sh`, `wire-sooperlooper-graph.sh`, `restart-sooperlooper.sh`, `configure-grid-sync.sh`, `generate-test-clips.sh`, `load-n-loops.sh`, `run-sooperlooper.sh` | systemd / CLI / each other | All live |
| `smoke-16-loops.sh` | `mpe looper sl-smoke` | Live, but broken and misnamed — see 🟡 6 |
| `slot_matrix.NUM_TRACKS` | tests only | Dead constant with a live-looking comment — see 🟡 9 |
| `looper_songs.LISTEN_PORT` / `MPE_SL_SONGS_PORT` | assigned then overwritten | Dead knob — see 🟡 7(d) |
| `sl_grid_sync.set_count_in` (`:205-209`) | grep: no callers outside its own module | Self-labelled *"Deprecated alias"*. Dead |
| `sl_grid_sync.EIGHTH_PER_CYCLE` | one of two sites — see 🟡 8 | Half-dead, and that is worse than dead |

The charter says the codebase was *"built as a series of experiments."* That is
visible and, mostly, honestly labelled. The problem is not that experiments exist —
it is that a finished experiment and a live tool are indistinguishable from the
directory listing. Nine files in `scripts/sooperlooper/` are one-shot measurement
rigs; none says so in its filename or sits in a subdirectory.

**Recommendation:** `scripts/sooperlooper/experiments/` (or delete outright — the
results already live in `docs/measurements/`). A file whose question has been
answered and whose answer is written down elsewhere is archaeology, and archaeology
in the load path is how `-l 16` survived three weeks past its own refutation.

---

## Constant inventory

Ranked by what breaks if the homes diverge.

| Constant | Homes | Values | Authoritative | Agree? |
|---|---|---|---|---|
| **loop count** | `sl_limits.MAX_USABLE_LOOPS:43`; `apc_grid.NUM_LOOPS:32`; `slot_matrix.NUM_TRACKS:38`; `loop_mix.num_loops:143`; `track_gesture:121`; `sl_grid_sync` ×5; `sl_bench_listener:30`; `sl_osc_session:42`; `looper_songs:23`; `sl_hud_monitor:43`; **`sooperlooper-apc-bench.py:113` (unclamped env)**; 8 shell scripts `${MPE_SL_LOOPS:-15}`; **`player-env-parity.pi5.env:24`=16**; `player-env-parity.pi4.env:20`=8; `bootstrap-pi5-looper.sh:46`=15; `measure-soak.sh:23`=16; `measure-t7a.sh:16`=16; README ×9=16; `CODE-MAP` ×3=16; `AGENTS.md` ×3=16; `mpe-sooperlooper.service:2`=16 | **15 / 16 / 8** | `sl_limits.MAX_USABLE_LOOPS` = **15** | ❌ **NO** |
| **scratch loop** | `looper_songs.SCRATCH:27` (−1); `sl_hud_monitor.SCRATCH_LOOP:35` (−1); **`player-env-parity.pi5.env:27`=14**; `mpe.env.example:234`=14; `bootstrap-pi5-looper.sh:45` deletes it | **−1 / 14** | `looper_songs.py:24-26` = **−1** (none reserved) | ❌ **NO** |
| **`wet` composition** | `loop_mix.wet_for:313` (claims sole); **`looper_songs.py:677`** | one composed, one raw | `loop_mix.wet_for` | ❌ **NO** |
| **mk2 notes 0x70–0x73** | `apc_panel.SCENE_COLUMN_MK2:78` (scene rows 7–4); `apc_transport.ARROW_NOTES_MK2:94` (arrows) | two meanings, same notes | `apc_panel` (rule 2; MEASURED via `device_facts.apc.scene.led_observed`) | ❌ **NO** |
| **`device_facts` fact ids** | `device_facts.FACTS` (9 real ids); cited as `apc.scene.led_colours` / `apc.track.led_colours` in `led_table.py:45-46`, `apc_leds.py:31`, `apc_transport.py:368`, `slot_matrix.py:330`, `probe-apc-buttons.py:10` | 2 cited ids resolve to **nothing** | `device_facts.FACTS` | ❌ **NO** — and unenforceable: `fact()` has zero callers |
| **scene/track LED capability** | `device_facts.apc.scene.led_observed:109` + `.track.led_observed:121` + `.buttons.single_colour:143` (MEASURED, CLOSED); 5 source comments say "unmeasured"; `apc-control-surface-architecture-spec.md:130-131` says UNKNOWN/VENDOR | MEASURED vs "unmeasured" ×6 | `device_facts` at MEASURED (charter tier 1) | ❌ **NO** |
| **eighth_per_cycle** | `sl_grid_sync.EIGHTH_PER_CYCLE:107` (env); `apply_grid_sync:178` (hard 8) | env / 8 | `EIGHTH_PER_CYCLE` | ⚠️ only while env unset |
| **beats per bar** | `sl_grid_state.BEATS_PER_BAR:24` (env); `tail_phase.BEATS_PER_BAR:84` (4); `sl_hud_monitor.bar_beat:56` (4) | env / 4 / 4 | `sl_grid_state.BEATS_PER_BAR` | ⚠️ only while env unset |
| **slots per track** | `apc_grid.NUM_SLOTS:37`; `slot_matrix.NUM_SLOTS:39`; `looper_songs.NUM_SLOTS:35` | 8 / 8 / 8 | `apc_grid` (= `GRID_ROWS`) | ✅ |
| **grid rows/cols** | `apc_grid.py:33-34`; `apc_panel.py:60-61` | 8 / 8 | `apc_panel` | ✅ |
| **grid note range** | `apc_panel:65-66` (0/63); `apc_leds:50-51` (0x00/0x3F); `apc_grid.note_to_row_col:67` (inline) | identical | `apc_panel` | ✅ |
| **engine OSC port** | 10 readers, all `MPE_SL_OSC_PORT` default `"9951"` | 9951 | env default | ✅ |
| **bench listen port** | `sl_osc_session:23-27`; `mpe-looper-session.service:17`; README:120; `CODE-MAP:237` | 9953 | `sl_osc_session` | ✅ |
| **watchdog / health ports** | `sl-watchdog:65` (9961); `sl-health:40` (9954) | single-homed | own module | ✅ (9954 undocumented in CODE-MAP) |
| **fader CCs** | `apc_faders:29-35`; README:31-32 | 48–55 / 56 | `apc_faders` | ✅ (variant branch inert) |
| **transport hold** | `MPE_APC_TRACK_RESET_HOLD_MS` `bench:117` = 3000; README:111 "3 s" | 3000 ms | bench | ✅ |
| **gesture hold** | `MPE_APC_HOLD_MS` `bench:110` = 2000; bench docstring:6 "~2 s" | 2000 ms | bench | ✅ |
| **fader taper floor/ceil** | `loop_mix:56-57` only | −40/0 dB | `loop_mix` | ✅ |
| **fade samples** | `sl_grid_sync.DEFAULT_FADE_SAMPLES:26` = 256; `mpe.env.example:217` = 256 | 256 | `sl_grid_sync` | ✅ |
| **sample rate** | `generate-test-clips.sh:25`; `slot_matrix_spike.py:43` | 48000 | (test fixtures only) | ✅ |
| **`-t` time max** | `run-sooperlooper.sh:21`, `restart-sooperlooper.sh:14`, `smoke-16-loops.sh:13` | 40 | shell default | ✅ |
| **JACK client** | `run-sooperlooper.sh:22`, `sl-health:168`, `sl-watchdog:66` | `mpe-looper` | env default | ✅ |

---

## Environment variable inventory

Every `MPE_*` read anywhere in the looper. **Read at import** means the value is
frozen at module load and a `systemctl set-environment` or `/etc/mpe/mpe.env` edit
needs a service restart — which is most of them.

### Disagreements and hazards (the part that matters)

| Name | Defaults found | Readers | When | Problem |
|---|---|---|---|---|
| `MPE_SL_LOOPS` | `""`→clamped 15 (`sl_limits.py:56`); **`str(NUM_LOOPS)` unclamped (`sooperlooper-apc-bench.py:113`)**; `15` (8 shell scripts); written as `16` by `player-env-parity.pi5.env:24`, `measure-soak.sh:114`, `measure-latency-run.sh:727`; written as `15` by `bootstrap-pi5-looper.sh:46` | 12 modules + 8 scripts | import | **Split-brain.** The clamp is the whole point of `sl_limits` and the bench opts out. Raw `int()` → `ValueError` on typo |
| `MPE_SL_SCRATCH_LOOP` | `-1` (`looper_songs.py:27`, `sl_hud_monitor.py:35`) — **agree**; shipped as `14` in `player-env-parity.pi5.env:27` and `mpe.env.example:234` | 2 of 6 loop-addressing modules | import | Defaults agree; the **config disagrees with both**, and 4 of 6 modules ignore the key entirely → recorded-track data loss |
| `MPE_SL_SYNC_MODE` | `"grid"` in both readers (`bench:118`, `sl_grid_sync.py:263`) | 2 | call | Defaults agree; README:106 implies `freeform` is current |
| `MPE_SL_AUTOSET_LATENCY` | `"1"` (`sl_grid_sync.py:154`); shipped `0` in both parity files; `mpe.env.example:239` documents `1` | 1 | call | Code default and shipped value are opposites; the example file documents the code default, not the shipped one |
| `MPE_LOOPER_EIGHTH_PER_CYCLE` | `"8"` (`sl_grid_sync.py:107`) but `apply_grid_sync:178` hardcodes `8` | 1 of 2 sites | import | Half-honoured |
| `MPE_LOOPER_BEATS_PER_BAR` | `"4"` (`sl_grid_state.py:24`); `tail_phase.py:84` and `sl_hud_monitor.py:56` hardcode `4` | 1 of 3 sites | import | Half-honoured |
| `MPE_SL_SONGS_PORT` | `"9955"` (`looper_songs.py:20`) | 1 | import | **Dead** — overwritten by ephemeral bind at `:395-396` |
| `MPE_SL_OSC_HOST` / `MPE_SL_OSC_PORT` | `"127.0.0.1"` / `"9951"` in 10 and 10 readers | 10 | import | ✅ unanimous |
| `MPE_SL_JACK_CLIENT` | `"mpe-looper"` in 2 Python + 4 shell | 6 | import/call | ✅ unanimous |

### Documented but read by nothing (`config/mpe.env.example`)

All eight are remnants of the offline seam-weld pipeline deleted 2026-08-26. Four of
them are still **actively shipped** in `player-env-parity.pi5.env:25-28`.

`MPE_SL_TAIL_CAPTURE` (:227) · `MPE_SL_TAIL_THRESH` (:228) · `MPE_SL_TAIL_MAX_MS` (:230) ·
`MPE_SL_TAIL_PEAK_MS` (:231) · `MPE_SL_SEAM_WELD` (:233) · `MPE_SL_SCRATCH_CAPTURE_WET` (:235) ·
`MPE_SL_SCRATCH_CAPTURE_FEEDBACK` (:236) · `MPE_SL_MIN_TAIL_WAV_BYTES` (:237) ·
`MPE_SL_SEAM_MERGE_SAMPLES` (:238).
Plus `MPE_SL_TAIL_MODE=extend` in `player-env-parity.pi4.env:19`, read by nothing.
`mpe.env.example:225` still documents *"scratch loop (default 14 on Pi)"*.

### Read by code, documented nowhere — **94 variables**

Neither `config/mpe.env.example` nor `AGENTS.md` mentions any of these.
`AGENTS.md:32` lists the looper's entire env surface as two names.

`MPE_APC_DEBOUNCE_MS` `MPE_APC_FADER_CEIL_DB` `MPE_APC_FADER_FLOOR_DB` `MPE_APC_FADER_INTERVAL_MS`
`MPE_APC_FADER_PICKUP_CC` `MPE_APC_FADER_SILENCE_CC` `MPE_APC_FADER_SMOOTH_MS` `MPE_APC_FADER_SMOOTH_SNAP`
`MPE_APC_FADER_WET_ECHO` `MPE_APC_HOLD_BLINK_START_MS` `MPE_APC_HOLD_MS` `MPE_APC_MIDI_PORT`
`MPE_APC_MK1_GHOST_S` `MPE_APC_SHIFT_NOTE` `MPE_APC_STOP_ALL_NOTE` `MPE_APC_TRACK_RESET_HOLD_MS`
`MPE_APC_TRANSITION_BLINK_S` `MPE_APC_VARIANT` `MPE_CPU_GOVERNOR_PATH` `MPE_CPU_GOVERNOR_UNIT`
`MPE_LOOPER_BEATS_PER_BAR` `MPE_LOOPER_BPM` `MPE_LOOPER_BPM_MAX` `MPE_LOOPER_BPM_MIN`
`MPE_LOOPER_BPM_TARGET` `MPE_LOOPER_EIGHTH_PER_CYCLE` `MPE_LOOPER_FSYNC` `MPE_LOOPER_MAX_BARS`
`MPE_LOOPER_MIN_LOOP_WAV_BYTES` `MPE_LOOPER_SAVE_POLL_S` `MPE_LOOPER_SAVE_TIMEOUT_S`
`MPE_LOOPER_SONGS_DIR` `MPE_LOOPER_SONG_MIN_LEN_S` `MPE_MEASURE_LATENCY_DEADLINE_S`
`MPE_METER_STALE_AFTER_S` `MPE_METER_STATE` `MPE_SLOT_SPIKE_DIR` `MPE_SL_BENCH_LISTEN_PORT`
`MPE_SL_BENCH_PEAK_MS` `MPE_SL_BENCH_REREGISTER_S` `MPE_SL_BENCH_STATE_MS` `MPE_SL_BENCH_WET_MS`
`MPE_SL_CLIP_DB` `MPE_SL_COUNT_IN` `MPE_SL_DIAG_CAPTURE_SEC` `MPE_SL_DIAG_OUT` `MPE_SL_DIAG_SEC`
`MPE_SL_ENGINE_EVENT_POLL_S` `MPE_SL_ENGINE_LOG` `MPE_SL_GOVERNOR_FIGHT_LIMIT`
`MPE_SL_GOVERNOR_FIGHT_WINDOW_S` `MPE_SL_GRAPH_WAIT_S` `MPE_SL_GRID_ANCHOR_WRAP_HIGH`
`MPE_SL_GRID_CLOCK` `MPE_SL_HEALTH_PORT` `MPE_SL_HUD_STALE_S` `MPE_SL_HUD_STATE_FILE`
`MPE_SL_HUD_TRANSPORT_STALE_S` `MPE_SL_HUD_WRITE_INTERVAL_S` `MPE_SL_JACK_CLIENT`
`MPE_SL_JACK_WAIT_S` `MPE_SL_LOOPS` `MPE_SL_LOOP_GAIN` `MPE_SL_LOOP_GAIN_LAW` `MPE_SL_OSC_HOST`
`MPE_SL_OSC_PORT` `MPE_SL_PENDING_TIMEOUT_S` `MPE_SL_PROBE_RESTORE` `MPE_SL_QUANTIZE_TIMEOUT_S`
`MPE_SL_RING_OUT` `MPE_SL_SESSION_LISTEN_HOST` `MPE_SL_SESSION_LISTEN_PORT` `MPE_SL_SONGS_PORT`
`MPE_SL_SURGE_CLIENT` `MPE_SL_SYNC_MODE` `MPE_SL_TAIL_CAP_MS` `MPE_SL_TAIL_FADE_SAMPLES`
`MPE_SL_TAIL_FLOOR` `MPE_SL_TAIL_INPUT_GAIN` `MPE_SL_TAIL_MIN_OVERDUB_MS` `MPE_SL_TAIL_RATIO`
`MPE_SL_TAIL_RESTORE_INPUT_GAIN` `MPE_SL_TAIL_SEAM_END_MS` `MPE_SL_TAIL_SEAM_RATIO`
`MPE_SL_TAIL_SILENT_MS` `MPE_SL_TAIL_TRACE` `MPE_SL_TEST_CLIPS` `MPE_SL_TEST_CLIP_SEC`
`MPE_SL_TIME_MAX` `MPE_SL_WATCHDOG_ALARM_FILE` `MPE_SL_WATCHDOG_PORT` `MPE_SL_XRUN_ALARM_PER_MIN`
`MPE_SL_XRUN_WINDOW_S` `MPE_SOOPERLOOPER_BIN`

**The shape of the problem:** `mpe.env.example` is 13 KB and documents 8 keys nothing
reads while missing 94 that the looper does — including `MPE_SL_LOOPS` itself, the one
key that can manufacture a phantom track. It is not a registry; it is a diary.

**Fix direction:** one `looper_env.py` module in the style of `sl_limits.py` — every
key declared once with its default, its type, its coercion, and the reason it exists;
`mpe.env.example` generated from it; a test that fails when a `MPE_*` literal appears
outside it. That single move closes 🟡 8, the `MPE_SL_SONGS_PORT` dead knob, the
`int()`-on-typo crash, and every future instance of this class.

---

## Dead code verdict

| File | Status | Recommendation |
|---|---|---|
| `scripts/sooperlooper/slot_matrix_spike.py` | Archaeology — referenced by three docs, zero code | **Delete.** SP1/SP2 results already live in `looper_songs.py:73-78, :654-658` and `docs/measurements/multi-clip-slot-spike-2026-08-26.md` |
| `scripts/sooperlooper/spike-load-halt.py` | Archaeology — one measurement doc | **Delete.** Question answered in `PI5-LOOPER-SEAM-WRAP.md` |
| `scripts/sooperlooper/set-input-latency.sh` | Dead — **zero references repo-wide** | **Delete** |
| `scripts/sooperlooper/sl-hud-monitor.py` | Self-declared deprecated shim, still launched by `mpe looper` (`looper.sh:325`) | **Delete the file and the CLI branch.** Launching it starts a second process that contends for the merged session's listen port |
| `sl_grid_sync.set_count_in` (`:205-209`) | Deprecated alias, no callers | **Delete** |
| `slot_matrix.NUM_TRACKS` (`:38`) | Referenced only by tests; runtime uses the bench's env value | **Delete, and move the assertion in `tests/test_slot_matrix.py:63` onto the value the runtime reads** |
| `looper_songs.LISTEN_PORT` / `MPE_SL_SONGS_PORT` (`:20, :380`) | Assigned then discarded | **Delete both** |
| `scripts/sooperlooper/measure_midi_osc_latency.py` | Duplicate instrument — bench has `--measure-latency` | **Keep as a tool, move to `experiments/`**, or fold into the bench flag and delete |
| `scripts/sooperlooper/synthpad.py` | Live tool — the only hands-free bench driver | **Promote.** Move beside the measurement entry point and reference it from `AGENTS.md` |
| `scripts/sooperlooper/diagnose-16loop-crackle.sh` | Live — `mpe looper sl-diagnose` | **Keep, rename** (`diagnose-loop-crackle.sh`) |
| `scripts/sooperlooper/smoke-16-loops.sh` | Live but broken (🟡 6) and misnamed | **Keep, fix the three `seq 0 15`, rename** to `smoke-all-loops.sh` |
| `apc_transport.ARROW_NOTES_MK1/MK2` (`:94-95`) | Live constants that collide with a measured fact (🔴 5) | **Measure, then move to `apc_panel.py` + `device_facts.py`.** Delete from `apc_transport` |
| `apc_faders` mk1/mk2 CC split (`:29-35`) | Both branches identical | **Keep** — the divergence hazard is real and documented; note in the docstring that the branch is currently inert |
| `docs/CODE-MAP.md` §3.4 rows for `apc_footswitch.py` (`:263-264`) | Module does not exist | **Delete the rows** |
| `device_facts.fact()`, `Fact.refuse_with()`, `AUTHORITATIVE`, `unmeasured()` | **Zero callers repo-wide.** Every "citation" is prose in a comment | **Promote, do not delete.** These are the enforcement mechanism the charter §5 and spec §5.4 both assume exists. Wire `refuse_with()` into the capability check and add a test that every `device_facts.<id>` string in the tree resolves — 🔴 5b |
| `scripts/probe-apc-buttons.py` | Ran 2026-08-29, rounds 1–5, results recorded as MEASURED. Its own header (`:10-18`) still describes the question as open | **Keep** — the sensor is Mitch's eyes and the arrow-note question (🔴 5) still needs it. **Rewrite the header** to say what it settled and what remains (`apc.shift.led` is still OPEN) |

---

## What is genuinely good, and should be the template

- **`sl_limits.py`.** The constant, the measurement, the date, the cost, and the
  failure mode in one 66-line file. Everything below should read like it.
- **`device_facts.py`.** The tier system and rule 4 are the right answer to a whole
  class of failure. The `apc.shift.led` entry — recording an *open, unresolved,
  against-expectation* result rather than closing it — is exactly right.
- **`apc_panel.py`.** The panel drawing and the "three wrong answers by reasoning"
  history. Its rules are correct; they are just not yet enforced (🔴 5).
- **`slot_runtime.LAUNCH_COMMANDS`** (`:55-65`) — a constant created specifically so
  there is one answer to "how do you start a loop" instead of two that drift. That is
  the ownership lens applied correctly, by hand, in the right place.
- **`looper_songs`' fsync section** (`:69-87`). The reasoning about what "Saved" has
  to mean on an appliance people switch off at the wall is the best comment in the repo.

The gap between these files and the config layer is the whole finding. The Python
modules have owners. `config/`, the README, and `docs/CODE-MAP.md` do not — and they
are where the number that reaches the engine actually comes from.
