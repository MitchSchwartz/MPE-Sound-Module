# Multi-clip slot matrix — spikes SP1/SP2/SP4/SP7

**Date:** 2026-08-26 (America/Toronto)
**Platform:** Raspberry Pi 5, SooperLooper 1.7.9 arm64, JACK 128×2 @ 48 kHz
**Spec:** [`multi-clip-per-track-spec.md`](../../Documents/specs/multi-clip-per-track-spec.md) §Spike (rev 3)
**Harness:** `scripts/sooperlooper/slot_matrix_spike.py` (`044c136` + instrument fixes)
**Register:** **measured** unless stated otherwise.

Run conditions: all 16 loops empty at start, no take loaded, no MIDI input,
Surge silent. Loops cleared afterwards.

---

## Instrument audit — run this before reading any number below

Two instruments in the first version of this harness **could not fail**, and
both produced plausible numbers. Recorded here because the numbers were nearly
written into the spec.

| # | Defect | Symptom | Fix |
|---|---|---|---|
| 1 | Load wait was *"state is no longer empty"* | On the second slot of a track the loop is already occupied, so the predicate was true the instant it was asked. Reported `load_loop p95 = 4.5 ms, min = 3.3 ms` — the OSC `get()` round-trip floor, on every call | Wait for `loop_len` to reach the **new** clip's length; alternate two clip lengths so a stale buffer cannot satisfy the wait |
| 2 | Save wait was *"file exists and > 64 bytes"* | Stopped the clock when SL wrote the WAV header. Flat 5.1–5.4 ms regardless of clip size — ~280 MB/s to an SD card | Wait for the expected byte count (`44 + frames × 8`) |

**Positive control** (`--control`), added after defect 1 and passing:

| clip | size | measured load |
|---|---|---|
| 1.0 s | 0.4 MB | 1.9 ms |
| 4.0 s | 1.5 MB | 6.2 ms |
| 16.0 s | 6.1 MB | 23.7 ms |

≈ 3.9 ms/MB, linear. The instrument registers the work. **A flat reading here
means the numbers below are a floor — do not use them.**

---

## SP1 — save/load latency across the full matrix

16 tracks × 8 slots = **128 clips**, ~4 s each (alternating 4.0 s / 3.0 s).

| call | n | min | median | p95 | max |
|---|---|---|---|---|---|
| `load_loop` | 128 | 4.6 | 6.0 | **7.1** | 7.7 ms |
| `save_loop` | 128 | 2.1 | 2.1 | **2.3** | 2.4 ms |

**Full-matrix save total: 0.3 s for all 128 clips.** All 128 loads reached the
expected length; no misses.

**Caveat on `save_loop`.** 2.1 ms for a ~1.5 MB WAV is ~700 MB/s — that is the
**page cache**, not the SD card. SooperLooper does not fsync and the harness
cannot see writeback. The figure is the right one for *UI responsiveness*; it is
**not** a durability guarantee. A power cut after a "Saved" toast can still lose
the file. Not a v1 blocker, but it belongs in the save/load design.

**Verdict: PASS with margin.** A full 128-clip song save is ~0.3 s of OSC-side
work. The touch-UI timeout budget is not under pressure.

---

## SP2 — single-swap `load_loop` latency (the launch path)

20 alternating swaps on one loop.

| n | min | median | p95 | max |
|---|---|---|---|---|
| 20 | 4.7 | 5.9 | **6.8** | 6.8 ms |

**Budget:** the swap must fit inside one bar. At 120 BPM 4/4 that is 2000 ms.

**Verdict: PASS with ~300× margin.** Load is not the constraint on quantized
slot switching. Scaling from the control (≈3.9 ms/MB), even a 30 s clip
(~11 MB) lands near 45 ms — still two orders of magnitude inside the bar.

---

## SP4 — switch at one boundary

Sequence: `mute_on` → `load_loop(B)` → `mute_off` → `trigger`, all in one pass.

