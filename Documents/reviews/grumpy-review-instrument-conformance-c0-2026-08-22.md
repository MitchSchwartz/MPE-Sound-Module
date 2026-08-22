# Grumpy dev review — C0 instrument conformance

*Scope: `instrument-conformance-c0` · Branch: `yolo/instrument-conformance-c0` · 2026-08-22 (America/Toronto)*

**Reviewer stance:** grumpy but fair. Every claim below was executed, not inferred. Probe
commands and their output are quoted inline so you can re-run them.

---

## What I read, and what I didn't

**Read in full:** `scripts/lib/measurement-result.sh`, `tests/test_instrument_conformance.sh`,
`scripts/instrument-conformance.sh`, all four fixtures under
`tests/fixtures/instrument-conformance/`, `docs/measurements/MEASUREMENT-DISCIPLINE.md`,
`docs/measurements/PROMPT-C0-instrument-conformance.md`,
`docs/measurements/instrument-conformance-c0-2026-08-22.md`, `Documents/PROGRESS.md`,
`.claude/skills/mpe-measurement/SKILL.md`, the diffs to `AGENTS.md`, both spec
pre-registration blocks, `docs/measurements/README.md`, and the
`mpe_meter_assert_live` / `mpe_meter_xruns_read` block in `scripts/lib/audio-engine.sh`.

**Read partially:** `scripts/measure-latency-run.sh` — the diff plus lines 245–512 and the
shell-option/call-site lines. `native/mpe-xrun-probe/mpe-xrun-probe.c` — the startup and
shutdown paths around `PROBE_START`. `scripts/measure-soak.sh`, `scripts/xrun-corr.sh`,
`scripts/bench-xruns.sh` — grepped for RESULT grammar and in-band failures only.

**Did not read:** `tests/test_audio_engine.py`, `tests/test_periodic_loop_lint.py`,
`Documents/DECISIONS.md`, `Documents/DIRECTION.md`, the rest of the harness fleet.

**Ran:** `./scripts/instrument-conformance.sh` (exit 0, 6.03 s wall), both bash test files,
and fifteen targeted probes against the library. `shellcheck` is not installed on this
machine, so the new shell went unlinted here.

---

## 1. First Impressions (The Gut Check)

This does not look like a hackathon. It looks like a project that has been burned badly
enough to start writing down *why* it got burned, and the writing-down is genuinely good.
Rule −1's framing — **"every instrument returns its value and its failure through the same
channel, so a broken one is indistinguishable from a working one at the reading site"** — is
a real root-cause statement. It collapses nine separate incidents into one missing
convention. That is the single most valuable artifact in this changeset and it is worth more
than the code.

Then I ran the code, and my mood changed.

The doctrine is a nine-item hall of shame with a mechanical gate bolted to the front of it.
I checked whether the gate would catch the nine items. **Occurrence #1 — `xrun-corr.sh`
writes to `~/xrun-corr.out` instead of stdout — is still unfixed and has zero conformance
coverage.** The founding instance of the entire doctrine, documented in three separate files
in this very changeset, is untouched:

```bash
$ rg -n 'xrun-corr.out' scripts/xrun-corr.sh
25:OUT=~/xrun-corr.out

$ rg -l xrun-corr tests/
  NO test references xrun-corr
```

And the C0 library itself — the thing built to eliminate silent-success instruments —
returns exit 0 when handed a file that does not exist:

```
--- TEST 8: v11 on a NONEXISTENT file ---
scripts/lib/measurement-result.sh: line 140: /tmp/c0probe/nope.log: No such file or directory
rc=0
```

That is the anti-pattern, in the tool named after the anti-pattern. There is a specific kind
of frustration in reading a beautifully argued document about instruments that lie, and then
watching its reference implementation lie to me on the fourth probe.

So: **excellent diagnosis, well-organized doctrine, honest halt semantics in several places
— and a gate that is thinner than the document claims it is.** The gap between what the
deliverable doc asserts and what the code enforces is the review.

---

## 2. Architecture & Structure

**The layering is right.** A sourced bash library (`scripts/lib/measurement-result.sh`) for
parse/assert primitives, a test file that exercises it against fixtures, a gate script that
runs the tests, and doctrine in prose. That is the correct shape, and it matches the existing
`scripts/lib/` convention rather than inventing a parallel one. Credit.

**Separation of concerns is good in one direction and broken in the other.** The library
knows nothing about the harness — good. But the harness now sources the library *from inside
the per-window function*:

```498:509:scripts/measure-latency-run.sh
    # shellcheck source=lib/measurement-result.sh
    source "$SCRIPT_DIR/lib/measurement-result.sh"
    MPE_R_xruns=$total_xr
    MPE_R_dsp_median=$dsp_median
    MPE_R_samples=$samples
    MPE_R_jitter_n=$jitter_n
    MPE_R_tag=$tag
    MPE_EXPECT_SAMPLES=$SECONDS_PER_RUN
    if ! mpe_result_physics_assert "$BUFFER"; then
```

