# SR&ED daily log — contemporaneous labour capture

**Not a timesheet for payroll.** Evidence of eligible investigation labour for whoever prepares the claim. **Not tax advice.**

| doc | role |
|---|---|
| [`SRED-EVIDENCE-2026.md`](SRED-EVIDENCE-2026.md) | What was investigated (uncertainties, chronology §4) |
| [`SRED-EFFORT-LOG.md`](SRED-EFFORT-LOG.md) | **Reconstruction** through 2026-08-22 (G1 backfill — do not duplicate here) |
| **This file** | **Going forward:** append at end of each work session |

**Agents:** invoke [`.claude/skills/sred-daily-capture/SKILL.md`](../.claude/skills/sred-daily-capture/SKILL.md) or run `scripts/sred-log-append.sh`. Use MCP time (`America/Toronto`) for dates. **Never invent hours** — ask Mitch or leave `?` with a note.

**Phase names:** must match §4 in `SRED-EVIDENCE-2026.md` (do not invent new phases).

**Instrumentation (when applicable):** cat **1** build · **2** discover wrong · **3** derive rule (Rule −1, C0, etc.) — see effort log §Instrumentation.

**G5 tag:** `eligible` · `routine` · `admissibility-pending` · `mixed` (note split in text).

**Cross-midnight sessions:** one row; put wall-clock span in `session` column; note calendar split in `note`.

---

## Log

| date | session | phase (§4) | hands-on | meas | instrument | review | cat | g5 | anchor | note |
|---|---|---|---:|---:|---:|---:|---|---|---|---|
| 2026-08-22 | starting daily capture | — | — | — | — | — | — | — | [`SRED-EFFORT-LOG.md`](SRED-EFFORT-LOG.md) | **Backfill complete through today.** From next session onward, append here — do not rely on G1-style reconstruction. |
| 2026-08-22 | 16:40–18:10 | SR&ED evidence (G1/G3) | ~1.5 | - | 0 | 1 | 3 | eligible | docs/SRED-DAILY-LOG.md, AGENTS.md, sred-daily-capture skill | Daily capture stack: skill, append script, AGENTS rule; G1 effort log closed; G3 archive committed. |
| 2026-08-22 | evening (A3) | A3 `-mcpu=cortex-a72` | ? | 1 | 0 | 0.5 | - | eligible | reference-suite-pi4-a3-a72-comparison-2026-08-22.md | NULL result: all cells &lt;3% by pre-registration; stock reverted; U7 `-mcpu` branch closed. Hours pending Mitch recall. |
| 2026-08-23 | doc revision | A3 `-mcpu=cortex-a72` | ? | - | - | 0.5 | - | eligible | 90e4a7e, SRED-EVIDENCE-2026.md | Mitch review: pre-reg framing, stock revision 253f8d86, V1 cross-validation, U7 chronology. Hours pending. |
---

*Daily capture started: 2026-08-22 (America/Toronto)*

## 2026-08-22 — instrument-system arc (retro entry, ~13 h hands-on)

**Sessions:** 00:04-04:15 · 07:56-15:24 · 16:40-late. Continuous from 2026-08-21 13:16 with one
3 h 41 sleep break — the calendar boundary falls mid-session; see `SRED-EFFORT-LOG.md`.

**Advancement claimed.** Root-cause analysis of ten instrument failures spanning five days and
four subsystems, resolving to **one** structural defect: value and failure sharing a channel.
Derived five general mechanisms, implemented them as a conformance gate (offline + live),
reviewed it adversarially (F1-F5), fixed the findings, and ran it green on the appliance.

**Experimental results.**
- V11: `512x2` = 21.3 ms clean for Crystals @3, Duduk @3 (0/0/0 x3); Cloud Horn @5 marginal.
  **Floor is patch-dependent** (U9). DSP column void — occurrence nine.
- Live conformance: DSP 80.2% @ 256 under load vs V11's 0.9-1.6% — **~50-80x refutation,
  measured**.
- Gate 1 soak: never ran; logged 253 bytes over 4 h — occurrence ten.
- A2 reference suite pass 1 established, revalidated offline against the later parser
  (`494e8b4`) — control stands.

**Validation of the mechanism, same day.** C0 found real defects on first Pi run (#99); refused
reference-suite cell P1 under a threshold it could not defend, halting at cell 4 of 15; Rule 0.5
pilot caught that defect in ~2 min against the 25 min the equivalent V11 failure cost two days
earlier. The threshold fix then introduced a **fail-open**, caught in review (`a1e80e3`) — the
failure mode reproducing inside its own remedy.

**Instrument/measurement-system labour (eligible, invisible in commits):** root-cause analysis,
doctrine authoring, adversarial review of the gate across two cycles, threshold derivation from
V9/W1 anchors, and the offline revalidation helper.

**Routine (G5, excluded):** repo hygiene, branch merges, PROGRESS bookkeeping.

**Open:** P7 (clock scaling — cheapest Pi 5 forecast), Cloud Horn variance, A4 (noise floor),
V11 DSP re-run, Gate 1 soak re-run, ear test.

**Closed 2026-08-22:** A3 (a72 A/B) — NULL; `-mcpu=cortex-a72` lever retired.
