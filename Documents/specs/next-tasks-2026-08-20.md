# Work order — 2026-08-20

**Delegable. Each task is self-contained and states how it will be verified.**
Tasks are independent unless a "Blocked by" line says otherwise. Nothing here needs
Mitch except T1's reboot, which is already staged.

**PAUSED 2026-08-20.** Session rollup:
[`docs/measurements/session-handoff-2026-08-20.md`](../docs/measurements/session-handoff-2026-08-20.md).
I1–I3 and T6 done; T4 partial (stopped); T5 not started. Resume at I3 n=15 if continuing.

## Standing rules for every task

1. **Label confidence.** Every claim is one of **measured** / **experiment** / **guess**.
   Never present the three in the same voice. A configuration change made to test a
   hypothesis is an experiment and must say so, in the commit and in the handoff.
2. **Verify on the device, not only in tests.** Passing tests did not stop two xrun
   counters from reading dead sources for months. A fix is not done until it has been
   observed working on `raspberrypi2` with real output pasted into the PR.
3. **A reading must not look the same broken or fine** (`docs/measurements/README.md`).
   Failure paths return `None` or raise. Never `0`, `""`, `False`, or a default.
4. **No forks in periodic loops** (`Documents/DECISIONS.md`).
5. **Do not withdraw a conclusion silently.** If a prior finding is wrong, say so
   explicitly and record why, in the doc where the original claim lives.
6. **Bisect before you grid.** Run the cheapest test that could falsify the hypothesis,
   and elaborate only if it survives. A four-point curve at two buffer sizes is 120 runs
   and three hours; the endpoints alone are 30 runs and 45 minutes, and if they match, the
   other 90 runs measure nothing. State the decisive comparison first, then decide whether
   the shape is worth buying.
7. **Certification comes last.** Long soaks prove a configuration is sound. Running one
   before the configuration is decided spends hours certifying something that may not
   ship. Defer soaks until the config is final.
8. **Announce the block.** Before starting anything over ~15 minutes, say which task it is
   and its expected runtime, so a long run is never indistinguishable from a hung one.
9. **One variable per measured comparison.** E1 changed `CPUAffinity` and `irqaffinity`
   together and so could not answer its own question -- 68 minutes and a reboot for a
   confounded result.
10. Read `Documents/specs/low-latency-512-256-spec.md` first — it carries six traps that
   have already voided runs on this hardware.
7. **One variable per measured comparison.** State the single knob explicitly in the work
   order. Changing two things (E1 changed both `irqaffinity` and `CPUAffinity`) voids the
   answer — not just the code path, the experiment design. T3 guards should eventually
   cover this too; the failure mode here was design, not a silent counter.

---

## T1 — E1: three cores instead of two

**Status: done (2026-08-20). Reverted.** Artifact:
`docs/measurements/e1-three-cores-T1-2026-08-20.md`. E1 refuted the **configuration** (A
6.2× worse with no looper); crowding hypothesis **not** isolated. Best split on this
hardware: `irqaffinity=0,1` + `CPUAffinity=2 3`.

**Hypothesis (experiment, not measured):** sooperlooper's +2.13 xruns/60 s is crowding
caused by our own `CPUAffinity=2 3`, not by anything in its code. Condition A runs two
processes on two cores; B/C/D run three. The only significant step in the entire ladder is
exactly that 2->3 transition (+2.13, t=4.73), while session (+0.27) and watchdog (+0.60)
are both ns.

**Already applied on the Pi and on branch `exp/e1-three-cores`:**
- `irqaffinity=0` in `/boot/firmware/cmdline.txt` (backup `cmdline.txt.bak-e1-*`)
- `CPUAffinity=1 2 3` on `mpe-jackd`, `surge-xt-cli`, `mpe-sooperlooper`

**Steps**
1. After Mitch reboots, confirm cores 1-3 take **zero** interrupts:
   `grep -E "^ *(30|41|44):" /proc/interrupts` — all counts on cores 1-3 must be 0.
   Confirm all three processes report `taskset -cp` = `1-3`.
   *A changed cmdline is not evidence. Only per-core counts are.*
2. Measure **A, B and D**, n=15, 512x3. **B is the point** — minimal condition with three
   processes, no session or watchdog confound. Do not shortcut to A and D.

**Compare against** (all n=15, 512x3, xruns/60 s):
A 0.13 · B 2.27 · C 2.53 · D 3.13

