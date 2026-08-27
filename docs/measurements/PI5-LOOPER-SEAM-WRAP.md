# Pi 5 looper — seam wrap finish line

*Branch:* `yolo/pi5-looper-seam-wrap` · *Opened:* 2026-08-23 (America/Toronto)

**Goal:** Ship **stop-then-weld** (scratch loop 14 + offline merge) on the Pi 5 player —
continuous wrap at N→0 with release tail at the seam, not at sample 0.

**Pi 5 scratch slot:** loop **14**, not 15. SooperLooper 1.7.9 (arm64 build copied from Pi 4)
accepts `save_loop` on indices 0–14; **loop 15 always saves an 88 B empty WAV** even after
0.8 s record with Surge wired to `loop15_in`. Symptom: merge runs but skips (`tail too small`) →
hard stop at seam on replay. Track pad 14 is unbound while seam weld is on (scratch reserved).

**Canon:** [`Documents/specs/looper-loop-seam-spec.md`](../../Documents/specs/looper-loop-seam-spec.md) ·
[`DECISIONS.md`](../../Documents/DECISIONS.md) §2026-08-19 ·
[`archive/seam-weld-spike-2026-08-18.md`](archive/seam-weld-spike-2026-08-18.md) (archaeology)

**Player context:** Pi 5 audio path is live at 128×2 ([`PI5-SESSION-CLOSEOUT-2026-08-23.md`](PI5-SESSION-CLOSEOUT-2026-08-23.md)).
Looper stack is **not** part of daily player services yet — this branch adds/evaluates it.

---

## Product model (locked)

1. **Pad-down → stop** — length fixes immediately (defining take) or at quantised bar (grid).
2. **Tail pass** — parallel record on **scratch loop 14** while main loop plays at fixed N.
3. **Weld once** — `save_loop` both buffers → Python crossfade at seam → `load_loop` → resume.

**Rejected forever:** Tier 2 extend-until-quiet, Option E seam overdub ([`DECISIONS.md`](../../Documents/DECISIONS.md)).

### Merge model corrected 2026-08-25 — sum into head, do not weld onto the end

Step 3 above said "crossfade at seam". That was the bug, and it is why every
round of tuning moved the artifact instead of removing it.

The tail is audio from *after* sample N. Welding it onto `[N−L, N)` places it
earlier than it happened **and destroys the last L samples of the actual take**.
`merge_stereo_frames` then crossfaded the head `[0, M)` toward that seam, so at
`i = M` the buffer snapped back to untouched main in one sample. Measured on
`main=1.0, tail=0.3, M=2048`: a **0.70 full-scale step**, every wrap. That is
the pop. The head/end crossfade also pasted end-of-loop content over the start,
which is the "repeats inappropriately / hard stop" symptom.

Corrected model — the one a hardware looper uses:

| Rule | Why |
|------|-----|
| `out[(offset + i) % N] += tail[i]` | Tail is the continuation of N, so it belongs on the head, summed |
| Take is never overwritten, only summed onto | On pass 2 you hear pass 1's ring-out under pass 2's attack — what the player heard live |
| No head↔end crossfade at all | It was the step, and it duplicated the end over the start |
| Tail edge fades are **linear**, ~5 ms | Equal-power on a fade-to-silence lifts the middle ~3 dB → "tail gets loud at that part" |
| No normalisation of the merged buffer | Rescaling the take is the "level drops at the seam" symptom |

Measured after the fix, same inputs: max step **0.0015**/sample, take
bit-identical outside the tail region.

**Knobs** (`sl_seam_weld.py`): `MPE_SL_SEAM_DECLICK_SAMPLES` (default 256 ≈ 5 ms)
and `MPE_SL_SEAM_TAIL_OFFSET_SAMPLES` (default 0). `MPE_SL_SEAM_MERGE_SAMPLES`
is **retired and ignored** — it named the crossfade that no longer exists.

### Three more defects found in the same pass, 2026-08-25

**1. The reload restarted the loop at a random moment.** `_merge` sent
`set loop_pos` then `hit trigger`. `trigger` restarts from sample 0 — the
branch's own test says so
(`test_launch_is_a_quantized_trigger_from_the_clip_start`) — so it discarded
the position set one line earlier, and `loop_pos` is an SL *output* control
that almost certainly ignored the set anyway. Worse, `resume_pos` was sampled
when the merge was **queued**, then used after save + merge + reload. Measured
merge cost for a 4 s loop: **97 ms on this laptop**, so several hundred ms on a
Pi 5 — most of a bar stale. Net effect: the playhead jumped to 0 at an
arbitrary instant. That is the "didn't restart at the beginning of the tail,
then went into it abruptly" symptom.

