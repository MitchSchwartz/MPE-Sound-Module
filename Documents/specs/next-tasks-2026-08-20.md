# Work order — 2026-08-20

**Delegable. Each task is self-contained and states how it will be verified.**
Tasks are independent unless a "Blocked by" line says otherwise. Nothing here needs
Mitch except T1's reboot, which is already staged.

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
6. Read `Documents/specs/low-latency-512-256-spec.md` first — it carries six traps that
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

**Status: done (2026-08-20).** Artifact: `docs/measurements/t2-bug-class-sweep-2026-08-20.md`.
Findings only — no fixes in that commit.

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

**Status: done (2026-08-20).** `656b081` on `plan/next-tasks`. T3a:
`scripts/lib/periodic_loop_lint.py` + `tests/test_periodic_loop_lint.py`. T3b:
`patch_browser/health_source_liveness.py` wired into `sl-watchdog` and `looper_session`;
Pi demo output in `docs/measurements/t2-bug-class-sweep-2026-08-20.md` §T3.

**Was blocked by:** T2 (now complete).

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

## T4 — E3: the loop-count curve

**Highest product value. Needs no reboot and no Mitch.**

**Every measurement in this entire investigation has used an idle looper.** The instrument
under real use is unmeasured.

Record 0, 4, 8 and 16 loops and leave them playing. Measure at 512 and 1024, n=15 each.

This answers directly whether a latency/loop-count tier is a real spec ("16 loops at 64 ms,
8 at 32 ms") or whether the structural cost dominates and there is one number.

**Note:** condition B is sooperlooper with **zero** loops recorded, so its +2.13 cannot be
per-loop DSP work. Do not assume the curve rises; measure it.

**Acceptance**
- A curve: xruns/60 s vs loop count, at both buffer sizes, with sds.
- An explicit statement of which of the two worlds we are in.

---

## T5 — E4: long soak

**Needs no Mitch.** 0.13 xruns/min is one event every ~8 minutes, so fifteen one-minute
runs cannot distinguish it from zero. Run **8 hours unattended** at 512, condition A, and
count. Also record temperature and `throttled` throughout.

**Acceptance:** total xruns over 8 h, with the per-hour breakdown, and a statement of
whether 512 without the looper is genuinely clean or merely quiet.

---

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

## T6 — Apply the doctrine to the measurement rig itself

**Do this before T5, and before quoting any number from the harness again.**

**Why this is its own task.** T2 swept the appliance and T3 guards the appliance. Nobody
has ever swept the thing that measures it — and the harness has now produced **three**
silent-failure bugs on its own:

| | bug | what it did |
|---|---|---|
| 1 | `measure-cyclictest-floor.sh` piped cyclictest straight into the log | wrote the tool's **usage text** as a measurement, printed success, exited 0 |
| 2 | the guard added to fix (1) used `pipefail` + `grep -q` | SIGPIPE made a **successful match read as failure**; passed at 44 KB, failed at 875 KB |
| 3 | `measure-latency-run.sh` `_meter_xruns()` ends `\|\| echo 0` | a missing or stale `meter.state` reports **zero xruns** — identical to a perfect run |

Three instances of the same category in one component. It has never been held to the
standard it exists to enforce.

**It matters more than the appliance.** A lying appliance is caught by a measurement. A
lying measurement is caught by nothing, and silently poisons every decision downstream —
including the ones already made in this work order.

### Scope

Everything under `scripts/measure-*`, `scripts/lib/`, `native/mpe-xrun-probe/`, and any
script that produces a number a human will act on.

1. **Sweep for Class B** (readings that cannot fail loudly) exactly as T2 did for the
   appliance. Every `|| echo 0`, `|| true`, `2>/dev/null` that swallows a failure,
   `except: return 0`, and every `{ ... } >> log` block that writes regardless of exit
   status.
2. **Extend T3b's liveness check to the rig.** Before a run starts and after it ends,
   every source the harness reads must be asserted present and fresh, and the run must
   **abort** — not warn — if it is not. `MPE_PEAK_METER != 1` is currently a warning; it
   should be fatal.
3. **Record provenance of the measurement, not just of the code.** Each RESULT line should
   carry whether the meter was live for that window. A number whose source cannot be shown
   to have been alive is not a measurement.
4. **Prove each guard by making it fail**, on the Pi, with output pasted. A guard
   demonstrated only in its passing state is exactly what bugs 1 and 3 were.

**Acceptance**
- Table of findings in `scripts/measure-*` and `scripts/lib/`, same format as T2.
- Every fix demonstrated failing and passing on `raspberrypi2`.
- `mpe test local all` green.
- A one-line statement in `docs/measurements/README.md`: the rig is held to the same
  standard as the appliance, and here is what enforces it.

---

## Immediate queue (in order)

**I1 — commit and push the local doc/status updates.** T2/T3 status is marked done in an
uncommitted tree; `859d6d7` (E1 measurement doc) is local-only. Done work that exists on
one machine is not done.

**I2 — fix `_meter_xruns` (T2's own P0).** Fail loudly instead of returning 0; assert
`updated=` freshness per window with the same 3 s rule the counters use. **Gates T5.**

**I3 — verify the E1 revert by measurement.** 5x60 s, condition A, 512x3. The reboot
landed and the config reads correctly, but **no measurement has been taken since the
revert** — and E1 just demonstrated that a config which looks right can be 6x worse.
Expect ~0.13; anything near 0.80 means it did not take. This also exercises the I2 fix.

**Then T4** (loop-count curve) on a verified baseline with a harness that cannot lie,
**then T6**, then T5.

### Note for T3a

Check whether the periodic-loop lint walks **called functions** or only literal loop
bodies. The session's `journalctl` fork was not in the loop — it was two calls deep
(`collect_jack_graph_health` -> `XrunCounter.poll()`). A lint that inspects only loop
bodies would miss the exact bug it was written for.
