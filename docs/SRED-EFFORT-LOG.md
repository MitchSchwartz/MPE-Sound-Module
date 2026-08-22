# SR&ED effort log — MPE synth appliance (reconstruction)

**Cross-link:** [`SRED-EVIDENCE-2026.md`](SRED-EVIDENCE-2026.md) §7 G1 · phases in §4.

## Method

Mitch's recall, anchored to `git log --all` (765 commits, 2026-07-18 → 2026-08-22). Reconstructed interactively on **2026-08-22 (America/Toronto)**. **This is not a contemporaneous timesheet.** Hours are ranges with stated confidence where recall is weak; figures Mitch did not state are not recorded.

Commit data: local repo, author dates as committed. Invisible-work categories per [`PROMPT-G1-effort-reconstruction.md`](measurements/PROMPT-G1-effort-reconstruction.md).

**Standing caveat (Mitch, 2026-08-22):** Commit timestamps record when work was **written down**, not when it was done — on this project the two often diverge by hours. Every phase estimate anchored to commit windows is a **lower bound**, not a ceiling.

### Primary unit: **sessions**, not calendar days

**A session** = contiguous work bounded by a gap of **more than ~4 hours** without commits (sleep, life, or a hard stop). Calendar midnight is **not** a session boundary on this project.

| why | detail |
|---|---|
| **Defensible to a reviewer** | "You worked 08-17 and 08-18?" → one overnight session, not two independent days |
| **Stops boundary double-count** | 08-14 ends 21:49, 08-15 starts 03:00 — same push, not two days |
| **Stops under-counting pushes** | 08-16 23:55 → 08-17 00:13 (18 min) and 08-17 23:52 → 08-18 00:02 (10 min) are continuations |
| **Supporting evidence** | Calendar-day commit tables remain below for git anchoring; **hours attach to sessions** |

Six of seven Phase 2 calendar days start before 06:00 or end after 23:00 — calendar shape alone misstates labour.

**Going forward:** [`SRED-DAILY-LOG.md`](SRED-DAILY-LOG.md) rows are per session (wall-clock span in `session` column; note calendar split when relevant).

---

## Instrumentation work — three categories (SR&ED material)

Separate from "running measurements." Commits **undercount** category 1 (a C instrument that took a day often appears as one commit). **Instrument-building hours stay in their own column** so they do not disappear into "measurement."

| # | category | what it is | evidence |
|---|---|---|---|
| **1** | **Building instruments** | Native probes, harnesses, parsers | `mpe-peak-meter` (C), `mpe-xrun-probe` (C), `measure-latency-run.sh`, census parser |
| **2** | **Discovering they were wrong** | Nine Rule −1 occurrences; diagnoses; retracted conclusions | [`MEASUREMENT-DISCIPLINE.md`](measurements/MEASUREMENT-DISCIPLINE.md) table; V8-b auto-pick; V10-b ramp; census `unison_voices` |
| **3** | **Deriving the general rule** | Structural cause → enforced mechanism | Rule −1, Rule 0.5, C0 conformance suite (positive/negative/physics controls, plausibility floors). **Strongest claim material.** |

### Instrument-building evidence (08-17 → 08-19) — cat. 1

Concrete artifacts for the **instrument-building** column (not incidental — strongest defensible category in the claim):

| artifact | lines / scope | git | note |
|---|---|---|---|
| **`mpe-peak-meter.c`** | 379 lines C, RT-safe JACK client | `2607286` 08-18 | Exists because `0dc9e5b` 08-17 found **Python stalling jackd's RT cycle** — rewrite in C to stop perturbing what it measures |
| **`mpe-xrun-probe.c`** | 213 lines C, µs xrun delays + period-jitter histogram | `d6d0fa4`, `63aa2b7` 08-19 | |
| **Native tree** | 11 commits touching `native/` since 08-17 | — | Plus two Makefiles and two build scripts |
| **Latency tap** | Built and diagnosed **twice** | `c33b52e` | "Tap the OSC client, not the bench's `_send`" — Rule −1 #3 |
| **Total RT-safe C on target** | **~592 lines** | — | Debugged on a live Pi audio graph |

**Instrument-building estimate (08-17 → 08-19):** **10+ h** (medium confidence). Mitch: *would be surprised if under 10 h across those three days.* **Your number** — adjust the range if recall differs; do not fold into measurement hours.

---

## Per-phase estimates

