# Agent prompt — Plan V: find the fixed per-callback cost

Copy everything below the line.

---

**Invoke the `measurement-design` skill before designing or altering any cell here.**

## What changed

W1 settled the question this project has been chasing all day. Journal during W1-c:
`JackEngine::XRun: Surge XT was not finished`, with **zero** `ALSA: xrun of at least N msecs`
lines at 1024, 512 **and** 256. Fill level flat at ~83% throughout.

**Every xrun ever measured on this appliance is a JACK graph overrun, not an ALSA underrun.
The ring buffer has never drained.**

**The binding term is compute time inside the audio callback.** Nothing else.

## Already dead — do not test, do not revisit

`lowlatency=N` · aligned periods (240/480/1008) · URB queue depth · URB completion rate ·
swap / SD major faults · frame alignment · Scarlett-vs-dongle as a *latency* question.

## Retired by W1 — do not resurrect

The "~600 us" wakeup-path gap · the cushion/drain model · `threadirqs` · `irq/30` priority ·
IRQ placement · `isolcpus` · `nohz_full` · PREEMPT_RT · the nperiods sweep **as a
diagnostic**.

All of these targeted getting audio *out of the buffer* on time. **The buffer was always
full.** They are not wrong engineering; they act on a term that does not exist here.

## Read first

| doc | why |
|---|---|
| `docs/measurements/W1-VERDICT-compute-bound-2026-08-21.md` | the finding, the model, and its weaknesses |
| `docs/measurements/PLAN-V-fixed-callback-cost.md` | this plan in full |
| `docs/measurements/xrun-counter-audit-2026-08-21.md` | what our instruments actually count |
| `docs/measurements/MEASUREMENT-DISCIPLINE.md` | pre-registration, claim classes, shortest-useful-version |

## The hypothesis — and why it must be verified before it is chased

Fitting `T = a + b*N` across W1's three cells gave **a = 1.10 ms fixed per callback**, which
retrodicts the entire T11 ladder.

**It is weak evidence:** a least-squares fit over **three points, n=1 each**, using
`dsp_p99` — a statistic W1 itself showed is wrong, because the overruns live past p99.7.
**1.1 ms is also suspiciously large** for JACK graph overhead, typically tens of microseconds
on ARM.

**Do not profile, refactor, or optimise anything until V1 confirms the fixed cost exists.**

---

## V0 — free pre-checks (read-only, before any window)

Two live confounds. Both are cheap and both may have contaminated **every prior measurement**.

| # | check | why it matters |
|---|---|---|
| V0-a | `MPE_CPU_GOVERNOR` in `/etc/mpe/mpe.env`, and the **actual** governor in `/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor` + current MHz | the example file has it **commented out**, so the box may be on stock `ondemand`. Our own comment says it *"can drop voices on polyphony spikes."* For a compute-bound problem this acts directly on the tail |
| V0-b | is `surge-poly-governor` active? `MPE_POLY_CEILING` / `MPE_POLY_FLOOR` / `MPE_POLY_EMERGENCY` values | it **dynamically reduces polyphony under CPU load**. If it ran during measurements, the load was **not constant across the window** — a confound possibly present in every cell to date |
| V0-c | is `-s softmode` still on jackd? | the Pi came back with it after the W1 restore; it changes xrun handling |

### V0-d — PIN THE CPU GOVERNOR FIRST. This is a precondition, not an experiment.

**If the box is on `ondemand`, CPU clock has been an uncontrolled variable in every
measurement this project has ever taken.** Every `dsp_p99` figure was recorded at whatever
clock the governor happened to select — and it selects based on the very load we vary. Per
the one-variable rule, **the clock must be pinned before any fixed-cost measurement means
anything.**

**Corroborating evidence (Mitch, by ear, 2026-08-21):** *quick pops when hitting chords on
heavy patches **from silence**.* That is the textbook `ondemand` signature — idle means the
clock has ramped **down**; a chord on a heavy patch is a near-instant load step; the governor
only reacts on its next sampling interval (tens of ms on Pi); during that window Surge
computes a heavy block at a low clock and overruns. Then the clock catches up, which is why
the pops are brief and do not sustain.

**Action:** set `MPE_CPU_GOVERNOR=performance`, apply, and **verify the actual value and
current MHz in `/sys/.../scaling_governor` and `scaling_cur_freq`** before running any cell.
Record what it was before. Monitor `vcgencmd measure_temp` and `get_throttled` throughout the
session.

**Then** revert softmode.

### Poly governor — OFF for this entire pass (Mitch, 2026-08-21)

**Disable `surge-poly-governor` and pin poly to a fixed value for every cell in Plan V.**