- state before = 4 (playing), state after = **4 (playing)**
- whole sequence: 500 ms wall clock (dominated by the harness's own settle sleeps)

**Verdict: PASS at the state level.** The loop is playing the incoming clip.
Two clips cannot sound simultaneously here by construction — `load_loop`
replaces the buffer rather than mixing — so the "one pitch, not two" ear check
is a check on the *bench* sequencing, not on SL. Deferred to P2 on hardware.

---

## SP7 — switch queued while the ring-out overdub runs (settles OPEN-4)

New in rev 3: a take now closes into a one-pass overdub (`117f4cc`, `1a90d51`),
so a track sits in `OVERDUBBING` for a full pass and a switch can be queued
against it.

Sequence: `trigger` → `overdub` (on) → `overdub` (off) + `mute_on` +
`load_loop(B)` + `mute_off` + `trigger`.

- state during overdub = **5 (overdubbing)** — confirms the track is busy
- state after = **4 (playing)**

**Verdict: PASS at the state level — supports OPEN-4 option (b), *defer*.**
SooperLooper accepts overdub-off and a full switch in the same burst and lands
playing. Since the overdub already ends at the wrap, and a quantized switch
lands on that same boundary, the two coincide with nothing lost.

**Limitation — this is not yet a full test of OPEN-4.** Surge was silent, so the
overdub recorded digital zero. The state machine is confirmed; the *audible*
seam, where the overdub has actually summed a ring-out into the head just
before the buffer is replaced, is not. That case needs a played take and is
P2 hardware work.

---

## SP6 — Scene Launch note numbers (mk1) — **MEASURED**

`aseqdump -p "APC MINI"` subscribed alongside the running bench (ALSA seq allows
a second subscriber, so the looper kept working). Mitch pressed Scene 1–8 twice,
then once more with Shift held.

| button | note | matches constant |
|---|---|---|
| Scene Launch 1–7 | **82–88** (`0x52`–`0x58`) | `SCENE_LAUNCH_NOTES_MK1 = range(0x52, 0x59)` ✅ |
| Scene Launch 8 | **89** (`0x59`) | `NOTE_STOP_ALL_CLIPS_MK1` ✅ — scene 8 **is** Stop All |
| Shift | **98** (`0x62`) | `NOTE_SHIFT_MK1` ✅ |

Every mk1 constant the spec depended on is confirmed. They were previously
**recalled**; they are now **measured**.

**Shift does not modify the scene notes.** With Shift held, Scene 1–8 send the
identical notes 82–89. Shift+Scene chords are therefore distinguishable only by
software shift-state, which the bench already tracks. P3 is viable as designed.

**OPEN-1 settled for mk1: 7 usable scene rows.** The 8th physical button is
Stop All. Option (a) — 7 scene rows, row 7 pad-only — stands. mk2 remains
unverified (no mk2 hardware on hand); `SCENE_LAUNCH_NOTES_MK2` is still recalled.

### ⚠️ The mk1 Shift ghost did not reproduce

`apc_transport.py` states: *"Pressing Shift on mk1 often spuriously fires Scene
1–8 / Track Select notes within a few ms."* On that basis `a1bcb4b` filters all
scene notes, the track-overlap notes `0x30–0x37`, and Stop All for
`MK1_GHOST_SHIFT_S = 0.08` after Shift goes down.

**This capture contains no ghost.** `Note on 98` is followed directly by
`Note on 82` — the deliberate press — with no spurious notes in between, and no
stray events anywhere else in 52 lines.

One capture does not refute "often"; the ghost may be intermittent or
condition-dependent. But the stake is high enough to re-test deliberately:

- If the ghost is real, the filter is correct and must stay.
- If it is rare or absent, **the filter is swallowing genuine Shift+Scene
  presses in the first 80 ms** — which is precisely the P3 gesture, and would
  present as "the scene button sometimes does nothing".

The discriminating test is trivial: press **Shift alone**, several times,
touching nothing else, and capture. Any note other than 98 is a ghost.
**Do this before P3 wires the scene rows.** Filed as SP8.

---

## Not run

| # | Why |
|---|---|
| SP3 / SP3b | Cancel paths. Model-level restored in `c509ed9` (they had been dropped by `2500782`); hardware confirmation still outstanding — **SP3b has only ever run against `FakeSlEngine`** |
| SP5 | Scene row launch across 16 tracks — needs the scene handler, which is P3 |
| ~~SP6~~ | **Done — see above.** |

---

## Consequences for the spec

1. **Latency is not a design constraint.** SP1/SP2 clear their budgets by two
   to three orders of magnitude. Lazy-loading inactive slots (spec §Risks) is
   still right for *memory*, but no longer needs defending on load time.
2. **OPEN-4 → option (b), defer**, on the state-level evidence. Do not close it
   until the audible case runs.
3. **`save_loop` durability is an open question** the spec does not currently
   raise. Page-cache timing is not persistence.
