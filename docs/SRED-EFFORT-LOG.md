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
| **Defensible to a reviewer** | "You worked 08-17 and 08-18?" → answer in **sessions**, not two independent calendar days |
| **Stops boundary double-count** | 08-14 ends 21:49, 08-15 starts 03:00 — not two full days of labour |
| **Supporting evidence** | Calendar-day commit tables remain below for git anchoring |

**Do not estimate hands-on from session wall time.** A "session" can span 25 h wall clock with multi-hour gaps inside (Phase 2 P2-S4a). Wall time **overstates** as badly as calendar days **understated** overnight pushes. Sessions exist only to **stop double-counting midnight boundaries**; **hours come from recall** (e.g. 3–4 h per session worked), not from subtracting gap timestamps.

Six of seven Phase 2 calendar days start before 06:00 or end after 23:00 — calendar shape alone misstates labour. Phase 2 work in 08-16→18 was **fragmented bursts** with 2–4 h gaps between them, not sustained marathons.

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

| artifact | date | phase / session | git | note |
|---|---|---|---|---|
| Python→C driver (RT stall diagnosis) | 08-17 | **P2-S4c** | `0dc9e5b` | Python stalling jackd's RT cycle — observer effect |
| **`mpe-peak-meter.c`** + Makefile + build script | 08-18 | **P2-S4d** | `2607286` | 379 lines C, RT-safe JACK client — rewrite forced by measured observer effect (**design work**, not transcription) |
| **`mpe-xrun-probe.c`** + jitter histogram | 08-19 | **Ph. 3/4** | `d6d0fa4`, `63aa2b7` | 213 lines C, µs xrun delays |
| Latency taps v1/v2 + **`measure-latency-run.sh`** | 08-19 | **Ph. 3/4** | `c33b52e`, `e2ae996` | Tap OSC client not `_send`; harness everything since depends on |
| **Native tree** | 08-17+ | — | 11 commits `native/` | Plus two Makefiles and two build scripts |
| **Total RT-safe C on target** | — | — | **~592 lines** | Debugged on a live Pi audio graph |

**Instrument-building estimate (08-17 → 08-19):** **10–16 h, medium confidence.**

| split | range | artifacts |
|---|---|---|
| **Phase 2** (peak meter, Python→C) | **~5–8 h** | `0dc9e5b`, `2607286` — roughly **half** |
| **Phase 3/4** (08-19) | **~5–8 h** | probe, harness, taps — roughly **half** |

Mitch floor: *would be surprised if under 10 h total.* Upper bound uncertain (C velocity unknown); both programs are RT-safe JACK clients debugged on target. **Do not fold into measurement hours.**

---

## Per-phase estimates

| phase (§4) | span (sessions / git) | commits | hands-on (range) | instrument-building (range) | confidence | notes |
|---|---|---:|---|---|---|---|
| 1. Build-out | 07-18 → ongoing | ~200 (07-18→08-09) + continuing | **40–60** | — | medium | Continues through today. ~**20% routine**. **08-06/07 unresolved** — possibly days off (see §Open). |
| 2. First fault isolation | **08-12 → 08-18** (7 cal. days, **7 sessions**) | **296** | **20–28 h** (7 × 3–4 h/session) | **~5–8 h** (inst.; see §Inst.) | medium | **`jack_lsp` enters 08-12.** Fragmented bursts 08-16→18, not one marathon. Self-inflicted fault; heavy AI review. |
| 3. Looper cost | 08-18 → 08-19 | ~50 themed | **~4.5–6 h/day** | **~5–8 h** (08-19 inst.; see §Inst.) | medium | xrun-probe, harness, tap v1 land 08-19. G5: admissibility pending. |
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
| 08-13 23:39 → 08-14 10:31 (~11 h) | **New session** (P2-S2) |
| 08-14 21:49 → 08-15 03:00 (~5 h) | **New session** (P2-S3) |
| 08-16 23:55 → 08-17 00:13 (18 min) | **Same session** (within P2-S4a wall span) |
| 08-17 00:49 → 10:11 (~9 h) | **New session** — sleep; ends P2-S4a, starts P2-S4b |

### Sessions (primary boundary unit — **7 sessions**)

| session | span | wall | notes |
|---|---|---:|---|
| **P2-S1** | 08-12 00:32 → 08-13 23:39 | ~47 h | 65 commits. `jack_lsp` enters; fault introduced 08-13 |
| **P2-S2** | 08-14 10:31 → 21:49 | ~11 h | 45 commits. Crackle diagnostic lands |
| **P2-S3** | 08-15 03:00 → 23:08 | ~20 h | 53 commits |
| **P2-S4a** | 08-16 00:02 → 08-17 00:49 | ~25 h | Part of 08-16→18 block; **not** one marathon — see gaps below |
| **P2-S4b** | 08-17 10:11 → 12:06 | ~2 h | After ~9 h sleep gap |
| **P2-S4c** | 08-17 16:35 → 08-18 06:13 | ~14 h | `0dc9e5b` Python RT stall |
| **P2-S4d** | 08-18 10:33 → 21:13 | ~11 h | Resolution `dd130a5`; **`mpe-peak-meter`** `2607286` |

