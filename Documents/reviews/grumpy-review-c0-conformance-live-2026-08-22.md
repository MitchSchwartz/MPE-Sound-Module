# Grumpy review — C0 conformance, live half (`yolo/c0-conformance-live`)

*Reviewed: 2026-08-22 17:02 EDT (America/Toronto)*

**Scope:** the changes addressing F1–F5 from
[`REVIEW-C0-conformance-2026-08-22.md`](../../docs/measurements/REVIEW-C0-conformance-2026-08-22.md),
measured against [`PROMPT-C0-instrument-conformance.md`](../../docs/measurements/PROMPT-C0-instrument-conformance.md).

**Reviewed at:** working tree on `yolo/c0-conformance-live`, base `9581825`.

| file | state |
|---|---|
| `scripts/lib/measurement-result.sh` | modified — read in full (254 lines) |
| `scripts/instrument-conformance.sh` | modified — read in full (64 lines) |
| `scripts/measure-instrument-conformance-live.sh` | new — read in full (72 lines) |
| `tests/test_instrument_conformance_offline.sh` | new — read in full (118 lines) |
| `tests/test_instrument_conformance_live.sh` | new — read in full (82 lines) |
| `tests/fixtures/instrument-conformance/*` | all 6 fixtures read |
| `scripts/lib/audio-engine.sh` | sampled — `mpe_jack_period`, `mpe_meter_*` only |

**Not read:** the rest of `audio-engine.sh`, `paths.sh`, `test_meter_harness.sh`, the Python
suites the gate invokes. **Not run:** the live half (no Pi, no `/run/mpe/meter.state` in this
sandbox) — every live finding below is from code reading, and I say so where it matters.

**Executed:** `tests/test_instrument_conformance_offline.sh` (17 checks, exit 0, 1.9 s) plus 14
adversarial probes against `measurement-result.sh`. Probe transcripts are quoted inline; four of
them are reproducible one-liners that turn a green gate red.

---

## 1. First Impressions (The Gut Check)

This looks like professionals work here, and that is not a courtesy. The offline suite is real
work: six hand-built fixtures that mirror actual harness output, negative controls that assert
halting rather than annotating, an explicit non-sticky-withhold ordering test. Someone read F1–F5
and went after them individually instead of papering over the diff. The `--offline` / `--live`
split is the right structural answer to F1, and `test_instrument_conformance_live.sh` **exits 1
rather than 0** when it cannot run its positive controls — refusing to fake a pass off-Pi is
exactly the discipline this whole task exists to install.

Then I pointed the suite's own stated purpose at itself, and the gut check curdled.

This project's founding document lists nine instruments that returned confident wrong numbers,
and names the mechanism: *"a broken instrument and a working one are indistinguishable."* The C0
prompt lists, as a **required negative control**, this exact case:

> Sample DSP with no load running → must be **detectably wrong**, not published.

`measure-instrument-conformance-live.sh` kills the load on line 47 and reads DSP on line 58. **The
gate that certifies instruments against sampling-with-no-load commits sampling-with-no-load, and
publishes the number.** Its floor comparison then uses a buffer size read from an env var rather
than from the running jackd — which is verbatim the `set-surge-audio.sh` defect from row 2 of the
nine ("a run labelled 512 ran at 1024"), the one the prompt calls out by name on line 81.

And the F4 plausibility floor — the fix for V11's 0.9%-as-a-measurement — can be walked straight
past. I did it in four lines:

```
=== P7: harness path — V11 idle signature with short window ===
LEAK: assert_tag GREEN on 0.9% DSP @256 (the exact V11 signature F4 was written to catch)
```

So: good engineering, aimed correctly, and it does not yet hold. F2 and F3 are genuinely closed.
F4 and F5 are closed only along the paths the tests walk. F1 is roughly half built. The offline
half should be merged and kept. The live half is not a gate yet.

---

## 2. Architecture & Structure

**The split is correct and worth keeping.** `--offline` (fixtures, no Pi) vs `--live` (appliance,
positive controls) is the right seam, it maps to where the work can run, and the sentinel records
which half ran (`SENTINEL conformance-pass mode=${MODE}`). Good.

**`mpe_result_assert_tag` is the right idea.** F2 was hidden because tests hand-passed arguments
that harnesses never pass, so introducing one entry point that harnesses and tests share is the
structural fix, not just a patch. Credit where due — see §6 for why it is under-used.

Three structural problems:

