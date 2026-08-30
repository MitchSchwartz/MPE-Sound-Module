# Audit — ownership review, cycle 1

**Branch:** `refactor/looper-ownership-2026-08-30`
**Date:** 2026-08-30, overnight
**Auditor:** the coordinating session, against the six cycle-1 reviews.

Per `~/.claude/skills/review-audit/SKILL.md`, nothing here is taken on a
reviewer's word. Each row says **how** it was checked. Where a reviewer was
wrong, or where I was, that is recorded rather than quietly dropped.

Reviews audited: control identity · clock/grid/tail · track state · lifecycle ·
config drift · test integrity · LED ownership. All seven are in.

---

## 1. Verified by execution or direct inspection

### ✅ A — Banking is dead on the attached hardware — **P0, live**

`ARROW_NOTES_MK2 = (0x70,0x71,0x72,0x73)` (`apc_transport.py:94`) lies inside
`SCENE_COLUMN_MK2 = range(0x70,0x78)` (`apc_panel.py:78`).

Checked by running the real resolvers, not by reading them:

```
note 0x70 (up   ) shift=False -> scene_press_row=7   SWALLOWED
note 0x71 (down ) shift=False -> scene_press_row=6   SWALLOWED
note 0x72 (left ) shift=False -> scene_press_row=5   SWALLOWED
note 0x73 (right) shift=False -> scene_press_row=4   SWALLOWED
```

Identical with `shift=True`. In the bench loop the scene branch `continue`s
(~`:690`) before `handle_arrow` (`:722`), and `handle_arrow` is the **only**
caller of `set_view` (`:430`). So the viewport is pinned at offset 0 and
tracks 9–15 cannot be reached from the surface.

Confirmed live on the appliance — every session start logs:

```
APC [1] APC mini mk2 (mk2) | bottom row -> 8 of 15 tracks
  (Up/Down page 8, Shift+Left/Right nudge 1) | ...
```

The instrument advertises a feature it cannot perform, on every boot.

**Note the fix has a half I cannot do.** `0x70–0x73` are very likely not the
arrow notes at all — the README flags them unverified, and they are exactly
scene buttons 1–4. Determining the real notes needs `--dump-midi` and Mitch's
fingers. What is fixable tonight is the *class*: a registry that refuses two
controls claiming one note, and a reachability test. `device_facts` rule 4
forbids me ruling anything impossible from a vendor document, so the note
question stays open and marked open.

### ✅ B — The silent-session launch killed the process — **P0, live. FIXED.**

`slot_surface.py:355` called `self.repaint(self._sl_states)`; `repaint` is
`(self, *, force=False)`. Verified by introspection:

```
repaint signature: (self, *, force: 'bool' = False) -> 'None'
positional-acceptable params: []
```

`TypeError` on the first due launch, in a bench loop with no `try`, after
`load_loop` and `trigger` have already gone out. The unused loop variable is
the tell: the branch had never executed, so the feature its own comment calls
"the only thing that will fire them" has never once worked.

Live: `MPE_SL_MULTIGRID=1` in `/etc/mpe/mpe.env` **and** in the running
process's environ (checked `/proc/677162/environ`).

Fixed in `7c57107`. The regression test was run against the reverted code and
fails there with the exact `TypeError` — it is not another test that passes
either way.

### ✅ C — The ring-out cap shrank by the bar count — **P0, same-day regression**

`tail_phase.cap_for` returns `(60.0/bpm) * BEATS_PER_BAR` — **one bar**,
labelled `"one bar"`. `Documents/specs/looper-timing-model-spec.md:192` states
the cap is **one cycle**: *"cap — one cycle; a ring-out longer than that is not
a ring-out."* Canon rank 3 beats rank 5, so the code is the defect.

Until `d06fb08` (today, 02:11) a take was one bar unconditionally, so bar ==
cycle and the code was right. That commit introduced `BAR_CANDIDATES = (1,2,4,8)`
with `derive_tempo` returning `(bpm, bars)` — and `cap_for`, unchanged, became
`cycle/bars`. A 4-bar cycle now caps the ring-out at a quarter of spec.

**Correcting the reviewer on its own evidence.** It said the proof was already
in `MPE_SL_TAIL_TRACE` as `exit_reason=cap`. I read the trace off the Pi. It is
**229 rows, every one `decay`**, all `tail_id=1, loop=0, exit_elapsed=2.0353` —
one ring-out on the defining take, sampled 229 times, written 01:35, i.e.
**36 minutes before the commit that caused the regression.** It is one loop-0
event, and loop 0 takes the `loop_len` branch anyway, so this trace could not
show the bug even if it postdated it. The code/spec divergence stands on the
source; the trace neither confirms nor refutes, and should not be cited as if
it did.

### ✅ D — Grid establishment is gated on a state the defining take may not be in

