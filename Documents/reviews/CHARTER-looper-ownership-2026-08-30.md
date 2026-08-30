# Charter — looper + controller ownership unification

**Branch:** `refactor/looper-ownership-2026-08-30` (from `dev` @ `0bc0b63`)
**Opened:** 2026-08-30, at Mitch's direction, to run overnight.
**Standard:** code we would put in front of an audio engineer without apologising.

Every agent working this branch reads this file first. It is the governing
document. Where it conflicts with habit, it wins.

---

## 1. The brief, in Mitch's words

> "ANY code smell — especially where there are multiple sources of truth,
> multiple owners of some state or lifecycle, or no unified owner or source of
> truth. What I want at the end is code we could proudly show to audio
> engineers, not some vibe-coded project built as a series of experiments that
> never reached holistic maturity. Rather something that takes all of the
> intentions that are there now, and the code that is there, and creates a
> well thought out, skilfully, thoughtfully crafted codebase — especially as
> we've been hitting many of the same bugs and regressions over again."

The recurrence is the diagnostic. A bug that returns is not a bug that was
fixed badly; it is a bug whose *owner was never decided*. Two writers, and
which one you see depends on call order. Fix the ownership and the bug class
closes. Fix the instance and it comes back next week wearing a different hat.

Read [`Documents/specs/apc-control-surface-architecture-spec.md`](../specs/apc-control-surface-architecture-spec.md)
before anything else. It diagnoses four defects (D1–D4) on the controller side
and is already correct. This charter extends the same lens to the looper.

---

## 2. The canon — what outranks what

When two things disagree, the higher tier is right and the lower one is the
defect to be fixed.

| Rank | Source | Note |
|---|---|---|
| 1 | `device_facts.py` at **MEASURED** or **OWNER** tier | Physical reality. Outranks everything, including us. |
| 2 | `Documents/DECISIONS.md` | Locked decisions with dates and reasons. |
| 3 | `Documents/specs/*.md` | Stated intent. Where two specs disagree, the later date wins and the older one gets corrected. |
| 4 | `scripts/sooperlooper/README.md`, `AGENTS.md` | Operating knowledge. Frequently stale — see §6. |
| 5 | The code as written | What actually runs. |
| 6 | `device_facts.py` at VENDOR/INFERRED tier | A guess with provenance. Never load-bearing. |
| — | **The tests** | **Not canon. See below.** |

### The tests are evidence, not intention

Mitch, explicitly: *"the tests may not all assert correct behaviour, so they
should not be taken to be canonical records of intention."*

A green suite means the code does what some earlier session decided to write
down. It does not mean the code is right. Several of these tests were written
to lock in behaviour that was itself the bug — that is exactly how a
regression becomes permanent.

**Therefore:**

- A test that contradicts canon (tiers 1–3) is **the defect**, not the code.
- You may change or delete such a test. You may **never** do so silently.
  Every test change carries a comment or commit-body line naming the canon it
  now conforms to: *"asserts the pre-2026-08-29 two-writer behaviour;
  contradicts apc-control-surface-architecture-spec §5.3."*
- You may **never** weaken an assertion, loosen a tolerance, add a skip, or
  delete a case merely to get to green. If a test fails and you cannot argue
  from canon that the test is wrong, **the code is wrong.**
- A test whose failure would not have caught the bug it is named for is a
  test that needs rewriting, even if it passes today.

This cuts both ways: the suite is still the only regression net we have, and
1597 passing tests is real work. Treat a red test as a question, not an
obstacle.

---

## 3. The lens — ownership

For every piece of state, every LED, every lifecycle, ask three questions and
write the answer down:

1. **Who owns it?** Exactly one module. Not "these three cooperate."
2. **Who may write it?** Ideally the owner alone; if others may, through what
   single seam?
3. **How would you know if that were violated?** If the answer is "you'd
   notice the bug on the device," that is the finding. Make it a test.

Known and named already — verify each, do not assume:

| Contested thing | Claimants |
|---|---|
| A button's LED | `slot_surface`, `apc_transport`, `track_gesture`, `apc_link`, `sooperlooper-apc-bench` |
| A note number | `apc_panel` (declares itself sole owner) vs `apc_transport` (defines three anyway) |
| A track's state | `TrackGesture`, `SlotRuntime`, `SlotSurface`, `slot_matrix`, plus the engine's own reply |
| Tempo / phase / bar | `GridState`, `sl_grid_sync`, `TrackGesture._maybe_establish_grid`, bench callbacks |
| The tail/ring-out | `TailPhase`, `track_gesture`'s tail methods, `sl_grid_sync`'s `TAIL_*` constants |
| Loop level | `loop_mix.wet_for` (claims sole ownership — check it) |
| **"How many loops"** | README says **16** throughout; `sl_limits.MAX_USABLE_LOOPS` says **15**; `config/platform/player-env-parity.pi5.env` ships `MPE_SL_LOOPS=16`; `sooperlooper-apc-bench.py` is the one Python reader that bypasses `resolve_num_loops()` and so never clamps. Live service runs `-l 15`. |

That row is the whole brief in one line: a number with several homes, one of
which is known to be dangerous, in a repo that already contains a beautifully
written explanation of why it is dangerous.

> **Correction, 2026-08-30 03:0x.** This row originally accused
> `smoke-16-loops.sh` of passing `-l 16`. It does not. Line 12 reads
> `LOOPS="${MPE_SL_LOOPS:-15}"  # 15 usable max — see sl_limits.py`, and the
> `-l 16` literal was removed in `0e9987c` on 2026-08-27 — the same day
> `sl_limits.py` was written. I sourced the claim from the **filename** and
> repeated it as fact, which is precisely the defect this branch exists to fix,
> committed by the document that defines the fix. The config-drift reviewer
> caught it and noted it nearly repeated the error from the same charter.
>
> The real residual issue is smaller and different: the script's *name* still
> says 16, it iterates a hardcoded range against a `LOOPS` that is now 15, and
> it restarts the engine from the ambient environment — so with
> `MPE_SL_LOOPS=16` present it is the fastest route to the phantom config
> *and prints PASS for it*. The 🔴 belongs to the parity env file that supplies
> that variable, not to the smoke script.

### A structural defect is not a bug until you trace the path

Added 2026-08-30, after the coordinator ranked a fourth finding 🔴 on shape
alone and had to retract it (audit §6).

Code that *looks* like the bug — a function omitting a setting its sibling
sends, two stores that *could* disagree, a cache that *might* go stale — is a
candidate, not a finding. Before it gets a severity, walk the path: what reads
this state, in what order, and is there an execution that actually reaches the
bad value? If you cannot write that sequence down, the finding is **structural**
and must be labelled so.

This is not pedantry about severity. Ranking a structural candidate as live
spends the night fixing something that never fires, while a real defect waits.
It is also the same error the whole branch is about — inference promoted to
premise — arriving from the reviewer's side instead of the author's.

Both halves matter. `slot_surface.repaint(self._sl_states)` looked structural
and was a live process kill. `apply_freeform` omitting `smart_eighths` looked
live and reaches nothing. Only tracing tells them apart.

### What good looks like

`sl_limits.py` is the standard. One constant, one home, and the reason it
exists travels with it — including what it cost to learn. Anything we build
should read like that module. When you finish a change, ask whether it reads
like `sl_limits.py` or like the thing `sl_limits.py` was written to prevent.

---

## 4. Scope

**In** — everything looper-adjacent, per Mitch:

- `scripts/sooperlooper/**` — all modules, including `sl-watchdog.py`,
  `sl-health.py`, `sl_hud_monitor.py`, `looper_session.py`, the shell scripts
- `scripts/sooperlooper-apc-bench.py` — the orchestrator
- `patch_browser/touch_browser_looper_songs.py`, `looper_songs.py` — the
  second consumer of looper state, and therefore a prime suspect
- `tests/` — every looper/controller test, under §2's rules

**Out** — Surge, patch scanner/normalisation, wifi modal, touch browser
internals unrelated to the looper. Do not refactor them. If a looper fix
requires touching one, keep the touch minimal and say so.

---

## 5. Hard rules