**Acceptance**
- Per-core interrupt counts pasted, showing cores 1-3 clean.
- 15 values per condition recorded, means and sds reported, A->B tested for significance.
- An explicit verdict: **crowding** (B collapses) or **structural** (B holds near +2.13).
- If B collapses, say plainly that sooperlooper is exonerated and that the earlier
  "sooperlooper is the biggest step" framing was our configuration, not its code.

---

## T2 — Sweep for the two bug classes we keep rediscovering

**Why:** four instances of two patterns, found serially, each costing a measurement cycle.
Both are mechanically searchable. Do this once, properly, instead of a fifth time.

### Class A — forks in periodic loops (3 known instances)

Found so far: `jack_lsp` in surge-watchdog (35 xruns/min); `journalctl` at 2 Hz in the
session HUD thread; `jack_lsp` + three more per 10 s tick in sl-watchdog.

Search every `while`/timer loop, and every function reachable from one, for:
`subprocess`, `Popen`, `check_output`, `os.system`, backticks, `$(...)`, and specifically
`pgrep`, `pkill`, `jack_lsp`, `jack_cpu_load`, `journalctl`, `systemctl`, `lsusb`, `df`.

`jack_lsp` and `jack_cpu_load` are worse than a plain fork: each registers and unregisters
a JACK client, forcing two graph reorders.

### Class B — readings that cannot fail loudly (4 known instances)

Found so far: cyclictest wrapper logging usage text as a measurement and exiting 0;
`JournalXrunCounter` reading a journal with no xrun lines; watchdog `XrunCounter` tailing
a file that does not exist; `.stale // true` in the jq snapshot.

Search for:
- `except ...: return 0` / `return False` / `return ""` on any health or measurement path
- jq `//` defaults (it treats `false` as absent)
- `set -o pipefail` with `grep -q` on a pipe (SIGPIPE makes a match read as failure)
- any `{ ... } >> logfile` block that writes regardless of the command's exit status

### Class B-live — the half that needs the appliance

**This is the category that found the dead counters, and it cannot be done from the repo.**
For every file, journal, or log any counter or monitor reads: **check it exists and is
non-empty on the running Pi.** The code looks correct in isolation; the source is what is
missing.

Enumerate every path read by anything under `scripts/`, `patch_browser/`, and the watchdogs,
then verify each on `raspberrypi2`.

**Acceptance**
- One table: file, line, class (A / B / B-live), what it reads or forks, and whether it is
  live on the Pi.
- Every Class B-live entry carries the actual command output proving live or dead.
- Findings ranked; anything reading a dead source is P0.
- **Do not fix anything in this task.** Report only — fixes are separate, reviewable
  changes. A sweep that also edits is a sweep nobody can check.

---

## T3 — Make the doctrine enforceable

**Blocked by:** T2 (so it is written against real instances, not imagined ones).

Two guards, both small:

**T3a — a test that fails when a periodic loop forks.** Walk the AST of the watchdog,
session, HUD, and publisher modules. Find loop bodies; assert no subprocess call is
reachable from them. `Documents/DECISIONS.md` already states the rule — this makes it
enforced instead of aspirational.

**T3b — boot-time liveness self-check for every health source.** On start, each counter
and monitor asserts its source exists and is fresh, and **fails loudly** if not. Both dead
xrun counters would have screamed on their first boot instead of lying for months.

This is the higher-value half. It is the harness's fail-loud check applied to the
appliance itself.

**Acceptance**
- T3a fails on a deliberately reintroduced fork, and passes on the current tree.
- T3b fails on a deliberately renamed `meter.state`, and passes normally. **Demonstrate
  both on the Pi**, with output.

---

## T4 — E3: does cost scale with active loops?

**Needs no reboot and no Mitch. Bisect first — see standing rule 6.**

**Every measurement in this investigation has used an idle looper.** The instrument under
real use is unmeasured. Note condition B is sooperlooper with **zero** loops recorded, so
its +2.13 cannot be per-loop DSP work — do not assume the curve rises.

### T4a — the decisive comparison. ~30 runs, ~45 min.

**0 loops vs 16 loops, recorded and playing, at 512 only.** n=15 each.

That is the whole question in one comparison. 1024 is already known-good and is the less
interesting end; the shape between 0 and 16 is worth nothing until the endpoints differ.

