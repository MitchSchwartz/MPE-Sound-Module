# C0 — instrument conformance suite

**This is now the first thing that runs, before any measurement, in every session.** Nothing
else in the queue proceeds until it exists and passes.

Doctrine: [`MEASUREMENT-DISCIPLINE.md`](MEASUREMENT-DISCIPLINE.md) **Rule -1**. Skill:
OM-Repo `.claude/skills/measurement-design/` **Step 0**.

---

## Why this exists — read before designing anything

**Nine measurements on this appliance have produced confident wrong numbers from blind
instruments.** They have one root cause, not nine:

> **Every instrument returns its value and its failure through the same channel.** At the
> reading site there is no way to distinguish *"here is a measurement"* from *"I could not
> measure."* So a broken instrument and a working one are indistinguishable, and the failure
> arrives as a **result** — believed, written up, acted on.

| date | instrument | returned | should have returned |
|---|---|---|---|
| 08-19 | `xrun-corr.sh` | exit 0, empty file, 12 runs | write failure |
| 08-19 | `set-surge-audio.sh` | continued without `sudo`; a run labelled 512 ran at 1024 | hard stop |
| 08-19 | latency tap v1 | `n=0` after 267 presses | no-events error |
| 08-19 | latency tap v2 | `n=0` after 115 presses | no-events error |
| 08-21 | V8-b auto-pick | a plausible patch name — the wrong one | selection failure |
| 08-21 | peak-meter shutdown | looked stopped; wasn't | shutdown failure |
| 08-22 | V10-b ramp probe | `0` xruns via `\|\| start=0` swallowing a blind meter | blind-meter error |
| 08-22 | census `unison_voices` | plausible integer, summed engine selectors | unsupported-field error |
| 08-22 | V11 `dsp_med` | `unknown`, plus idle readings as measurements | field + alignment error |

**The proof that this is fixable cheaply:** V11's xrun column is trustworthy and its DSP column
is not, and the *only* difference is that a positive control ran on the xrun path that morning.
Five minutes saved half a 25-minute run. **Generalise that, do not invent something new.**

---

> **Status 2026-08-22:** first pass merged as #96. **Offline/parser half is good; the live half
> does not exist and four defects block the gate** — see
> [`REVIEW-C0-conformance-2026-08-22.md`](REVIEW-C0-conformance-2026-08-22.md). The queue stays
> halted. `instrument-conformance.sh` must split into `--offline` and `--live` and require both.

## Deliverable

`scripts/measure-instrument-conformance.sh`

- Runs in **≤ 15 minutes**. It runs before every suite; if it is slow it will be skipped, and a
  gate that gets skipped is not a gate.
- Tests **every metric any harness reports**, not only the ones in today's plan.
- Emits a structured result plus a one-line verdict: `CONFORMANCE PASS` / `CONFORMANCE FAIL`.
- **Exit non-zero on any failure.** Callers must be unable to proceed by accident.
- Takes a platform label and records kernel, JACK version, Surge revision, buffer/periods.

---

## Part 1 — inventory every reported metric (offline, first)

Before writing any test, enumerate what the harnesses actually emit. At minimum:

`xruns` · `dsp_med` · `dsp_p99` · `dsp_max` · `frames_late` · buffer fill level ·
achieved clock · temperature · `get_throttled` · voice count · patch identity ·
applied buffer/periods · applied governor

For each, in a table: **what increments it, where it is read, what it returns when broken,
and what reading would be arithmetically impossible.**

The last two columns are the point. *If "what it returns when broken" matches a healthy
reading, it is not an instrument* — fix that before testing it.

---

## Part 2 — three tests per metric

### 2a. Positive control — force a known answer, assert the reading matches

Not *"did it return something."* **"Did it return the right something."**

| metric | forced condition | assertion |
|---|---|---|
| `xruns` | load well above a known floor, 8 s | count **> 0** |
| `xruns` | known-clean config at a confirmed floor | count **== 0** |
| `dsp_*` | silence, no voices | reading **low but non-zero**, and *present* |
| `dsp_*` | load at a confirmed floor | reading in the **band V9/V11 established** — V11's 0.9% at 256×3 fails this instantly |
| `dsp_*` | load near overload | reading **high** (approaching the deadline) |
| applied buffer | set 512, read back | reports **512**, not the requested value echoed |
| clock / temp | compare two known-different states | both **move in the right direction** |

