# G2 — recalibrate the poly governor and re-enable it

**Gate 2.** The governor has been **off since V9 (2026-08-22)**, deliberately, to hold one
variable. The consequence is that the appliance as currently played has **no voice limiting at
all** — patches whose floor is 3 voices overrun on a four-note chord. Mitch reports certain
patches "insta crackle." **That is expected and is not a regression: it is the absence of the
mechanism this task restores.**

**This now blocks Gate 1.** B3's ear test certifies `1024x2` as the shipping default, and the
shipping default *includes* the governor. Ear-testing with it off tests a configuration that will
never ship.

---

## Read first — the diagnosis

`MPE_POLY_CPU_HIGH=50.0` was chosen before anyone had measured what clean operation costs. The
A2 reference suite now says (`dsp_median`, governor off, condition A):

| patch | 1024x2 | 512x2 | 256x3 |
|---|---|---|---|
| Crystals @3 | 38.4 | 39.3 | 40.7 |
| Duduk @3 | 38.3 | 39.4 | 40.4 |
| **Cloud Horn @5** | **56.9** | **58.2** | **59.4** |

**Two defects, both fatal:**

1. **`CPU_HIGH=50.0` sits below Cloud Horn's clean operating point at every buffer.** Enabled as
   configured, the governor sits permanently in panic and steals voices from a patch that is
   running fine. **This is the most likely identity of the original "crackle at 512"** — the
   reason `512x2` was abandoned. The governor, not the buffer.
2. **`CPU_LOW=40.0` is the *release* threshold, and it is below every clean operating point.**
   Once engaged on Cloud Horn, DSP would not fall below 40 even after voices are stolen, so **the
   governor would latch on and never release.**

---

## Do not use `dsp_p99` from the reference suite to set these

It is tempting and it is wrong, for two independent reasons:

1. **It is not a p99.** The suite samples DSP at **1 Hz over 25 s = 25 samples**. The 99th
   percentile of 25 samples is the maximum wearing another name.
2. **Resolution mismatch.** The governor reacts to **150 ms** of sustained load
   (`CPU_HIGH_HOLD_S=0.15`). A **1 Hz** sampler cannot observe 150 ms events at all — it is far
   below the Nyquist rate of the signal the governor responds to. *(Skill Step 3: a sampler below
   the Nyquist rate produces an authoritative-looking trace with the answer removed.)*

**Use `dsp_max` as a coarse upper bound on observed load, call it that, and set thresholds
conservatively. Then verify empirically.** The only trustworthy test is whether the governor
actually engages during clean play.

---

## Proposed values — pre-register before changing anything

```
MPE_POLY_CPU_HIGH=78.0          # was 50.0
MPE_POLY_CPU_LOW=68.0           # was 40.0
MPE_POLY_CPU_HIGH_HOLD_S=0.15   # unchanged
MPE_POLY_CPU_LOW_HOLD_S=5.0     # unchanged
MPE_POLY_GOVERNOR_HEADROOM=3    # unchanged
```

**Reasoning, to be recorded in the result doc:** `dsp` is already deadline-relative, so an
absolute threshold is the correct control variable — it simply has to sit near the deadline
rather than halfway to it. HIGH must clear the highest measured clean point (59.4%) with room for
transients while leaving reaction margin below 100%; at `1024x2`, 150 ms is ~7 periods of lead
time. LOW at 68 sits above every clean median so the governor can release.

**These are a starting point, not a result.** If verification shows engagement during clean play,
raise both and re-test. **Record every value tried and why**, not just the final pair.

---

## Sequence

### 0. Check the X1 dependency first — offline, free

`PROMPT-X1-confirm-vs-soak.md` asks, among other things, **whether the confirm harness leaves the
poly governor on.** If it does, the confirmed floors (Crystals 3, Cloud Horn 5, Duduk 3) were
measured *with voice limiting active* and are not the floors — and the table above may be
measuring a governed load.

**If X1 has not run, check that one thing before proceeding.** It is a grep. If confirm leaves the
governor on, **stop and report** — this task's inputs are invalid.

### 1. The fade must land first

Gate 2 has always been blocked on **two** things: thresholds *and* the fade. A governor that drops
voices abruptly is audible as a click, which makes the ear test fail for a reason unrelated to
buffer size. **Confirm the fade is implemented and merged before re-enabling.** If it is not, that
is the first task and this one waits.

### 2. Apply thresholds, governor still off

Set the values. Do not enable yet. Confirm they are read back from the running config, not merely
written to a file — *"I applied it earlier" is not a record* (Rule 3).

### 3. Verify — 30 minutes maximum, and this is the real test

Governor **on**. **Cloud Horn @5 at `1024x2`** — the highest clean operating point in the library,
so the case most likely to trip a threshold falsely.

**Primary question: does the governor engage during clean play? It must not.**

Log, per minute: whether the governor engaged, how many voices it stole, xruns, and DSP.
`MPE_POLY_GOVERNOR_VERBOSE=1` if that is what surfaces engagement events.

**Pre-register:**
- **PASS** — zero engagements across 30 minutes of clean Cloud Horn @5.
- **FAIL** — any engagement while the patch is within its confirmed floor. Raise HIGH and LOW,
  record both values, re-test.

Then the opposite arm, briefly: **deliberately exceed the floor** (Crystals at 6+ voices) and
confirm the governor **does** engage and **does** release afterwards. A governor that never fires
passes the first test trivially — **both arms are required**, exactly as C0's positive and
negative controls are (`PROMPT-C0-instrument-conformance.md` — force a known overrun, then confirm
clean play produces zero).

**Anything longer than 30 minutes needs Mitch's explicit approval** with the event-rate
arithmetic. It should not be needed here: engagement is a per-second event, not a 2/min one.

### 4. Hand off to V12 — not B3 yet

Once both control arms pass, **Gate 2 closes.** Next in stack: **`PROMPT-V12-certify-buffer.md`**
(buffer comparison at long windows, governor on). B3 ear test comes **after V12** — audibility is
not answerable from G2's 30-minute governor check alone.

---

## Constraints

- **One variable.** Thresholds change; buffer, clock, and binary do not.
- **Do not tune to make a test pass.** If clean Cloud Horn trips the threshold, that is a finding
  about the threshold, not a reason to lower the bar for what counts as clean.
- Record the config **as read back from the running system**, not as intended.
- Instrument conformance (C0) must have passed in this session before any measurement.
- If the governor engages on a clean patch even at high thresholds, **stop and report** — that
  would mean absolute DSP is the wrong control variable, which is a design question and not a
  calibration one.

## Hand back

The X1 governor check result, fade status, every threshold pair tried with its outcome, the
30-minute verification log with engagement counts, both control arms, and whether Gate 2 can
close.