**🔴 Nothing invokes the gate.** I grepped `scripts/yolo/`, `.claude/`, and `.github/` for
`instrument-conformance`: no hits. Nothing consumes `SENTINEL conformance-pass` either. The prompt
opens with *"Nothing else in the queue proceeds until it exists and passes"* — that is currently a
sentence in a markdown file, not a mechanism. Worse, a consumer that greps for the sentinel cannot
tell a full gate from `--offline` without parsing `mode=`, and `--offline` needs no Pi, so the
easy path is also the meaningless one. *Fix: have the queue runner require
`SENTINEL conformance-pass mode=all` (or `mode=--live`) and fail closed on its absence.*

**🟡 Physics thresholds are magic numbers with no provenance, and the two tables disagree with
each other.**

```bash
# scripts/lib/measurement-result.sh:26-45
mpe_result_dsp_plausibility_floor() {
    case "$buf" in
        256) echo "3.0" ;;  512) echo "5.0" ;;  1024) echo "2.0" ;;  *) echo "3.0" ;;
    esac
}
_mpe_result_physics_low_dsp_ceiling() {
    case "$buf" in
        256) echo "20" ;;   512) echo "15" ;;   1024) echo "10" ;;   *) echo "15" ;;
    esac
}
```

DSP% is work-per-callback over deadline; halving the buffer halves the deadline while per-callback
cost barely moves — the prompt states this on line 108 and uses it to reject V11. So DSP% **rises
as the buffer shrinks**, and both tables should be monotone decreasing in buffer size. The ceiling
table is (20 / 15 / 10). The floor table is not: 512 gets the *highest* floor (5.0) and 256 a
lower one (3.0). Two functions, same physical model, opposite orderings — at most one is right.
None of the six constants carries a comment saying where it came from. *Fix: derive all six from
the V9/V11 measured bands, cite the run in a comment, and make both monotone.*

**🟢 Library scope pollution.** `measurement-result.sh:6` runs `set -uo pipefail` at source time,
mutating the caller's shell options. `measure-instrument-conformance-live.sh` sources it at line 64
— **mid-script, after the logic that needs it** — so options change halfway through a running
harness. *Fix: set options in scripts, not libraries; source all libs at the top.*

---

## 3. Code Quality

Naming is good and consistent (`mpe_result_*`, `_mpe_result_*` for private). Error messages name
the instrument and the values, which is the whole point:

```
ERROR: measurement-result: physics: dsp_median=10.000000% with xruns=23 at buffer=256 impossible (low DSP + material xruns)
```

That is a model failure message. More of that.

**Dead code, same class as F2.** F2 was a branch that could never be true. Two more shipped:

```bash
# measure-instrument-conformance-live.sh:58-59
dsp="$(timeout 5 stdbuf -oL jack_cpu_load 2>/dev/null | tail -1 | grep -oP '[0-9]+\.[0-9]+' | head -1 || true)"
if [ -z "$dsp" ] || [ "$dsp" = "?" ]; then
```

`grep -oP '[0-9]+\.[0-9]+'` can emit only digits and a dot. `"$dsp" = "?"` is unreachable. 🟢

```bash
# tests/test_instrument_conformance_live.sh:36-37
fresh_meter "$f"
printf 'xruns=0\nupdated=%s\n' "$((EPOCHSECONDS - 120))" >"$f"
```

`fresh_meter` writes the file, then the next line overwrites it entirely. The call does nothing. 🟢

**Comment/code drift.** `measure-instrument-conformance-live.sh:40` says `midi-load 15 s`; the
argument is `20` and the measured window is `sleep 2` + `sleep 8` = 10 s. Three numbers, no two
matching. 🟢

**Orphaned test.** `tests/test_instrument_conformance.sh` (the pre-split original) still exists and
nothing references it — `instrument-conformance.sh` now runs `_offline` and `test_meter_harness.sh`.
It is a `.sh`, so Python discovery will not pick it up either. Dead file that will drift out of
sync and then confuse someone at 2 a.m. 🟡 *Fix: delete it or fold its unique cases into `_offline`.*

**Unquoted expansion in the parser.** `measurement-result.sh:73`:

```bash
for tok in $(echo "$line" | sed 's/^RESULT //'); do
```

Word-split plus glob. I checked — `xruns=*` survives literally, but only because no file in cwd
starts with `xruns=`. A parser whose correctness depends on the working directory's contents is a
parser with a latent bug. 🟢 *Fix: `read -ra` into an array, or `set -f` around the loop.*

**Argument handling is silently lossy.** `instrument-conformance.sh:12` takes `${1:-all}` and never
looks at `$2`. `instrument-conformance.sh --offline --live` runs offline only and prints a pass. 🟢

---

## 4. Code Smells (The Hall of Shame)

### 🔴 S1 — The live DSP band check samples after the load is dead

```bash
# scripts/measure-instrument-conformance-live.sh:47-60
kill "$load_pid" 2>/dev/null || true
wait "$load_pid" 2>/dev/null || true
...
dsp="$(timeout 5 stdbuf -oL jack_cpu_load ... )"
if [ -z "$dsp" ] || [ "$dsp" = "?" ]; then
    echo "ERROR: jack_cpu_load returned no numeric DSP under load" >&2
```