`track_gesture.py:303` and `:341` both gate `_maybe_establish_grid()` on
`sl_state == SL_STATE_PLAYING`. `SL_STATE_PLAYING = 4` and
`SL_STATE_OVERDUBBING = 5` are distinct, and the tail machinery is driven by
`prev_sl == SL_STATE_OVERDUBBING`, so a take closing through the weld sits in
OVERDUBBING. Structurally confirmed. Whether it *always* lands there through
the live seam needs a device pass — marked accordingly.

### ✅ E — The tail seam constants are dead

Every name appears exactly once, at its own definition:

```
TAIL_SEAM_RATIO 1 · TAIL_SEAM_END_MAX_S 1 · TAIL_MIN_OVERDUB_S 1
TAIL_WELD_INPUT_GAIN 1 · TAIL_WELD_FADE_SAMPLES 1
TAIL_WELD_RESTORE_INPUT_GAIN 1 · COUNT_IN 1 · anchor_phase 1
```

Six tail constants, a count-in flag, and a phase function — all dead. So "the
tail has constants in two homes" is not the finding; **one home is empty** and
has been shipping in `config/mpe.env.example` as though it were live.

### ✅ F — The fact base has no callers at all

```
callers of fact() / refuse_with() / unmeasured() / AUTHORITATIVE
outside device_facts.py:  (none)
```

This supersedes my own earlier framing. The five prose citations do not raise
`KeyError` in production because **nothing ever calls them**. Consequences:

- `apc-control-surface-architecture-spec.md:102` claims `refuse_with()` makes
  rule 4 "executable rather than aspirational." It has never executed.
- The charter's capability rule has no boundary to be enforced at.

Building that boundary is Stage 1's real deliverable. Prose cannot fail a build
— which is precisely why five bad citations accumulated in one day.

### ✅ H — The reconnect erases the matrix, and the erasure sticks

The LED review's headline, and it is the sharpest statement of the whole
ownership problem. In `reopen_apc` (`sooperlooper-apc-bench.py:509-511`):

```
slot_surface.repaint(force=True)   # paints the matrix
repaint_scenes(force=True)         # paints the scene column
transport_leds.repaint()           # -> clear_unwired_surfaces()
```

That last call darkens `RESERVED_GRID_NOTES` (8-63 — rows 1-7, the entire
matrix) and all 8 scene notes, which `SlotSurface` owns. Its docstring says
"not wired until P3"; it is stale by two features.

The part that makes it permanent: the forced repaint has already written the
full 64-entry map into `SlotSurface._painted`, so the next diffing repaint
sees no change and sends **nothing**. The reviewer measured 12 LEDs erased and
zero messages across the following 50 poll cycles. For an idle set the pads
stay dark until a colour genuinely changes — i.e. never.

So after any APC re-enumeration the player's stored takes vanish from the
matrix and do not come back. Live, since `MPE_SL_MULTIGRID=1` (finding G).

The same collision fires at startup on the scene column alone — the
`TransportButtonLeds` constructor (`apc_transport.py:405`) runs after the
bench paints at `:378` — which means **under multigrid the scene launch
buttons have been dark since session start**, invisible because a dark scene
button is also what a correct idle one looks like.

`apc_transport.py` contains zero occurrences of the string `multigrid`. It
darkens surfaces it has never heard of. Charter Stage 3's "delete
`clear_unwired_surfaces`" now has its concrete proof.

Two further results from that review worth carrying:

- **Four diff caches** — `_painted`, `_scene_painted`, `_last_vel`,
  `_led_last` — plus one writer with none (`poll_hold_led`, measured at
  **87,174 writes to a single note in 0.3 s**), and none at the chokepoint
  where one would actually work. The race resolves by private-cache history,
  not call order, which is worse: it is not deterministic.
- **Every `force=` flag is a manual cache invalidation** issued because the
  cache's owner does not own the wire. Proof: the bench's startup
  `repaint_scenes(force=True)` is silently undone 60 lines later.
- **Compositor risk, inverted.** Ranked HIGH — but the danger is a *corrected*
  panel being read as a regression, because the current ordering is accidental
  and there is no intended behaviour to preserve.

### ✅ G — Multigrid is ON, so the multigrid defects are live

`MPE_SL_MULTIGRID=1` in `/etc/mpe/mpe.env` and in the live process environ,
against a code default of `"0"` (`sooperlooper-apc-bench.py:273`). The
lifecycle reviewer explicitly flagged this as needing someone with the Pi.
Answered: **on**. Its LED-erasure and CPU findings are therefore live, not
latent, and rank accordingly.

---

## 2. Corrections — reviewers, and me