Re-sourcing a library once per 60-second window is harmless in cost but wrong in structure —
it belongs beside the other `source` calls at the top. More importantly, sourcing it *here*
means the physics assertion is unreachable from any earlier failure path, and the coupling
is invisible to a reader of the top of the file. I checked the shell options: both files use
`set -uo pipefail`, so there is no `set -e`/`set -u` surprise from the late source. That's
luck rather than design, but it holds.

**No emitter↔parser contract.** This is the structural hole. The fixtures are hand-typed
transcriptions of what someone believed the harness emits. There is nothing tying them to
the emitter, so the two can drift silently — which is precisely the `dsp_med` bug class the
whole exercise exists to kill. They have *already* drifted, at birth. See §4 🔴-5.

**Two incompatible RESULT grammars.** SKILL.md Step 5 says "never hand-roll `grep` for
RESULT fields. Use `scripts/lib/measurement-result.sh`." But `mpe_result_load_tag` requires
`^RESULT tag=`, and `measure-soak.sh` emits:

```186:186:scripts/measure-soak.sh
    echo "RESULT soak_hours=${HOURS} buffer=${BUFFER} loops=${LOOPS} xruns_total=${FINAL} invalid_windows=${invalid_windows}"
```

No `tag=`. The library physically cannot parse soak output, so the instruction is
unfollowable for one of the four sources PROMPT-C0 Task 1 named. Either the grammar gets a
`tag=` or the library gets a second entry point; right now the rule just quietly doesn't
apply.

**Dependency hygiene: one real hazard.** A 64 MB / 2748-file virtualenv is sitting untracked
and *un-ignored* in the working tree:

```bash
$ du -sh .venv          → 64M   (2748 files)
$ git check-ignore -v .venv/  → rc=1   (NOT ignored)
$ rg -n 'venv' .gitignore     → (no match)
```

`AGENTS.md` has an explicit rule about exactly this — *"an untracked build tree inside a git
checkout is the sweep hazard that cost us files on 2026-08-14."* One `git add -A` commits a
virtualenv into the appliance repo. **Fix:** add `.venv/` to `.gitignore` in this changeset.

---

## 3. Code Quality

**Naming is good.** `mpe_result_parse_line`, `mpe_result_require_fields`,
`mpe_result_physics_assert`, `mpe_result_physics_buffer_halving`, `mpe_result_load_tag`,
`mpe_result_v11_recover` — I could predict every signature from the name. The `MPE_R_*`
prefix for parsed fields is a sane convention for bash's flat namespace. The header comment
in `scripts/lib/measurement-result.sh` naming the exact trap (`dsp_median` NOT `dsp_med`) is
the right kind of comment: a constraint the code can't show.

**Error handling is loud in the harness and quiet in the library.** The harness gets this
right in several places — `scripts/measure-latency-run.sh:415-419` voids the window when
`PROBE_START` never appears, and `:436-441` voids it when the meter counter goes backwards
mid-run. Both print `ERROR:` and `return 1`. That's the doctrine actually implemented.

The library is inconsistent. `_mpe_result_die` writes to stderr and returns 1, and callers
propagate — fine. But `mpe_result_v11_recover` never fails at all (§4 🔴-2), and
`mpe_result_physics_assert` treats *absent* as *fine*:

```bash
--- TEST 3: physics_assert with EMPTY metrics ---
RESULT: empty metrics PASS physics
```

Every check in that function is guarded by `[ -n "$x" ]`. A window that produced nothing
passes the physics gate. For a function whose whole job is rejecting impossible readings,
"no reading" should be the most impossible one.

**Dead code — a guard that cannot fire.** This line reads like a tag-based fallback for
detecting 512-frame runs:

```93:98:scripts/lib/measurement-result.sh
        if [ "$buf" = "512" ] || [ "${MPE_R_tag-}" = *"-b512-"* ]; then
            if awk -v d="$dsp" -v x="$xr" 'BEGIN { exit !(d+0 < 15 && x+0 > 5) }'; then
                _mpe_result_die "physics: dsp_median=${dsp}% with xruns=${xr} at 512 impossible"
```

`[` does not glob. Inside `test`, `*"-b512-"*` is a literal string, not a pattern. Verified
both ways:

```
--- TEST 1: tag-based 512 detection (buf arg NOT 512) ---
RESULT: PASSED (physics did NOT fire) -> tag fallback is DEAD

--- TEST 2: what does [ tag = *-b512-* ] actually evaluate? ---
literal compare -> NO match (bug confirmed)
```

The tests never catch it because they always pass `512` as `$1`, satisfying the first clause.
**Fix:** `[[ "${MPE_R_tag-}" == *-b512-* ]]`, and add a test that passes an empty `$1` with a
512 tag.

**Documented behaviour that was never implemented.** `MEASUREMENT-DISCIPLINE.md:47` lists
this physics assertion: *"`meter_live=1` but `meter_max_age_s` > 3 | Freshness positive
control failed."* It does not exist:

```bash
$ rg -n 'meter_max_age_s' scripts/lib/measurement-result.sh tests/test_instrument_conformance.sh
scripts/lib/measurement-result.sh:126:    unset MPE_R_meter_live MPE_R_meter_max_age_s ...
```