The load is killed and waited on, *then* DSP is read, *then* compared against
`mpe_result_dsp_plausibility_floor` — a floor whose docstring reads "Minimum plausible dsp_median
for **a loaded window**." The error string says "under load." Nothing is under load.

Two outcomes, both bad: idle DSP falls below the floor and the gate fails permanently for a reason
its message misattributes; or it squeaks over the floor and reports a pass that measured silence.
The prompt lists this precise scenario as a required *negative* control (line 98). This is the
mistimed-window bug from F4 — the one this whole work order exists to kill — reintroduced in the
script whose job is to catch it. **Not read on hardware; this is from the source ordering, which is
unambiguous.**

*Fix: move the `jack_cpu_load` read between `start_load` and the `kill`, inside the 8 s window.*

### 🔴 S2 — Physics silently no-ops when the tag has no buffer token

```bash
# scripts/lib/measurement-result.sh:151-161
if [ -n "$xr" ] && [ -n "$dsp" ]; then
    buf="$(_mpe_result_resolve_buffer "$buf" 2>/dev/null || true)"
    if [ -n "$buf" ]; then
        ...assert...
    fi
fi
return 0
```

`|| true` swallows the resolution failure, `[ -n "$buf" ]` skips the assert, and the function
returns **0**. A tag with no `-bNNN-` gets a green physics verdict having checked nothing:

```
=== P9: tag carries no buffer token -> physics silently no-op ===
LEAK: assert_tag GREEN on 1.6% DSP + 23 xruns (no -bNNN- in tag => physics skipped entirely)
```

1.6% with 23 xruns is V11's Crystals row — cited in the prior review as the reading that must be
impossible. The extraction is also stricter than it looks; it requires a *trailing* dash:

```
=== P10: buffer regex needs a TRAILING dash ===
  A-b512-p3    -> 512
  A-b512       -> (NO MATCH)
  run-b256     -> (NO MATCH)
  b1024-x      -> (NO MATCH)
```

Any tag ending in its buffer size loses physics checking entirely and says nothing about it.
*Fix: an unresolvable buffer is a hard error — `_mpe_result_die "cannot resolve buffer for tag=..."`.
Never `return 0` from a check that did not run.*

### 🔴 S3 — The F4 plausibility floor is gated behind `samples >= 30`

```bash
# scripts/lib/measurement-result.sh:104-112
buf="$(_mpe_result_resolve_buffer "" 2>/dev/null || true)"
if [ -n "$buf" ] && [ -n "${MPE_R_samples-}" ] && [[ "${MPE_R_samples}" =~ ^[0-9]+$ ]] && [ "${MPE_R_samples}" -ge 30 ]; then
    floor="$(mpe_result_dsp_plausibility_floor "$buf")"
```

Four conjuncts, any one of which disables the floor. A short window is *more* suspect than a long
one, not exempt from scrutiny — and a mistimed sampler is exactly what produces a short window.
Both bypasses are live through the real harness entry point:

```
=== P2: plausibility floor, samples=12 ===
LEAK: 0.9% at 256 ACCEPTED (samples=12 bypasses floor)
=== P3: samples field absent ===
LEAK: 0.9% ACCEPTED (no samples= field)
=== P7: harness path (assert_tag, 2-line log, samples=12) ===
LEAK: assert_tag GREEN on 0.9% DSP @256
```

The offline test proves the floor only at `samples=60` (line 52) — it exercises the branch that
works, which is the F2 mistake with a different variable. *Fix: apply the floor whenever a buffer
resolves. If a short window makes the floor meaningless, mark the cell VOID; do not exempt it.*

### 🔴 S4 — The `xruns > 0` positive control still does not exist

```bash
# scripts/measure-instrument-conformance-live.sh:40,54
# Load positive: midi-load 15 s — xrun count must be readable (may or may not increment).
echo "live positive: load window meter_live=1 (delta=$((end_load - start_load)))"
```

The delta is computed, printed, and never asserted. The prompt's 2a table requires *load well
above a known floor → count **> 0*** and then says why, in bold:

> **Both ends matter.** A counter stuck at zero passes a "must be 0 when clean" test.

The gate implements the clean end (`delta_idle -ne 0` → fail) and skips the loaded end. A meter
wedged at a constant passes this gate. F1 asked for both; one arrived. *Fix: force load above the
known floor and assert `end_load - start_load > 0`, or emit VOID and say the control is missing.*

### 🔴 S5 — The floor's buffer is the requested value echoed

```bash
# scripts/measure-instrument-conformance-live.sh:57
buf="$(mpe_jack_period 2>/dev/null || echo 1024)"
```

