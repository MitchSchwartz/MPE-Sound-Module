# Engineering decisions log

Dated rows agents and humans can trust over stale specs. Full research lives in
OM-Repo [`internal/projects/mpe-synth-launch/research/`](https://github.com/opsMachine/OM-Repo/tree/main/internal/projects/mpe-synth-launch/research).
Orientation canon: OM-Repo [`GROUNDING.md`](https://github.com/opsMachine/OM-Repo/blob/main/internal/projects/mpe-synth-launch/GROUNDING.md).

---

## 2026-08-14 — The Pi onboard headphone jack is never an audio output

**Decision:** The Pi's onboard PWM headphone jack (`bcm2835 Headphones`) is
**never** a valid output for this product. Mitch, 2026-08-14: *"never use pi
output jack, it's poor quality and useless for us."* Not as a fallback, not as
a last resort, not as an idle sink.

**Answers** GROUNDING §6.2, the last open product question from the 2026-08-13
audit, and closes the last live false-green (§2.11 / table row 3): today the
appliance plays through that jack with **no interface attached** and reports
`state=ok`, across four separate detection paths.

**Consequences — this is the no-device fix (GROUNDING §4 S1a, ~1 day, own PR):**

| Change | Detail |
|---|---|
| `standalone`, no interface | New terminal state **`state=no-device`**. Fail loud and legible, consistent with the JACK-only philosophy — never a silent wrong-device success |
| Remove onboard from detection | Four paths reach it: `detect-audio-device.sh:154` (tier 3 name match) and `:178` (tier 4 last resort), `detect-jack-device.sh:127` (tier-3 card pattern) and `:140` ("first non-virtual card", which excludes only `Loopback\|vc4hdmi\|UAC2`) |
| `usb-host` profile | Must bind **UAC2**, not the Pi jack. Tier 3 originally existed to give `usb-host` an idle sink; under this decision that role is UAC2's. **Prior art exists** — `yolo/shutdown-timing-fix` (orphan, no PR, 78 behind `dev`) carries `db14367 fix(audio): usb-host without Sound Blaster must use UAC2, not Pi headphone`. Triage that commit before rewriting it from scratch (GROUNDING §2.9 / Q1) |
| Green test to invert | `test_usb_host_idle_no_sound_blaster_uses_pi_headphone` asserts `TIER=3` on a `bcm2835` device list — it pins the behaviour now banned. Rewrite, don't delete silently |
| HUD | Badge is under 10 characters, so "No audio interface — plug one into USB" needs a second HUD element and a non-error semantic token. Also fix: a stale `state=degraded` file currently renders as a healthy `JACK` badge, and a Pi 5 (no headphone jack) renders `NONE·fai` |

**Ship before** any demo unit boots without an interface attached — a
customer's first impression would otherwise form on the worst converter in the
box while the system reports success.

**Does not block** the SooperLooper Session A/B test; that runs with an
interface attached.

---

## 2026-08-13 — No Python on the JACK audio thread

**Decision:** Python must not run on the realtime audio path. No
`python3-jack-client` callback, no NumPy mixer in-process with JACK, no
`arecord`/`aplay`/`snd-aloop` pipeline (Phase 0 — also retired).

**Python stays for:** touch UI, OSC/MIDI control, file I/O, systemd wrappers,
`pedal-to-osc.py`, calibration — everything that is *not* mixing audio every
graph period.

**Rationale:** [`recipe-answer-01.md`](https://github.com/opsMachine/OM-Repo/blob/main/internal/projects/mpe-synth-launch/research/recipe-answer-01.md)
— shipped audio products keep interpreted code off the audio thread; GIL contention
with the touchscreen UI is the binding risk; pre-committed rule: **no outcome
where Python-in-callback wins.** Mitch confirmed 2026-08-13.

**Supersedes:** `Documents/specs/looper-jack-client-spec.md` Tasks 1–11 (Python
JACK client path). Keep §A.2 (`audioop` analysis) and §B.3/criterion 19
(fail-open topology) as reference only.

**Next:** Evaluate Phase 2 options that respect M3 — see [`DIRECTION.md`](DIRECTION.md).

---

## 2026-08-13 — Looper is a product requirement

**Decision:** The looper is core to the instrument (not an exploration). Phase 1
without a looper is incomplete product, not a shippable v1.

**Rationale:** Mitch confirmed 2026-08-13 ("seemingly" → affirmed). Matches
`LOOPER-PLAN.md` product framing and `product-narrative.md`.

**Does not decide:** which looper implementation — that is gated on the
SooperLooper Pi test and the audio-input question (see GROUNDING §6.3).

---

## 2026-08-13 — Phase 2 path: evaluate before building

**Decision:** Do **not** start Phase 2 implementation code until the
**SooperLooper Pi test** completes (~4 h timebox,
[`looper-vetting.md` §7](https://github.com/opsMachine/OM-Repo/blob/main/internal/projects/mpe-synth-launch/research/looper-vetting.md)).

**During the test:** prove B1 (`dry=0`), B7 (xruns beside Surge), B10 (20-min play).
Try mic/guitar on the interface for ~10 min — audio input in/out is decided by
playing in the same session, not as a separate prerequisite.

**Dropped:** play `yolo/looper-phase0` on the Pi — branch predates JACK; checkout
downgrades to deleted ALSA stack (repriced after PR #50).

**Pre-committed rule** (from recipe analysis, not yet executed):

| SooperLooper fits? | GIL experiment tail | Choose |
|---|---|---|
| Yes | (irrelevant) | **Adopt SooperLooper** — Python never touches audio |
| No | ugly | **Build compiled JACK client** (C/Rust) |
| No | flat | **Build compiled** — simplest kernel, not Python |

**Supersedes:** `Documents/HANDOVER-mixer-looper-2026-08-13.md` TL;DR (Tasks 1–4,
whole #48 merge). Handover retained for Phase 1 landing notes only.

---

## 2026-08-14 — Loop UX: pad-per-loop, not Zynthian's row-per-loop

**Decision:** If SooperLooper is adopted, the APC grid maps **one pad = one
SooperLooper loop** — not Zynthian's row-is-a-loop / column-is-a-command
layout. Commands (multiply, reverse, oneshot, feedback, undo_all-as-clear)
live on a **held-pad shift layer**, not fixed columns.

**Rationale:** Mitch's actual play style is Ableton Session View — up to 16
loops playing simultaneously, each independently startable/droppable, played
as finished takes rather than one loop kept open and overdubbed EDP-style.
Zynthian's layout was sized for a 5×8 Key 25 (40 pads, 6 loops); our 8×8 APC
mini has room to keep the clip-grid picture **and** the full command set,
which `looper-ux-comparison.md` §4 assumed was a forced choice. It isn't —
the constraint was Zynthian's hardware, not the engine.

**Consequence:** `APC-LOOPER-UX.md`'s pad state machine, LED semantics, and
Scene Launch rows survive close to as specced. Per-pad clear — missing from
both the code and the approved spec — is **`undo_all` on that loop**, free
from adoption. Multiply needs its own UI: hold a clip pad, its row becomes a
1×/2×/4×/8× multiplier strip.

**Scale target:** design and test for **16 simultaneous loops**, not 64.
Mitch: "I don't expect that I can play 64 clips at the same time... I'm just
sort of trying to discuss how I would play." Recorded-but-idle count (up to
64 across the grid) is a separate, cheaper memory question — see Session B
additions below.

**Downgraded from the vetting docs:** overdub/undo/substitute are no longer
the headline argument for adoption — Mitch's model uses clip-clear (random
access) instead of an undo stack, so build-your-own would not need to
implement SooperLooper's Echoplex-style overdub engine either. What adoption
still buys, narrower but intact: a compiled realtime engine we don't have to
write, the fail-open parallel topology, sync/quantize/latency compensation,
click-free seams, and persistence.

**Open:** whether `pitch_shift` (rubberband-dependent) matters enough to make
rubberband a hard build requirement rather than the optional fallback
`looper-vetting.md` §4 assumed. Verify at the bench (Session A).

---

## 2026-08-14 — Master gain: two independent limiters + sidechain ducking, live now; watchdog bypass deferred

**Decision:** Ship two small limiter JACK clients, not one shared one:

```
Surge XT → limiter_live  → system:playback
Surge XT → sooperlooper loop inputs
sooperlooper outs → limiter_loops → system:playback
```

`limiter_loops` takes a **sidechain feed from the live signal**, ducking loop
level as live playing gets louder, on top of a static per-bus ceiling
(loop bus targets roughly −6 dBFS so live + loops has real headroom before
the true sum, which nothing else bounds, approaches 0 dBFS).

**Rationale:** jackd has no mixer — it sums client outputs in float32 and
hands off to ALSA's `S24_3LE` conversion, which is where an unbounded sum
clips. The parallel fail-open topology (Surge direct to `system:playback`,
loops fail-open through their own bus) means **no single point in the graph
sees the true N+1 sum**, so a shared full-chain limiter would have to sit
after everything — which reintroduces the single point of failure the
parallel topology exists to avoid. Two independent limiters keep the
fail-open granularity (lose `limiter_live`, loops are still safe and vice
versa) while giving both paths a real, if separately-scoped, ceiling.
Sidechain ducking is a musicality refinement on top — it lowers how often
the static ceiling is audibly hit, but has attack time and cannot replace
the hard limiter as the actual guarantee.

**Deferred, revisit after Session B results:** full-chain limiter with the
same watchdog/`mpe_engine_reconcile_reset()`-style bypass-on-death pattern
PR #50 already built for jackd itself — would give a real N+1 guarantee
instead of a statistical one, at the cost of a new failure-visibility
requirement (limiter death must surface as its own HUD state, or the
appliance drifts back into the class of unfalsifiable health claim
`GROUNDING.md` §5.3 item 8 is entirely about). Not built now.

**Build vs adopt for the limiter itself:** check trixie arm64 for a
packaged, headless LV2/JACK peak limiter first (x42 `dpl.lv2`, Calf,
`zita-dpl1` — **none verified yet**, same `dak ls` treatment the loopers
got, not a recommendation). If none is packaged and headless, a peer limiter
is small enough (gain stage + lookahead peak detection + smoothed gain
reduction, no allocation in the callback) that writing it doubles as a dry
run of the compiled-JACK-client toolchain the build-our-own looper fallback
would need anyway.