The only appearance is in an `unset` list. The field is emitted, required by nothing, and
asserted nowhere. PROMPT-C0 Task 1 names `meter_max_age_s` as an in-scope metric needing
three tests; it has none.

**TODO graveyards:** none. Genuinely clean on that axis.

---

## 4. Code Smells (The Hall of Shame)

### 🔴 1 — `window_align=1` certifies an alignment the code never establishes

This is the worst defect in the changeset, because it manufactures false confidence in
exactly the dimension C0 was commissioned to secure.

The harness waits for `PROBE_START` before opening the sample window, then stamps the RESULT
row with `window_align=1`:

```409:419:scripts/measure-latency-run.sh
    local w=0
    while [ "$w" -lt 50 ]; do
        grep -q '^PROBE_START' "$xrun_events" 2>/dev/null && break
        sleep 0.1
        w=$((w + 1))
    done
    if ! grep -q '^PROBE_START' "$xrun_events" 2>/dev/null; then
        echo "ERROR: probe never signalled PROBE_START — window VOID" >&2
```

Now look at when the probe emits that sentinel:

```184:192:native/mpe-xrun-probe/mpe-xrun-probe.c
    fprintf(g_log, "PROBE_START client=%s buffer_frames=%u sample_rate=%u expected_period_us=%.0f\n",
            CLIENT_NAME, (unsigned)g_buffer_frames, (unsigned)g_sample_rate, g_expected_period_us);
    fflush(g_log);

    jack_set_process_callback(g_client, on_process, g_client);
    jack_set_xrun_callback(g_client, on_xrun, NULL);

    if (jack_activate(g_client) != 0) {
```

`PROBE_START` is flushed **three statements before `jack_set_xrun_callback` and four before
`jack_activate`.** The sentinel means "the probe opened its log and read the buffer size." It
does not mean the probe is counting anything. The window opens on an event that precedes the
instrument becoming live.

It gets worse when you add the other half of the change. The meter baseline is now captured
*before* the probe is even spawned:

```399:407:scripts/measure-latency-run.sh
    # Sampler window: meter baseline before probe attach (V10-b misalignment fix).
    if ! start_xr="$(_meter_xruns)"; then
        return 1
    fi
    prev_xr="$start_xr"

    if ! _start_xrun_probe "$xrun_events"; then
```

So the actual timeline of a "window" is: meter baseline → probe spawn → `PROBE_START`
printed → callbacks registered → `jack_activate` → DSP sampling starts. `total_xr` is a
meter delta measured from the *first* of those points; `dsp_median` is sampled from the
*last*, up to 5 s later (50 × 0.1 s of polling, plus JACK client activation). The commit
message calls this the "V10-b misalignment fix." It trades a misalignment in one direction
for a misalignment in the other, and the deliverable doc reports it as resolved:

> `measure-latency-run.sh`: meter baseline captured **before** probe attach; sample loop
> starts only after `PROBE_START` in probe log. RESULT carries `window_align=1`.

`window_align=1` is a string literal on line 512. It is not computed, and no assertion
anywhere compares the two window boundaries. That is SKILL.md anti-pattern #5 — *"positive
control checks presence only ... Assert value correctness"* — labelled "instant P0" by this
same changeset.

**Fix:** emit a second sentinel from the probe *after* `jack_activate` returns 0 (e.g.
`PROBE_ACTIVE`) and gate the sample loop on that. Take the meter baseline immediately after
it, not before the spawn. Then compute `window_align` from the measured gap between the meter
baseline timestamp and the first DSP sample — emit the gap in milliseconds and halt above a
threshold, instead of stamping a constant.

### 🔴 2 — The V11 recovery tool cannot fail, and its output is order-dependent

`mpe_result_v11_recover` produced the recovery verdict table in
`instrument-conformance-c0-2026-08-22.md` and the "DSP withheld" row in `PROGRESS.md`. It has
three defects, all verified.

**(a) `withhold` is never reset per row.** It is initialised once in the `local` declaration
at line 143 and latches on the first bad row, contaminating every row after it:

```bash
--- TEST 6: sticky withhold via real file ---
tag=bad-b512-p3-l0-run1  xruns=23 dsp_median=WITHHELD    dsp_withheld=1
tag=good-b512-p3-l0-run1 xruns=2  dsp_median=38.520000   dsp_withheld=1   ← valid DSP, marked withheld

--- good row first (same two rows, order swapped) ---
tag=good-b512-p3-l0-run1 xruns=2  dsp_median=38.520000   dsp_withheld=0
tag=bad-b512-p3-l0-run1  xruns=23 dsp_median=WITHHELD    dsp_withheld=1
```

Same input rows, different verdicts depending on line order. A tool whose entire purpose is
deciding which historical numbers survive is order-dependent. The existing tests miss it
because both fixtures contain only one primary row each.

**(b) Missing input file → exit 0, no output.** (TEST 8, quoted in §1.) The redirection fails,
the `while` loop never runs, and `return 0` executes anyway.

**(c) Empty input → exit 0, zero bytes.**

```
--- TEST 7: v11 on a log with NO RESULT rows at all ---
rc=0 bytes=0
```