| phase (§4) | span (sessions / git) | commits | hands-on (range) | instrument-building (range) | confidence | notes |
|---|---|---:|---|---|---|---|
| 1. Build-out | 07-18 → ongoing | ~200 (07-18→08-09) + continuing | **40–60** | — | medium | Continues through today. ~**20% routine**. |
| 2. First fault isolation | **08-12 → 08-18** (7 cal. days, **4 sessions**) | **296** | **3–4 h per session worked** → **~12–16** if 4 sessions | **10+ h** (08-17→19 block; see §Inst.) | medium | **`jack_lsp` enters 08-12** — crackle thread starts **two days before** the window previously assigned. Self-inflicted fault; heavy AI review. |
| 3. Looper cost | 08-18 → 08-19 | ~50 themed | **~4.5–6 h/day** | Overlaps inst. column | medium | Step 0 harness, xrun-probe, tap v1. G5: admissibility pending. |
| 4. Jitter hunt (refuted) | 08-19 → 08-21 | 39+5+part of 71 | **~4.5–6 h/day** | Low–med | medium | Scarlett ~30 min. Blind-instrument drag 08-20/21. |
| 5. E1 (refuted) | 08-20 12:52–14:12 | 5 | **1–2** | — | medium | **Separate row.** 68 min monitored run. `#86`. |
| 6–8. W1 / V0–V2 / V7–V8 | **08-21→22 push** | part of 71 | **(push — see below)** | — | medium | Start 13:16 08-21. |
| 9. V9 / V10-b | **08-22** (push cont.) | 50+ | **(within push)** | Med (cat. 2) | medium | Soak **fully unattended** (`7b8032c`). Retractions 11:00–12:30. |
| 10. Ceiling / P7–P8 | **08-22** | docs | **(within push)** | — | medium | `2b78339` 12:37; interleaved with V10-b. |
| 11. V11 | **08-22 pm** | 2+ | **~2** | Med (cat. 1–2) | medium | Harness repair dominates. |

---

## Phase 2 — first fault isolation (08-12 → 08-18)

**296 commits across 7 calendar days** — not 5 days / 228 commits. `jack_lsp` enters at **08-12** (`bb9dea8`); the crackle thread predates the window it was originally assigned to.

### Calendar days (supporting evidence only)

| date | commits | commit window |
|---|---:|---|
| 08-12 | 36 | 00:32 → 23:31 |
| 08-13 | 29 | 00:44 → 23:39 |
| 08-14 | 45 | 10:31 → 21:49 |
| 08-15 | 53 | 03:00 → 23:08 |
| 08-16 | 38 | 05:30 → 23:55 |
| 08-17 | 39 | 00:13 → 23:52 |
| 08-18 | 56 | 00:02 → 21:13 |
| **total** | **296** | |

*Git recount 2026-08-22: same totals; minor last-commit time variance on 08-15/18 vs author-date display — table above is Mitch's anchor.*

**Boundary examples (why sessions, not days):**

| gap | interpretation |
|---|---|
| 08-13 23:39 → 08-14 10:31 (~11 h) | **New session** |
| 08-14 21:49 → 08-15 03:00 (~5 h) | **New session** (>4 h) |
| 08-16 23:55 → 08-17 00:13 (18 min) | **Same session** |
| 08-17 23:52 → 08-18 00:02 (10 min) | **Same session** |

### Sessions (primary labour unit)

| session | span | commits | notes |
|---|---|---:|---|
| **P2-S1** | 08-12 00:32 → 08-13 23:39 | 65 | `jack_lsp` enters; fault introduced 08-13 |
| **P2-S2** | 08-14 10:31 → 21:49 | 45 | Crackle diagnostic lands |
| **P2-S3** | 08-15 03:00 → 23:08 | 53 | |
| **P2-S4** | 08-16 05:30 → 08-18 21:13 | 133 | Overnight 08-16/17/18; resolution `dd130a5`; **`mpe-peak-meter`** `2607286` |

**Hands-on (Mitch):** **3–4 h per session worked** — apply to **sessions**, not calendar days. Four sessions → **~12–16 h** investigation labour if all four were full work sessions (medium confidence; confirm).

**SR&ED narrative:** Defect **introduced by the same work that hunted it** — `jack_lsp` as graph owner (08-12/13) → crackle diagnostic (08-14) → resolution (08-18).

**Git sequence (crackle + instruments):**

| date | commit | event |
|---|---|---|
| 08-12 | `bb9dea8` | Phase 1 JACK engine — **jack_lsp enters codebase** |
| 08-13 | `fb7a48c` | `jack_lsp` as graph owner under sudo — **fault introduced** |
| 08-14 | `73d2d67` | Crackle diagnostic script |
| 08-17 | `0dc9e5b` | Python meter stalling jackd RT cycle — **observer effect** |
| 08-18 | `dd130a5` | **Resolution:** crackle was the graph probe |
| 08-18 | `2607286` | **`mpe-peak-meter`** compiled C |
| 08-19 | `e2ae996` | **`measure-latency-run.sh`** + cyclictest floor |
| 08-19 | `d6d0fa4` | **`mpe-xrun-probe`** C |
| 08-19 | `c33b52e` | Latency tap v1 fix — OSC client not `_send` |

---

## Continuous push — 08-21 13:16 → 08-22 18:06+ *(one session, calendar split)*

**Do not shape as two independent days.** Record as **one session** with a sleep break; calendar boundaries fall mid-push throughout the project, not only here.

| metric | value |
|---|---|
| **Wall clock** | ~30 h (08-21 13:16 → 08-22 18:06+, with **3 h 41 sleep** 04:15→07:56) |
| **Hands-on (Mitch)** | **18–23 h, medium confidence** — not a point estimate |
| **Wall segments** | 08-21 13:16→22:59 (**9.7 h** wall) + 08-22 blocks below |