| # | Claim | Verdict |
|---|---|---|
| 1 | Charter: `smoke-16-loops.sh` passes `-l 16` | **❌ My error.** Line 12 is `LOOPS="${MPE_SL_LOOPS:-15}"  # 15 usable max — see sl_limits.py`. The `-l 16` literal went in `0e9987c`, 2026-08-27. I sourced it from the *filename* — the exact defect this branch exists to fix, committed in the document defining the fix. Corrected in place. |
| 2 | Charter: five files cite ids that "raise KeyError" | **⚠️ My error, in the misleading direction.** True only if called; nothing calls them. The real finding (F) is worse. Corrected in place. |
| 3 | Reviewer: tail trace already shows `exit_reason=cap` | **❌ Not supported.** 229 rows, all `decay`, one loop-0 event, predating the regression by 36 min. Finding C survives on source evidence alone. |
| 4 | My count: 11 `maybe_reregister` sites in the bench | **⚠️ Off by one.** Lifecycle reviewer counted 10 in the bench, 13 total. Its enumeration matches the file. |
| 5 | My framing: re-registration is a CPU problem | **⚠️ Wrong emphasis.** ~4 packets/s is not a CPU issue. The measurement-integrity angle — an unconditional silent repair making a real fault invisible — is the finding. Reviewer was right to re-rank. |
| 6 | Reviewer: no mk1/mk2 prefix-shadowing bug | **✅ Verified negative, recorded as such.** Discriminator is `"mk2" in name or "mkii" in name`; both live port strings classify correctly. Recorded so nobody re-investigates. |
| 7 | LED reviewer: suite is "1602 green" | **❌ Wrong count.** Full suite run twice here: **1599 passed, 3 skipped, 106 subtests**. Minor, but a reviewer's own numbers get audited too. |
| 8 | LED reviewer: my regression test only half-closes the harness gap | **✅ Correct, and taken.** The first version wrote `_grid_wait` directly. Rewritten in `ac146c0` to queue the launch through a real pad press with `grid_boundary` injected; re-checked against the reverted code, still fails there. |

Six corrections in one cycle, two of them mine, both from inferring a fact
rather than reading its source. That is the same failure the branch is about,
and it is worth stating plainly: this codebase's defect is not carelessness,
it is *confident restatement*. It catches careful readers too.

---

## 3. Prioritized action matrix

Reported severities re-rated against live/latent status and blast radius.

| P | Finding | Verdict | Effort | Status |
|---|---|---|---|---|
| **P0** | Silent-session launch → `TypeError`, process death | ✅ | quick | **fixed `7c57107`** |
| **P0** | Arrow/scene note collision → banking dead, tracks 9–15 unreachable | ✅ | half-day + Mitch | structural half tonight |
| **P0** | Ring-out cap = one bar, spec says one cycle (regression from `d06fb08`) | ✅ | quick | next |
| **P0** | `SlotRuntime.reset()` leaves `_flush` alive → silent take loss, model says clean | reported | half-day | audit pending |
| **P0** | Banking while holding a pad unlinks another track's clip | reported | quick | audit pending |
| **P0** | `reopen_apc` repaints then erases 56 of 64 pads + 8 scene buttons; four private caches make it permanent | ✅ | half-day | Stage 2/3 |
| **P0** | Scene launch buttons dark since session start under multigrid (ctor ordering) | ✅ | quick | Stage 3 |
| **P0** | `player-env-parity.pi5.env` reinstates `MPE_SL_LOOPS=16` + `SCRATCH=14` | reported, **latent** | quick | bootstrap is last writer today |
| **P1** | Grid establishment gated on PLAYING; take closes into OVERDUBBING | ✅ | half-day | needs device pass |
| **P1** | `loop_mix`'s "nothing else writes wet" is false — `looper_songs.py:677` | reported | half-day | audit pending |
| **P1** | `_tail` shared across OSC threads and main loop, unlocked | reported | half-day | audit pending |
| **P1** | Song load resets engine cycle to one bar; manifest never stores `bars` | reported | half-day | audit pending |
| **P1** | Dead tail-seam constants shipped in `mpe.env.example` as live | ✅ | quick | fold into Stage 1 |
| **P1** | Fact base has zero callers; capability rule unenforceable | ✅ | half-day | **Stage 1 deliverable** |
| **P1** | `test_periodic_loop_lint` passes; 10 of 12 evasions get through | reported | half-day | audit pending |
| **P2** | Scene-note test fixture uses 7 notes; production wires 8 | reported | quick | hides the D2 race |

---

## 4. What is not proven

- **What the panel looks like.** Unchanged, and unchangeable without eyes.
- **Whether the defining take always closes through OVERDUBBING** on the live
  seam (finding D). Structural only.
- **The real mk2 arrow notes.** Needs `--dump-midi` and Mitch.
- **Pi-scaled CPU numbers.** The lifecycle reviewer measured on x86 and
  extrapolated ×3, and said so.
- **Anything audible.** No sound was made. Every finding above is from source,
  logs, process environ, and one CSV.