This is SKILL.md anti-pattern #4 verbatim — *"Instrument writes to a file; harness reads
stdout → Single channel; assert non-empty output"* — in the C0 library, on the tool that
generated the deliverable's conclusions.

There is a fourth, smaller sharp edge: `${out:-/dev/stdout}` is not portable across
invocation contexts. Under one of my probe shells the redirect failed outright with
`/dev/stdout: No such device or address` while still returning 0.

**Fix:** move `withhold=0` inside the `RESULT` case arm; validate the input file with
`[ -r "$file" ] || _mpe_result_die`; count emitted rows and `_mpe_result_die` on zero; drop
the `/dev/stdout` default in favour of writing to stdout unconditionally when `$2` is empty.

### 🔴 3 — The residual in-band failures are in the harness C0 was chartered to fix

`.claude/skills/mpe-measurement/SKILL.md:100-102` declares these "instant P0":

| # | Pattern | Fix |
|---|---|---|
| 1 | `\|\| echo 0` on a measurement read | Halt |
| 3 | `unknown` / `?` in a RESULT field treated as data | Halt or omit field with ERROR |

`scripts/measure-latency-run.sh`, same changeset:

```bash
245:    if [ "$(mpe_read_appliance_env_var MPE_PEAK_METER 2>/dev/null || echo 0)" != "1" ]; then
430:        dsp="$(tail -1 "$dsp_raw" 2>/dev/null | grep -oP '[0-9]+\.[0-9]+' | head -1 || echo '?')"
445:        printf '  %4d %8s %8s %7d%s\n' "$i" "${dsp:-?}" "$cur_xr" "$delta" >>"$run_file"
468:                if (n==0) { print "0 0 0"; exit }
490:    temp="$(vcgencmd measure_temp 2>/dev/null || echo 'temp=unknown')"
491:    throttle="$(vcgencmd get_throttled 2>/dev/null || echo 'throttled=unknown')"
```

Line 468 is the dangerous one. If `jack_cpu_load` produces nothing for the whole window —
process died, `ststdbuf` wrong, JACK not up — every per-second row is `?`, awk's `n==0` branch
fires, and the harness emits `dsp_median=0.000000` as a measurement. Both C0 gates accept it:

```
--- TEST 14: dsp_median=0 (the awk n==0 sentinel) with 0 xruns ---
PASSED -> dsp_median=0.000000 accepted as a real reading

--- TEST 15: require_fields on dsp_median=0 ---
PASSED -> 0 accepted by require_fields
```

`mpe_result_require_fields` rejects `?` and `unknown` (lines 54-57) but not `0`. And the 512
physics rule only fires when `xruns > 5`, so a clean window with a totally dead DSP sampler
reports 0.0% DSP and sails through. This is the void-run class, still live, after the gate.

**Fix:** replace `print "0 0 0"` with an awk `exit 1` and halt the window; add `0` /
`0.000000` for `dsp_*` to the invalid-value set in `mpe_result_require_fields`; add a fixture
whose per-second rows are all `?` and assert the gate halts.

### 🔴 4 — PROMPT-C0 Task 1 is ~25% implemented, and the deliverable reads as complete

Task 1 names four sources and their metrics. Coverage:

| Source | Task 1 metrics | Conformance tests |
|---|---|---|
| `measure-latency-run.sh` | `xruns`, `meter_live`, `meter_max_age_s`, `dsp_median`, `dsp_p99`, `dsp_max`, `jitter_n`, `jitter_*`, `frames_late_*`, `samples` | partial — `meter_max_age_s`, `jitter_*`, `frames_late_*` have no positive, negative, or physics test |
| `xrun-corr.sh` | per-second `dsp%`, `peak`, `xrun`; `TOTAL` | **none** |
| `measure-soak.sh` | `xruns_total`, `invalid_windows` | **none** |
| `bench-xruns.sh` | per-buffer xrun count | **none** |

```bash
$ for s in xrun-corr measure-soak bench-xruns; do rg -l "$s" tests/; done
  NO test references xrun-corr
  NO test references measure-soak
  (bench-xruns: only tests/test_systemd_units.py — unit file check, not conformance)
```

The gap itself is defensible triage. What isn't defensible is that
`instrument-conformance-c0-2026-08-22.md` presents a "Metric inventory → tests" table with
six rows, all populated, and no statement that three of four instruments were skipped. A
reader — including the next agent, which is the actual audience — concludes the fleet is
gated. Meanwhile `xrun-corr.sh` is occurrence #1 in the nine-instance table and still writes
to `~/xrun-corr.out`.

That table also overstates one row it does include: `window_align` is listed with negative
control *"absent on void run (harness)"*. No fixture omits `window_align`, no test asserts
its absence halts, and it is not in `mpe_result_require_fields`.

**Fix:** add a "Not covered" section to the deliverable naming the three ungated instruments
and file them as C0b, and drop or implement the `window_align` negative-control claim.

### 🔴 5 — Hand-written fixtures already disagree with the emitter, and the merge logic hides it

The real emitter puts `window_align=1` on the primary DSP row:

```512:512:scripts/measure-latency-run.sh
        echo "RESULT tag=${tag} xruns=${total_xr} meter_live=1 meter_max_age_s=${_meter_max_age_s} dsp_median=${dsp_median} dsp_p99=${dsp_p99} dsp_max=${dsp_max} window_align=1"
```

The fixtures put it somewhere else — three fixtures, three different places, none of them
row 1:

```bash
$ grep -n window_align tests/fixtures/instrument-conformance/*.log
good-1024-b.log:2:   RESULT tag=B-... samples=60 temp=... window_align=1
good-512-a.log:11:   RESULT tag=A-... file=/tmp/... xrun_events=/tmp/... window_align=1
physics-low-dsp-high-xr.log:2: RESULT tag=physics-... samples=60 window_align=1
```

`tests/test_instrument_conformance.sh:20` asserts `MPE_R_window_align = 1` and passes anyway,
because `mpe_result_load_tag` parses every row for the tag into one flat namespace. Fields
merge across rows, so no single row need be complete:

```bash
--- TEST 9: load_tag merges fields across rows (no single row is complete) ---
PASS: satisfied require_fields from two different rows
```

That means `mpe_result_require_fields xruns meter_live dsp_median dsp_p99 dsp_max samples`
does not validate the primary row — it validates the union of all rows. A future emitter
change that drops `dsp_median` from row 1 and leaks it into row 5 passes silently. The
fixtures have already drifted from the emitter and the gate did not notice, which is a
literal re-run of the `dsp_med` incident with the detector installed.

**Fix:** generate fixtures from the emitter (extract the RESULT-emitting block into a
function the test can call with injected values), and add `mpe_result_require_primary_row`
that validates row 1 in isolation before any merging.

### 🟡 6 — Physics assertions that silently abstain

Three separate abstention paths, all verified:

```
--- TEST 3:  physics_assert with EMPTY metrics    → PASSED (empty metrics pass physics)
--- TEST 12: MPE_R_jitter_n="?"                   → PASSED (jitter_n=? slipped through)
```

TEST 12 is the subtle one. `[ "$jitter_n" -lt 100 ] 2>/dev/null` (line 85) returns 2 on a
non-numeric operand, which the `if` reads as false, which means *no halt*. The `2>/dev/null`
hides the `integer expression expected` diagnostic that would have told you. Same shape as
the bug the whole project is about.

`mpe_result_physics_buffer_halving` also abstains when `dsp_large <= 0` (line 109, `exit 1` →
caller returns 0/pass), so a zeroed baseline can never trip the halving check.