**Then stop and report.** Two outcomes:

- **Indistinguishable** — the structural cost dominates, there is one number, and there is
  no tier to sell. **T4b is cancelled**, and 90 runs are not spent measuring a flat line.
- **They differ** — the tier is real. Proceed to T4b for the shape.

### T4b — the shape. **Only if T4a shows an effect.** ~90 runs, ~2.5 h.

Fill in 4 and 8 loops at 512, then repeat the whole set at 1024. That yields the
latency-vs-loop-count curve, which is what a spec claim like "16 loops at 64 ms, 8 at
32 ms" would rest on.

**Acceptance**
- T4a: 30 values, means and sds, a significance test, and an explicit statement of which
  of the two worlds we are in.
- T4b, if run: the curve at both buffer sizes with sds.

## T5 — long soak. **Deferred. Do not queue yet.**

**Blocked by:** a decided shipping configuration.

0.13 xruns/min is one event every ~8 minutes, so fifteen one-minute runs cannot
distinguish it from zero. Only a multi-hour run can. When it happens: 8 hours unattended
at the shipping buffer size and condition, recording temperature and `throttled`
throughout, plus per-hour xrun counts.

**Why it is deferred rather than queued.** A soak certifies that a configuration is sound.
Running one now would spend eight hours certifying 512/condition-A — which is synth-only,
while 512 is not shippable with the looper. That is hours spent on something that may not
be the product. Soak once, on the configuration that ships.

**Trigger:** the buffer size and stack composition are settled, and T6 has landed so the
harness cannot report a silent zero across an unattended overnight run. Without T6 a meter
death ten minutes in yields a flawless fake "0 xruns in 8 hours" — the exact claim the
soak exists to make.

## Merge sequencing

1. **PR #85** (xrun counter fix + E1) — the counter fix should land regardless of E1's
   result. If E1 goes badly, split them.
2. Then `docs/experiment-plan` -> `feat/audio-core-affinity` -> `dev`.
3. **Do not merge anything whose D number was measured against a bug we had already
   named.** That gate has now caught two merges; keep it.

## Current state, for reference

| condition | xruns/60 s, n=15, 512x3 | clean |
|---|---:|---|
| A — synth only | 0.13 | 14/15 |
| B — + sooperlooper | 2.27 | 2/15 |
| C — + session | 2.53 | 1/15 |
| D — full stack | 3.13 | 3/15 |

**512 is usable without the looper and not shippable with it. 1024 remains the default.**
Ship criterion for 512 is 0 xruns across all runs in condition D.

---

# Adjustments after the 2026-08-20 partial run

Supersedes I3, T4 and T5 above. Source:
[`session-handoff-2026-08-20.md`](../../docs/measurements/session-handoff-2026-08-20.md).

## BLOCKER — the baseline moved and stayed moved

| measurement | condition A, 512x3 | |
|---|---:|---|
| baseline, n=15, pre-E1 | **0.13** | 14/15 clean |
| E1 three-core, n=15 | 0.80 | |
| **I3 after full revert, n=5** | **0.80** | 2,0,0,0,2 |

The revert restored the configuration but **not the number**. Everything downstream of
"A = 0.13" rests on that figure, including the claim that 512 is usable without the looper.

**Named hypothesis (guess, must be tested):** the harness changed between those two
measurements. I2 fixed `_meter_xruns`, which previously returned **0** on an unreadable
meter. If that ever fired mid-run, the old harness under-counted — and the original 0.13
is partly an artifact of the bug we later fixed. *512-without-the-looper may never have
been as clean as recorded.*

### I3 (revised) — n=15, blocker

Re-run condition A at 512x3, **n=15**, on the reverted config with the fixed harness.

- Lands near **0.13** -> the n=5 was underpowered; baseline stands.
- Lands near **0.80** -> **the baseline is 0.80.** Revise the claim in every doc that
  quotes 0.13. Do not hunt a regression that is not there.

**Also required:** diff the running configuration against what was live when A = 0.13 was
measured — the watchdog fix, the counter fix and T6 all landed in between. None should
touch the audio path, but "should" is what E1 taught us to distrust. State explicitly what
differs.

**Nothing that quotes an absolute xrun number merges until this resolves.**

## T4 — the partial grid already answered T4a. Do not finish 512.

```
512:   loops0 3.00   loops4 1.33   loops8 2.67   loops16 3.40
```

