# APC bank switching — hardware test protocol

**Purpose:** verify that when the viewport moves, *all three* bindings move with
it — the clip LED, the pad control, and the fader level — and that nothing is
left pointing at the track that scrolled away.

**Applies to:** `dev` at or after `f304536` (Ableton-style horizontal track lane).
**Status of the code under test:** unit-tested only (711 pass). The arrow note
numbers in `apc_transport.py` are **recalled, not measured** — step 0 exists to
settle them before anything else is trusted.

---

## Why this needs hardware and can't be judged by ear

The failure that matters is a binding that *didn't* travel — fader 1 still
writing track 0 after banking to tracks 8–15. Turning fader 1 down and hearing
something get quieter does not distinguish that from success. So every level
assertion here reads the engine instead:

```bash
scripts/sooperlooper/dump-loop-levels.py          # human-readable
scripts/sooperlooper/dump-loop-levels.py --json   # for diffing
```

It sends `/sl/N/get` for `wet` and `state` on every loop. Pure read — safe
during a live take. A loop that exists always answers; silence means the command
path is wedged, and the script exits non-zero saying so rather than printing
zeros.

**Diff, don't eyeball.** Capture before and after each bank change:

```bash
scripts/sooperlooper/dump-loop-levels.py --json > /tmp/before.json
# ... perform the step ...
scripts/sooperlooper/dump-loop-levels.py --json > /tmp/after.json
diff /tmp/before.json /tmp/after.json
```

---

## Setup

No human recording needed — load the fixtures:

```bash
scripts/sooperlooper/smoke-16-loops.sh    # 16 clips, distinct content
scripts/sooperlooper-apc-bench.py         # in a second shell
```

Give each track a **distinct level before you start**, so a mis-bound fader is
visible as a wrong number rather than a plausible one. Ramp them: track 0
quietest through track 15 loudest. Record that baseline as `/tmp/baseline.json`.

Bench startup should print the bank line: `bank: tracks 1-8 of 16`.

---

## Step 0 — arrow note numbers (blocks everything else)

```bash
scripts/sooperlooper-apc-bench.py --dump-midi
```

Press **Up, Down, Left, Right**. Record the four note numbers.

| Arrow | Expected (mk2) | Expected (mk1) | Measured |
|---|---|---|---|
| Up | `0x70` (112) | `0x40` (64) | |
| Down | `0x71` (113) | `0x41` (65) | |
| Left | `0x72` (114) | `0x42` (66) | |
| Right | `0x73` (115) | `0x43` (67) | |

If these don't match, banking silently does nothing and **every step below fails
for that one reason**. Fix `ARROW_NOTES_MK1` / `ARROW_NOTES_MK2` in
`scripts/sooperlooper/apc_transport.py` first — one tuple, no call sites.

Also confirm the variant resolved correctly: the bench prints the APC label at
startup. Wrong variant = wrong tuple = same symptom.

---

## Step 1 — LEDs travel

With tracks 1–8 showing, note which pads are lit and in what colour (playing vs
idle vs recording differ — see `led_table.py`).

Press **Down** (pages by 8 → tracks 9–16).

- [ ] **1a** The clip row now shows tracks 9–16's states. A track that was
      playing in the old bank and idle in the new one must go dark.
- [ ] **1b** **No pad retains its old colour.** The row is cleared before the
      repaint precisely so a stale lit pad can't claim a track is running.
      This is the failure the player cannot debug from the surface.
- [ ] **1c** Rows 1–7 are dark and stay dark. Nothing writes them anymore; the
      bench blanks all 64 pads at startup so leftovers from a previous build or
      a crash are gone.
- [ ] **1d** Bank back **Up**. Tracks 1–8's LEDs are correct again, including
      any track whose state changed while it was off screen.

## Step 2 — pad control travels

Still on tracks 9–16:

- [ ] **2a** Tap column 0's pad. `dump-loop-levels.py` shows **track 8**
      changed state. Track 0 is untouched.