*P2-S4 is four sessions (S4a–d), not one overnight block. Short gaps (<4 h) inside S4a do not split further at this reconstruction pass.*

**Internal gaps ≥2 h inside 08-16 → 08-18** (why S4a is not a marathon):

| gap | from → to |
|---|---|
| 3 h 16 | 08-16 05:40 → 08:56 |
| 2 h 05 | 08-16 11:33 → 13:38 |
| 3 h 42 | 08-16 13:38 → 17:20 |
| **9 h 22** | **08-17 00:49 → 10:11 (sleep)** |
| 4 h 29 | 08-17 12:06 → 16:35 |
| 3 h 50 | 08-17 19:04 → 22:54 |
| 3 h 14 | 08-18 00:52 → 04:06 |
| 4 h 20 | 08-18 06:13 → 10:33 |

**Hands-on (Mitch):** **3–4 h per session worked** — count **sessions**, not calendar days; **do not** derive from wall spans above. **Seven sessions → 20–28 h**, medium confidence.

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
| **08-06 / 08-07** | **Unresolved — possibly days off** (low confidence; not filled in) |

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

See table row. Instrument diligence for the 08-19 probe window: §Instrument diligence below.

### Instrument diligence — 08-19 probe window (not Rule −1 #11)

#### `26f940c` — x86 ELF in repo (**closed**)

**Not occurrence eleven.** No Pi confirmation needed.

| point | detail |
|---|---|
| **Failure class** | **Fail-loud** — x86-64 ELF on aarch64 cannot execute; garbled error, not a plausible number. Rule −1 is instruments that return a **believable wrong reading**; this returns nothing. |
| **Timeline** | ARM probe tracked **08-19 18:46** (`d6d0fa4`, source + local build) → **08-20 12:59** removal (~18 h). Same window produced real data: `713cc9c` (delay stats), `63aa2b7` (jitter histogram), `fcf8507` (Task B baseline at 512). x86 artifact sat in git **without overwriting** the working ARM build. |
| **Verdict** | Deployment hazard caught before it bit — **diligence line**, not a failure. Repo hygiene removal. |

#### `223f31d` — accumulation verdict withdrawn (**reasoning correction**, not Rule −1)

**08-19 20:33** — withdraw Step 1b accumulation verdict; re-gate Step 2 on jitter.

Archive [`low-latency-step0-step1-2026-08-19.md`](measurements/archive/low-latency-step0-step1-2026-08-19.md): trend **did not replicate** (Step 1 ρ=0.90 p=0.042 → Step 1b block 1 ρ=0.50 p=0.225); restart effect **indistinguishable from noise** (run 6 post-restart z=−0.75). Conclusion withdrawn on **arithmetic**, not because an instrument lied. Pattern: **inference promoted to premise** — same family as other retracted conclusions, not blind-instrument Rule −1.

#### `74faa00` — wait for PROBE_END (**development catch**, same family as V11)

**08-19 21:44** — read jitter stats only after `PROBE_END` between runs. Same family as V11's DSP-window misalignment (read before instrument finished writing).

Post-fix Step B in the same archive doc (`74faa00`, 08-20): jitter p99 CV **~0.02** vs xrun count CV **~0.41–0.53** on identical runs — stable readings after fix. Probe lifecycle bug separately fixed in `713cc9c` (`pkill -x mpe-xrun-probe` per window). **Caught during development**; no evidence a believed-wrong jitter number shipped as a verdict.

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
| Phase 2 investigation | **20–28 h** (7 sessions × 3–4 h) | medium |
| Instrument-building (08-17→19 total) | **10–16 h** | medium |
| — Phase 2 share | ~5–8 h | medium |
| — Phase 3/4 share (08-19) | ~5–8 h | medium |
| 08-21→22 push (Ph.6–11) | **18–23 h** | medium — **confirmed** |
| Phases 3–4 (daily rates, excl. inst.) | not rolled up | |

---

## Open questions

- Phase 1: **08-06/07 unresolved — possibly days off** (low confidence). Left open intentionally; honest gap reads better than a filled one.

---

## Ongoing capture (G1 closed)

**G1 reconstruction complete 2026-08-22** — this file is historical baseline only. **Do not backfill again.**

Append per **session** to [`SRED-DAILY-LOG.md`](SRED-DAILY-LOG.md). Skill: [`.claude/skills/sred-daily-capture/SKILL.md`](../.claude/skills/sred-daily-capture/SKILL.md). CLI: `scripts/sred-log-append.sh`. See `AGENTS.md` §SR&ED daily labour capture.

---

*Last updated: 2026-08-22 (America/Toronto) — G1 closed; 26f940c diligence; 08-19 probe window triaged*