Endpoints 3.00 vs 3.40, **non-monotonic in between**, spread inside this measurement's
known noise. **Loop count does not drive xruns at 512.** That is the "indistinguishable"
branch of T4a: the structural cost dominates and **there is no latency-vs-loop-count tier
to sell.**

The interrupted grid produced the bisect answer by accident. **Keep it — it is the
result.** Cancel the remaining 512 runs.

### T4c — finish 1024 only. ~30 runs, ~45 min. Worth buying.

```
1024:  loops0 0.00 (n=15)   loops4 0.00 (n=15)   loops8 interrupted
```

**Thirty consecutive runs at zero xruns, with loops recorded and playing.** The strongest
result in this investigation, and the only one that supports a spec sentence a customer
would read: *"16 loops at 64 ms."*

Run `loops8` and `loops16` at 1024, n=15. Either the claim completes or it breaks — both
are worth knowing. This is the one remaining measurement with product value.

**Acceptance:** 30 values, and an explicit statement of whether "16 loops at 1024" holds.

## T5 — still deferred

Now blocked on **both** the I3 resolution and a decided shipping configuration. A soak
against a baseline we cannot reproduce would certify a number we do not trust.

## Revised queue

| # | task | time | needs Mitch |
|---|---|---:|---|
| 1 | **I3 at n=15** — is the baseline 0.13 or 0.80? | ~25 min | no |
| 2 | **T4c** — 1024 loops8 + loops16 | ~45 min | no |
| — | ~~remaining 512 runs~~ — answered, cancelled | — | — |
| 3 | T5 — blocked on 1 and a decided config | — | — |

---

# T5-pre — remove the jack_lsp fallback. **Blocks the soak.**

The T3a lint fails on `scripts/sooperlooper/sl-watchdog.py:188`,
`fork-in-periodic-loop / jack_graph`. It is a true positive.

`read_graph_snapshot()` prefers `meter.state` but falls back to `jack_graph()` — which
forks `jack_lsp -c`, registering a JACK client and forcing two graph reorders — **whenever
the meter is off or stale.** So a stale meter silently reinstates the exact bug E2 removed,
at six forks a minute, *precisely when something is already wrong*. The fallback makes the
problem worse exactly when the problem is happening.

**Why it blocks T5 specifically.** An 8 h unattended overnight run is where a stale meter
is most likely and least likely to be noticed. If it trips at hour three, the remaining
five hours measure a machine being actively degraded by its own monitor, with nobody
watching — and the soak's whole purpose is to characterise rare events over long spans.

Note the lint found this at line 188, inside `read_graph_snapshot()`, **called from** the
loop rather than sitting in the loop body. A lint that only inspected loop bodies would
have missed it. The call-graph upgrade earned its keep on first contact.

## Decision: remove the fallback, and recover the diagnosis without forking

Not a suppression, and not a rate-limit. Removal loses nothing, because the only thing the
fallback buys is distinguishing *"the meter is dead"* from *"JACK is dead"* — and that
distinction is available fork-free:

| meter stale | `jackd` in `/proc` | conclusion |
|---|---|---|
| yes | present | **meter/peak-meter problem** — JACK is fine |
| yes | absent | **JACK is down** |

`engine_running()` already scans `/proc/*/comm` without forking; use the same technique for
`jackd`. Same information, no subprocess, no graph reorder.

The alarm path already exists and needs no new wiring — the caller handles
`snap.jack_reachable is None` and raises a problem. Only the message changes, and it
becomes more accurate: today it reads *"JACK down or not reachable (meter stale and
jack_lsp failed)"*, which conflates the two cases it is now able to tell apart.

`jack_graph()` itself may stay if a non-periodic caller needs it. Nothing on a timer may
call it.

## Acceptance

- `read_graph_snapshot()` makes no subprocess call on any path.
- The stale-meter case distinguishes meter-fault from JACK-down, via `/proc`, and the
  alarm text says which.
- T3a lint passes; full suite green.
- **Demonstrated on the Pi**: stop `mpe-peak-meter` with `jackd` running and show the
  alarm names the meter; stop `jackd` too and show it names JACK. Paste both.
- Confirm with `ps`/`/proc` that **no `jack_lsp` process is spawned** during either
  demonstration — the point is the absence, so prove the absence.

---

# T5 — the soak. Unblocked 2026-08-20.