*Deliberately not stated as "~87% utilisation of waking hours" — wall clock **permits** a long push; whether hands-on reached that density is Mitch's range above.*

| segment | span | work |
|---|---|---|
| **08-21 pm → 08-22 pre-dawn** | 13:16 → 04:15 | Ph.6–8 (W1, V0/V1/V2, V7/V8); V8-b fix; V9 a/b/c/d |
| **sleep** | 04:15 → 07:56 | 3 h 41 |
| **08-22 morning** | 07:56 → 15:24 | V10-b, soak start, P7/P8, census fix, PROGRESS, SR&ED doc, Rule −1 |
| **08-22 afternoon** | 16:40 → 18:06+ | G3 archive, C0 (#96/#97), C0 review, Rule 0.5, mechanism 5 |

**08-22 calendar day** was the largest single calendar day (~13 h hands-on Mitch recall) — but early block 00:04–04:15 belongs to **this push**, not a separate "08-21 day."

---

## Non-commit work (by phase)

### Phase 1 — Build-out

| category | Mitch's recall |
|---|---|
| Physical bench | A couple of hours |
| Listening / live playing | Yes — live playing sessions |
| **08-06 / 08-07** | Unsure — could have been mostly off |

### Phase 2 — additional recall

| category | Mitch's recall / git |
|---|---|
| Physical bench | Screen setup, GPIO power — couple of hours |
| Listening / live playing | Not much on crackle; **lots** testing 1024/512 feasibility live |
| AI-assisted review | **A lot this week** |
| Browse/carousel | Polish — **exclude** (G5) |

**Mitch's narrative:** Crackle predates window; voice governor attempted fix; looper built for compute path; looper pushed latency → deeper investigation.

### Phase 3 — Looper cost (08-18 → 08-19)

| category | Mitch's recall |
|---|---|
| Listening | ~3 h Tue 08-19 extended live-load test |
| Soaks | ~20% monitoring average |
| AI review | More than Phase 2 |

### Phase 4 — Jitter hunt (refuted)

| category | Mitch's recall |
|---|---|
| Scarlett swap | ~30 min |
| Soaks | ~20% monitoring |
| AI review | ~same as Phase 3 |

### Phase 5 — E1 (refuted)

See table row. `26f940c`: x86 probe binary removed — **repo hygiene** unless confirmed run on Pi.

### Phases 6–8 — start of 08-21→22 push

Evening 08-21 (13:16→22:59). End burst 21:32–22:09 ≠ 37 min of work. Continues 00:04→04:15 (V9 arc).

- **`8e7e1e9` MEASUREMENT-DISCIPLINE 21:38** before W1 verdict 21:54 — doctrine mid-investigation (cat. 3).
- AI review **peak day** (#88–#91, review loop).

### Phase 9 — V9 / V10-b (08-22)

| category | Mitch's recall / git |
|---|---|
| Gate 1 soak | **Fully unattended** — `7b8032c`; mechanism 5 narrative if header-only failure undetected |
| Retractions (cat. 2) | **11:00–12:30** — V10-b, census parser |

### Phase 11 — V11 (in flight)

| category | Mitch's recall / git |
|---|---|
| Hours | **~2 h** — harness repair dominates |

---

## Routine / excluded (G5)

| phase | tag |
|---|---|
| 1 | ~20% routine |
| 2 | Mostly investigation; browse/carousel exclude |
| 3–4 | Admissibility review pending |
| 5 | Eligible — explicit investigation row |
| 6–8 | Admissibility review pending |

---

## Totals

*Open — ranges preserved; session model supersedes calendar-day shaping for labour.*

| bucket | range | confidence |
|---|---|---|
| Phase 1 build-out | 40–60 h | medium |
| Phase 2 investigation (sessions) | ~12–16 h (4 sessions × 3–4 h) | medium — **confirm session count / per-session hours** |
| Phase 2 instrument-building (08-17→19) | **10+ h** | medium — Mitch floor |
| 08-21→22 push (Ph.6–11) | **18–23 h** | medium |
| Phases 3–4 (daily rates) | not rolled up | |

---

## Open questions

- Phase 1: 08-06/07 off or partial?
- Phase 2: Confirm **12–16 h** from 4 sessions × 3–4 h — or revise per-session recall
- Phase 2 instrument-building: Mitch's **10+ h floor** — upper bound / split across P2-S4 vs 08-19?
- `26f940c`: confirm x86 probe never measured on Pi

---

## Ongoing capture

**Implemented 2026-08-22.** Append per **session** to [`SRED-DAILY-LOG.md`](SRED-DAILY-LOG.md). Skill: [`.claude/skills/sred-daily-capture/SKILL.md`](../.claude/skills/sred-daily-capture/SKILL.md). CLI: `scripts/sred-log-append.sh`. See `AGENTS.md` §SR&ED daily labour capture.

---

*Last updated: 2026-08-22 (America/Toronto) — session model; Phase 2 span + instrument evidence; push 18–23 h*
