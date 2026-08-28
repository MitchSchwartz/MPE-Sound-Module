# Request: SR&ED account of the looper work

*Paste the section below to the agent/session that carried out the SooperLooper
seam and multi-clip investigation.*

---

I'm assembling an SR&ED technical narrative for this project covering
**2026-07-18 to 2026-08-28**. It lives at `docs/sred/SRED-NARRATIVE-2026.md`.
The looper thread is the one section I could not write properly, because most of
that investigation happened in your session and I only have its endpoints.

**Do not write marketing copy, and do not inflate this.** SR&ED rests on
*uncertainty* and *systematic investigation*, and the strongest evidence is the
work that failed. Routine debugging weakens a claim if it's included — leave it
out. If part of the work was straightforward engineering with no real unknown,
say so plainly; that's a useful answer.

Please produce a markdown document at `docs/sred/looper-thread.md` answering:

### 1. What was genuinely uncertain

For each distinct uncertainty — not each task — state what was unknown at the
outset and **why it could not be resolved by standard practice** (documentation,
a library's API, an obvious experiment). Be specific about the technical
obstacle. "Making loops sound good" is not an uncertainty; "whether a loop could
be closed sample-accurately when the recorder, synthesiser and control layer are
separate processes with independent clocks" is.

### 2. What you actually tried, in order

Include the approaches that were **abandoned**, and why. Specifically:

- The offline "seam weld" approach: what was the hypothesis behind it, what did
  it assume about the recorder's behaviour, and how were those assumptions
  eventually tested?
- I have recorded that **two premises were measured false** — that `load_loop`
  halts playback (it does not; loop position ran straight through), and that the
  `SEAM_LOAD_LEAD_MS` sweep was tuning the join (it was measuring the landing
  error of a subsequent retrigger). Confirm, correct, or expand on that.
- What made the eventual native-overdub mechanism work where the previous
  approach could not?
- Anything else abandoned: tail capture, scratch loops, timing offsets.

### 3. Measurements and instruments

- What was measured, with what instrument, and how did you establish the
  instrument was sound before trusting it?
- Were there **positive controls** — a case where the instrument should show
  nothing, used to prove a null result was real rather than a dead probe?
- Any measurement discarded for a confound, and what the confound was.
- Any result that **contradicted** an expectation.

### 4. What was advanced

What is now known, or now possible, that was not before — stated narrowly and
scoped to the conditions actually tested. Do not generalise beyond the hardware,
buffer configuration and patch library measured.

### 5. Multi-clip work

`multi-clip-slot-spike-2026-08-26.md` and
`multi-clip-p2-composition-failure-2026-08-27.md` suggest a composition failure
and a spike series. Same four questions for that thread.

---

**Format.** Follow the structure of §1 in `SRED-NARRATIVE-2026.md`: for each
uncertainty, four headed parts — **Uncertainty / Work performed / Advancement /
Evidence** — with Evidence citing repo paths (documents, commits, scripts) that
already exist. Do not cite anything you have not verified is there.

**Flag anything you are unsure of** rather than smoothing it over. A gap I know
about is far more useful than a confident sentence I have to retract in front of
an auditor.