Both prerequisites are met: I3 cleared at n=15 (A = 0.13, 14/15 — the n=5 read of 0.80 was
underpowered, the baseline stands), and T6 landed so the harness cannot report a silent
zero across an unattended overnight run.

## What to soak, and why it is not the comfortable case

**1024 x 3, full stack, 16 loops recorded and playing. 8 hours.**

The measured 1024 curve:

| loops | xruns/60 s, n=15 | clean | DSP median |
|---|---:|---|---:|
| 0 | 0.00 | 15/15 | 34.8% |
| 4 | 0.00 | 15/15 | 32.9% |
| 8 | 0.00 | 15/15 | 35.8% |
| **16** | **0.13** | **13/15** | **41.8%** |

**Sixteen loops is the only 1024 configuration that has ever produced an xrun.** Soaking
`loops8` would certify the easy case and leave the interesting one untested. If 16 holds
for eight hours, everything below it holds by implication.

It is also the only place an 8 h run tells you something 15 minutes cannot: 0.13/min is one
event every ~8 minutes, so n=15 genuinely cannot distinguish **rare** from **clustered**.
Two isolated singles and one burst of two look identical at this sample size and mean very
different things for a live instrument.

## Protocol

- `scripts/measure-soak.sh --hours 8`, 1024x3, full stack, **16 loops recorded and
  playing** before the soak starts. Verify the loops are actually playing, not just armed.
- Record per-hour xrun counts, not just a total. **When** they occur matters as much as
  how many.
- `vcgencmd measure_temp` and `get_throttled` throughout — 8 h is the first run long
  enough for thermal drift to appear at all.
- Every window must carry `meter_live=1`. **A window without it invalidates that window,
  not the run** — record which, do not silently drop them.
- Announce start and expected finish (standing rule 8).

## Acceptance

- Total xruns over 8 h **and** the per-hour breakdown.
- Explicit statement of the distribution: isolated singles, or clusters?
- Temperature range and whether `throttled` was ever non-zero.
- Count of windows where `meter_live` was not 1.
- A one-sentence verdict on whether **"64 ms, 16 loops"** is a claim that survives eight
  hours, or whether the honest claim is **"64 ms, up to 8 loops"** with 16 footnoted.

## What the result feeds

The spec sentence. Current best-supported claim from 60 measured runs:
**"64 ms, up to 8 loops, clean"** (45/45 across loops 0/4/8), with 16 loops available and
honestly footnoted. The soak either promotes 16 into the headline claim or confirms the
footnote.

**512 is not part of this.** It is unusable with the looper at every loop count measured
(2.87 / 1.33 / 2.13 / 3.93) and no soak changes that.

---

# T7 — periods per buffer. Never varied; the biggest free lever left.

**Every measurement in this investigation has run at `-n 3`.** `-p 512 -n 3`,
`-p 1024 -n 3`. The period *size* has been swept repeatedly; the period *count* has never
been touched once.

## Why it matters, and why it is not obvious

Total latency is `period x nperiods`. The **deadline** is the period alone. The **cushion**
— how much lateness the sound card can absorb before it runs dry — is `(nperiods - 1) x
period`. Three separable quantities, and we have only ever moved one.

| setup | total delay | deadline | **cushion** | wakeups/s |
|---|---:|---:|---:|---:|
| 1024 x 3 — today's default | 64 ms | 21.3 ms | 42.7 ms | 47 |
| 512 x 3 — unshippable with looper | 32 ms | 10.7 ms | 21.3 ms | 94 |
| **256 x 6** | **32 ms** | 5.3 ms | **26.7 ms** | 188 |
| 512 x 4 | 42.7 ms | 10.7 ms | 32 ms | 94 |
| **256 x 8** | **42.7 ms** | 5.3 ms | **37.3 ms** | 188 |
| 1024 x 2 | 42.7 ms | 21.3 ms | 21.3 ms | 47 |
| 256 x 4 | 21.3 ms | 5.3 ms | 16 ms | 188 |

**At equal total latency, a smaller period buys MORE cushion.** 256x6 and 512x3 are both
32 ms to the ear, but 256x6 absorbs 26.7 ms of lateness against 21.3 ms. Strictly more
robust at identical latency.