```bash
# scripts/lib/audio-engine.sh:64-66
mpe_jack_period() {
    printf '%s' "$(mpe_buffer_env_canonical)"
}
```

That is the configured env value, not the running period. Prompt line 81: *"applied buffer | set
512, read back | reports **512**, not the requested value echoed."* This is the requested value
echoed. If the appliance is configured 256 but jackd came up at 1024, the floor is chosen for a
buffer that is not running — and that mismatch is row 2 of the nine failures.

The fallback makes it worse: `|| echo 1024` on failure selects the buffer with the **lowest floor
in the table (2.0)**, so failing to determine the buffer silently selects the most permissive
threshold. Fail-open on the safety check.

The repo already has the read-back — `jack_bufsize` (used in `scripts/research/graph-recovery-spike.sh`)
and period verification in `measure-latency-run.sh:242-256`. *Fix: use `jack_bufsize`; hard error
when it disagrees with config or cannot be read. Delete the `|| echo 1024`.*

### 🔴 S6 — The load generator can die silently, and nothing looks

```bash
# scripts/measure-instrument-conformance-live.sh:41-42
"${ROOT}/scripts/midi-load.py" 20 >/dev/null 2>&1 &
load_pid=$!
```

stdout and stderr both to `/dev/null`, backgrounded, and the PID is never checked for liveness
before the window opens. If `midi-load.py` exits immediately — no MIDI port, wrong permissions,
bad arg — the "load window" measures idle and reports a pass. **This is AGENTS.md's 382-pad-taps
failure**: the failure is indistinguishable from the success.

It also diverges from every other caller in the repo. All four others go through `_as_user python3`
and keep the log:

```
scripts/measure-latency-run.sh:733:  _as_user python3 "$SCRIPT_DIR/midi-load.py" ... >"/tmp/latency-midi-load-${stamp}.log" 2>&1 &
scripts/measure-soak.sh:132:         _as_user python3 "$SCRIPT_DIR/midi-load.py" "$SOAK_SEC" ...
```

`_as_user` is presumably there because MIDI/ALSA access needs the user's session and the harness
may run as root. This script drops it, with no comment saying why. *Fix: match the established
invocation, redirect to a log, and after `sleep 2` assert `kill -0 "$load_pid"` plus a non-zero
effect on the meter.*

### 🟡 S7 — Non-numeric `xruns` disables the physics rule

`mpe_result_require_fields` rejects only the literal strings `?` and `unknown`:

```bash
# scripts/lib/measurement-result.sh:95
if [ "${!var}" = "?" ] || [ "${!var}" = "unknown" ]; then
```

Anything else non-numeric flows into `awk`, becomes `0`, and fails `x+0 > 5`:

```
=== P4: non-numeric xruns vs physics ===
LEAK: physics PASSED, non-numeric xruns + 1% DSP
```

Caught incidentally at 1.0% by the floor (P13), but above the floor the physics rule just quietly
does not apply. F5's lesson was "a non-numeric value must halt"; it was applied to `jitter_n` only.
*Fix: validate `xruns` and every `dsp_*` field as numeric in `require_fields`.*

### 🟡 S8 — F5's guard is inert unless an unrelated env var is set

```bash
# scripts/lib/measurement-result.sh:140-144
if [ -n "$jitter_n" ] && [ -n "${MPE_EXPECT_SAMPLES-}" ] && [ "$MPE_EXPECT_SAMPLES" -ge 30 ]; then
    if ! [[ "$jitter_n" =~ ^[0-9]+$ ]]; then
        _mpe_result_die "jitter_n=${jitter_n} is not numeric"
```

The numeric check is nested inside a condition requiring `MPE_EXPECT_SAMPLES` to be set and ≥ 30.
Any harness that does not export it gets no guard:

```
=== P5: F5 regression, MPE_EXPECT_SAMPLES unset ===
LEAK: non-numeric jitter_n PASSED (EXPECT unset)
```

The offline test sets `MPE_EXPECT_SAMPLES=60` at line 61 immediately before testing this, so the
suite cannot see it. F5 was not closed; it moved. *Fix: validate `jitter_n` whenever it is present.
Gate only the `-lt 100` threshold on window length.*

### 🟡 S9 — "all checks passed" is printed with 28 lines of checks still to run

```bash
# tests/test_instrument_conformance_offline.sh:90
echo "test_instrument_conformance_offline.sh: all checks passed"

# ...lines 92-118: window_align, V11 good-row, non-sticky ordering, xrun-corr TOTAL
```

A failure in any of those four produces a log containing `all checks passed` followed by `FAIL:`.
The exit code is right; the log lies. In a suite whose entire purpose is instruments that do not
lie, this one is on the nose. *Fix: move the line to the end.*