*Fix:* `resume_pos` (a stale float) is replaced by `position` (a live
callable). `_swap_at_wrap` waits for the playhead to reach the wrap, then fires
`load_loop` + `trigger` so **the restart IS the wrap** and nothing moves.
Knobs: `MPE_SL_SEAM_LOAD_LEAD_MS` (150), `MPE_SL_SEAM_TRIGGER_LAG_MS` (5).

**2. Every tail peak was being dropped, so the tail was always 750 ms.**
`SlBenchStateListener.on_update` did `fs = self._by_loop.get(loop_index)` and
returned on `None` *before* the `in_peak_meter` branch. During seam weld the
meter is registered on the scratch loop, which has **no footswitch bound** — so
the lookup returned `None` and every tail peak died there. `_tail_saw_loud`
never set, and `poll_tail_capture` fell through to the fixed `TAIL_MAX_S` cut.
The tail ended at 750 ms regardless of how the note actually decayed. The
existing test for this had been failing on the branch and was being carried.
*Fix:* route `in_peak_meter` before the `_by_loop` lookup. Tails now end on
decay, bounded by `TAIL_ABSOLUTE_MAX_S`.

**3. A tail longer than the loop stacked copies.** Consequence of fixing #2 —
tails can now run to 15 s. Summed modulo N that baked seven overlapping copies
of one ring-out into a buffer that repeats forever. *Fix:* cap the tail at one
loop length; the declick fade lands on the cap.

### Early arming — the last known gap (2026-08-26)

After the fade fix the wrap still stepped down to ~0.5–0.7x of the take-end level, and the
amount varied per take. Cause: the scratch loop armed only once SL reported `PLAYING`, an OSC
state round-trip. Measured across four takes it armed **17, 36, 53 and 68 ms** after the loop
wrapped — that much release tail was recorded by nothing, and no merge tuning can recover it.

*Fix:* arm at `WAIT_STOP` instead. SL is already counting to the cycle boundary there, so the
scratch is rolling **before** the take ends and the release is captured continuously. The cost
is that the scratch head is then take content, which the merge skips
(`loop_len − arm_pos`, biased 10 ms long — clipping a few ms of ring-out is inaudible, while
leaving take content doubles it onto the head as a flam).

Guard worth keeping: arming requires a real `loop_pos` reading. Without one the arm position is
0 and the skip computes a full `loop_len`, discarding the **entire** tail. Found by writing the
test, not by ear.

Kill switch `MPE_SL_SEAM_EARLY_ARM=0`. **Unverified by ear** as of this note.

**Superseded, ear-only:** the scratch loop only arms once SL reports the main loop
PLAYING, so `tail[0]` is already some ms past the stop instant. The declick fade
hides the discontinuity but not the timing. Tune
`MPE_SL_SEAM_TAIL_OFFSET_SAMPLES` by ear on the Pi and pin the value here.

**Defaults:** `MPE_SL_TAIL_CAPTURE=1`, `MPE_SL_SEAM_WELD=1`.

---

## Code map (already on branch)

| File | Role |
|------|------|
| `scripts/sooperlooper/sl_seam_weld.py` | Background save → merge → load worker |
| `scripts/sooperlooper/seam_merge.py` | Crossfade tail onto `[N−M,N)` ↔ `[0,M)` |
| `scripts/sooperlooper/apc_footswitch.py` | `SEAM_WELD` tail capture + hooks |
| `scripts/sooperlooper-apc-bench.py` | Wires `SeamWeldWorker` |
| `scripts/sooperlooper/sl_hud_monitor.py` | Scratch loop excluded from phrase bar |
| `tests/test_seam_merge.py` | Offline merge geometry |
| `tests/test_apc_footswitch.py` | Tail poll, merge hooks, abort |
| `scripts/sooperlooper/spike-seam-weld.sh` | Manual Tier 3 spike procedure |

---

## Acceptance still open (spec S5–S7)

| ID | Criterion | Status |
|----|-----------|--------|
| S5 | Grid clip length = one cycle after weld; tail at seam not head-only | **Ear + OSC** — not closed on Pi 5 |
| S6 | Fast pad switch during weld → no overdub bleed on other loop | **Manual** — Mitch scenario |
| S7 | Grid BPM unchanged by tail capture on clips 1+ | **`derive_tempo` unchanged** |

Tier 1 (latency) was calibrated on Pi 4 (`MPE_SL_INPUT_LATENCY=3072`) — **re-tune on Pi 5**
after looper is up ([`archive/looper-p0-latency-calibration.md`](archive/looper-p0-latency-calibration.md)).