- [ ] **2b** Hold column 0's pad ~2 s. **Track 8** clears. Track 0 still has
      its loop.
- [ ] **2c** Bank Up. Tap column 0. Now **track 0** responds, not track 8.

## Step 3 — fader level travels

The core of the change: fader N used to drive tracks N *and* N+8.

- [ ] **3a** On tracks 1–8, move fader 3. Only **track 2**'s wet changes.
      Track 10 does not — that pairing is gone.
- [ ] **3b** Bank Down. Move fader 3. Now only **track 10**'s wet changes.
- [ ] **3c** Master fader still scales **all 16**, on screen and off.

## Step 4 — the fader must not jump on first touch after banking

Faders have no motors, so the physical position after a bank change is a lie
about the new track. Every anchor is dropped on a bank change; the first CC
re-anchors and changes nothing.

- [ ] **4a** Set fader 3 near the top. Bank Down (track 10 is quiet).
      **Nudge fader 3 slightly.** Track 10's wet must **not** jump to match the
      fader's physical position — the first move anchors only.
- [ ] **4b** Keep moving it. Now it tracks, relative to where you anchored.
- [ ] **4c** Repeat banking Up. Same behaviour for track 2.

A jump here is the most likely regression and the most audible one.

## Step 5 — off-screen tracks keep their levels and keep playing

- [ ] **5a** Bank Down, wait a few bars, bank Up. Tracks 1–8's wet values are
      **unchanged from `/tmp/baseline.json`**. Banking moves bindings, never
      levels.
- [ ] **5b** Tracks 9–16 keep playing audibly while banked off screen.

## Step 6 — the held-pad hazard (regression, fixed this branch)

This one destroyed a loop before the fix. Two hands:

- [ ] **6a** **Hold** column 0's pad on tracks 1–8. Keep holding. With the other
      hand press **Down**. Keep holding ~3 s, past the hold threshold, then
      release.
- [ ] **6b** **Track 0 must still have its loop.** Before the fix the abandoned
      pad-down survived the bank change and `poll_hold()` fired the long-press
      seconds later, `undo_all`-ing a track that wasn't even on screen.
- [ ] **6c** Track 8 (which took over that pad) is also unaffected — the
      release lands on it but must be a no-op.

## Step 7 — banking arithmetic

- [ ] **7a** **Up/Down page by 8.** From tracks 1–8, Down → 9–16.
- [ ] **7b** **Shift+Right nudges by 1.** From offset 0 → tracks 2–9. Column 0
      is now track 1 (0-indexed: loop 1). Confirm with a fader move.
- [ ] **7c** **Bare Left/Right do nothing.** Nudging is gated behind Shift.
- [ ] **7d** **Clamp, never wrap.** At tracks 1–8 press Up repeatedly: stays at
      1–8, does not jump to 9–16. At 9–16 press Down repeatedly: stays.
- [ ] **7e** Shift+Left/Right at both extremes likewise clamps.
- [ ] **7f** The bench prints `bank: tracks N-M of 16` on every *actual* change
      and stays silent when clamped.

## Step 8 — interaction with the existing transport

- [ ] **8a** Shift is used for nudging now. Confirm **Shift + Stop All Clips**
      still stops all loops (tap) and clears all (hold 3 s) — Shift being held
      for banking must not have broken the combo, and vice versa.
- [ ] **8b** Recording a new take while banked to 9–16 lands on the right track.

---

## Known gaps, expected — not bugs

- **No bank indicator.** With 8 of 16 showing, nothing on the surface says which
  half you're on; only the bench's stdout does. Worth fixing, not in scope here.
- **Banking away mid-take** leaves that loop recording with no pad bound to stop
  it. That's the documented "off-screen tracks keep their state" property. Bank
  back and press the pad.

## Recording results

Note the APC variant, the four measured arrow notes, and any step that failed
with its `dump-loop-levels.py` diff. A failure in steps 1–3 means a binding did
not travel; in step 4, the anchor drop; in step 6, `release_pad()`.