That is counter-intuitive — smaller buffers are supposed to be harder — and it is only
true here because of what we measured: **the audio thread finishes with 91% of its time
spare** (max period deviation 917 us against 10,667 us) and the xruns originate below JACK,
in the USB path, as the card running dry. **Cushion is what we are short of. CPU is what we
have spare.** The knob nobody turned is the one aimed at the actual failure.

## Implementation blockers — fix these first

1. **`mpe_jack_periods()` in `scripts/lib/audio-engine.sh:68` accepts only `2 | 3 | 4`.**
   6 and 8 hit the `*)` branch, print a warning and **silently fall back to the default** —
   so a naive T7 run would measure `-n 3` three times and report it as three configs.
   Widen the allowlist (2,3,4,6,8) and keep the warning for genuinely invalid values.
2. **The harness has no `--periods` flag.** Add one, thread it to `MPE_JACK_PERIODS`, and
   put the value in the tag and in the provenance block.
3. **Verify the period count from JACK, not from the argument** — trap 5. The harness
   already reads back the period size; read back the count the same way and assert it.
   `mpe_jack_state_write` records it, and `jackd`'s own startup line prints
   `period = N frames ... buffer = M periods`.

## Configs to measure — bisect, per standing rule 6

Condition **D** (full stack), 1024-equivalent loop load, n=15 each.

**T7a — the sharp test. 2 configs, 30 runs, ~45 min.**

- **256 x 6** (32 ms) against the known-failing **512 x 3** (32 ms). Identical latency,
  25% more cushion. Clean means the mechanism is cushion, not deadline, and it is a direct
  win over a config we know fails.
- **256 x 8** (42.7 ms) as the safe variant — still a third better than today's 64 ms.

**T7b — only if T7a shows cushion is the mechanism.** Fill in 512 x 4 and 1024 x 2, both
42.7 ms, to separate cushion from wakeup cost at constant latency. 1024 x 2 has the same
cushion as 512 x 3 with a quarter of the wakeups; if it also fails, cushion is confirmed as
the driver.

**Skip 256 x 4.** 16 ms of cushion, *less* than the 512x3 that already fails.

## What to watch besides xruns

**DSP.** At 256 the Pi wakes 188 times a second against 47 — four times the per-wakeup
overhead. Measured DSP is 35-38% at 512 with loops; 256 could reach the 50s. Record
`dsp_median` and `dsp_p99` per config, and say explicitly whether headroom is the new
constraint.

**The 1 ms USB frame floor still applies underneath all of this.** At a 5.3 ms period you
have roughly five USB frames per chunk — coarse. That constraint does not move until the
audio interface does (see the device decision), so T7 may find a wall that is not JACK's.

## Acceptance

- The three blockers above fixed, with the period count **read back from JACK** and
  asserted per run.
- T7a: 30 values, means and sds, and an explicit verdict on cushion vs deadline.
- DSP reported per config, with a statement on whether headroom became the limit.
- If a config beats 1024 x 3 on latency while matching it on xruns, say so plainly —
  that is a shipping-default change and should be called out, not buried in a table.

---

# Sequence as of 2026-08-21 09:20

**The T5 soak is done.** 445 xruns over 8 h at 1024x3, condition D, 16 loops playing.
`invalid_windows=0`, `meter_live=1` on all 480 samples. Full verdict in
`docs/measurements/t5-soak-2026-08-21.md`. The Pi has been idle since ~12:17 Pi time.

## What the soak changed

**445 is not a regression against T4c's 0.13/min.** T4c ran **condition B**; the soak ran
**condition D**. Both used `SECONDS_PER_RUN=60`, so the windows compare directly -- the
conditions do not. B->D costs +0.80/min here (0.13 -> 0.93), matching the +0.86/min the
ladder measured at 512 (2.27 -> 3.13). **This fixed-cost reading was refuted by T9** --
at 8 loops the stack costs 0.00. See `docs/measurements/t9-loops8-d-2026-08-21.md`.

Two consequences, and they reorder the queue:

1. **The shipping claim is unmeasured.** "64 ms, up to 8 loops, clean" came from T4c's
   0/4/8-loop cells, all 0.00 -- at condition B, without the full stack running. The
   8-loop cell under **D**, which is what the appliance actually runs, has never been
   measured. That cell is the claim.
2. **+0.80/min is now the largest single term** -- larger than buffer geometry is likely
   to recover. T7a can still settle cushion vs deadline, but a win there does not remove
   this.