Additionally, the harness never passes `meter_live` into the assertion — lines 500-504 set
`xruns`, `dsp_median`, `samples`, `jitter_n`, `tag`, but not `meter_live` — so the `meter_live
!= 1` branch is dead on the real path. (In fairness this is belt-and-braces: line 493 calls
`mpe_meter_assert_live` and halts first, which does legitimately justify the literal
`meter_live=1`. But the dead branch reads as coverage it isn't.)

**Fix:** make absent metrics a halt, not a skip, in `mpe_result_physics_assert`; validate
numeric-ness explicitly before `-lt`/`-ge` comparisons; drop the `2>/dev/null`.

### 🟡 7 — The gate prints nothing for a third of its work, and can convert a failure into a pass

```13:19:scripts/instrument-conformance.sh
# Unit tests that touch measurement / meter paths (no direct python3 discover — project rule)
if [ -x "${ROOT}/.venv/bin/python" ]; then
    "${ROOT}/.venv/bin/python" -m unittest tests.test_audio_engine tests.test_periodic_loop_lint -q 2>/dev/null \
        || python3 -m unittest tests.test_audio_engine tests.test_periodic_loop_lint -q
else
    python3 -m unittest tests.test_audio_engine tests.test_periodic_loop_lint -q
fi
```

Three problems in seven lines.

First, the comment says "no direct python3 discover — project rule" and the next two lines
call `python3 -m unittest` directly. The comment is technically true (`discover` is absent)
and materially misleading. A comment that survives only on a technicality is the next stale
claim.

Second, `unittest` writes everything to **stderr**, so `2>/dev/null` discards the entire
result of the successful path. Verified:

```bash
$ .venv/bin/python -m unittest tests.test_periodic_loop_lint -q 2>/dev/null
stdout-only rc=0 (nothing printed above = all output was stderr)
```

The full gate run confirms it — thirteen `OK:` lines from the two bash suites and **not one
line** from the python suites:

```
$ ./scripts/instrument-conformance.sh
=== instrument-conformance 2026-08-22T20:41:53+01:00 ===
...
test_meter_harness.sh: all checks passed
conformance wall_time_s=6
SENTINEL conformance-pass
```

`AGENTS.md`: *"A deploy step that prints nothing has not necessarily succeeded."* A silent
stage is indistinguishable from a stage that didn't run — which is Rule −1 applied to the
gate itself.

Third, the `||` fallback structurally converts a venv-specific failure into a pass. If the
venv python fails and the system python3 passes, the gate is green and the first failure's
output was destroyed. I confirmed a genuine failure does still propagate through the
fallback (rc=1, `set -e` aborts), so this is not currently masking anything — but the
mechanism is there, and it also runs every failing suite twice.

**Fix:** drop `2>/dev/null`, drop the `||` fallback (choose one interpreter and fail if it's
absent), and fix the comment to say what the line does.

### 🟡 8 — `MPE_R_*` state leaks between `load_tag` calls

Line 125-127 unsets a hardcoded allowlist of nine fields. Anything else persists:

```bash
--- TEST 10: state leak between load_tag calls ---
after good load: window_align=1 jitter_n=5640
ERROR: measurement-result: missing required field dsp_median=
leaked from previous file -> jitter_n=<unset> dsp_p99=41.0
```

`dsp_p99=41.0` from the *previous* file survived into the next parse. The test file already
works around this — `tests/test_instrument_conformance.sh:30` hand-writes `unset
MPE_R_dsp_median` — which is the tell that the author hit it and patched the symptom.

**Fix:** enumerate and unset all `MPE_R_*` dynamically (`compgen -v MPE_R_`) at the top of
`mpe_result_load_tag`.

### 🟡 9 — Everything is uncommitted; there is no reviewable branch state

```bash
$ git rev-parse dev HEAD
e0d857454d4f8b0640f808e9b92d41b6031ab36d
e0d857454d4f8b0640f808e9b92d41b6031ab36d
$ git diff --stat dev...HEAD     → (empty)
```

`yolo/instrument-conformance-c0` is byte-identical to `dev`. All fifteen changes are
working-tree state. There is no commit, no PR, and no diff for anyone to review — combined
with the un-ignored 64 MB `.venv` (§2), the first `git add -A` here is going to be a mess.
`AGENTS.md` requires unit tests run before opening a PR to `dev`; there is nothing to open
yet.

### 🟢 10 — Minor annoyances

- **Doubled key prefix in RESULT rows.** `temp="$(vcgencmd measure_temp ...)"` already
  returns `temp=54.0'C`, so line 517 emits `temp=temp=54.0'C` (visible in
  `good-512-a.log:9`). The parser survives it — `MPE_R_temp` becomes `temp=54.0'C` — but
  every consumer has to know that.
- **Placeholder left in the deliverable.** `instrument-conformance-c0-2026-08-22.md:5` says
  *"Wall time (nerdrack): recorded at end of this doc after test run"* while line 85 has the
  actual number. Collapse the two.
- **Confusing test call.** `tests/test_instrument_conformance.sh:53` calls
  `mpe_result_physics_buffer_halving 19.14 38.52` under the label *"plausible DSP increase
  passes"*, passing the smaller value as the `dsp_large` parameter. It documents the intended
  physics but inverts the parameter contract; a reader can't tell which is deliberate.
- **No `shellcheck` in the gate.** Three of the defects above (the `[ ]` glob, the
  non-numeric `-lt`) are exactly what SC2053-class linting catches. `shellcheck` isn't
  installed here, so I couldn't lint; adding it to the gate is cheap and directly on-mission.

---

## 5. Logic & Business Rules

**The business rule is stated unusually well.** "Value and failure share a channel" is a
falsifiable, mechanically checkable claim, and the four required mechanisms
(`MEASUREMENT-DISCIPLINE.md:21-30`) are a real specification rather than an aspiration. The
pre-registration blocks landing in `Documents/specs/low-latency-512-256-spec.md` and
`rerun-order-2026-08-19.md` — declaring "Impossible if" *before* the run — is genuine
scientific discipline and the most under-appreciated part of this changeset.

**The rules are expressed in prose and only partly in code.** That's the core logic problem.
Four documents assert nine instances, four mechanisms, and six doctrine locations; the
executable subset is roughly one instrument, six metrics, and two physics rules. The
authority gradient runs the wrong way — the prose is the spec, the tests are a sample, and
nothing flags the delta.

**Race conditions and timing:**

- The `PROBE_START` wait is a 5-second budget (50 × 0.1 s) with no lower bound on how long
  activation actually takes after the sentinel. On a loaded Pi at 8 loops, JACK client
  activation is not instant. The window boundary is therefore variable run-to-run, and
  `window_align=1` is stamped regardless. That variability lands in the same measurement
  that stream-start variance (`stream-start-variance-2026-08-21.md`) already showed this
  project is sensitive to.
- `_kill_jcl` is redefined as a global function on every `_run_window` call, closing over
  that call's `$jcl`. Pre-existing, not introduced here, but it means a stale definition
  survives between windows.
- **Correctly handled:** the meter-restart check at `:436-441` catches a counter going
  backwards and voids the window. That's a real race, correctly detected, failing loud.
- **Correctly handled:** `_enable_strict_xrun_reporting` is called once at line 548, outside
  the run loop. The deliverable's claim that "jackd strict restart remains once per harness
  invocation, not per probe window" is accurate — I verified it.

**State management** in the library is bash-flat-namespace with a manual unset list, which is
the weakest link (§4 🟡-8). It works for one tag at a time and breaks quietly for two.

---

## 6. Test Strategy & Execution

**What's good, and it's genuinely good:** the negative controls actually break things.
`tests/test_meter_harness.sh` verifies that a missing meter, a stale meter, and a missing
`xruns=` field each *fail* — not that a mock returns a canned value. That is the difference
between testing behaviour and chasing coverage, and it's the right instinct throughout
`tests/test_instrument_conformance.sh` too: the `dsp_med` typo test asserts a **halt**, the
physics tests assert **rejection**. Eight of the eight assertions in the new test file check
a failure mode or a value, not mere presence. Credit where due.

**The gate is fast, and the claim is accurate.** I reproduced it:

```
conformance wall_time_s=6
SENTINEL conformance-pass
./scripts/instrument-conformance.sh  3.26s user 1.64s system 81% cpu 6.032 total
```

6 s against a 900 s budget. The reasoning in `MEASUREMENT-DISCIPLINE.md:33` — *"a gate that
is slow gets skipped; a skipped gate is not a gate"* — is correct and the implementation
honours it. The `ELAPSED > 900 → exit 1` self-check is a nice touch: the gate fails if it
becomes too slow to trust.

**What's dangerously untested:**

1. **The emitter.** Zero tests execute the RESULT-emitting path in
   `measure-latency-run.sh`. Every test runs against hand-typed fixtures that have already
   drifted from it (§4 🔴-5). The gate validates the parser against a *belief* about the
   emitter.
2. **Three of four instruments.** `xrun-corr.sh`, `measure-soak.sh`, `bench-xruns.sh` (§4
   🔴-4).
3. **Multi-row and multi-tag inputs.** Every fixture is one tag. The sticky-`withhold` bug
   (§4 🔴-2a) and the cross-row merge (§4 🔴-5) both need exactly two rows to surface, and
   both are real.
4. **The abstention paths.** No test asserts that *absent* or *non-numeric* metrics halt —
   which is why TEST 3 and TEST 12 pass silently.
5. **`window_align` absence.** Claimed as a negative control in the deliverable, not
   implemented.

**Brittleness:** the fixtures are the coupling point. They encode field order and row
placement as literals, so they are simultaneously too tightly coupled to a *guessed* format
and not coupled at all to the *real* one — the worst of both.

**Missing category:** there is no test that the gate itself fails. A conformance gate should
have a self-test that deliberately breaks a fixture and asserts a non-zero exit from
`scripts/instrument-conformance.sh`. Right now nothing proves the gate can go red.

---

## 7. Security & Performance

No security surface here worth alarm — no network I/O, no credential handling, no untrusted
input beyond log files the project produces itself. Two notes:

- **Command injection is not a real risk but the parser is loose.** `mpe_result_parse_line`
  does `for tok in $(echo "$line" | sed 's/^RESULT //')` — unquoted command substitution, so
  a RESULT line containing shell glob characters would be pathname-expanded before tokenising.
  The key is sanitised (`key="${key//[^a-zA-Z0-9_]/_}"` — good defensive touch) but the value
  is not. Since inputs are self-generated logs this is a robustness issue, not a
  vulnerability. Worth a `read -ra` rewrite when convenient.
- **`.venv` in the working tree** (§2) is a repo-hygiene risk, not a security one, but for an
  appliance repo that gets pulled onto the Pi it's the kind of accident that ships 64 MB to a
  device with an SD card.

**Performance — and this project cares more than most.** `AGENTS.md` is emphatic that CPU is
the scarcest resource and demands cost × cadence for any new loop. Assessed:

- **The `PROBE_START` poll loop** — `sleep 0.1` × up to 50, with a `grep` fork per iteration.
  That is up to 50 forks per window. It runs once per 60 s window during a measurement
  session only, not on the appliance, so it doesn't touch the standing CPU budget. But 50
  short-lived forks landing immediately before a latency measurement begins, on the machine
  under test, is not free — and the measurement is about scheduling jitter. **Fix:** poll on a
  0.02 s `read`-based check or use `inotifywait`, and cap the loop at something justified
  rather than 5 s of round numbers.
- **The awk median** at lines 469-471 is an O(n²) bubble sort. n = `SECONDS_PER_RUN` = 60,
  so 1800 comparisons — irrelevant. Not worth touching, but note it degrades badly if anyone
  ever raises the window to 3600 s for a soak.
- **Re-sourcing the library per window** (§2) is one extra file read per 60 s. Negligible,
  but structurally in the wrong place.

No fork-in-periodic-loop violation on the appliance path. The changeset respects the CPU
doctrine.

---

## 8. Developer Experience

**Onboarding is a genuine strength.** `AGENTS.md` plus `MEASUREMENT-DISCIPLINE.md` plus
`PROMPT-C0-instrument-conformance.md` would get a new agent productive in well under a day,
and the "hand this to a fresh agent, self-contained, assumes no prior context" convention on
the specs is doing real work. The nine-occurrence table in
`.claude/skills/mpe-measurement/SKILL.md:111-121` is the best onboarding artifact in the repo
— it teaches the failure mode by example rather than by rule. I checked the count for
consistency across `MEASUREMENT-DISCIPLINE.md`, `PROMPT-C0`, the deliverable, and
`PROGRESS.md`: all four say nine, and PROMPT-C0's arithmetic ("AGENTS.md lists four ... T2/T6
add five more") reconciles. That kind of cross-document numerical consistency is rare and I'm
pleased to find it.

**The documentation is lying in specific, findable places** — which is worse than vague
documentation, because it's trusted:

| Claim | Location | Reality |
|---|---|---|
| `meter_live=1` with `meter_max_age_s` > 3 is a physics assertion | `MEASUREMENT-DISCIPLINE.md:47` | Not implemented anywhere |
| Sampler window alignment: fixed | `instrument-conformance-c0-2026-08-22.md:52-56` | Sentinel fires before `jack_activate`; `window_align=1` is a literal |
| `window_align` negative control: "absent on void run" | `instrument-conformance-c0-2026-08-22.md:38` | No such fixture or test |
| Metric inventory (6 rows, all populated) | `instrument-conformance-c0-2026-08-22.md:32-39` | 3 of 4 required instruments have no tests at all |
| "no direct python3 — project rule" | `scripts/instrument-conformance.sh:13` | Calls `python3 -m unittest` on the next line |
| V11 verdict: xruns stand, DSP withheld | `PROGRESS.md:32-33` | Produced by an order-dependent tool that exits 0 on missing files |

**Build/deploy sanity:** the gate is a single command, fast, offline, and exits non-zero
appropriately. `PROGRESS.md`'s HALTED banner blocking A1-A4 behind C0 is the right process
control, and freezing a queue on a *tooling* dependency rather than pushing forward with
known-bad instruments is exactly the judgement call this project has been failing to make for
five months. That deserves explicit credit.

One process friction: `PROGRESS.md:16` lists C0 as "in progress" while the deliverable doc
reports the gate green. The banner's release condition ("exits 0 on the branch under test")
is now satisfied, so nothing tells the next agent whether A1 is unblocked. Say so explicitly.

---

## Verdict

The diagnosis is excellent and the implementation does not yet earn the confidence the
documentation projects. "Value and failure share a channel," backed by a consistent
nine-instance inventory, is a real root-cause finding, and the surrounding discipline — 6 s
offline gate, pre-registered "Impossible if" lines in the specs before runs, negative
controls that genuinely break things, a queue frozen on tooling rather than pushed forward on
bad instruments — is the strongest measurement practice this repo has had. I would merge the
doctrine tomorrow. But the gate is thinner than its own report: `window_align=1` certifies an
alignment that the probe demonstrably hasn't established when the window opens; the V11
recovery tool exits 0 on a missing file and produces order-dependent verdicts; `dsp_median=0`
from a dead sampler passes both the field check and the physics check; three of the four
instruments PROMPT-C0 named have zero coverage, including `xrun-corr.sh`, which is occurrence
#1 and still writes to `~/xrun-corr.out`. The pattern is uncomfortably familiar: a conformance
layer that reports success while measuring less than it claims. Fix the five 🔴 items — none
is large — and this becomes the gate it is described as. Do not lift the `PROGRESS.md` HALTED
banner on the strength of the current green, because the current green is partly the fixtures
agreeing with themselves.

---

## Priority backlog

1. **🔴 Make `window_align` mean something.** Emit `PROBE_ACTIVE` from
   `mpe-xrun-probe.c` *after* `jack_activate()` returns 0, gate the sample loop on that,
   take the meter baseline immediately after it, and compute `window_align` from the measured
   meter-baseline→first-DSP-sample gap instead of stamping the literal `1` at
   `measure-latency-run.sh:512`. Until then, stop reporting the V10-b misalignment as fixed.
2. **🔴 Make `mpe_result_v11_recover` capable of failing.** Move `withhold=0` inside the
   `RESULT` case arm (fixes the order-dependent verdicts), validate the input file with
   `[ -r "$file" ]`, and halt on zero emitted rows. Then re-run the V11 recovery and re-derive
   the `PROGRESS.md` DSP-withheld conclusion, because the current one came from this tool.
3. **🔴 Close the zero-sentinel path.** Replace `print "0 0 0"` at
   `measure-latency-run.sh:468` with an awk failure that voids the window, reject
   `0`/`0.000000` for `dsp_*` in `mpe_result_require_fields`, and add a fixture whose DSP rows
   are all `?` asserting the gate halts.
4. **🔴 Bind the fixtures to the emitter and validate the primary row.** Extract the
   RESULT-emitting block into a callable function, generate fixtures from it, and add
   `mpe_result_require_primary_row` so `require_fields` stops being satisfied by the union of
   all rows. This is the check that would have caught `dsp_med`, and it is the one still
   missing.
5. **🔴 Fix or declare the Task 1 coverage gap.** At minimum: fix occurrence #1
   (`xrun-corr.sh:25` → stdout) and add its conformance tests, and add a "Not covered"
   section to `instrument-conformance-c0-2026-08-22.md` naming `measure-soak.sh` and
   `bench-xruns.sh` as ungated, filed as C0b. A gate whose report implies coverage it lacks
   is the failure mode this whole exercise is about.