**Audio safety outranks the task.** `AGENTS.md` §"Audio output safety". Mitch
is asleep and headphones may be on the desk or on his head — you cannot tell.
**Make no sound overnight.** LED and MIDI work is safe; anything that could
trigger playback, recording, or a level change is not. Do not raise a level to
diagnose silence, ever. If a change cannot be proven without making sound,
leave it for the morning and say so.

**CPU is the scarcest resource.** `DECISIONS.md` § 2026-08-18. No subprocess
in a periodic loop — a `python3` fork is ~400 ms on the Pi, so once every 5 s
is 9% of a core forever. Any new timer or poll states its cost × cadence in
the commit body.

**`device_facts` rule 4 constrains the capability check.** The spec's §5.4
says a colour a control cannot show should raise. That is right *only for
MEASURED/OWNER facts.* Raising on a VENDOR or INFERRED guess would encode a
guess as law and let the code tell Mitch "impossible" on unmeasured grounds —
precisely the failure `device_facts` was written to end. So: **capability
violations raise on authoritative tiers and warn on unmeasured ones.**

**The device is an APC mini mk2** (`aconnect` client 28, card 3, verified
2026-08-30). The mk1 paths are live code that no hardware here exercises.
Do not delete them on that basis alone — but do not trust them either, and
say plainly which branch is covered by evidence.

**Facts get recorded the moment they are established**, at the right tier,
in `device_facts.py`. Not at the end of the session. That rule exists because
breaking it cost a week.

**No note literal outside the registry** once the registry lands. Enforced by
test, not by docstring. A rule a build cannot fail is not a rule.

---

## 6. What can and cannot be verified tonight

The Pi (`raspberrypi5.local`) is up, on `dev`, with the APC mini mk2 connected
and `mpe-looper-session` + `mpe-sooperlooper` running.

**Verifiable overnight:** unit suite on laptop and on the Pi; that the session
starts, stays up, and logs clean after each stage; MIDI port and variant
resolution; that LED writes reach the device without error; OSC command-path
health via read-only checks.

> **Correction to this correction, 2026-08-30 03:4x.** What follows says
> `device_facts.unmeasured()` returns `[]` and reads that as "nothing left to
> measure." The Stage 1 builder caught the flaw: the list was empty because the
> **open questions had never been written down as facts at all**, not because
> they were answered. An empty `unmeasured()` is only as honest as the fact
> base is complete, and a fact base records what someone thought to record.
> Two live unknowns were missing entirely — the mk2 bank-arrow notes and the
> fader CCs, both load-bearing, both still VENDOR recall. They are now recorded
> and `unmeasured()` returns 2.
>
> So the button-colour half below is right and stands. The conclusion drawn
> from the empty list was not. This is the third time in one night that a
> confident reading of a written artifact beat reading the source — which is
> the finding, not the footnote.

**Stage 0's button-colour half is done — corrected 2026-08-30, 02:5x.** This section
originally said the button colours were UNKNOWN and that the probe was Mitch's
job in the morning. That was wrong, and wrong in the exact way this whole
branch exists to fix: a stale claim about hardware, written confidently, in a
document whose subject is stale claims about hardware. `device_facts.unmeasured()`
returns `[]`. The probe ran on **2026-08-29**, three rounds, with Mitch reading
the panel:

| Fact | Tier | Content |
|---|---|---|
| `apc.scene.led_observed` | MEASURED | Scene buttons are **green only**. 0 = off, 2 = blinking green, every other velocity = solid green. The grid's RGB palette indices are not honoured. |
| `apc.track.led_observed` | MEASURED | Track row identical, in **red**. |
| `apc.buttons.channel_response` | MEASURED | LEDs answer on channel 0 (`0x90`) and **nothing else**. All 16 channels painted at once; only `0x90` lit. The channel axis is **exhausted, not sampled**. |
| `apc.buttons.single_colour` | MEASURED | **CLOSED as a bounded negative.** Three states per button — off, on, blink. No addressing scheme tried produces any other colour. |
| `apc.buttons.all_have_leds` | OWNER | Every button has an LED, Shift included. |

Three consequences, all load-bearing for this branch:

