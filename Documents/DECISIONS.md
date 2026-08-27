# Engineering decisions log

Dated rows agents and humans can trust over stale specs. Full research lives in
OM-Repo [`internal/projects/mpe-synth-launch/research/`](https://github.com/opsMachine/OM-Repo/tree/main/internal/projects/mpe-synth-launch/research).
Orientation canon: OM-Repo [`GROUNDING.md`](https://github.com/opsMachine/OM-Repo/blob/main/internal/projects/mpe-synth-launch/GROUNDING.md).

---

## 2026-08-26 — Multi-clip per track Gate A approved

**Decision (Mitch, 2026-08-26):** Gate A approved for
`Documents/specs/multi-clip-per-track-spec.md` — **15 tracks** × 8 clip slots (loop 14 =
scratch/weld buffer), one audible clip per column, quantized switch/cancel, Scene Launch
**1–7** across all 15 tracks (row 7 pad-only on mk1 — see spec OPEN-1).

**Locked implementation choices:**

| Topic | Choice |
|-------|--------|
| Inactive slot storage | Disk-only; lazy `load_loop` on launch; song load restores active slot per track |
| Record into non-active slot | Save active slot to disk if dirty, clear loop, record into target slot |
| v1 songs | Read forever; overwrite Save upgrades to manifest v2 |
| Scene empty cells | Skip silently |
| APC LED | Occupied stopped stays yellow (unchanged) |
| Autosave | Deferred — GitHub [#115](https://github.com/MitchSchwartz/MPE-Sound-Module/issues/115) |
| P0 (pending-mute cancel) | Laptop build |

**Next:** P0 pending-mute cancel → spikes → P1 manifest v2 + touch save/load.

---

## 2026-08-23 — V12 buffer comparison; "clean" criterion retired

**Stack:** **G2 → V12 → B3 → Gate 1 ship.**

**Finding (X1):** xrun arrivals are bursty (Fano 4.32, 33% silent minutes). **"0 xruns at
confirmed voice counts" was never achievable** — B2 measured 2.06/min at `1024×2`; V9/V11 "clean"
cells sampled windows too short to see bursts.

**V12 asks:** (1) how much worse is `512×2` (21.3 ms) than `1024×2` (42.7 ms)? (2) audibility
is **B3 only**. **Prohibition:** no PASS/FAIL or "clean" for a buffer in V12 reporting.

**Design:** two 30-minute arms, governor on (G2 thresholds), buffer only variable, Cloud Horn @5;
alternate/randomise order (rate decays in first ~30 min). Fano-corrected window ~130 raw events ≈
33 min at 3.87/min. Reuse `measure-soak-instrument.sh` with `--minutes`; confirm harness =
screening only. Stamp governor state in every log (X1 provenance gap).

**Fallback:** if `512×2` is materially worse, `1024×2` at 42.7 ms is still 1.5× better than
shipping 64.0 ms and is soak-tested.

**Prompt:** `docs/measurements/PROMPT-V12-certify-buffer.md`

---

## 2026-08-23 — G2 recalibration spec; Gate 2 now blocks Gate 1

**Ordering change:** **Gate 2 (governor) must close before Gate 1 ship or B3 ear test.** The
shipping default includes the poly governor; ear-testing with it off certifies a configuration
that never ships.

**Diagnosis (A2 reference suite, governor off, `dsp_median`):** `MPE_POLY_CPU_HIGH=50.0` sits
below Cloud Horn @5 clean load (56.9–59.4% at every buffer) — permanent panic and voice steal
on a healthy patch; likely identity of original "crackle at 512." `CPU_LOW=40.0` is below every
clean operating point — governor latches and never releases.

**Proposed thresholds (starting point, not result):** `MPE_POLY_CPU_HIGH=78.0`, `MPE_POLY_CPU_LOW=68.0`;
hold times and headroom unchanged. Reasoning: deadline-relative DSP needs thresholds near the
deadline, not halfway; HIGH clears max clean (59.4%) with transient margin; LOW at 68 sits above
every clean median so release is possible.

**Hard rule:** Do **not** set thresholds from reference-suite `dsp_p99` — (1) 25 samples makes
"p99" the max in disguise; (2) 1 Hz sampling cannot observe 150 ms governor hold events
(sub-Nyquist).

**Verification (empirical, both arms — same structure as C0 positive/negative controls):**
- **Negative control:** Cloud Horn @5, 1024×2, governor on — **zero engagements** across 30 min clean play.
- **Positive control:** Crystals pushed past floor (6+ voices) — governor **must** engage and **release**.

A governor that never fires passes the negative arm trivially; both arms required.

**Dependencies before G2 runs:** (1) X1 governor check — confirm harness must not leave governor on;
(2) fade implemented and merged — abrupt voice drop fails ear test for a reason unrelated to buffer.

**Prompt:** `docs/measurements/PROMPT-G2-governor-recalibration.md`

---

## 2026-08-23 — G2 closed: governor ON at 78/68

**Result:** Gate 2 **closed** on Pi 4 control. Thresholds **78.0 / 68.0** verified empirically;
only pair tested. Governor **re-enabled** (`MPE_POLY_GOVERNOR=1`, `surge-poly-governor.service` active).

**Negative control:** Cloud Horn @5, 1024×2, 30 min — `governor_engagements_total=0`, 8 xruns,
`dsp_max=78.3`. **Positive control:** Crystals @6, 3 min — `governor_engagements_total=1`
(emergency limit drop).

**Fade:** not merged (V7 Fix 2 open). Threshold recalibration unblocks shipping stack; steal
audibility deferred to **B3 after V12**.

**Harness fixes during close:** engagement-only journal filter; G2 RESULT line parsing; GOV_SINCE
total recount; stop touch UI during soaks.

**Doc:** `docs/measurements/G2-RESULT-2026-08-23.md` · Pi logs under `~/g2-governor-2026-08-23-*`

**Next:** V12 buffer comparison (~70 min, Mitch approval) → B3 ear test → Gate 1 ship.

---

## 2026-08-22 — Gates: ship 1024×2 after soak; governor waits on fade

**Gate 1 (Mitch):** Ship **1024×2** for instrument profile after **one overnight soak**
(`scripts/measure-soak-instrument.sh` — default Cloud Horn @ 5 voices, 8 h). Looper stack
stays **1024×3 condition D**. Measured cells (V9-b/d) used confirm harness only — ramp bug
did not contaminate ×2 evidence.

**Gate 2 (Mitch):** **Do not re-enable poly governor** until fade + steal order
(released → quietest → oldest) and **CPU_HIGH_THRESHOLD** re-calibrated (50.0 is below
~58.9% baseline @ 1024 — governor permanently “high”). Confirm-based floors are correct
floors but fix the wrong failure mode (hard voice cut, not ceiling value).

**Gate 3:** Percussive **voice-count metric deferred**. Rate metric (notes/sec roll) is the
musically real question; Attenborough load fault stays separate.

**V10-b (agent):** Ramp `_xruns_delta` fixed — blind reads abort; per-second sampling.
See `docs/measurements/V10-b-ramp-probe-fix-2026-08-22.md`.

**Measured (instrument-only):** **1024×2** matches **×3** at verified-clean counts (Cloud Horn
5, Duduk 3, Brave New World 3, Crystals 3). **64.0 → 42.7 ms** total latency, no DSP cost in
measured cells. Confirm floors @ 1024×3: Crystals **3**, Cloud Horn **5**, Closed Hat **5**.

**Canon:** `docs/measurements/V9-REVIEW-2026-08-22.md`, `docs/measurements/session-handoff-2026-08-22.md`.

---

## 2026-08-19 — Tier 2 rejected; stop-then-weld is the only tail model

**Decision:** Remove “extend Recording until release quiet” (Tier 2) and Option E
(seam overdub) from product design. Every clip close — defining take and grid clips —
uses the same **stop-then-weld** path:

1. **Stop** — pad sends `record` stop; length fixes immediately (clip 0) or at the
   quantised bar (grid clips).
2. **Tail pass** — parallel record on scratch loop 15 while main loop plays at fixed N.
3. **Weld once** — offline merge at wrap seam; no second copy of release already in the buffer.

**Why:** Tier 2 duplicated release, did not stop on the pad, and was an AI compromise —
not Mitch’s model. Ear-failed on Pi 2026-08-18–19.

**Defaults:** `MPE_SL_TAIL_CAPTURE=1`, `MPE_SL_SEAM_WELD=1`. Set `MPE_SL_SEAM_WELD=0`
for stop-only (no reload merge) while tuning latency.

**Canon:** `Documents/specs/looper-loop-seam-spec.md` §Stop-then-weld.

---

## 2026-08-18 — Anything that touches the audio path must be declared, including what you did not write

**Three faults in one week, one shape.** Each was a component doing something
invisible on the realtime path, undeclared anywhere in the design, discoverable only
by reading `/proc`:

| Looked like | Actually was | Cost |
|---|---|---|
| `mpe-peak-meter` — a passive level display | a node **inside** the JACK graph, stalling it on the GIL | 30 points of peak DSP headroom |
| `jack_cpu_load` — a diagnostic read | a real JACK client registration, unreaped | 705 orphans, registry saturation |
| `pygame.init()` — drawing a screen | **a second audio device held open**, streaming 44.1 kHz silence to the onboard jack | 41 xruns / 75 s at 45% spare DSP |

The third is the one that proves the rule, because **nobody wrote it**. The repo has no
audio assets, no `pygame.mixer.init()`, no `Sound()`, no `.play()`. `pygame.init()`
initialises every subsystem — display, font, **mixer**, joystick — because pygame is a
game engine and a game wants all of them. We use it as a touchscreen GUI toolkit, so we
inherited a game-shaped default: grab an audio device at startup. It opened the only
output it could find and streamed silence into it, at nine call sites (`touch_browser_app`,
`dsi_splash` ×6, `calibration_loader`, `clear-dsi-framebuffer`) — including during boot
and **during loudness calibration**, when we are measuring through the real DAC.

That put a second PCM stream on a second clock domain — 44.1 kHz, 512-frame period,
free-running against jackd's 10.67 ms USB period — driving bcm2835 DMA and interrupts on
the same silicon as the USB controller feeding the DAC. It also made
[*The Pi onboard headphone jack is never an audio output*](#2026-08-14--the-pi-onboard-headphone-jack-is-never-an-audio-output)
(2026-08-14) true in intent only: the jack had been an output the entire time, just never
playing anything audible.

**Rule — the audio path has three entry points, and all three are declarable:**

1. **Registering a JACK client** (`set_process_callback`). Enforced today by
   `tests/test_jack_rt_boundary.py` criterion 33, allowlist empty.
2. **Opening an audio device** (ALSA PCM, SDL, PortAudio, PulseAudio). **Not yet
   enforced.** `SDL_AUDIODRIVER=dummy` is set appliance-wide in `/etc/mpe/mpe.env`;
   a test should assert it, and a boot check should assert only the DAC's PCM is open.
3. **Taking a realtime priority.** Every `SCHED_FIFO` thread should belong to a named
   audio component. Today: `jackd` 70, `surge-xt-cli` / `sooperlooper` /
   `mpe-peak-meter` 65 — and nothing else outside kernel threads.

Criterion 33 covers one third. The other two are just as checkable, and either would
have caught this years before it caused an audible crackle.

**Corollary — "we did not write it" is not a defence.** A dependency's convenience API
is part of your audio design whether you read its source or not. Audit what the process
*holds*, not what the code *says*: `fuser -v /dev/snd/*`, `/proc/asound/card*/pcm*p/sub0/status`,
`jack_lsp`, and `ps -eLo cls,rtprio`.

**Diagnostic note that made this findable.** None of it was visible from load. The
appliance ran 41 xruns in 75 s with DSP never above 55% — nearly half the deadline
unused. Xrun counting under shipping softmode (`jack_set_xrun_callback` in the compiled
meter, see the session-control spec Q10) is what separated "the graph is busy" from
"the graph is being interrupted". Without it, every reading said `0` and meant
`not measured`.

---

## 2026-08-18 — CPU is the scarcest resource: no subprocess spawning in periodic loops

**Decision (Mitch, 2026-08-18):** *"We cannot let subprocesses start eroding CPU, it's
our most precious resource, we need good engineering. If Python is no longer the correct
tool at times and we need to write C modules then we'll consider it at those points. For
now bash seems acceptable."*

Every core-second spent forking is a core-second `jackd` may need to hit its deadline.
This is the cost-side sibling of **No Python on the JACK audio thread** (2026-08-13):
that rule keeps interpreted code off the RT path; this one keeps the RT path's *budget*
from being eaten by processes that never touch it.

**Measured on `raspberrypi2`, 2026-08-18** — memorise the order of magnitude, not the digits:

| Spawn | Cost | At a 5 s cadence |
|---|---|---|
| `python3 <script>` (interpreter start) | **~360–440 ms** | **~9% of a core** |
| `python3 -m <module>` CLI vs in-process call | 418 ms vs 58 ms | 360 ms is pure startup |
| `jack_lsp` (registers a real JACK client) | **~116 ms** | ~2.3% per call per tick |
| `systemctl is-active a b c d` (batched) | ~22 ms | ~0.4% |
| `systemctl is-active` ×4 (separate) | ~72 ms | ~1.4% |

**Rules, each paid for:**

1. **Cost × cadence before you add a poll.** A fork that is "only 400 ms" is 9% of a core
   forever at 5 s. Compute the product; put it in the PR.
2. **Cheap batched pre-filter, expensive tool only on the exception path.** `surge-watchdog.sh`
   went 9% → 3% by asking `systemctl is-active` for all four looper units in one call and
   forking Python only when a line came back non-`active`.
3. **Throttle to what the fault requires, not to the loop you happen to be in.** Recovering
   from an aborted calibration does not need 5 s granularity; 30 s is free.
4. **Batch `systemctl is-active a b c d` — one call, per-unit lines. Never `--quiet` with
   multiple units: it is an OR, not an AND.** `is-active --quiet live.service missing.service`
   exits **0**. This looks like the obvious optimisation and is silently wrong.
5. **Never invoke a Python module CLI on a timer.** Call `build_snapshot()` in-process.
   `python3 -m patch_browser.session_snapshot` is a debugging command, and is documented
   as such in its module docstring.
6. **Prefer bash builtins to forks.** `$EPOCHSECONDS`, not `date +%s`. Absolute paths for
   hot binaries — a `PATH` search costs extra failed `execve` per call.
7. **`jack_lsp` is not a cheap liveness probe.** It registers a real client on the graph
   every call. See *The "wedge" was an orphaned JACK client* (2026-08-15) and the
   705-leaked-client incident (2026-08-17) for what that costs when it goes wrong.
8. **Language escalation is deliberate, not incremental.** Bash is the current answer.
   C is considered only when a specific measured hot path defeats bash — not pre-emptively,
   and never as a way to make an unnecessary poll cheaper. Deleting the call beats
   optimising it.

**How to measure — the parent's own jiffies will lie to you.**

`/proc/<pid>/stat` fields 14+15 (`utime`+`stime`) read **0** for a supervisor that is
burning 9% of a core, because forked children only land in fields 16+17 (`cutime`+`cstime`)
once reaped. Reading the parent alone shows a process that looks free while it isn't.

```sh
# cost including reaped children
awk '{print $14+$15+$16+$17}' /proc/<pid>/stat     # sample, sleep N, sample again
# jiffies over N seconds ÷ N = % of one core (USER_HZ=100)

# what is actually being spawned, per tick
sudo strace -f -e trace=execve -p <pid>
```

**AMENDED SAME DAY — CPU was the wrong currency for one of these.** Rule 7 says
`jack_lsp` is not a cheap liveness probe *because it registers a client*. This entry
then priced the fix in **CPU only**: the probe was bounded to 10 s and called affordable
at 1.16% of a core. That evening it was measured against the audio graph rather than the
processor: **35 xruns/min against 6 when rare** — the single largest xrun source on the
appliance, larger than the entire looper stack, and audible as crackle.

**Rule 9 — a probe has two costs, and CPU is the cheaper one.** Anything that touches
the realtime graph must be priced in *deadlines missed*, not just cycles consumed. A
1.16% probe that reorders the JACK graph six times a minute is expensive; the CPU figure
said it was free. Measure with `xruns=` from `meter.state` and a deterministic load
(`scripts/midi-load.py`), not with `/proc/<pid>/stat` alone.

**Corollary — prefer an observer that is already there.** The fix was not a cheaper
probe but *no probe*: `mpe-peak-meter` is a long-lived compiled client permanently on
the graph, already re-checking its wiring every 2 s and publishing `wired=`. Reading
that file answers the question for nothing. See
[`docs/measurements/archive/crackle-root-cause-2026-08-18.md`](../docs/measurements/archive/crackle-root-cause-2026-08-18.md).

**Applied:** PR #68 (`b6355b4`) for the looper reconcile; `0f9875c` replaced the
`jack_lsp` graph probe with a `meter.state` read (35 -> 0 xruns/min at matched load).
**Historical note:** the line below described `mpe_jack_server_ready()` running
`jack_lsp` twice per 5 s tick as a ~4.6%-of-a-core problem — it was, but that framing is
exactly the mispricing above. The supervisor's per-tick question is `systemctl is-failed`, and the
graph probe is only needed when it is about to act.

---

## 2026-08-15 — A reading that looks the same whether or not it means anything

Every defect found in the looper stack on 2026-08-14/15 was the same shape, and
it is worth naming because it is not a coding error and code review does not
catch it. **A measurement, a status, or a colour that is indistinguishable
between "fine" and "not instrumented".**

| Instance | What it showed | What it meant |
|---|---|---|
| The orphan | `sl-health` green, `/get` answering | Engine had no JACK client; every command discarded |
| Watchdog audio check | "healthy" | Guarded on `if srcs` — an empty graph reported OK |
| B7 xruns | `0/0` | The node has no xrun counter at all; that was a fallback |
| First B7 launch | would have been 0 xruns, 22% DSP | Zero fixture clips — 16 *empty* loops |
| Pad LED | solid green | A command had been *sent*, not that audio existed |
| `sl-health` at 23:30 | **WEDGED** | Two probers fighting over one control |

**Rules, each paid for:**

1. **Absent instrumentation must look absent.** Never default a missing counter
   to 0. Return `n/a`. `0` and "we could not measure" must not render alike.
2. **Distinguish "could not ask" from "asked, got nothing".** `None` and `""`
   are different answers; conflating them is how the watchdog called a silent
   graph healthy.
3. **A read-only check cannot detect a failure of the write path.** They fail
   independently. Round-trip a write.
4. **Before trusting silence, prove the channel is alive.** Zero xruns in the
   journal counts only because jackd's startup lines are demonstrably in that
   same journal.
5. **A monitoring race must never recommend a destructive action.** A false
   WEDGED points at `sl-restart`, which erases every take. Verdicts whose
   remedy loses data need corroboration, a retry, and a cheaper check named
   first.
6. **Solid means confirmed; blinking means requested.** The one UI rule that
   makes a control surface trustworthy (§L).

**Test the gap, not the call list.** A `MagicMock` answers instantly and always
agrees, so it cannot see any of the above. `tests/fake_sl_engine.py` holds
quantized actions until an explicit `boundary()` and found three real defects on
first run.

## 2026-08-15 — The "wedge" was an orphaned JACK client, not an engine fault

**Five occurrences across two evenings were all misdiagnosed.** Root cause,
verified live: `jackd` restarted 4.5 minutes after SooperLooper; SL survived as
a *process* but lost its JACK client and never re-registered. `jack_lsp` showed
no `mpe-looper:*` ports at all.

`/set` and `/hit` go through `push_nonrt_event()`, drained from the **JACK
process callback**. No callback, no drain — every command silently discarded,
while `/get` reads state directly and keeps answering.

**It explains all three standing symptoms with no race required:** pads green
with no audio, a grid still quantized after a reset, and the watchdog logging
PROBLEM every cycle without ever repairing (it was connecting a port that did
not exist).

`restart-sooperlooper.sh` already had `jack_client_visible()` and already logged
"orphan detected". **The watchdog never called it.** The condition was named in
the codebase the whole time; nothing that ran automatically checked for it.

**Rules this earns:**

- **Check JACK visibility before probing OSC.** An orphan and a wedge are
  indistinguishable to the OSC probe, so diagnosing in the other order names the
  wrong component — which is what sent three sessions into the engine internals.
- **A read-only health check cannot detect a failure of the write path.** This
  is why `sl-health` round-trips a `set`.
- **A repair that fails silently is indistinguishable from no repair.** Log the
  exit code and the stderr, always.
- **`pause` is a toggle.** `stop-all-loops.sh` hit `/sl/-1` and then every loop
  individually, pausing and un-pausing each one, so "stop all" left everything
  running. `pause_on` is idempotent; use the explicit form. Third instance of
  this same error after `trigger` and `mute`.

Detail: spec `looper-transport-clock-spec.md` §M.

## 2026-08-15 — Looper control layer: engine truth plus intent that expires

**Two independent grumpy reviews and two audits converged** on one defect: the
bench kept a parallel `self.state` written the instant a command was *sent*, so
it disagreed with the engine for as long as the engine took to answer, and any
poll landing in that window could clobber it.

**Both reviews prescribed deleting `self.state`. That prescription was wrong**
and is recorded as wrong. The optimism is load-bearing: a double tap must mean
"record exactly one cycle" before the engine has acknowledged the first tap, and
a second tap during a quantized mute must mean "keep playing".

**Decision — keep the intent, stop calling it truth.** `sl_state` is
authoritative always; `pending` is what we asked for and have not seen
confirmed, and it expires. `state` is derived from the two.

**The LED renders `pending` as a blink and paints a solid colour only from
`sl_state`.** A solid green pad is now a promise that the engine says there is
audio in that loop. It used to be a promise that we had *sent* a command.

**Test the gap, not the call list.** A `MagicMock` answers instantly and always
agrees, so it cannot see a timing bug — and every looper bug that cost an evening
was a timing bug. `tests/fake_sl_engine.py` holds quantized actions until an
explicit `boundary()`. It immediately caught three defects the unit suite could
not: a fast double tap cancelling its own take, a queued launch blinking green
forever, and a queued stop dropping its expectation mid-take.

Confirmed on hardware 2026-08-15. Detail: spec §L.

## 2026-08-15 — Racknerd YOLO → Pi access (spec draft, not implemented)

**Spec:** [`docs/racknerd-pi-access-spec.md`](../docs/racknerd-pi-access-spec.md)

**Intent:** Let the Racknerd YOLO agent run **allowlisted** `mpe test pi …` (Phase A) and optionally **bounded deploy** behind `pi_soak` (Phase B) — via **Tailscale ACL** (Pi:22 only, no LAN subnet routes), **forced-command SSH user** `mpe-yolo` on the Pi, **`mpe-cli` yolo profile** on Racknerd, and **`yolo-shell-guard` allowlist**. Mitch admin path (`mitch@pi`, laptop `mpe`) unchanged.

**Status:** Draft — **Gate A not cleared.** Do not install Tailscale agent path or widen guards until Mitch approves spec.

**Non-goals:** LAN pivot, interactive agent shell on Pi, automated ear tests, `dev`→`main` appliance promotion from Racknerd.

---

## 2026-08-15 — Browse carousel + filter pane (spec locked, not built)

**Spec:** [`specs/touch-browser-browse-carousel-spec.md`](specs/touch-browser-browse-carousel-spec.md)

**Layout (concept J):** horizontal track `[Filter 532 | Nav 268 | Patch 532]`. Default **Home** (Nav + Patch); **Filter** stop shows Filter + Nav; patch fully off-screen. Filter pane uses masonry instrument tags (full `INSTRUMENT_VOCAB`); **no** inline chip row in nav; **no** funnel button in nav header.

**Gestures:** **No third-party library** (pygame + evdev — nothing mature to drop in). New in-repo `gesture_router` + `browse_carousel`. **Zone at pointer-down** only — not angle-based scroll/pan lock. **Left screen edge 48px** is the sole carousel grab strip in v1; nav list scrolls in the interior; patch pane is mixer-only. Filter tag tap persists selection and does **not** change stop. No bottom tab bar; nav scroll uses existing edge hints.

**Deferred:** inner seam handles, nav-header swipe, J2 right-pane swap, right-edge global menu (future 12–16px rim).

**Supersedes** inline chip UX in instruments epic Phase 4 for browse UI only — metadata work unchanged.

---

## 2026-08-15 — Browse carousel + filter pane: Phases A–D implemented, deviations from spec text

**Spec:** [`specs/touch-browser-browse-carousel-spec.md`](specs/touch-browser-browse-carousel-spec.md) (still Draft status; this row records where the build diverged from its literal text and why)

**Carousel scoped to FOLDERS/PATCHES only, not ALL_PATCHES.** The spec's layout section says "Nav width fixed at LEFT_NAV_WIDTH (268)" without carve-out, and Phase C lists "All-patches mode: carousel enabled." But `_left_nav_width()` in ALL_PATCHES mode is elastic (~726px, fills the screen) and `main_rect` is zero-width there — there is no "Nav 268 + Patch 532" arrangement to slide in that mode; it doesn't exist in the code the spec was written against. Forcing 268px onto ALL_PATCHES would silently redesign a screen the spec never describes and break the A–Z rail's reason for existing. `_browse_carousel_active()` in `touch_browser_browse.py` excludes ALL_PATCHES; that screen keeps its pre-carousel legacy layout untouched. `instrument_filter` state still applies there (set via the Filter pane in FOLDERS/PATCHES, respected when you switch to All patches) — only the picker UI itself is unavailable in that mode.

**Filter pane content is inset to `x ≥ BROWSE_EDGE_GRAB_W` (48px), not drawn to the pane's own left edge.** The router's `edge_carousel` zone (`x ∈ [0, 48)`) outranks `filter_tap` in the priority table, so a chip drawn at the pane's true left edge would be untappable — any touch there is a swipe-back gesture, not a tag tap, regardless of what's drawn on top. Not called out in the spec; implemented as the only correct reading of the router's own stated priority order.

**Instant snap, not tweened**, per the spec's own open-question fallback ("else instant snap acceptable"). Tweening `BrowseCarousel.offset_px` would make it mid-animation when `_layout()` reads it for hit-testing/rect placement — conflicts with the Phase A contract (`end_drag()` leaves `offset_px` at the exact target synchronously), which Phase B/C's layout and dispatch code, and their tests, depend on.

**Nav list scroll-edge hint (goal: "reuse `draw_vertical_scroll_edge_hints`") not implemented.** `draw_vertical_scroll_edge_hints` needs a `ContentScrollArea`-shaped `.edge_hint_strength()`; `ScrollList` (what backs `nav_list`) has no such tracking today and never has. Adding it is a shared-widget change affecting every `ScrollList` caller, not scoped to this feature — left as a follow-up.

**Not verified:** acceptance criteria 9–11 and 13 are marked "Manual (Pi)" in the spec's own testing strategy and require a real touchscreen; not exercised in this sandbox (no pygame, no display, no Pi). Everything else has automated coverage — see the Tests lines in `docs/TOUCH_PATCH_BROWSER.md`'s browse carousel section.

---

## 2026-08-15 — Looper grid clock: RESOLVED — SL internal sync, first take defines it

**Supersedes** the 2026-08-14 "Who owns the looper grid clock (open — ranked,
gated)" entry, which is removed. Option 1 won on the bench; options 2 and 3
were never needed.

**Decision:** the grid is SooperLooper's **internal tempo** (`sync_source = -3`).
No JACK timebase master, no MIDI clock, no compiled C client, nothing on the
realtime thread. The **first take defines the grid** and is then an ordinary
clip.

**The model — a grid needs three things, and we had been building two.**

| | Where it comes from |
|---|---|
| tempo | the first take's length (it *is* one bar, by definition) |
| unit  | one bar — `eighth_per_cycle = 8`, fixed (see note below) |
| **phase** | `Engine::set_tempo` zeroes `_quarter_counter`/`_tempo_counter`, so re-sending the tempo IS the phase reset (engine.cpp, verified) |

Missing phase was the single cause of clips joining out of phase, stop landing
on the wrong boundary, and position not resetting. The `tap_tempo` guess carried
since `8d7a426` was never needed.

**Correction 2026-08-15 — the unit is fixed at one bar, deliberately.** This
entry originally claimed `eighth_per_cycle` was "sized to the first take". It is
not: it is set to 8 once at startup and never changed, so the quantize unit is
always one bar. That is the *correct* behaviour — clips count in to the next
**bar**, not to the end of a multi-bar phrase — but it was true by accident
rather than by decision, and the canon asserted a mechanism that does not exist.
`derive_tempo` still returns `bars`, and `on_grid_established` still ignores it.
What is genuinely missing is that nothing records the *phrase length* of a
multi-bar first take. That is a real gap, but a small one, and it is not what
this row said.

**Two states, not settings.** No grid → every loop free-form (quantize/sync/
round all 0). Grid established → count in and snap length to the cycle. Written
down because tracking a *setting* rather than the *state* was the recurring bug
shape all session.

**No clips, no grid.** Clearing the last clip — by pad hold or track reset —
drops the grid, driven by engine state, so the next take defines a new one.

**Load-bearing engine facts (read from source, not inferred):**

- `smart_eighths` **silently doubles the cycle below 60 BPM** and pushes it to
  every loop. "First take = one bar" makes a 6 s take 40 BPM, so we tripped it
  every time. **Disabled.**
- `ports[PlaybackSync] = 0.0f` is SL's default; forcing 1 made a fresh clip
  wait for the *next* boundary after record-stop had already landed on one.
- `round` on top of a quantized stop adds another whole cycle. Always 0.
- `pause`/`trigger` are toggles; `pause_on`/`pause_off` are explicit. Toggles
  desync the moment bench and engine disagree — same root error as mirroring
  state instead of reading it.

**Rule that came out of this:** every correct answer this session came from
reading `engine.cpp`. None came from reasoning about parameter names. Read the
engine before changing a parameter.

**`scripts/sooperlooper/jack_timebase.py`, `spike-jack-transport.py`,
`jack_transport_util.py`, `start-jack-timebase.sh`** are now dead — the reason
they existed (getting phase) is solved. They are a documented "must not ship"
component still importable and startable. **Delete; git has them** if external
clock sync ever becomes a real feature, which is a different problem.

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
see [`docs/measurements/archive/sooperlooper-eval-2026-08-14.md`](../docs/measurements/archive/sooperlooper-eval-2026-08-14.md).
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