---

## Known risks from Pi 4 ear spike (re-verify on Pi 5)

| Symptom | Likely cause | Branch task |
|---------|--------------|-------------|
| Release tail missing | Peak meter / merge skip / scratch mis-align | Tune `MPE_SL_TAIL_*`; log peak during weld |
| Jump to loop head after weld | `load_loop` + `trigger` playhead reset | Confirm `pause_off` + `trigger` sequence; ear |
| **Crackle / level rise during live tail** | Scratch loop monitored while recording; `undo_all` at stop | Silence scratch wet/feedback; defer prepare until PLAYING |
| **Live tail sounds “added”, level drops at seam** | Stop-then-weld: main buffer frozen; Surge fail-open still audible; merge skipped → no weld | Fix scratch capture (loop 14); expect splice at merge, not live overdub |
| **Hard stop on replay** | `save_loop` on loop 15 → 88 B; merge guard skips | **Use loop 14 scratch** (2026-08-23) |
| `save_loop` timeout | Wedged engine (B8 history) | **`mpe looper sl-health`** before every merge test |
| HUD bar count wrong during tail | Scratch loop in phrase ref | Fixed in `sl_hud_monitor` — regression test |
| Seam weld busy drop | Overlapping closes | Log + guard in bench |

---

## Deploy rule (hard)

**Laptop/repo is source of truth. Pi is deploy target only.**

**Superseded 2026-08-26:** the seam-weld work is merged to `dev` (`9c5b4ba`, fast-forward),
so `dev` now carries it and deploying `dev` is correct. The original warning — that `dev`
lacked the seam fixes and the Pi would look like "hard stop, no tail" — no longer applies.

What still holds: **always verify what is actually running.** `git log -1 --oneline` on the Pi
must match `origin/dev`, and `pgrep -c sooperlooper` must return `1`. A second engine from a
`jackd` restart keeps the OSC port while losing its JACK ports, so it answers every query
(`state=playing`, `wet=1.0`) while recording digital silence — the readings look identical
whether the audio path is fine or gone. Cost 2026-08-26: one ear-test round and several
diagnostics taken as ground truth before the orphan was found.

| Do (laptop) | Don't (Pi) |
|-------------|------------|
| Commit on `yolo/pi5-looper-seam-wrap` | Edit files under `~/MPE-Module` on Pi |
| `git push origin yolo/pi5-looper-seam-wrap` | `scp` one-off patches (drifts from git) |
| `PI_HOST=192.168.1.106 mpe looper deploy yolo/pi5-looper-seam-wrap` | Hand-fix bench/footswitch on Pi and forget to commit |
| `mpe looper sl-restart` / `sl-bench restart` after deploy | Debug by editing live Python on the appliance |

Partial deploy (e.g. footswitch without bench) **will crash the bench** — see 2026-08-23
`resume_pos` / `SeamWeldWorker.request` mismatch. Always deploy the **whole branch**.

---

## Pi 5 bring-up sequence (before ear tests)

1. **SooperLooper on Pi 5** — build eval tree per [`AGENTS.md`](../../AGENTS.md) scoped exception
   (`~/src/sooperlooper-*`, not under repo). Mitch gate: `sudo apt` if deps missing.
2. **Deploy branch** — `mpe looper deploy yolo/pi5-looper-seam-wrap` (or laptop pull + `configure-pi-paths.sh`).
3. **Engine health** — `mpe looper sl-health` → `save_loop` smoke before seam tests.
4. **Bench** — `mpe looper sl-bench restart`; watch log for `seam-weld: done loop N`.
5. **Spike script** — `scripts/sooperlooper/spike-seam-weld.sh` on Pi.

### Status 2026-08-23 (19:10 America/Toronto)

| Step | State |
|------|--------|
| Binary | Copied Pi 4 `sooperlooper` → `~/src/sooperlooper-1.7.9/src/` (native Pi 5 build still todo) |
| Branch on Pi | **`yolo/pi5-looper-seam-wrap` @ `c955d09`** via `mpe looper deploy` |
| Seam fixes | `_scratch_started` merge gate; scratch loop 15 peak meter; merge hook crash guard |
| ~~tail max 750 ms~~ | **Not a fix — a symptom.** The bench listener dropped every scratch-loop `in_peak_meter` (`_by_loop` guard ran before the routing branch, and the scratch loop has no pad bound), so `_tail_saw_loud` never set and every tail was cut at the fixed `TAIL_MAX_S` window regardless of the note's decay. Corrected 2026-08-26; tails now end on measured decay. |
| sl-health | Run before each ear session |
| APC bench | **`mpe looper sl-bench restart`** after every deploy |