1. **The capability check may now raise.** These are MEASURED, therefore
   authoritative, therefore `device_facts` rule 4 is satisfied. The hedged
   version in §5's original note applies only to facts that are still
   VENDOR/INFERRED — of which there are now none for the buttons.
2. **All colour-carrying UI must live on the 8x8 grid.** Not a preference, a
   measured constraint. Any design that wants a third colour on a scene or
   track button is impossible, and we may now say so on authoritative grounds.
3. **Five files cite fact ids that do not exist** — `led_table.py:45-46`,
   `apc_leds.py:31`, `apc_transport.py:368`, `slot_matrix.py:330`,
   `probe-apc-buttons.py:10`, all naming `device_facts.apc.scene.led_colours`
   / `.apc.track.led_colours`. The real ids end `_observed`. All five also
   still describe the claim as vendor-tier and unmeasured, which stopped being
   true the day before this branch opened. The worst is `slot_matrix.py:328`:
   a standing TODO telling the next session to implement yellow scene LEDs
   "if the probe shows they can" — the probe ran, and `apc.scene.led_observed`
   records velocity 13 as *not honoured, just green*. A trap pointed at a
   refuted outcome.

   **Correction, 2026-08-30 03:0x.** An earlier version of this line said those
   citations "raise `KeyError`". They would — but nothing calls them.
   `fact()`, `refuse_with()`, `unmeasured()` and `AUTHORITATIVE` have **zero
   callers anywhere in the repo**; every one of the five is prose in a comment.
   That is the sharper and worse finding, and it has two consequences this
   charter must own:

   - `apc-control-surface-architecture-spec.md:102` says `Fact.refuse_with()`
     raises "so the rule is executable rather than aspirational." It is
     aspirational. It has never executed.
   - **§5's capability rule is therefore unimplementable as written** —
     there is no boundary to enforce it at yet. Building that boundary, and
     giving the fact base its first caller, is the concrete Stage 1 deliverable
     rather than a documentation fix.

   Five bad citations accumulating in a single day is exactly what a fact base
   with no callers predicts: prose cannot fail a build.

**Still not verifiable overnight:** whether a cue is *good UI* — blink versus
colour versus grid. That is Mitch's ear and eye. Our job is to make trying
three options cost three one-line edits.

Also not verifiable: whether a cue is *good UI*. That is Mitch's ear and eye.
Our job is to make trying three options cost three one-line edits.

---

## 7. How the work runs

Driven by `/loop` in dynamic mode, self-pacing overnight. Each cycle dispatches
**fresh-context subagents** — a reviewer never inherits the builder's context,
and an auditor never inherits the reviewer's. That independence is the only
reason the findings are worth anything.

Per `~/.claude/skills/review-loop/SKILL.md`:

```
review (parallel, fresh)  →  audit (fresh, verifies every claim against code)
                          →  fix (P0/P1 confirmed only)
                          →  suite green  →  next cycle
```

**Staging** — each stage is one commit, revertible alone, suite green, so the
morning device pass can bisect:

| Stage | Work | Depends on hardware? |
|---|---|---|
| 1 | Control registry — every note in one place, invariant test | No |
| 2 | LED compositor — one writer to the wire; delete hand-rolled diffs | No |
| 3 | Ownership — one owner per control; delete `clear_unwired_surfaces` | No |
| 4 | Looper state ownership — track state, clock/phase, tail | No |
| 5 | Binding table — press/hold/shift routing as rows | No |
| 6 | Grid behind the compositor | No |
| 0 | Capability probe — **Mitch, morning** | Yes, eyes |

Artifacts land in `Documents/reviews/` under the existing naming convention.

### Honest reporting

`DECISIONS.md` and `device_facts.py` are full of hard-won admissions of being
wrong, and that is the most valuable content in this repo. Match it. If a
stage is half-done, say half-done. If the suite went green because a test was
changed, say which test and why. If something is unproven without hardware,
say unproven — do not let "tests pass" stand in for "it works," because on
this project that substitution has cost multiple evenings and is the exact
failure shape `DECISIONS.md` § 2026-08-15 is named after.
