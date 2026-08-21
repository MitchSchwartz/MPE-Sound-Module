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