## Queue

| order | task | needs the Pi |
|---|---|---|
| ~~1~~ | ~~T9 -- 8 loops at 1024x3, condition D~~ **done: 0.00, 15/15 clean** | -- |
| **2** | **T11 -- condition A ladder: 256, 128, 64**, n=15 per cell | ~45 min |
| 3 | **T10 -- wakeup delay vs callback duration**, one instrumented run | ~20 min |
| 4 | **T7a** -- 256x6 vs 512x3, plus 256x8, n=15 each | ~45 min |
| 5 | decide whether the shipping default changes | -- |
| 6, only if it changes | re-confirm the winner at n=15 | ~15 min |

T9 localises **which component**. T10 localises **which mechanism**. Instrumenting before
the ladder means instrumenting without knowing where to point the instrument.

## What the soak ruled out

Arrivals are **Poisson** -- index of dispersion 1.091, chi-square ~4.0 (full table in the
soak doc). Random independent events, not a steady drain.

That kills clock drift as the cause. The Sound Blaster is full-speed **adaptive** with no
feedback endpoint, so the host guesses the sample rate and drift is plausible a priori --
but drift underruns on a metronome and produces **underdispersed** arrivals, index well
below 1. We measured 1.09.

**So do not spend on:** `zita-ajbridge` / `alsa_out` adaptive resampling with a DLL
(exists to hold ring depth against drift; costs a resampler on the hot path, against the
CPU doctrine, to fix a problem we do not have), or PipeWire's dynamic quantum (same
reason, and it adds latency under exactly the load where it would be noticed).

## The callback is not the problem

Max callback lateness ever recorded is **917 us against a 10.7 ms deadline** -- 8.6%.
Lateness does not correlate with xruns (r = -0.07). Nothing is running long.

That is a localisation, not a null. Either the audio thread is **not being scheduled** when
the period interrupt fires -- the cycle is late although the work is fast -- or the drain is
below JACK in the USB path. **Those two are indistinguishable if you only measure callback
duration, which is what has been measured so far.**

## T9 -- A/B/C/D ladder at 1024x3, 8 loops

The 512 ladder is already a clean single-variable bisect, and it does not point where the
recent work has been pointing:

| condition | adds | xruns/min @ 512 | delta |
|---|---|---|---|
| A | synth only | 0.13 | -- |
| B | + sooperlooper | 2.27 | **+2.14** |
| C | + session | 2.53 | +0.26 |
| D | + sl-watchdog | 3.13 | +0.60 |

**Sooperlooper is 71% of the cost.** Session and watchdog together are under a third of
what sooperlooper alone costs -- and this is already post-`jack_lsp`-removal, so it is not
a fork.

Sooperlooper is not CPU-heavy at these loop counts. What it does is **add a node to JACK's
serial process chain**: surge completes, hands off, sooperlooper runs, hands off. Each
handoff is a context switch between processes and a fresh chance not to be scheduled
promptly. Adding a client adds a scheduling hop, not compute. That fits every observation
-- fast callbacks, no compute correlation, Poisson arrivals (each hop an independent
chance to miss), and a cost roughly fixed per minute regardless of period.

**Run all four cells at 1024x3 with 8 loops, n=15, `--no-restore-buffer` between cells.**
Verify loops are *playing*, not armed. Require `meter_live=1` per run; a run without it is
void, not zero.

This **subsumes the earlier T9** (8-loop cell at condition D) -- that cell falls out of the
ladder as the D row, and the ladder additionally says where the cost sits at the buffer
that actually ships. Report per-cell mean, clean-run count out of 15, and DSP.

## T11 -- condition A buffer ladder: 256, 128, 64. **The instrument-only number.**

**Why this exists.** Every 256 measurement to date has been taken with sooperlooper in the
graph. "512 is unshippable" is a *looper* verdict, not an instrument verdict -- condition A
at 512 is 0.13/min, 14/15 clean, and has been sitting there unremarked while every task
since has been about B, C and D. The stated goal is a low-latency **live instrument**. That
is condition A, and its low end has never been measured.

At condition A there is no sooperlooper: the process chain is jackd -> surge, **one hop
instead of three**, and the +2.14/min sooperlooper term does not exist. The cyclictest
floor (209-320 us idle, 257 us under load) is 16-24% of a 64-frame deadline, so the
scheduler does not block any of these on paper.