**Both ends matter.** A counter stuck at zero passes a "must be 0 when clean" test. A counter
stuck high passes "must be > 0 when loaded." Only both together constrain it.

### 2b. Negative control — break it, assert the harness HALTS

**This is the test that would have caught all nine.** For each metric, deliberately induce the
failure and assert the harness **stops and names the instrument** — not that it returns a
default, not `unknown`, not zero.

- Stop the peak meter mid-cell → xrun read must **fail**, not return 0.
- Stale/delete `meter.state` → `mpe_meter_assert_live` must fail **and propagate**.
- Rename a field the parser expects (`dsp_med`/`dsp_median`) → **hard error**, not `unknown`.
- Point the harness at a non-existent patch → **halt**, not silent substitution.
- Kill jackd mid-window → the cell must be **marked invalid**, not reported.
- Sample DSP with no load running → must be **detectably wrong**, not published.

**Any case where the harness produces a number instead of an error is a defect to fix now**,
in this task, not to record for later.

### 2c. Physics assertions — in the harness, permanently

These run on **every result, forever**, not just during conformance. A human noticing at review
time is not a mechanism.

- **DSP% must not fall when the buffer halves.** Deadline halves; per-callback cost barely moves
  (`a = 0.13 ms`). V11 reported 39.6% → 1.6% and 9.7% → 0.9%. **Impossible; must be rejected.**
- **A cell reporting xruns cannot report low DSP.** At the deadline, DSP is ~100% by definition.
  V11 reported 10.0% with 23 xruns. **Impossible.**
- **Unrelated patches must not converge on the same DSP value.** ~1% across three different
  patches is a signature of an idle read, not a measurement.
- Counts monotone where physics is monotone; parts sum to the whole.

Each violation names the assertion and the values. **Rejected, not annotated.**

---

## Part 3 — fix what Part 2 finds

Expect real defects. Two are already known and in scope:

1. **`dsp_med` / `dsp_median` field mismatch** — fix the name **and** make a missing field a hard
   error. The rename alone just hides the next one.
2. **DSP sampler window alignment** — V11's readings look like idle samples. Determine whether
   the sampler is provably running *during* the load window; the V10-b per-probe jackd restart
   is the prime suspect. **A correct instrument sampling at the wrong time is indistinguishable
   from a broken one.**

Then sweep the harness for in-band failures — every `|| x=0`, `// 0`, `except: pass`,
`unknown`, `n/a`, and continue-on-error on a reading path. **Each becomes a halt.**

---

## Part 4 — recover V11 if possible (offline first)

Raw artifacts are in `~/plan-v11-20260822-144259/`. Once the sampler is understood, re-read
those logs offline and determine whether the DSP samples are recoverable or were never valid.

- Recoverable → publish corrected values, note the correction.
- Not recoverable → mark the DSP column **withheld**, with the reason.

**Do not re-run V11 for the DSP numbers until conformance passes.** The xrun column is sound;
re-running now would just buy more bad readings.

---

## Constraints

- **Offline work first.** Parts 1 and 3's source sweep need no Pi time. Establish the Pi is idle
  before Part 2.
- **No forks in polling loops** — the conformance harness is subject to the same CPU doctrine as
  everything else, and a DSP sampler that perturbs DSP is its own instrument bug.
- **Do not weaken an assertion to make a test pass.** If a physics assertion fires on real data,
  the instrument is wrong or the physics model is wrong — **both are findings; neither is a
  reason to lower the threshold.**
- Do not bundle unrelated harness improvements into this.

---

## Hand back

The metric inventory table, PASS/FAIL per metric for positive and negative control, every defect
found and whether it is fixed, the in-band-failure sweep results, the V11 DSP disposition
(recovered or withheld), and the runtime.

**Then, and only then, the queue resumes at A1.**