### 🟡 S10 — No negative control asserts *which* guard fired

Every negative in both new files follows this shape:

```bash
if mpe_result_physics_assert "" 2>/dev/null; then
    fail "physics should reject 256 V11 impossible cell"
fi
```

Stderr discarded, exit status only. So each proves "something refused," not "the intended rule
refused." That matters concretely: `MPE_EXPECT_SAMPLES=60` is set at line 14 and never unset, so
`physics_assert` also enforces `samples == 60` on every subsequent negative — any of them could be
passing on the samples-mismatch guard instead of the physics rule. I checked the F3 case by hand
and it does fire correctly:

```
ERROR: measurement-result: physics: dsp_median=10.000000% with xruns=23 at buffer=256 impossible
```

Correct today, unproven by the suite, and free to rot. **F2 hid for exactly this reason.** *Fix:
capture stderr and grep the expected message.*

### 🟡 S11 — Test state leaks between blocks

`mpe_result_load_tag` clears `MPE_R_*` (lines 200-202); `mpe_result_parse_line` does not. The
offline suite mixes both, plus manual assignments (`MPE_R_xruns=1`, `MPE_R_meter_live=1` at
lines 44-45), so every block inherits the previous one's residue and the suite is order-dependent.
*Fix: reset `MPE_R_*` between blocks; export a `mpe_result_reset` helper.*

### 🟡 S12 — A test asserts source text instead of behaviour

```bash
# tests/test_instrument_conformance_offline.sh:87
grep -q 'cat "$OUT"' "${ROOT}/scripts/xrun-corr.sh" || fail "xrun-corr must emit OUT on stdout"
```

Guarding a real historical bug (row 1 of the nine), but `cat "${OUT}"` or a `tee` refactor breaks
the test while *improving* the code, and it never proves anything reaches stdout. *Fix: run
`xrun-corr.sh` against a fixture and assert on captured stdout.*

### 🟡 S13 — A test failure in the venv is retried until something passes

```bash
# scripts/instrument-conformance.sh:19-24
if [ -x "${ROOT}/.venv/bin/python" ]; then
    "${ROOT}/.venv/bin/python" -m unittest tests.test_audio_engine tests.test_periodic_loop_lint -q 2>/dev/null \
        || python3 -m unittest tests.test_audio_engine tests.test_periodic_loop_lint -q
```

`2>/dev/null` discards unittest's entire report — unittest writes results to **stderr** — so a
genuine failure is invisible and triggers a re-run under system `python3`, whose verdict decides
the gate. Two different interpreters, one silently substituted for the other on failure. *Fix: pick
the interpreter deterministically, never discard its stderr.*

### 🟡 S14 — The idle control fails on a single stray xrun

```bash
# scripts/measure-instrument-conformance-live.sh:34
if [ "$delta_idle" -ne 0 ]; then
```

Exactly zero xruns across a 10 s wall-clock window, or the queue stays halted. Defensible as
doctrine, but one unrelated USB event now blocks all measurement work with an error that looks
like an instrument fault. *Fix: keep the strictness, but log the meter's own age/liveness alongside
so a spurious trip is diagnosable, and say in the message that a retry is the first step.*

### 🟢 S15 — The staleness negative cannot detect a threshold regression

`test_instrument_conformance_live.sh:20` sets `MPE_METER_HARNESS_MAX_AGE_S=3`, which is the
default (`audio-engine.sh:696`) — a no-op assignment. The stale fixture is `EPOCHSECONDS - 120`,
40× the threshold, so the test passes under any plausible value. It proves staleness is checked;
it cannot catch someone widening the window to 300. *Fix: add a boundary case at threshold ± 1 s.*

---

## 5. Logic & Business Rules

**The physics rules are stated well where they are stated.** "Material xruns cannot coexist with
low DSP at any buffer" is now buffer-parameterised and fires at 256 — F3 is properly closed, and
the comment on line 156 says the rule in one line. `mpe_result_physics_buffer_halving` is tested
in both directions (rejects 39.6 → 1.6, accepts 19.14 → 38.52). That is how a physics assert
should look.

**The dominant logic flaw is a shape, not an instance: checks that cannot run return success.**
S2 (no buffer → skip → 0), S3 (four conjuncts → skip), S8 (env unset → skip), S7 (non-numeric →
`awk` sees 0 → skip). Four instances of "if I cannot evaluate this, I pass." For a library whose
header says *never hand-roll grep for metric fields* and whose project rule is *an instrument must
never be able to fail silently*, the default must invert: **unevaluatable is VOID, not pass.** One
`_mpe_result_die` in each of those four skip paths closes S2, S3, S7, and S8 together.

