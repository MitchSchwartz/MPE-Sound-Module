# Morning handoff — looper + controller ownership branch

**Branch:** `refactor/looper-ownership-2026-08-30` (from `dev`, not pushed)
**Suite:** 1746 passed, 3 skipped, 2451 subtests — from 1631/3/714 at the start
**Not deployed.** Nothing ran on the Pi. No sound was made.

Read this first, then the commit messages — they carry the reasoning and each
one is revertible alone.

---

## The 60-second version

Five things owned state that nothing owned in one place: the LEDs, the clock,
the ring-out, "is this take on disk", and `/etc/mpe/mpe.env`. Each now has one
owner and a test that fails if a second one appears. Nine real defects were
fixed along the way; two reported ones were refuted; **six claims were
corrected, five of them mine.**

The pattern behind the repeat bugs has a name now, and it is not carelessness:
**a reading that is identical whether the thing works or not.** The LED cache
said painted while the panel was dark. The flush ledger said clean while the
take was gone. The CPU lint said clean while blind to four of the nine files it
was pointed at. Three instances in one night, same shape.

---

## What needs YOU, in priority order

### 1. Five minutes with the APC — the only thing blocking tracks 9–15

Banking is dead on the mk2 and has never worked. The recalled arrow notes
`0x70–0x73` are actually scene buttons 1–4 (MEASURED 2026-08-29), so the bench's
scene branch takes them and `continue`s before `handle_arrow` is ever reached.

```
stop the session
sooperlooper-apc-bench.py --dump-midi
press Up / Down / Left / Right
```

Record the four notes at **MEASURED** tier in `device_facts.py` and as rows in
`control_registry.CONTROLS`. The startup banner now says banking is unavailable
instead of advertising it, and `resolve_arrow_notes` returns `{}` for mk2 rather
than a guess. **Do not fill these in by reasoning — reasoning has produced three
wrong answers about this panel already.**

Same trip, same five minutes: move each fader and confirm the CCs. They are
VENDOR-tier recall and the failure is silent — a wrong CC is indistinguishable
from a fader nobody touched.

### 1b. The button capability probe is DONE — do not run it again

`scripts/sooperlooper/probe-apc-buttons.py` is stage 0's other half, and its
sensor is your eyes. **It already ran, 2026-08-29, three rounds, with you
reading the panel.** Five facts came out of it and all five are authoritative:

| Fact | Tier |
|---|---|
| `apc.scene.led_observed` — scene buttons are green only; 0 off, 2 blink, everything else solid | MEASURED |
| `apc.track.led_observed` — track row identical, in red | MEASURED |
| `apc.buttons.channel_response` — LEDs answer on channel 0 and nothing else; all 16 channels painted, only `0x90` lit | MEASURED |
| `apc.buttons.single_colour` — **closed as a bounded negative**: three states per button, no addressing scheme produces another colour | MEASURED |
| `apc.buttons.all_have_leds` — every button has one, Shift included | OWNER |

I am flagging this because the overnight fallback prompt described the button
probe as still pending, and it is not. `device_facts.unmeasured()` returns
exactly **two** facts, and neither is a button colour:

- `apc.bank_arrows.notes` — VENDOR
- `apc.faders.ccs` — VENDOR

Those two are §1 above, and `--dump-midi` closes both in the same five minutes.

**Nothing structural was allowed to depend on either.** Verified in
`control_registry.check_colour`: it raises only when every supporting fact is
MEASURED or OWNER, and merely *warns* when any is VENDOR or INFERRED. That is
`device_facts` rule 4 made executable — this code cannot tell you your device
is incapable of something on the strength of a manufacturer's PDF, which is
precisely what happened twice on 2026-08-29. Before this branch the rule was
aspirational: `fact()`, `refuse_with()` and `unmeasured()` had **zero callers
anywhere in the repo**, so it had never once executed.

### 2. Your ear on one deliberate change

**The ring-out cap is now one CYCLE, not one bar** — on a 4-bar cycle that is
4× longer. The spec (§6) has always said one cycle; the code said one bar, which
was correct while every take read as one bar and stopped being correct when
`d06fb08` introduced multi-bar takes. Whether it *sounds* right is yours to
judge. `MPE_SL_TAIL_CAP_MS` is the fallback only; the cap now comes from
`GridState.cycle_s`.

### 3. Your eyes on the panel

Stage 2 rebuilt LED ownership, and **no test and no subagent can see the
panel.** Deliberate, canon-backed changes you may notice:

- Scene row 0 lit from session start (was dark since boot — a constructor
  ordering bug).
- Stop All release hands `0x77` back to the scene indicator instead of darkening
  it.
- Shift no longer darkens the scene column or grid rows 1–7.
- Bank change sends only changed pads.

If one looks wrong, `8106513`'s message lists every behaviour change with its
canon, and reverting that commit alone is safe.

### 4. One decision I did not make for you

The song manifest stores the **composed** level — `save_song` reads the engine's
`wet`, which already has master and law baked in. Reload with the master
somewhere else and loops return to their saved *absolute* level while the master
fader stops corresponding to them until you next move it. Fixing it changes how
songs already on the appliance sound, so it is flagged, not silently changed.

---

## What was fixed

