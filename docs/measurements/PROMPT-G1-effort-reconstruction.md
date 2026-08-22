# G1 — walk the git history with Mitch and put hours on it

**Gap G1 from [`docs/SRED-EVIDENCE-2026.md`](../SRED-EVIDENCE-2026.md), and the highest-value
one.** SR&ED claims are computed from **labour**, and this repository records none. Git
timestamps show when commits landed — not how long the thinking took, not the hours spent on
lines that produced no commit, not the runs that failed and were never written up.

This is an **interactive session with Mitch**, not a research task. You cannot derive the
answer; you can only build the scaffold that makes it fast for him to answer, and record what
he says accurately.

**Do it soon.** This is the gap that decays. Every week that passes makes recall worse, and
by the time a claim is being prepared the detail is unrecoverable.

---

## The failure mode to avoid

**Do not ask Mitch "how many hours did you spend in August?"** He cannot answer that, the
number would be a guess, and a guessed aggregate is worse than useless — it is the kind of
figure that collapses under a single follow-up question.

**Present evidence, ask for a bounded estimate against it.** "On 08-21 there are 71 commits
across the W1 window, V0/V1/V2, and the hygiene work, first at 09:12 and last at 23:47 — was
that a full day, and roughly how much of it was hands-on?" is a question a person can answer
honestly. Anchor every question to something visible.

---

## Prepare before involving him

Do all of this first. His time is the expensive input; arrive with the scaffold built.

**1. Build the daily table.** From `git log --all` across all branches:

| date | commits | first / last commit time | branches touched | what the messages say happened |
|---|---|---|---|---|

Commit *times* matter as much as counts — they bound the working window. Note that the
period runs 2026-07-18 → 2026-08-22, ~770 commits, with clear intensity structure: light
days in July, a build-out phase through 08-09, then near-daily heavy work 08-10 → 08-22
peaking at 71 commits on 08-21 and 63 on 08-18.

**2. Group days into the phases already established.** Use the chronology table in §4 of the
SR&ED record — build-out, first fault isolation, looper cost, jitter hunt, core allocation,
W1, V0/V1/V2, V7/V8, V9, V10-b, ceiling analysis. **Do not invent a new phase scheme.** The
two documents must agree, or the record contradicts itself.

**3. Pre-compute what git can tell you and he should not have to.** Lines changed per phase,
files touched, which PRs (#86, #88, #90–#95) span which days, and — importantly — **the
gaps**: 08-06/07, and 08-20 has only 5 commits sitting between two 60+ commit days. Ask
about the quiet days specifically; a 5-commit day in the middle of an intense stretch is
often a long unproductive debugging day, which is eligible time that leaves no trace.

**4. Flag the invisible work explicitly.** Prompt him on each, per phase — these are the
categories that generate hours and no commits:

- **Measurement wall-clock where he was blocked.** Soaks and ladders ran for hours. Some of
  that is unattended (not labour); some is monitoring and interpretation (labour). He has to
  draw that line — ask, do not assume either way.
- **Runs that were abandoned mid-flight** and never written up.
- **Reading and interpretation** — the analysis documents represent thinking time far beyond
  what writing them cost.
- **Physical bench work** — recabling, the Scarlett 4i4 swap, power supply changes, GPIO
  jumper vs USB-C. None of it commits.
- **Listening tests.** Ear-testing patches is real evaluation work and leaves no artifact.
- **Direction and review of AI-assisted work** — reviewing output, catching the four
  instrument defects, the retractions. Whatever the eventual treatment of this, **record the
  hours now and let the accountant decide**; a category omitted cannot be added back later.

---

## Run the session

**Go phase by phase, oldest first.** Chronological order lets recall build momentum; jumping
around produces worse numbers.

For each phase, put three things to him and record his answers verbatim:

1. **Elapsed days** — which calendar days did this actually span? (Git bounds it; he
   confirms or corrects. Work often starts before the first commit.)
2. **Hands-on hours** — roughly, across those days. A range is fine and more honest than a
   point estimate.
3. **Anything the commits do not show** — from the §4 list above.

**Record uncertainty as uncertainty.** If he says "somewhere between 4 and 8 hours, I really
don't remember," write **"4–8, low confidence"**. Do not average it to 6 and present that as
a figure. A range with a stated confidence survives scrutiny; a false precision does not, and
this project's whole discipline is built on not promoting an inference to a premise.

**Ask about the routine work separately.** Gap G5 needs eligible work separated from
packaging, README/onboarding docs, CLI ergonomics, deploy scripting, and dependency
maintenance. Easiest while he is already walking the history — mark those days or commits as
he goes rather than doing a second pass.

---

## Output

Write `docs/SRED-EFFORT-LOG.md`, cross-linked from §7 of the SR&ED record (update the G1 row
to point at it). Structure:

- **Method** — one paragraph: this is Mitch's recall, anchored to git, reconstructed on
  <date>. **State plainly that it is a reconstruction, not a contemporaneous timesheet.**
  Overstating what it is, is the one thing that would actively damage the claim.
- **Per-phase table** — phase, dates, commits, estimated hours (range), confidence, notes.
- **Non-commit work** — the invisible categories, per phase.
- **Routine/excluded** — what he identified as ineligible (feeds G5).
- **Totals** — with the ranges preserved. Do not collapse to a single number.
- **Open questions** — anything he could not recall. An honest gap beats a fabricated figure.

Then, **for the future**: propose the lightest possible ongoing capture so this is never
reconstructed again. A dated line per work session in a running log is enough, and it is the
same habit already in force for measurement conditions.

---

## Constraints

- **This is a conversation.** Do not write the effort log and ask him to approve it — build
  it from his answers as you go.
- **No Pi contact.** This task is entirely git and conversation.
- Do not estimate on his behalf. A number he did not say is not evidence.
- Do not inflate. The strongest position is a defensible one, and this record already has
  unusually good documentation of genuine investigation. Do not put that at risk with soft
  hours.