**512 is not re-run** -- already measured at n=15.

| period | deadline | total @ n3 | cushion |
|---|---|---|---|
| 256 | 5.33 ms | 16 ms | 10.7 ms |
| 128 | 2.67 ms | 8 ms | 5.3 ms |
| 64 | 1.33 ms | 4 ms | 2.7 ms |

`--condition A --runs 15`, `--no-restore-buffer` between cells. Nothing else running:
no sooperlooper, no session, no watchdog. Require `meter_live=1` per run; a run without it
is void, not zero. Report mean, clean-run count out of 15, and DSP per cell.

### The transport floor, and a possible confound at the low end

The Sound Blaster is **full speed**: one USB frame per 1 ms, which at 48 kHz is **48
samples per frame**. 64 frames is the last power of two above that quantum, so it is the
right place to stop -- below ~48 frames the period is smaller than the transport's own
granularity and JACK cannot win regardless of scheduling.

Note that **no power-of-two period aligns to the 1 ms frame.** 64 frames = 1.33 frames,
128 = 2.67, 256 = 5.33. Each period straddles USB frame boundaries. This is evidently
harmless at 1024 (21.3 frames, misalignment ~1.5%), but at 64 the misalignment is a third
of a frame -- a much larger fraction of the budget.

**If 64 fails while 128 passes, add USB-aligned cells before concluding the deadline is the
limit:** 96 frames (2 ms, exactly 2 frames) and 48 frames (1 ms, exactly 1 frame). If 96
beats 128 despite being the smaller period, alignment is the mechanism, not deadline --
and that is a finding that transfers to any full-speed device.

### What a clean result means

If 256 or below runs clean at condition A, **you have a shippable low-latency instrument
mode today**, independent of every open architectural question about sooperlooper. That
also reframes the single-client work: it stops being "how do we reach low latency" and
becomes "how do we extend the low-latency mode to cover looping" -- a better position to
decide from, and a reason to run this before committing to that architecture.

## T10 -- wakeup delay vs callback duration

The measurement that separates "work took too long" from "we were not scheduled".

Record, per cycle, the interval from **period interrupt to audio thread running**, not the
callback's own duration. `jack_get_cycle_times()` exposes nominal cycle start against
actual; `native/mpe-xrun-probe` already has the hook point.

**Verdict to produce:** if wakeup delay has a fat tail while callback duration does not,
the cause is scheduling and the remaining work is scheduling work. If neither has a tail,
the drain is below JACK in the USB path and no amount of JACK-side tuning will touch it.

Two follow-ons, only if T9 implicates sooperlooper and T10 implicates scheduling:

- **Which client was last to complete at xrun time.** If it is consistently the handoff
  into or out of sooperlooper, the chain hypothesis is confirmed exactly.
- **`sched_switch` ftrace armed by the probe** -- do not leave it running; have the probe
  snapshot the last few hundred events when an xrun fires and read what preempted the
  audio thread. This names the culprit rather than the layer.

## Status of the code tasks

**T8 (delete `jack_graph()`) and the T7 prep are done locally and uncommitted** on
`plan/t7-sequence`: periods 6/8 accepted, `--periods` threaded through the harness, the
trap-5 read-back assert added, `scripts/measure-t7a.sh` written, `tests/test_sl_watchdog.py`
deleted. These need staging and a test run before T7a can be trusted -- the periods widening
in particular, since without it T7a measures `-n 3` three times and reports it as three
configs.

**No second 8 h soak.** Superseded by the soak's own result. The deferral rule assumed
0.13/min, where a 15 min window cannot separate rare from zero. Condition D actually runs
at 0.93/min -- ~14 events per 15 min, ample to rank configurations. And the failure mode is
**stationary**: flat hourly (44/51/64/72/52/47/58/57), first-half 231 vs second-half 214,
isolated singles rather than clusters (199 clean minutes, worst minute 6), 55-59 C with
`throttled=0x0` throughout. A long soak only earns its cost if accumulation, thermal creep,
or rare storms exist. We looked for all three and found none.

## Scarlett

Unchanged: a different interface invalidates every Sound Blaster latency number, T9's and
T7a's included. The *mechanism* findings transfer -- cushion vs deadline, the fixed stack
cost -- the *winning configuration* does not. If the Scarlett test is imminent, run it
before T7a; if it is weeks out, run T9 and T7a now.
