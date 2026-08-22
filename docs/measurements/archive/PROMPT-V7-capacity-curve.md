# Agent prompt — V7 capacity curve + V3 latency win

Copy everything below the line.

---

**Invoke the `measurement-design` skill before designing or altering any cell.**

## Where this stands

The appliance is **compute-bound**. `W1` established it with zero ambiguity:
`JackEngine::XRun: Surge XT was not finished`, **zero** `ALSA: xrun of at least N msecs`
lines at 1024/512/256, buffer fill flat at ~83%. **Every xrun ever measured here is a JACK
graph overrun. The ring buffer has never drained.**

Confirmed by ear 2026-08-21: with the poly governor off, Mitch can provoke crackle at will by
maxing CPU. Compute-bound, reproducible by hand.

## Dead — do not test, do not revisit

| line | killed by |
|---|---|
| the "~600 us" wakeup gap, cushion/drain model, `threadirqs`, `irq/30` priority, `isolcpus`, `nohz_full`, PREEMPT_RT | W1 — the buffer never drains |
| USB runway, URB depth, URB completion rate, frame alignment, Scarlett-vs-dongle **as latency** | Steps 1-4 + W1 |
| **~1.1 ms fixed per-callback cost** | **V1 — a = 0.13 ms; cost scales with buffer** |
| **single-client architecture refactor** (Surge hosting the looper) | **V2 — graph overhead is ~35 us, noise** |
| **`ondemand` governor / `arm_boost` arms (V5, V6)** | **V0 — already `performance` @ 1800 MHz with `arm_boost=1`** |
| **`ondemand` ramp as the cause of Mitch's pops** | **V0 + ear test — pops were the poly governor** |
| **profiling for a fixed constant (V4)** | **V1 — no fixed constant exists** |

## Read first

`V1-VERDICT-no-fixed-cost-2026-08-21.md` · `V7-capacity-curve-plan.md` ·
`W1-VERDICT-compute-bound-2026-08-21.md` · `xrun-counter-audit-2026-08-21.md` ·
`MEASUREMENT-DISCIPLINE.md`

## Standing conditions for every cell

- Poly governor **OFF**, poly ceiling raised out of the way. It is a feedback controller that
  reacts to the load being measured; it confounded W1's entire ladder.
- **Strict mode** (no `-s softmode`).
- CPU governor `performance` @ 1800 MHz (already set — verify, do not change).
- Same patch, same voice-entry pattern, same everything else across cells.
- **Report DSP in absolute ms as well as percent, and `p99.9` / `p99.99` / `max` — never
  `p99`.** At 256, p99 = 76% while 0.28% of periods overran; p99 excludes the events of
  interest by construction.
- **Capture the jackd journal per window** and report `JackEngine::XRun` lines and any
  `ALSA: xrun of at least N msecs` lines. If ALSA lines ever appear, that is new — say so
  loudly.
- **Stamp actual state into every result:** period, nperiods, rate, resolved card index
  (it moved 6 -> 2), governor + MHz, poly ceiling, softmode state, git SHA.
- **No commands against the Pi while a window is open**, including read-only ones.

---

## V7 — the capacity curve (~25 min) — PRIMARY

**Every cell in this project has used one fixed 75-voice load and counted xruns.** That
measures failure at an arbitrary point. It does not measure **capacity**, which is what both
the player and the poly governor need.

**Question: for each buffer size, how many voices of a defined heavy patch can be sustained
cleanly?**

| cell | buffer | method |
|---|---|---|
| V7-a | 1024 x 3 | ramp voice count upward; record **first graph overrun** and **highest count sustained clean** |
| V7-b | 512 x 3 | as above |
| V7-c | 256 x 3 | as above |

**Pick one heavy patch and state which.** Mitch reports crackle on heavy patches at 512; use
something representative of that, not a light default. Record the patch name in the
deliverable — this number is meaningless without it.

**Shortest useful version:** the transition is the datum. **Do not soak at each voice count.**
Ramp to first failure, then confirm the sustained-clean count with **one short window** per
buffer.

### Report

| buffer | voices to first overrun | highest sustained clean | DSP ms @ that count | p99.9 / max |
|---|---|---|---|---|

### What this produces

1. **The answer to "what can we actually run reliably?"** — asked early in this project and
   never measured.
2. **A data-derived poly ceiling per buffer size**, replacing the current
   `MPE_POLY_CEILING=12` guess.
3. **The buffer tradeoff expressed in voices** — what a player experiences — rather than in
   xruns/min at an arbitrary fixed load.

## V3 — `1024 x 2` (~15 min) — independent, run regardless

W0 confirmed ALSA accepts `nperiods=2` on this device. `1024 x 2` = **42.7 ms** total against
today's 64.0 ms — **a third off shipping latency with no change to the compute deadline**
(Surge still gets 21.3 ms for 1024 frames, exactly as now).

**n >= 3 streams**, strict mode, same conditions as above. Report xruns/min and DSP against
the `1024 x 3` baseline.

**Unaffected by everything else in this document.** If V7 runs long, V3 still gets run.

## Poly governor — DOCUMENT ONLY this pass (Mitch)

**Do not implement, do not re-enable, do not change its code.** Record the design conclusions
in the deliverable so they are not lost:

- The pops were **the poly governor stealing sounding voices** when it cut polyphony under CPU
  load. Confirmed by ear: governor off, pops gone.
- **Fix 2 (fade, not hard cut) is the actual fix.** A hard cut is a step discontinuity — that
  *is* the pop. Ramp a removed voice out over a few ms.
- **Fix 1 is a steal *policy*, not a prohibition.** An earlier draft proposed refusing
  note-ons instead of stealing; **that is wrong for a performance instrument** — the player
  presses a key and gets silence, which reads as broken. Steal in order:
  **released/in-release first, then quietest, then oldest.**
- **Fix 3: hysteresis.** Separate raise/lower thresholds and rate-limit changes so the ceiling
  does not oscillate.
- **Open question to record, not answer:** the governor sets Surge's poly limit over OSC, and
  **Surge decides what dies** when that limit drops below the sounding count. Establish
  whether the fade belongs in Surge's voice handling or in how we drive the limit (e.g.
  lowering only at note-off boundaries) before writing any code.

**V7's ceiling numbers are the input this work needs.** That is why V7 comes first.

## Rules

1. **One variable per cell.** Write both configs side by side before calling it a comparison.
2. **Report n and the claim class it supports.** Shape claims need n >= 10 streams.
3. **Ask the shortest useful version of each cell** and justify anything longer.
4. **Resolve the card index live.** Never hardcode it.
5. If a result refutes something above, **name the doc and say so plainly.** Four hypotheses
   have already been retired today; retiring a fifth is a good outcome, not a failure.

## Deliverable

`docs/measurements/v7-capacity-curve-2026-08-21.md` on a branch off `dev`:

- the V7 table, with the **patch name** stated
- the recommended **per-buffer poly ceiling** derived from it
- V3 result vs the `1024 x 3` baseline, with n stated
- the poly-governor design notes above, recorded for later implementation
- a **"what this retires"** section
- anything you could not measure, and why