**Deploy (canonical):**

```bash
PI_HOST=192.168.1.106 mpe looper deploy yolo/pi5-looper-seam-wrap
PI_HOST=192.168.1.106 mpe looper sl-bench restart
```

**Next:** Mitch ear — defining take close with release through wrap; log must show
`seam merge queued` → `seam-weld: saving` → `seam-weld: done` (no traceback).

Env on Pi 5 (`/etc/mpe/mpe.env`):

```bash
MPE_SL_TAIL_CAPTURE=1
MPE_SL_SEAM_WELD=1
MPE_SL_SCRATCH_LOOP=14
# After P0 re-tune:
# MPE_SL_AUTOSET_LATENCY=0
# MPE_SL_INPUT_LATENCY=<samples>
```

---

## Work queue (this branch)

| # | Task | Gate |
|---|------|------|
| 1 | SooperLooper binary + `mpe-sooperlooper.service` on Pi 5 | Mitch apt/build if needed |
| 2 | `sl-health` + single-loop `save_loop` smoke | Blocks merge path |
| 3 | Defining take ear pass — release through wrap, no pop | Mitch ear (S2/S5 partial) |
| 4 | Grid clip weld — length + BPM unchanged | S5, S7 |
| 5 | Fast pad-switch during weld | S6 — Mitch |
| 6 | Document Pi 5 result in `docs/measurements/`; promote spec status | Gate B |

**Out of scope here:** poly governor, Suite 1 latency ladder, Pi 5 IRQ experiments.

---

## SR&ED note

Extends Phase 2 looper investigation onto **second platform** (U10) — seam geometry is
stack-specific; Pi 4 ear failures may or may not reproduce on RP1 USB + 128×2 JACK graph.
Log labour via OM-Repo [`sred-daily-capture`](../../../OM-Repo/.claude/skills/sred-daily-capture/SKILL.md) after bench sessions.

---

---

## CLOSED 2026-08-26 — the weld is gone; a native overdub replaced it

Everything above describes the **stop-then-weld** pipeline: capture the
ring-out into a scratch loop, merge it into the head offline, load the merged
buffer back and retrigger at the wrap. That pipeline was deleted in `a99cf63`
(-1927 lines). The work queue and env block above are historical — do not
follow them.

**The first-loop stutter was the retrigger, not the load** (`712f012`).
`_swap_at_wrap` sent `load_loop`, waited, then `pause_off` + `trigger`. The
trigger existed on the assumption that `load_loop` halts playback and needs a
restart. Both premises were measured on Pi 5 and both were wrong:

- `load_loop` does **not** halt playback — `loop_pos` ran straight through one
  (0.805 → 0.813), no stall beyond the 10 ms update interval, no position
  reset. The buffer swaps under a running loop.
- The trigger's landing error was **-4.9 ms on an idle machine**, and it aims
  at the wrap by predicting the playhead from 20 ms OSC frames and sleeping
  ~145 ms. Early cuts audio off the end of the pass; late replays the head.
  Either is the stutter, and the error swings with scheduling jitter.

An independent probe (`spike-load-halt.py`, since removed with the pipeline)
reached the same conclusion from the other direction: polling a playing loop
across a `load_loop` showed no stall, and its same-loop control — which must
stall if the halt is real — showed none either.

Corollary for anyone reading the `SEAM_LOAD_LEAD_MS` sweep above (600 ms full
dropout / 150 ms pop / 20 ms nearly clean / 5 ms pop plus volume step): that
table is real, but it was never measuring a load. It was measuring **how far
off the wrap the retrigger landed**. Tuning the lead was tuning the size of the
cut, which is why no value was ever inaudible.

**The replacement** (`117f4cc`, `1a90d51`): one `overdub` hit while recording
makes SooperLooper close the take and begin overdubbing at the same sample,
inside its own audio thread — the ring-out the take cut off lands in the loop
head with no file I/O, no swap and no retrigger. The overdub is ended at the
first wrap, armed off `sl_state == OVERDUBBING` rather than off the command
sent. An earlier Pi 4 test that popped at `fade_samples` 512/1024 used *two*
hits (record off, then overdub on), which leaves a gap while SL closes the loop
and restarts playback — the fade never mattered, the gap did.

**Method note.** A full day was lost before this to a rollback that was
believed deployed and was not: it targeted a commit 35 minutes *after* the one
that introduced the behaviour, and `git checkout` does not reload a running
Python process. `scripts/deployed-version.sh` reports the deployed commit and
flags any unit started before the checkout. Separately, `MPE_SL_LOOPS` and
`MPE_SL_SEAM_WELD` are written by `bootstrap-pi5-looper.sh`, not by git —
running bootstrap moved the appliance from 8 loops to 16, which created the
scratch loop the weld needed and let it start firing where it had previously
skipped. Appliance behaviour changed with no commit behind it.