**Why:** the poly governor is a **feedback loop that reacts to CPU load** — the exact quantity
being measured. Left on, it partially absorbs the fixed cost we are trying to isolate: load
rises, poly drops, load falls. The controller fights the measurement.

**Understand the raw machine first, then re-enable the controller and measure what it does to
a system we understand.** That second pass is deliberately deferred, not forgotten.

**Consequence to state in the deliverable:** these numbers describe the appliance with its
polyphony controller disabled. They are **not** the shipping configuration, and the shipping
xrun rate may differ. Record the pinned poly value used, and use the **same value in every
cell**.

## V1 — silence test (~10 min): does the fixed cost exist?

Surge running, patch loaded, **zero notes playing**. With no voices, what remains is
essentially all fixed cost. **This measures `a` directly instead of inferring it.**

Measure **absolute callback time in milliseconds** (not just percent) at **1024 / 512 / 256**,
n >= 3 runs each.

| outcome | conclusion |
|---|---|
| **~1.1 ms, flat across all three buffers** | **confirmed** — a real per-callback constant. Proceed to V2 |
| **flat but much smaller (tens of us)** | the regression was noise. The real story is **per-voice cost scaling badly**, not a fixed constant. Re-aim at voice cost and say so |
| **not flat — scales with buffer** | **there is no fixed term.** The model is wrong. Say so plainly and stop |

State which row you landed in.

## V2 — client-count test (~10 min): engine cost or Surge cost?

Measure JACK's own DSP load with **no clients**, then with **Surge alone**. The difference
isolates graph traversal and inter-client context switching from Surge's internals.

**This is the direct test of the single-client-architecture idea** (Surge hosting the looper
in-process):

| result | consequence |
|---|---|
| graph traversal is a **meaningful share** of the fixed cost | the refactor is worth building |
| graph traversal is **~50 us** | **the refactor is dead** — a large piece of work avoided for 10 minutes |

## V3 — `1024 x 2` (~15 min): the free latency win, independent of everything above

W0 confirmed ALSA accepts `nperiods=2`. `1024 x 2` = **42.7 ms** against today's 64.0 ms — a
third off shipping latency with **no change to the compute deadline** (Surge still gets
21.3 ms for 1024 frames).

Confirm at **n >= 3 streams**, strict mode, fixed poly. Report xruns/min and DSP against the
`1024 x 3` baseline. **Run this regardless of how V1 and V2 turn out.**

## V5 — `ondemand` vs `performance` as a shipping question (~15 min)

**Note: the governor is already pinned to `performance` by V0-d as a precondition.** V5 is
the separate question of *how much it is worth*, measured deliberately — not the act of
setting it.

**This is not a new idea. It is a documented, prioritised lever that got lost.**
`docs/LATENCY-SPIKE.md` recorded the governor as **`ondemand`**, flagged it as *"a classic
cause of dropouts on transient polyphony spikes"*, and called the governor arm **"the most
promising, and the cheapest."** The mechanism was built (`14da2ca`) but **ships off by
default** and the example file has it commented out. Its sibling knob (Surge RT scheduling)
was completed — Surge runs FF 65 today. **The governor apparently was not.**

It is only *meaningful* now: while this looked like an IRQ/latency problem, clock speed was
irrelevant. For a compute-bound problem it is directly proportional, and `ondemand`'s ramp
acts precisely on the **tail**, which is where the overruns live.

Run `256 x 3` under **both** governors, poly governor **OFF** with poly pinned (as with every
cell in this pass), as a single-variable comparison. Report DSP p99.9/max in **ms** and xruns/min for each.

**Add a transient cell that reproduces Mitch's observation**: from **silence**, trigger a
full chord on a heavy patch, and capture `max` callback time across the transient — under
each governor. The steady-state figures may barely differ while the **transient** is the
whole effect. A steady-state-only comparison would miss it entirely.

**Safety check — monitor, do not avoid.** Record `vcgencmd measure_temp` and
`vcgencmd get_throttled` before, during and after. **If `get_throttled` becomes non-zero,
stop and revert** — the measurement is invalid.

**The historical `0x50000` in `LATENCY-SPIKE.md` is explained and is NOT a reason to avoid
this test.** Cause (Mitch, 2026-08-21): powering external devices without a powered hub, and
jumpers in the GPIO power chain. **Both resolved; recent readings are `0x0` at 55.5 C.**
Treat under-voltage as a live check, not as evidence of a marginal board. Do not cite the
historical value as a constraint.

## V6 — `arm_boost=1` (~15 min): diagnostic only

**Use `arm_boost=1` in `config.txt` — Raspberry Pi's own validated 1.8 GHz configuration for
the Pi 4, the same clock the Pi 400 ships at on identical silicon.**

