# Review — `yolo/instrument-conformance-c0` (merged as #96)

**2026-08-22.** Reviewed at `a121b01` against [`PROMPT-C0-instrument-conformance.md`](PROMPT-C0-instrument-conformance.md).

**Verdict: the offline half is good and should stay. The gate is not yet a gate — do not
resume the queue on it.**

---

## What is genuinely right

| change | why it matters |
|---|---|
| `PROBE_ACTIVE` handshake before the window opens (`measure-latency-run.sh`) | **This is the actual fix for the V11 sampler bug.** The window is now provably open before load. A window that never signals is VOID rather than reported. |
| `jack_cpu_load` started *after* the meter baseline | Removes an ordering race that could sample outside the window |
| `if (n==0) { print "0 0 0" }` → `exit 1` | A textbook in-band failure deleted. Zero samples used to read as `0 0 0`. |
| `dsp_med=` a forbidden field, hard error | The typo now halts instead of yielding `unknown` |
| `?` / `unknown` / `dsp_median=0` rejected in `require_fields` | Correct direction |
| V11 recovery is per-row, not sticky, with a test for it | Careful — that ordering bug is easy to write |

---

## Blocking findings

### F1 — the gate never touches an instrument (structural)

`scripts/instrument-conformance.sh` runs unit tests against **canned fixtures** in
`tests/fixtures/instrument-conformance/`. It never contacts the Pi, never forces an overrun,
never stops a meter, never asserts that a **live** instrument responds correctly.

**What was built is a parser conformance suite.** That is real and worth keeping — but it
certifies the *reader*, not the *instrument*. Prompt Part 2a/2b required forcing known
conditions on live instruments:

- force load above a known floor → xruns **> 0**
- known-clean config at a confirmed floor → xruns **== 0**
- load at a confirmed floor → DSP **in the band V9/V11 established**
- stop the peak meter mid-cell → read must **fail**, not return 0
- stale `meter.state`, kill jackd mid-window → cell **VOID**, not reported

None of that exists. **Resuming the queue on a green fixture test would repeat the exact
fallacy this rule was written to stop: a passing check that does not prove the instrument
works.** Split it explicitly — `instrument-conformance.sh --offline` (what exists) and
`--live` (what does not) — and require both.

### F2 — the physics assert's tag fallback is dead code

```bash
if [ "$buf" = "512" ] || [ "${MPE_R_tag-}" = *"-b512-"* ]; then
```

Inside `[ ]`, `*` is **literal**. This compares the tag to the string `*-b512-*` and is always
false. Needs `[[ ]]`. The test suite passes only because it hands `512` in as `$buf` directly —
**the test exercises the branch that works and never the fallback.**

### F3 — the physics check does not cover the case that motivated it

The in-row assert fires only at `buf = 512`. **V11's impossible readings were at 256×3:**
Cloud Horn 10.0% DSP with 23 xruns, Crystals 1.6%. At `buf = 256` the function does nothing
and returns 0.

The threshold `d < 15 && x > 5` is also hardcoded to 512's ~38% baseline. It needs to be
per-buffer, and **the rule "a cell with material xruns cannot report low DSP" is
buffer-independent** — express it that way.

### F4 — idle readings still pass

`require_fields` rejects `dsp_median == 0` exactly. **V11's signature was 0.9%–1.6%, not 0.**
Duduk @ 256×3 read 0.9% with zero xruns and would sail through every check here.

Needs a **plausibility floor per buffer**: a loaded window below some minimum DSP is invalid.
Rejecting only exact zero catches a dead sampler but not a mistimed one — and mistimed is what
actually happened.

### F5 — minor, but the same class

`[ "$jitter_n" -lt 100 ] 2>/dev/null` on a non-numeric value errors, the redirect hides it, the
condition is false, and the check passes. A non-numeric `jitter_n` should halt.

---

## Required before this counts as passing

1. Fix F2 (`[[ ]]`), F3 (per-buffer, all buffers), F4 (plausibility floor), F5 (numeric guard).
2. Add the **live** half (F1) and make `instrument-conformance.sh` require both.
3. **Re-run the fixture tests with the physics functions called the way harnesses call them**,
   not with hand-passed arguments — F2 was hidden precisely because the test bypassed it.

Until then the queue stays halted at C0. The offline suite is a real asset; it is just not the
thing that certifies an instrument.
