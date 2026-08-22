---
name: sred-daily-capture
description: Append contemporaneous SR&ED labour evidence to docs/SRED-DAILY-LOG.md. Use at end of investigation sessions, after measurement runs, after instrument build/fix/retraction, when closing SR&ED-relevant PRs, or when Mitch says log SR&ED hours. Prevents G1-style backfill.
---

# SR&ED daily capture (MPE-Module)

**Goal:** One append per **work session** so labour evidence exists when the claim is filed. G1 proved git timestamps and memory are not enough — and that **calendar days misstate sessions** (see effort log §Session model: gap **> ~4 h** = new session; midnight is not a boundary).

**Canon:** [`docs/SRED-EVIDENCE-2026.md`](../../docs/SRED-EVIDENCE-2026.md) (phases §4, uncertainties) · [`docs/SRED-EFFORT-LOG.md`](../../docs/SRED-EFFORT-LOG.md) (reconstruction baseline only — do not append there).

**Output file:** [`docs/SRED-DAILY-LOG.md`](../../docs/SRED-DAILY-LOG.md) — **append-only** table under `## Log`.

---

## When to run (mandatory triggers)

Invoke this skill and append **before ending the agent session** when any of:

1. **Pi measurement** finished or abandoned (soak, ladder, confirm cell, instrument conformance).
2. **Instrument work** — built, fixed, audited, or retracted (categories 1–3 in effort log).
3. **Investigation doc** landed in `docs/measurements/` (hypothesis → result → verdict).
4. **SR&ED-relevant PR** opened or merged (latency, instruments, refuted line, not pure README/deploy).
5. **Mitch states hours** or describes a session — capture verbatim ranges.
6. **Cross-midnight push** — still one session row; note sleep gap and calendar split.

**Do not wait for Friday.** Per-session is the habit (same cadence as recording measurement conditions).

**Skip** when the session was **only** routine (G5): README polish, deploy scripts, touch UX with no investigation — unless Mitch says log it as routine.

---

## Before appending

1. **Time:** MCP `get_current_time` with `America/Toronto` → `date` column (`YYYY-MM-DD`).
2. **Phase:** Pick from §4 chronology in `SRED-EVIDENCE-2026.md`. If multiple, pick dominant or use `mixed (Ph.9+10)` in note.
3. **Hours:** Mitch's words only — ranges OK (`3–4`, `~2`, `4–8 low confidence`). If unknown, ask one bounded question anchored to git ("commits 13:16–22:59 — full evening or ~4 h hands-on?"). If session ends without answer, use `?` and note *hours pending*.
4. **Split columns** (optional but preferred when known):
   - `meas` — running/measuring/monitoring Pi
   - `instrument` — building or fixing probes/harnesses/parsers
   - `review` — AI output review, retraction interpretation, doc write-up
   - Sum is **not required** to equal `hands-on`; overlaps are normal.
5. **Anchor:** commit hash, PR #, or `docs/measurements/foo.md` path.
6. **G5:** `eligible` | `routine` | `admissibility-pending` | `mixed`.

---

## How to append

**Preferred:** `scripts/sred-log-append.sh` (keeps table columns aligned).

```bash
scripts/sred-log-append.sh \
  --date 2026-08-22 \
  --session '16:40–18:06' \
  --phase '11 V11 + C0' \
  --hands-on '~1.5' \
  --meas 0 --instrument 1 --review 0.5 \
  --cat 1 \
  --g5 eligible \
  --anchor '6e98af0, docs/measurements/...' \
  --note 'Peak meter blind graph fix; harness repair dominated.'
```

**Manual:** Add one row to the table in `docs/SRED-DAILY-LOG.md` under `## Log`. Do not edit prior rows.

---

## Row template

| column | content |
|---|---|
| date | `YYYY-MM-DD` (Toronto) |
| session | Wall-clock span `HH:MM–HH:MM` or `continues from prior day` |
| phase (§4) | e.g. `9 V9/V10-b`, `instrumentation cat 3` |
| hands-on | Mitch's range or `?` |
| meas / instrument / review | Sub-hours if known; `-` if N/A |
| cat | `1`, `2`, `3`, `1+2`, or `-` |
| g5 | tag |
| anchor | git / doc / PR |
| note | One line; cross-midnight; unattended soak; retraction |

---

## Agent rules

- **Never inflate.** An honest `?` beats a fabricated `6`.
- **Never average ranges** Mitch gave (`4–8` stays `4–8`, not `6`).
- **Commit timestamps ≠ session duration** — effort log standing caveat applies; daily log captures Mitch's hands-on estimate.
- **Instrumentation fixes are SR&ED analysis** — tag cat 2 when a blind instrument was found; cat 3 when Rule −1 / C0 / mechanism work ships.
- **Unattended soaks are normal** — log `meas: 0` (or monitoring %) and note mechanism 5 / header-only risk in `note`; not a lapse.
- After append, mention in session handoff: *"SR&ED daily row added for &lt;date&gt;."*

---

## Mitch-only (agents ask, do not guess)

- Hands-on hours for the session
- Whether a block was `eligible` vs `routine` when mixed
- Admissibility when unsure (use `admissibility-pending`)

---

## Related skills

| skill | when |
|---|---|
| `measurement-design` | before Pi measurement (conditions) |
| **`sred-daily-capture`** | after session (labour) |
| G1 backfill | **historical only** — [`PROMPT-G1-effort-reconstruction.md`](../../docs/measurements/PROMPT-G1-effort-reconstruction.md) |