**Do not set `arm_freq`, `over_voltage` or `force_turbo` manually.** Manual overclocking
beyond `arm_boost` is out of scope for this pass.

Single-variable change at `256 x 3`, after V5. Report DSP p99.9/max in **ms**, xruns/min,
temperature and `get_throttled` throughout, against the V5 baseline.

**What this buys:** +20% clock should yield roughly -20% callback time. That both confirms
pure compute-bound and **calibrates how much the fixed cost is worth chasing** — if 20% moves
256 from 76% to ~63%, it tells us what removing 1.1 ms would be worth.

**This measures the effect. It does not ship it.** For an instrument, a clock that
occasionally throttles is **worse** than a lower clock that never does: throttling is a sudden
step down mid-performance, and on a compute-bound callback that is precisely the tail
excursion that produces `Surge XT was not finished`. **A 15-minute cell cannot clear that** —
it needs a sustained soak in the real enclosure at the hottest ambient the instrument will
see, ending at `throttled=0x0`. **Shipping an overclock is Mitch's decision with soak data,
not a measurement outcome. Revert `arm_boost` after the cell unless told otherwise.**

## V4 — profile (~30 min) — ONLY if V1 confirms

`perf` on the Surge process, or timestamps around `processBlock` entry/exit.

Surge XT processes internally in **32-sample blocks**, so anything per-*internal*-block scales
linearly with buffer size and **cannot** be the fixed term. The cost must be per
`processBlock` **call**. Candidates: JUCE wrapper setup/teardown, MPE/MIDI event-queue
walking, modulation-matrix or parameter-smoothing rebuild, denormal handling, cache reload.

Note: Surge's DSP is **single-threaded** and cannot be spread across cores. The useful
question is not "parallelise it" but **"what in the callback is not DSP and could be deferred
off the audio thread?"**

---

## Instrument requirements (all cells)

1. **Report DSP in absolute milliseconds as well as percent.** Percent-of-deadline hides that
   a fixed cost is constant.
2. **Report `p99.9`, `p99.99` and `max` — not `p99`.** At 256, p99 = 76% while **0.28%** of
   periods overran; p99 excludes the events of interest by construction.
3. **Capture the jackd journal per window** and report both `JackEngine::XRun` lines and any
   `ALSA: xrun of at least N msecs` lines. If ALSA lines ever appear, that is new and
   important — say so loudly.
4. **Stamp actual state into every result:** period, nperiods, rate, resolved card index,
   governor + current MHz, poly ceiling, softmode on/off, git SHA.

## Rules

1. **One variable per cell.** Write both configs side by side before calling it a comparison.
2. **Load must be identical and constant across cells** — poly governor **disabled**, poly
   pinned to the same value everywhere, and the same patch set in the same order.
3. **No commands against the Pi while a window is open**, including read-only ones.
4. **Resolve the card index live** (it moved 6 -> 2 after the Step 3 reboot).
5. **Report n and the claim class it supports.** Shape claims need n >= 10 streams; W1's
   rate column was n=1 and its 512-beats-1024 ordering is noise.
6. **Ask the shortest useful version of each cell** and justify anything longer.
7. If a result refutes something above, **name the doc and say so plainly.**

## Order

| # | cell | Pi time | gate |
|---|---|---|---|
| **V0** | pre-checks **and pin `performance`** + revert softmode | ~10 min | **everything — clock must be pinned first** |
| **V1** | silence test 1024/512/256 | ~10 min | V2, V4 |
| **V2** | client-count test | ~10 min | the single-client refactor |
| **V3** | `1024 x 2` at n >= 3 | ~15 min | independent — run regardless |
| **V5** | `ondemand` vs `performance`, incl. **silence->chord transient** | ~15 min | V6 |
| **V6** | `arm_boost=1` at `256 x 3`, diagnostic | ~15 min | revert after |
| V4 | profile the callback | ~30 min | **only if V1 confirms** |

**V0 first — the clock must be pinned before anything else is measured.** Then **V1 + V2 =
20 minutes and decide whether a refactor is worth building.**

## Deliverable

`docs/measurements/v1-fixed-cost-2026-08-21.md` on a branch off `dev`:

- V0 findings (governor, poly governor, softmode) and what you changed
- V1 table in **absolute ms**, and **which outcome row** you landed in
- V2 split: engine cost vs Surge cost, and the **explicit verdict on the single-client
  refactor**
- V3 result vs the `1024 x 3` baseline, with n stated
- V5 (governor) and V6 (`arm_boost`) results with temperature and `get_throttled` throughout
- an explicit statement that `arm_boost` was reverted
- a **"what this retires"** section
- anything you could not measure, and why

**Do not propose work beyond what these results imply.** V4 is gated on V1; if V1 says there
is no fixed cost, say so and stop rather than looking for one anyway.
