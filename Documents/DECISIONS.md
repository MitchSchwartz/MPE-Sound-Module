# Engineering decisions log

Dated rows agents and humans can trust over stale specs. Full research lives in
OM-Repo [`internal/projects/mpe-synth-launch/research/`](https://github.com/opsMachine/OM-Repo/tree/main/internal/projects/mpe-synth-launch/research).
Orientation canon: OM-Repo [`GROUNDING.md`](https://github.com/opsMachine/OM-Repo/blob/main/internal/projects/mpe-synth-launch/GROUNDING.md).

---

## 2026-08-15 — Browse carousel + filter pane (spec locked, not built)

**Spec:** [`specs/touch-browser-browse-carousel-spec.md`](specs/touch-browser-browse-carousel-spec.md)

**Layout (concept J):** horizontal track `[Filter 532 | Nav 268 | Patch 532]`. Default **Home** (Nav + Patch); **Filter** stop shows Filter + Nav; patch fully off-screen. Filter pane uses masonry instrument tags (full `INSTRUMENT_VOCAB`); **no** inline chip row in nav; **no** funnel button in nav header.

**Gestures:** **No third-party library** (pygame + evdev — nothing mature to drop in). New in-repo `gesture_router` + `browse_carousel`. **Zone at pointer-down** only — not angle-based scroll/pan lock. **Left screen edge 48px** is the sole carousel grab strip in v1; nav list scrolls in the interior; patch pane is mixer-only. Filter tag tap persists selection and does **not** change stop. No bottom tab bar; nav scroll uses existing edge hints.

**Deferred:** inner seam handles, nav-header swipe, J2 right-pane swap, right-edge global menu (future 12–16px rim).

**Supersedes** inline chip UX in instruments epic Phase 4 for browse UI only — metadata work unchanged.

---

## 2026-08-14 — Who owns the looper grid clock (open — ranked, gated)

**Settled:** the grid must not be owned by clip 0. Deleting clip 0 has to be
deleting a clip like any other — ordinary, indistinguishable from deleting clip
7, in behaviour and in code (Mitch, 2026-08-14). Every `if self.loop == 0`
branch in the control layer exists only to serve clip 0's hidden second job as
the clock. Spec: [`specs/looper-transport-clock-spec.md`](specs/looper-transport-clock-spec.md).

**Open:** *where the clock lives instead.* Three candidates, ranked by cost.
Do not build down the list before falsifying up it, and do not run an ear gate
against an unfixed clock.

**Gate ladder:**

1. **Internal sync + `tap_tempo` phase** — `scripts/sooperlooper/spike-internal-sync-phase.py`.
   No new process, no new code paths. Same ear protocol as spec Task 0.3.
2. **If (1) fails:** JACK transport Task 0, using the **fixed** Python spike
   (`jack_timebase.py`) as a measuring instrument only. Ear test 0.3.
3. **If (2) confirms BBT phase:** production clock is a **compiled JACK timebase
   client**; Python stays the control layer over OSC `/bpm`. No exception to the
   2026-08-13 no-Python-on-the-audio-thread rule is needed, and none is taken.

**Independent of all three:** the control-layer work on
`yolo/looper-transport-clock` — clip 0 made an ordinary clip, `sync_from_sl`
covering all loops and all states — **lands regardless of which clock wins.**

| # | Option | RT-thread cost | Status |
|---|---|---|---|
| 1 | SL internal sync (`sync_source = -3`) + explicit phase anchor | **none** | **Unverified — check first** |
| 2 | JACK transport (`sync_source = -1`) + compiled C timebase master | none (C, no DSP) | Fallback if 1 fails |
| 3 | MIDI clock (`sync_source = -2`) + SPP | none, but jittery | Rejected unless we separately need to clock outboard gear |

**Why option 1 is not already dead.** `8d7a426` was read as proving internal
sync unusable. It does not. That commit failed because it flipped `sync_source`
mid-flight and **never established phase** — not because internal sync lacks
phase. If SL exposes any way to say *"the downbeat is now"*, internal sync gives
an externalised grid with defined phase, no new process, and nothing on the
realtime thread.

The candidate is `tap_tempo`. `8d7a426` itself guessed at this and never checked
it:

```python
send("/set", ["tap_tempo", 0.0])  # noop pulse — anchors UI if needed
```

**This is the highest-leverage unverified assumption in the looper work.**
Check: `sync_source=-3`, set tempo, send `tap_tempo`, record two clips a minute
apart, confirm a shared downbeat. Same experiment as spec Task 0.3, against a
config needing no new code.

**Why option 2 is C and not Python.** A JACK timebase master is on the realtime
thread. See *2026-08-13 — No Python on the JACK audio thread* below. A timebase
callback does no DSP and touches no audio buffers, so there is a real argument it
sits outside that rule's literal scope — but it runs under the GIL on the RT
thread, which is the hazard the rule was written about. **We are not taking that
exception.** A timebase callback is ~80–120 lines of C that fills in a struct;
Python sets BPM over OSC. This mirrors the reasoning already recorded for the
limiter below: if nothing packaged fits, writing one is small and doubles as a
dry run of the compiled-JACK-client toolchain.

**Packaged alternatives surveyed 2026-08-14 — none fit.** `jack-midi-clock` and
`klick` both *consume* JACK transport rather than master it; `jack-tools`'
transport utility is start/stop control only. **Caveat:** surveyed on the laptop
archive, not trixie arm64 — give this the same `dak ls` treatment the loopers got
before treating it as closed.

**`scripts/sooperlooper/jack_timebase.py` is a spike instrument, not a shippable
component.** It exists so Task 0.3 can be answered without writing C first. It
must not reach the appliance. Two arithmetic defects found in review
2026-08-14 — truncated per-period tick accumulation (~2.34% slow, 2.8 beats/min
at 120 BPM) and a `pos.tick` expression that is identically zero — **must be
fixed before it is used as a measuring instrument**, because a wrong clock would
produce exactly the drift and boundary artifacts Task 0.3 is looking for and
would return a false negative.

---

## 2026-08-14 — Audio input is a jackd reconfiguration, not a cable (raises the cost of GROUNDING §6.3)

**Finding, verified live on the appliance:** jackd runs **playback-only**.

```
jackd -R -P70 -s -d alsa -P hw:1 -r 48000 -p 512 -n 3
                          ^^ -P = playback only (not -d duplex)

jack_lsp            → system:playback_1, system:playback_2   (no capture ports)
jack_lsp -l         → playback latency 1536 frames (32.0 ms) · capture latency 0
arecord -l          → card 1 Sound Blaster Play! 3 HAS a capture device
```

The hardware input exists; **jackd is not opening it.**

**Why the looper work did not reveal this:** SooperLooper has been looping
*Surge's own output*, wired client-to-client inside the graph
(`Surge XT:out_1 → mpe-looper:loop0_in_1`). That path never touches a hardware
capture device, so every loop test to date is consistent with playback-only.

**Consequence — the audio-input question is more expensive than it reads.**
GROUNDING §6.3 and `DIRECTION.md` frame it as "plug a mic/guitar in for ~10 min
and decide by playing." That is not sufficient. Answering it *yes* requires
switching jackd from `-P hw:1` to duplex (`-d hw:1`), which:

| Consequence | Detail |
|---|---|
| Adds a capture leg | Capture latency goes from 0 to a real number; total round-trip roughly doubles the current 32 ms figure |
| Invalidates current measurements | B5/B5b/B7 numbers (16 loops ~151 MiB, ~15% DSP, 0 xruns) were all taken **playback-only**. They do not transfer to a duplex graph |
| Raises xrun risk | Full-duplex on a single USB Audio Class device is materially harder than playback-only on the same hardware |
| Is a boot-path change | `start-jackd.sh` and the device-detection chain assume playback; this is not a runtime toggle |

**Also unblocks a measurement we currently cannot take:** `jack_iodelay`, the
standard round-trip latency tool, needs a capture device. Under `-P hw:1` it
cannot run, so the 32 ms figure is jackd's *declared* latency and has never
been measured acoustically.

**Not decided here.** Input in/out remains Mitch's call. This entry only
records that the decision costs a graph reconfiguration plus a re-run of the
Session B measurements — not ten minutes with a cable.

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
**SooperLooper Pi test** completes (Session A ~1.5 h, Session B ~3-4 h,
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

**Progress (2026-08-14):** Session A **continue**, Session B **partial** —
see [`docs/measurements/sooperlooper-eval-2026-08-14.md`](../docs/measurements/sooperlooper-eval-2026-08-14.md).
Test is **not complete**; implementation gate remains closed until **B8/B7**
close on hardware with real audio. **B1/B2/B9/B10 closed 2026-08-14.**

---

## 2026-08-14 — SooperLooper eval progress (not a final adopt/build verdict)

**Status:** Bench test ran on reference Pi (`d01d9c3`). Source at
`~/src/sooperlooper-1.7.9` (v1.7.9 + liblo 0.32 handler patch, eval-only on Pi).

| Outcome | Detail |
|---|---|
| **Build** | Pass with patch + `libtool-bin`/`autopoint`; rubberband linked |
| **Fail-open (B6)** | Pass — Surge direct path survives looper `SIGKILL` |
| **Multi-loop (B5)** | 16 loops driven via OSC; VmRSS ~151 MiB |
| **CPU (B7 partial)** | ~15% `jack_cpu_load` sample with 16-loop engine |
| **Blockers** | **B8 save to disk (fail)** · full B7 soak |
| **B1/B2/B9/B10 (2026-08-14)** | **Pass** — parallel + `dry=0`; free-form record/play; APC footswitch; **grid preferred** (both modes later) |
| **Automation gap** | `/mnote` + ffmpeg could not drive audible Surge (noise floor only) — human session required |

**Rollback:** eval adds build deps + `~/src/sooperlooper-1.7.9` only — see eval doc.

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

## 2026-08-14 — Master gain: ONE sidechained limiter on the loop bus; nothing in the live path

> **Supersedes the first version of this entry, same day.** That version
> specified *two* limiters, one of them on Surge's direct path, and claimed it
> "preserves fail-open per path." **That was wrong**, and wrong about the path
> that matters — see the correction below. Recorded rather than deleted because
> the error is instructive: it is the same insert-in-the-signal-path mistake
> §2.11-class fail-open reasoning exists to catch, made while explicitly
> reasoning about fail-open.

**Decision:** One limiter JACK client, on the loop bus only. **Nothing sits in
the live path.**

```
Surge XT → system:playback                      (direct — nothing in the way)
Surge XT → sooperlooper loop inputs
Surge XT → limiter_loops  [sidechain input — level detection only, no audio]
sooperlooper outs → limiter_loops → system:playback
```

**Why the sidechain is the mechanism, not a refinement.** jackd has no mixer:
it sums client outputs in float32 and hands off to ALSA's `S24_3LE`
conversion, which is where an unbounded sum clips. Under the parallel
fail-open topology no single point in the graph *carries* the true N+1 sum —
so the naive fixes are a full-chain limiter (single point of failure for
everything) or a limiter on each path (single point of failure for each).

The sidechain dissolves that: `limiter_loops` **sees** the live signal without
**carrying** it. It knows both terms of the sum, so it can hold
`live + loops` under 0 dBFS by moving the only term it controls. Live audio
passes through no process of ours at all.

**Why no `limiter_live`.** Putting a limiter on `Surge → system:playback`
means that if it dies, its ports vanish and **the instrument goes silent** —
the worst available failure, and exactly the property PR #50's parallel
topology was built to guarantee against. The protection it buys does not
justify that. Live stays bounded by per-patch normalization to −3 dBFS
(`PATCH_NORMALIZATION.md`), with the honest caveat that normalization is
calibrated per patch and a heavy 8-note chord can still exceed it. That
residual is what the sidechain absorbs on the loop side.

**Failure mode lands the right way round:** kill `limiter_loops` and loops go
unlimited and may clip, but the instrument keeps playing. There is no
component whose death silences the instrument.

**Backstop underneath the limiter:** the per-loop `wet` law (`loop_gain/N`
from `LOOPER-PLAN.md`), applied by our driver over OSC — arithmetic in the
control layer, not DSP. The limiter is the guarantee; the law is what keeps
levels predictable as you stack so the limiter is rarely working.

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
got, not a recommendation). Note the sidechain requirement narrows the field:
a plain stereo peak limiter will not do, it needs an external sidechain input
or the equivalent. If nothing packaged fits, writing one is small (gain
stage + lookahead peak detection + smoothed gain reduction + sidechain level
follower, no allocation in the callback) and doubles as a dry run of the
compiled-JACK-client toolchain the build-our-own looper fallback would need.

**Sequencing — measure before building.** The loop bus does not exist until
the looper does, so this lands *after* the Session A/B verdict. Session B's
job is therefore **measurement, not construction**: under the 16-loop load,
capture how far the summed output actually goes over and whether the per-loop
`wet` law alone holds it. Build the limiter to spec against those numbers
rather than guessing a ceiling. (`looper-vetting.md` B14 was originally
written as a build step inside Session B — corrected to a measurement.)