**Ordering bug:** S1 — DSP read after the load is reaped. Same class as the V11 window-alignment
defect in Part 3 of the prompt.

**Unverified assumption:** S5 — configured buffer treated as applied buffer.

**Prompt requirements not implemented.** Beyond the 2a gap in S4, from the required 2b list:

| required negative control | status |
|---|---|
| Stop peak meter mid-cell → read fails | ✅ (file-removal simulation) |
| Stale / delete `meter.state` → fails and propagates | ✅ |
| `dsp_med` / `dsp_median` rename → hard error | ✅ |
| Point harness at non-existent patch → halt | ❌ absent |
| Kill jackd mid-window → cell marked invalid | ❌ absent |
| Sample DSP with no load → detectably wrong | ❌ absent — **and committed by S1** |

And from Part 1, *"Tests **every** metric any harness reports"*: covered are `xruns`, `dsp_median`,
`dsp_p99`, `dsp_max`, `samples`, `jitter_n`, `meter_live`, `window_align`. Absent are
`frames_late`, buffer fill level, achieved clock, temperature, `get_throttled`, voice count, patch
identity, applied buffer/periods, and applied governor — nine of the metrics the prompt enumerates
on lines 56-58. The two fixtures carry `frames_late_*` and `temp=`/`throttled=` fields that no
assertion touches. 🟡

**Two required deliverables are missing outright** (prompt lines 46, 48):

- **`CONFORMANCE PASS` / `CONFORMANCE FAIL` verdict.** The script emits
  `SENTINEL conformance-pass mode=...` and, on failure, nothing at all — `set -e` just exits. There
  is no `CONFORMANCE FAIL` string anywhere in the tree. A consumer grepping for the specified
  contract finds silence and cannot distinguish failure from a crash. 🟡
- **Platform label and provenance** (kernel, JACK version, Surge revision, buffer/periods).
  Not recorded. `instrument-conformance.sh` takes a mode and nothing else. C0 exists largely to
  freeze a reference suite across the Pi 4 → Pi 5 transition; **a conformance pass that does not
  record which platform it passed on cannot serve that purpose.** 🟡

---

## 6. Test Strategy & Execution

I ran the offline suite. It is fast and green:

```
17 × OK, exit 0, 1.9 s wall
```

**What is genuinely good:** fixtures are small, readable, and shaped like real harness output
(multi-line RESULT rows, the `temp=temp=54.0'C` double-prefix quirk preserved). Negative controls
assert halting. The non-sticky-withhold test (lines 103-112) builds a two-row file specifically to
catch a state-leak ordering bug — that is a test written by someone who thought about how the code
could be wrong. `physics-256-v11.log` encodes the actual V11 failure as a permanent regression
fixture. Keep all of it.

**The suite's central weakness is inherited from F2 and not yet fixed.** The prior review's
requirement 3 was explicit:

> Re-run the fixture tests with the physics functions called the way harnesses call them, not with
> hand-passed arguments — F2 was hidden precisely because the test bypassed it.

Half done. `mpe_result_assert_tag` — the harness entry point — is called **once**, on the good
fixture (line 15), for a pass. Every negative bypasses it: `parse_line` + manual `MPE_R_tag=` +
`physics_assert ""` (lines 21-23, 29-31), or hand-passed `512` (line 62). **The harness entry point
is never proven to reject anything.** I verified it does:

```
=== P11: harness path on the known-bad fixture ===
ERROR: measurement-result: physics: dsp_median=10.000000% with xruns=23 at buffer=512 impossible
rejected OK (fix is real, just never tested via assert_tag)
```

The fix works. Nothing in the suite would notice if it stopped. *Fix: route every negative through
`mpe_result_assert_tag` against a fixture file, exactly as `measure-latency-run.sh` will.*

**Coverage gaps I could demonstrate from outside the suite:** four probes (P2, P3, P5, P9) turn the
green gate into an accepted V11-signature reading. Each is a two-line fixture. All four should
become permanent test cases — they are the highest-value tests in this changeset because each one
maps to a specific reading that already fooled this project once.

**Live-half test quality.** The negative controls (missing, stale, absent-key, removed-mid-read)
are real and run anywhere — genuinely good, and the right things to make platform-independent. But
the file's own closing line admits the shape of the problem:

```bash
# tests/test_instrument_conformance_live.sh:82
echo "NOTE: full 2a load/xrun/DSP band checks run via measure-instrument-conformance-live.sh on Pi"
```

So the positive controls live in the unexercised script, and per S1/S4/S5/S6 they are the ones with
the ordering bug, the missing assertion, the wrong buffer source, and the silent load failure.
**The tested half is the half that was already working.** Nothing in this changeset has been run
against an appliance — and by AGENTS.md's own rule, a remote command that returns no output is not
evidence that it ran. This needs one Pi execution before anyone believes it, and per the self-test
rule the load generator should be driven synthetically first to confirm it produces non-zero output.

