# APC looper — UX spec (Session View semantics)

*Last updated: 2026-08-11 (America/Toronto)*

**Status:** Approved direction for v1 on branch `yolo/looper-phase0`. Replaces the v0 Scene Launch transport hack (`Sc1/2/5/8` = record/overdub/play/clear).

**Related:** [`LOOPER-PLAN.md`](LOOPER-PLAN.md) · `patch_browser/control_surfaces/` · `scripts/mpe-looper.py` · touch header HUD (`touch_browser_draw._draw_looper_hud`)

---

## Product intent

Use the APC mini the way Ableton Session View expects — not as arbitrary transport buttons. Each **grid pad** is an independent audio clip (frozen PCM at record time). **Scene Launch** controls whole rows. **Shift + Scene 8** stops everything.

v0 proved audio path + MIDI + mk1 LEDs. v1+ is **controller-native semantics**.

---

## Locked decisions (2026-08-11)

| # | Topic | Decision |
|---|--------|----------|
| 1 | **Record start** | **Immediate** on empty pad tap. Bar-quantized *start* comes later — not the harder route; we add it once bar position exists for display + scene stop anyway. |
| 2 | **Monitor** | **On** while recording (hear yourself live through the chain). |
| 3 | **Scene Launch** | **Match Ableton** — launch/stop the row; when stopping, **finish at end of current bar** (quantize period), not instant cut. |
| 4 | **LEDs** | **Yes** — green = playing, red = recording, red blink = recording emphasis, amber/yellow = has clip, stopped (mk1 velocities first; mk2 RGB later). |
| 5 | **Maps** | **Same logic** mk1 + mk2; only LED protocol differs. |

---

## Control surface map (target)

### 8×8 grid

Notes `0x00–0x3F` — row `0` = top scene row in UI terms (see `ControlSurfaceMap.grid_position`).

| Pad state | Tap |
|-----------|-----|
| **Empty** | Start recording this clip immediately (monitor on). |
| **Recording** | Stop early → **auto-play** if any audio captured. |
| **Loop full** | **Auto-play** (pad LED green) — no extra tap. |
| **Playing** | Stop at **next bar boundary** (quantize period). |
| **Stopped** (after bar-end stop) | Tap to **launch** again. |

Each clip: fixed length in bars (`MPE_LOOPER_BARS`, default 4) @ tempo (`MPE_LOOPER_BPM` or MIDI clock when synced).

### Scene Launch (right column)

| Control | Action |
|---------|--------|
| **Scene Launch 1…8** | Launch or stop **entire row** (all clips in that scene). Ableton row semantics: if any clip in row is playing, row stop → all stop at bar end; else launch armed/ stopped clips in row. |
| **Shift + Scene Launch 8** | **Stop all clips** (mk1 label: `stop_all_clips`). Not bare Scene 8 alone. |
| **Shift** | Hold `MK1_SHIFT_NOTE` (`0x62`); mk2 `0x7A`. |

### Deferred (same spec family, later)

| Control | Intent |
|---------|--------|
| **Faders 1–8** | Column / track volume (mix gain into sum bus). |
| **Master fader** | Looper + live output ceiling (ties to LOOPER-PLAN gain staging). |
| **Send / Device** | TBD. |
| **Arrow keys** | TBD — useful if grid &gt; 8×8 or device commands. |

### Retired (v0 hack only)

Do **not** ship Scene 1/2/5/8 as global record/overdub/play/clear. Keep behind a dev flag or remove once grid handler lands.

---

## v1 build slice

Prove multi-clip before full 64:

| Scope | Detail |
|-------|--------|
| **Active slots** | **Row 0, all 8 columns** — independent clips. |
| **Scene Launch 1** | Row 0 launch/stop (Ableton rules + bar-end stop). |
| **Shift + Scene 8** | Stop all. |
| **Rest of grid** | Inert (LEDs off). |
| **Engine** | Replace single `LooperSession` with **clip matrix** — slot → ring buffer + state; one mix bus (live Surge + sum of playing clips). |

Acceptance: record and play multiple clips in row 0; Scene 1 stops row at bar; Shift+Scene 8 clears transport.

---

## Header timing display (Boss-style)

**Goal:** See where you are in the rhythm while looping — like a Boss RC-5/600 two-tier display, adapted to the touch browser **status header** (44px bar — no room for two full graphic bars).

### Reference (Boss)

- Upper tier: **beat** within the current bar (1–4 in 4/4).
- Lower tier: **bar** within total loop length (e.g. bar 2 of 4).

### MPE header widget (compact)

When the on-device looper is active (or any clip recording/playing), show in the header (extend / replace external-pedal BPM badge when internal looper owns timing):

```
┌─────────────────────────────────────────────┐
│  … patches …     [████░░░░] 2/4    128     │  ← status header
│                   beat bar   bar/total BPM  │
└─────────────────────────────────────────────┘
```

| Element | Behavior |
|---------|----------|
| **Beat bar** | Horizontal micro-bar, 4 segments (or `beats_per_bar` from config). Filled segments = current beat (1-based). Updates every beat from looper master clock. |
| **Bar fraction** | Text `n/N` — e.g. `1/4`, `2/4`, `3/4`, `4/4` for a 4-bar loop. `N` = `MPE_LOOPER_BARS`. |
| **BPM** | Optional right edge — internal tempo or synced MIDI clock (reuse `LooperClockMonitor` / `~/.mpe_midi_clock_state.json` when clock in enabled). |

**Show when:** Any clip recording or playing; or user toggle (existing **Settings → Looper → show HUD**).

**Hide when:** Looper idle and no external clock — same gating as today’s `looper_hud_should_show`.

**OLED path:** Same data model; OLED gets fraction + beat only (no graphic bar if pixels tight).

### Clock source (v1)

- **Internal:** `MPE_LOOPER_BPM` + sample counter or monotonic beat scheduler derived from audio period callbacks.
- **Later:** MIDI clock in (`midi-clock-in`) becomes master; header reads shared state file.

Bar-boundary stops (scene / clip stop) use the same master clock so display and quantize stay aligned.

---

## Architecture notes

| Layer | Change |
|-------|--------|
| **Pure logic** | `ClipSlot`, `ClipMatrix`, bar clock — unit tests, no ALSA. |
| **MIDI** | Grid + shift + scene handlers; drop `looper_transport` scene remap for product. |
| **Audio** | `mpe-looper.py` mixes N slots; monitor on = live always in bus. |
| **UI** | `LooperTimingState` published to touch browser (file, socket, or shared module) for header widget. |
| **LEDs** | Per-pad + per-scene from matrix state (`apc_led.py` extended). |

---

## Open questions (non-blocking)

- **Early stop on pad tap while recording** — v1 yes or wait for full bars only?
- **Empty row Scene Launch** — no-op or start record on all empty pads in row? (Ableton: typically no-op.)
- **Clip length** — one global `MPE_LOOPER_BARS` vs per-row later.

---

## Implementation order

1. Bar clock + timing state (feeds header + quantize stops).
2. `ClipMatrix` tests — 2 slots, mix, bar-end stop.
3. Grid MIDI + mk1 LEDs — row 0 only.
4. Scene 1 + Shift+Scene 8.
5. Header beat bar + `n/N` in touch browser.
6. Faders → column gain.
7. Quantized record start (optional polish after bar clock exists).