| | Defect | Where |
|---|---|---|
| P0 | Silent-session launch killed the process on arrival | `7c57107` |
| P0 | Reconnect repainted then erased 56 pads + 8 scene buttons, permanently | `8106513` |
| P0 | Scene launch buttons dark since session start | `8106513` |
| P0 | Ring-out capped at one bar against a spec that says one cycle | `5e12100` |
| P0 | **A pending save on one slot made another slot report `clean`** | `64af5ec` |
| P0 | `reset()` orphaned saves; next take marked clean over pre-reset bytes | `64af5ec` |
| P0 | `pi5.env` set `MPE_SL_LOOPS=16` — index 15 is a phantom that eats writes | `ccde96a` |
| P0 | `sl-restart` never told the bench the engine was new | `57f6dcd` |
| P1 | `_end_tail` raced across OSC threads; `overdub` is a toggle, so a double fire welded room tone onto the take | `5e12100` |
| P1 | A song could not restore its own grid (`bars` defaulted to 1) | `5e12100` |
| P1 | `wet` had two writers; the README claimed one | `64af5ec` |
| P1 | CPU lint blind to the main loop of 4 of its 9 modules | `2d370b9` |

**The worst of these is the flush ledger.** `_flush` was keyed by `loop` while
the thing being flushed is a `(loop, slot)` pair — the stored tuple even carried
the slot, and nothing compared it. The caller it lied to is the guard whose own
message reads *"REFUSING to switch — the take on the current slot did not reach
disk."* The wrong key routed around the one safety net protecting an unsaved
take.

## What was refuted

- **"Banking while holding a pad unlinks another track's clip."** Already fixed
  before this branch opened; `release_pad()` names that exact failure in its
  docstring and `apply_view` calls it on every bank-change path. A docstring
  describing a bug that was *fixed* was read as a bug that *exists*.
- **"Sending `eighth_per_cycle` before `tempo` re-arms the doubling bug."**
  Refuted against upstream `engine.cpp` — the mutation is gated on
  `_smart_eighths`, which is disabled first, and tempo must go last because
  `set_tempo` IS the phase reset.

## Corrections to the record — five of six were mine

The charter said `smoke-16-loops.sh` passes `-l 16`; I had read the filename,
not the file. I said five files "raise `KeyError`" when nothing calls them at
all — the sharper finding being that the fact base had zero callers. I read an
empty `unmeasured()` as "nothing left to measure" when it meant "nobody wrote
the questions down." I called `d06fb08` the regression when it never touched
`tail_phase.py`. I reported `looper_songs.py:473` as a broken song-load restore
when it is inside `stop_playback` — a deliberate Stop All phase reset; I had
read three lines without reading the function they sit in.

And once inside the enforcement itself: my first lifecycle guard matched the
event name as a bare substring, so deleting the emit while leaving the comment
that explains it passed clean. Found by injecting, not by reading. *Prose cannot
fail a build* is the charter's own sentence.

They are recorded because the corrections are the useful part, and they are only
cheap because each was caught by someone who did not write it.

---

## Stages — all complete except the one that needs you

| Stage | | |
|---|---|---|
| 1 | Control registry | `aeb7a61` |
| 2 | LED compositor — one writer to the wire | `8106513` |
| 3 | One owner per control | absorbed by Stage 2 |
| 4a | Clock + tail | `5e12100` |
| 4b | Track state | `64af5ec` |
| 5 | Binding table | `085e8d3` |
| 6 | Grid behind the compositor | absorbed by Stage 2 |
| **0** | **Capability probe** | **blocked on your eyes** |

Stage 6 was not skipped and I did not do it: Stage 2 already achieved it. Every
grid paint goes through the compositor — `slot_surface` → `LAYER_SURFACE` for
the 8×8 under multigrid, `track_gesture` → `LAYER_GESTURE` for the clip row
otherwise — and the only remaining write to the wire is `apc_link.py:107`,
which *is* the compositor's queue drain.

**I stopped here rather than inventing a Stage 7.** The next thing this branch
needs is not more refactoring, it is the device pass. Stage 2 changed how the
panel looks and Stage 5 rewrote how it routes; both are unverified on hardware.
Stacking more panel work on top would give you two unverified changes to bisect
instead of one.

## Still open

- **6 of 12 lint evasions** still get through. The specific ones are written
  into `periodic_loop_lint`'s docstring rather than left to be rediscovered.
- **`--measure-latency` records zero samples under `MPE_SL_MULTIGRID=1`**, which
  is what the appliance runs — the stamp lives in a clip-row branch multigrid
  never reaches. Pre-existing, found during Stage 5, deliberately not fixed
  there because it is not routing. It is an instrument that cannot sample in the
  live configuration, which is the exact thing AGENTS.md's doctrine is about.
- **`apc_panel.scene_press_row` has no production caller** — kept on purpose as
  the independent statement of old behaviour that the Stage 5 differential
  checks against. Delete it after the device pass.
- **`docs/CODE-MAP.md` is stale** independently of this work (it lists
  `apc_footswitch.py`, which does not exist). Untouched.
- **Nothing is proven on hardware.** Every claim here is from traced paths,
  injected violations and upstream engine source. In particular **the refactored
  event loop has never been executed** — no test runs `run_bench`, which needs
  `rtmidi` and a device. "Tests pass" is not "it works," and on this project
  that substitution has cost multiple evenings.