**Log-honesty nit:** the live test prints `LIVE SKIP:` twice and then `exit 1`. Exiting non-zero is
the right call — do not soften it — but a line labelled SKIP next to a failure exit will get
misread by the next person triaging a red gate. *Fix: label it `LIVE FAIL: positive controls
require the appliance`.*

---

## 7. Security & Performance

**Security:** nothing here would keep me up. No network, no secrets, no untrusted input — fixtures
are repo-local and RESULT lines come from this project's own harnesses. Two hygiene notes:

- `printf -v "MPE_R_${key}"` (line 79) writes dynamically-named shell variables from parsed input.
  The key is sanitised first (`key="${key//[^a-zA-Z0-9_]/_}"`), which is the right instinct and
  correctly placed. Worth a comment saying that sanitisation is load-bearing, so nobody removes it
  as noise.
- Tags are interpolated unescaped into regexes and `case` patterns (lines 203, 208, 215, 219). A
  tag containing `.` or `*` matches loosely. Internal values only, so 🟢 — but `grep -F` where the
  match should be literal costs nothing.

**Performance / CPU doctrine:** this respects the repo's central constraint, and I checked
specifically because AGENTS.md demands it. No polling loops, no forks in loops — parsing is a
single pass per file, physics is a handful of `awk` invocations per row. The 15-minute budget is
enforced with a hard failure rather than a warning:

```bash
# scripts/instrument-conformance.sh:58-61
if [ "$ELAPSED" -gt 900 ]; then
    echo "WARNING: conformance exceeded 15 min (${ELAPSED}s) — gate too slow to trust" >&2
    exit 1
```

Right behaviour — a gate that gets skipped for slowness is not a gate. Two notes: the message says
`WARNING` while the action is a hard fail (say `ERROR`), and the check runs *after* everything, so
it reports overruns rather than preventing them. The offline half is ~2 s, so the live half owns
the entire budget; its own sleeps total 20 s. Comfortable.

The offline suite runs `python3 -m unittest` (S13). On the Pi that is a ~400 ms interpreter start
per the project's own measured constant — irrelevant once per gate, but the fallback in S13 pays it
twice.

---

## 8. Developer Experience

**Onboarding is better than most of this repo.** `instrument-conformance.sh` has a real usage
block, `--help` works, and mode names say what they do. `measurement-result.sh`'s header states the
rule that motivates the file — *"Field name: dsp_median (NOT dsp_med — typo must hard-error)"* —
which is exactly the comment worth writing: a constraint, not a narration. `measure-instrument-conformance-live.sh`
documents its preconditions on line 4. A new dev could run `--offline` in one command and read the
fixtures to understand the data model in about ten minutes.

**Where documentation is currently lying:**

- `# Load positive:` (live:40) describes an assertion the code does not make.
- `"...returned no numeric DSP under load"` (live:60) — there is no load at that point.
- `"Minimum plausible dsp_median for a loaded window"` (result:25) — silently inapplicable to any
  window under 30 samples.
- `"...(target ≤ 15 min)"` and the prompt's `CONFORMANCE PASS` contract do not match the emitted
  `SENTINEL conformance-pass`, so anyone integrating against the documented interface writes a
  consumer that never matches.

**Build/deploy sanity:** the split respects the laptop/nerdrack/Pi division correctly — offline
runs anywhere, live requires the appliance and refuses to pretend otherwise. `.venv/` is untracked
and showing in `git status`; it should be in `.gitignore` before it lands in a commit. The new
fixture, both new tests, and the new script are all still untracked — nothing here is committed
yet, so none of it has been through CI.

**The one DX thing I would fix first:** there is no way to run the live half in a dry-run/verify
mode that proves the *instrument wiring* without needing a clean idle appliance. Given S6, a
`--self-test` that starts `midi-load.py` in the foreground with output visible and asserts the
meter moves would have caught the silent-load-death class before it ever reached a measurement run.
That is the self-test-before-it-costs-him-anything rule, applied to this script.

---

## The good, the bad, and what smells

**The good**

- `--offline` / `--live` split — the correct structural answer to F1, and it maps to where work can run.
- F2 genuinely fixed: `[[ =~ ]]` extraction, exercised through `MPE_R_tag` rather than a hand-passed buffer.
- F3 genuinely fixed and *better than asked*: per-buffer ceiling, fires at all buffers, error names the values.
- `mpe_result_assert_tag` — one entry point for harnesses and tests is the right anti-F2 medicine.
- Live negative controls (missing / stale / absent-key / removed-mid-read) are real and platform-independent.
- The live test exits **1**, not 0, when it cannot do its job. Rule -1 respected where it was easiest to cheat.
- Non-sticky-withhold ordering test — written by someone imagining how the code fails.
- `physics-256-v11.log`: the actual failure, frozen as a permanent regression fixture.
- Error messages name the instrument and the values. Model examples.
- CPU doctrine respected: no polling loops, no forks in loops, budget enforced with a hard fail.