---

## History — the entries the closure above supersedes

Kept because the *reasons* still apply even though the pipeline is gone.
The note about `bootstrap-pi5-looper.sh` writing `MPE_SL_LOOPS` outside git
is the same mechanism that hid a phantom loop 15 until 2026-08-27.


---

## 2026-08-26 — First-wrap artifact: cycle doubling at grid establishment

### Fixed: `establish_grid_clock` set the cycle, then had it doubled back

`Engine::set_tempo` runs `_eighth_cycle *= 2` below 60 BPM and pushes the
doubled value to every loop — the rewrite `apply_grid_sync` already documents
at startup. `establish_grid_clock` sent the cycle **before** the tempo, so it
set one bar and the engine immediately doubled it back to two. Turning
`smart_eighths` off does not prevent that rewrite.

"First take = one bar" puts every multi-second take under 60 BPM (a 4.4 s bar
is 54 BPM), so this fired on essentially every session. Observed as the HUD
reading **two bars on the defining take and snapping to one later** — the snap
being a later correction, not the grid settling.

Fix: assert `eighth_per_cycle` **after** the tempo. `apply_grid_sync` already
had the correct order; only `establish_grid_clock` was inverted. The existing
test pinned the inverted sequence while its own docstring stated the opposite
intent, so the suite was green on the bug.

### What else lands on the first wrap

Only the defining take does this work, which is why the artifact is
first-clip-only and later clips are clean. At that single boundary:

```
loop 0: seam weld done            <- buffer swap (load_loop + trigger)
loop 0: applying deferred grid clock — 1 bar(s) @ 54.1 BPM
loop 0: phase re-anchored at loop_pos=0.815s
```

plus `set_grid_active(active=True)`, which writes quantize/sync to all 16
loops. `_flush_deferred_grid_side_effects` defers the grid work to avoid
"baking a stutter into the loop buffer" during capture — and in doing so lands
a phase reset and a per-loop OSC burst exactly on the wrap.

### `SEAM_LOAD_LEAD_MS` — sweep, and a hypothesis that did NOT survive

Ear sweep, one take per value, artifact at the first wrap:

| `SEAM_LOAD_LEAD_MS` | Result |
|---|---|
| 600 | Full "record skip" |
| 150 *(default)* | Audible pop |
| 20 | Near nothing, still audible |
| 5 | Pop **plus** a large volume step |

The obvious reading — that `load_loop` halts playback, making the lead a hole —
**was tested and refuted.** `spike-load-halt.py` polls a playing loop's
`loop_pos` for stalls across three phases (baseline / load another loop / load
the polled loop itself). The same-loop control, which must stall if the halt is
real, showed no stall at all: worst gaps 8.2 / 9.2 / 8.5 ms against a 4 ms
polling median. The control is what makes the result readable — without it the
other two rows would have read as a clean "per-loop halt" and a double-buffer
design would have been built on nothing.

Current best explanation, **not yet measured**: `load_loop` swaps the buffer
immediately, so the lead is the duration of *wrong content* playing before
`trigger` restarts at zero. That fits the monotonic 600/150/20 progression and
the 5 ms partial-buffer step, but no instrument has confirmed it. A load-latency
probe (watch `loop_len` change after `load_loop`) returned 4.2 ms against a
baseline that read 0.0 for a loop known to be 2.09 s long — a bad baseline, so
that number is discarded, not reported.

**Do not tune `SEAM_LOAD_LEAD_MS` by ear against this table.** No value tested
is inaudible, and the mechanism setting the floor is still unidentified.

### Method note

The env file is not in git and the journal did not survive the session in
question, so "what was running" was unrecoverable after the fact — a full day
went to a rollback that was believed deployed and was not, because it targeted
a commit 35 minutes *after* the one that introduced the behaviour.
`scripts/deployed-version.sh` now reports the deployed commit and flags any unit
whose start time predates the checkout, since `git checkout` does not reload a
running Python process.

Also note `MPE_SL_LOOPS` and `MPE_SL_SEAM_WELD` are written by
`bootstrap-pi5-looper.sh`, not by git: running bootstrap moved the appliance
from 8 loops to 16, which created scratch loop 14 and let the weld begin firing
where it had previously skipped for want of a scratch slot. Appliance behaviour
changed with no commit behind it.

*Last updated: 2026-08-26 — pipeline removed, doc closed for history.*