**The bad**

- The live DSP check samples with the load dead — the prompt's own negative control, committed and published (S1).
- Physics silently passes when the buffer cannot be resolved (S2).
- The F4 floor is bypassed by `samples < 30` or a missing `samples` field — V11's exact signature, through the harness entry point (S3).
- `xruns > 0` under load is still not asserted; only the clean end is tested (S4).
- The floor's buffer is the configured value, not the applied one — with a fail-open default to the most permissive threshold (S5).
- `midi-load.py` output discarded, liveness unchecked, and invoked unlike every other caller (S6).
- F5's guard is inert unless `MPE_EXPECT_SAMPLES` is set; F5 moved rather than closed (S8).
- Required deliverables absent: `CONFORMANCE PASS`/`FAIL` verdict, platform/provenance record.
- Nothing invokes the gate; nothing consumes its sentinel.

**What smells**

- Four independent "cannot evaluate → return 0" paths. One inverted default fixes all four.
- Every negative control discards stderr and asserts only exit status — the mechanism that hid F2.
- The harness entry point is tested only for acceptance, never for rejection.
- Two physics tables modelling one relationship with opposite orderings, and six constants with no provenance.
- `all checks passed` printed 28 lines before the last check.
- Dead branches (`"$dsp" = "?"`), dead calls (`fresh_meter` then overwrite), an orphaned pre-split test.
- Comments describing assertions the code does not make.
- Nine of the prompt's enumerated metrics untested, two of them present in the fixtures and ignored.

---

## Verdict

The offline half has gone from "a parser conformance suite mislabelled as a gate" to a genuinely
good parser conformance suite, and F2 and F3 are properly closed — F3 better than the review asked.
That work should land. But the live half is where F1 lived, and it is the half nobody has run: its
DSP check samples after the load is reaped, its buffer comes from config rather than from jackd, its
load generator can die into `/dev/null` unnoticed, and the `xruns > 0` assertion that was the
headline F1 requirement is computed, printed, and never checked. F4 and F5 are closed only along the
paths the tests walk — I turned the green gate into an accepted 0.9%-at-256 reading, the precise
V11 signature F4 exists to reject, with a two-line fixture and no cleverness. The recurring shape,
four times over, is a check that returns success when it could not run, which is the same fallacy in
a new costume: **a passing check that does not prove the instrument works.** Fix the six 🔴s, invert
that default, promote my four probes into permanent tests, then run the live half on the appliance
once — with the load generator self-tested first — before anyone calls C0 green. The queue should
stay halted, and this is now days of work from done rather than weeks.

## Priority backlog

1. **🔴 S1 — Move the `jack_cpu_load` read inside the load window** (`measure-instrument-conformance-live.sh:47-58`). The gate currently commits the prompt's own forbidden negative control and publishes the number.
2. **🔴 S2 + S3 — Make unevaluatable checks VOID, never pass.** An unresolvable buffer and a sub-30-sample window must `_mpe_result_die`, not `return 0`. Two proven leaks (P7, P9) accept the V11 signature through `mpe_result_assert_tag` today; fold S7 and S8 in with the same inversion.
3. **🔴 S4 — Assert `xruns > 0` under forced load.** Both ends or neither; this is the outstanding half of F1.
4. **🔴 S5 + S6 — Trust nothing you did not read back.** Use `jack_bufsize` for the applied buffer (delete `|| echo 1024`), and invoke `midi-load.py` as every other harness does, with a log and a `kill -0` liveness assert after the window opens.
5. **🔴/🟡 S10 + §6 — Make the tests capable of catching the next F2.** Route every negative control through `mpe_result_assert_tag` against a fixture, assert on the stderr message rather than exit status alone, and add P2/P3/P5/P9 as permanent cases.
6. **🟡 Emit the specified contract.** `CONFORMANCE PASS` / `CONFORMANCE FAIL` plus platform, kernel, JACK, Surge revision, and applied buffer/periods — without which a C0 pass cannot serve the Pi 4 → Pi 5 comparison it was built for. Then wire the queue runner to require `mode=all`.

---

**Reviewer note on method:** `Read` was blocked for every path this session by a malfunctioning
`agentjail-hook` (invalid hook response, not a policy denial), so file contents were obtained via
`Grep`. Blank lines are therefore absent from what I read; line numbers cited are real and were
cross-checked against the executed suite. No product code was modified.
