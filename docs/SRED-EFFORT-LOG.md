# SR&ED effort log — MPE synth appliance (reconstruction)

**Cross-link:** [`SRED-EVIDENCE-2026.md`](SRED-EVIDENCE-2026.md) §7 G1 · phases in §4.

## Method

Mitch's recall, anchored to `git log --all` (765 commits, 2026-07-18 → 2026-08-22). Reconstructed interactively on **2026-08-22 (America/Toronto)**. **This is not a contemporaneous timesheet.** Hours are ranges with stated confidence where recall is weak; figures Mitch did not state are not recorded.

Commit data: local repo, author dates as committed. Invisible-work categories per [`PROMPT-G1-effort-reconstruction.md`](measurements/PROMPT-G1-effort-reconstruction.md).

**Standing caveat (Mitch, 2026-08-22):** Commit timestamps record when work was **written down**, not when it was done — on this project the two often diverge by hours. Every phase estimate anchored to commit windows is a **lower bound**, not a ceiling. Honest posture: protects the document without inflating numbers.

---

## Instrumentation work — three categories (SR&ED material)

Separate from "running measurements." Commits **undercount** category 1 (a C instrument that took a day often appears as one commit).

| # | category | what it is | evidence |
|---|---|---|---|
| **1** | **Building instruments** | Native probes, harnesses, parsers | `mpe-peak-meter` (C), `mpe-xrun-probe` (C), `measure-latency-run.sh`, census parser |
| **2** | **Discovering they were wrong** | Nine Rule −1 occurrences; diagnoses; retracted conclusions | [`MEASUREMENT-DISCIPLINE.md`](measurements/MEASUREMENT-DISCIPLINE.md) table; V8-b auto-pick; V10-b ramp; census `unison_voices` |
| **3** | **Deriving the general rule** | Structural cause → enforced mechanism | Rule −1, Rule 0.5, C0 conformance suite (positive/negative/physics controls, plausibility floors). **Strongest claim material:** stated problem → nine instances → general principle → implemented + reviewed solution. |

---

## Per-phase estimates

| phase (§4) | dates (git / Mitch) | commits | hours (range) | instrument-building (range) | confidence | notes |
|---|---|---:|---|---|---|---|
| 1. Build-out | 07-18 → ongoing | ~200 (07-18→08-09) + continuing | **40–60** | — | medium | Continues through today. ~**20% routine**. |
| 2. First fault isolation | **08-12 → 08-18** | ~228 (incl. 08-12–13) | **3–4 h/day on days worked** | **Major — TBD h** (see §Inst. cat. 1) | medium | **Self-inflicted fault:** `jack_lsp` introduced 08-12/13, hunted 08-14+, resolved 08-18. Governor + looper threads per Mitch. Browse/carousel = polish (exclude). Heavy AI review. |
| 3. Looper cost | 08-18 → 08-19 | ~50 themed | **~4.5–6 h/day** | **Major — TBD h** (harness + probe land 08-19) | medium | Step 0 harness, xrun-probe, tap v1 diagnosis (`c33b52e`). G5: admissibility pending. |
| 4. Jitter hunt (refuted) | 08-19 → 08-21 | 39+5+part of 71 | **~4.5–6 h/day** | Low–med (mostly using instruments) | medium | Scarlett ~30 min. Blind-instrument drag 08-20/21; days not lost, less effective. |
| 5. E1 (refuted) | 08-20 12:52–14:12 | 5 | **1–2** | — | medium | **Separate row.** 68 min monitored run. Self-correction documented unprompted. `#86`. |
| 6. W1 | **08-21→22 push** | part of 71 | **(push — see below)** | — | medium | U1 resolved. Start of continuous push 13:16. |
| 7. V0/V1/V2 | **08-21→22 push** | part of 71 | **(push — see below)** | — | medium | U2 resolved. |
| 8. V7/V8 + census | **08-21→22 push** | part of 71+50 | **(push — see below)** | Med | medium | V8-b 00:04; V9 arc through 04:15. |
| 9. V9 / V10-b | **08-22** (push cont.) | 50+ | **(within 08-22 ~13 h)** | Med (cat. 2) | medium | V10-b + census retractions **11:00–12:30**. Soak **fully unattended** (`7b8032c`). |
| 10. Ceiling / P7–P8 | **08-22** | docs | **(within 07:56–12:37 block)** | — | medium | CEILING + MULTITHREADING merged `2b78339` 12:37; interleaved with V10-b. |
| 11. V11 | **08-22 pm** | 2+ | **~2** | Med (cat. 1–2) | medium | Plan `5c18da8` 13:01 → run 15:07; harness repair dominates. **Ongoing** at log time. |

### Continuous push — 08-21 13:16 → 08-22 18:06+ *(one session, calendar split)*

**Do not shape as two independent days.** 08-21 runs 13:16→22:59, then straight into 08-22 00:04→04:15 before sleep. Record as **~30 h wall clock, one sleep break**; calendar boundary falls mid-session.

| segment | span | hands-on (Mitch) | work |
|---|---|---|---|
| **08-21 pm → 08-22 pre-dawn** | 13:16 → 04:15 | *(part of push)* | Ph.6–8 (W1, V0/V1/V2, V7/V8); V8-b fix; V9 a/b/c/d |
| **sleep** | 04:15 → 07:56 | — | 3 h 41 |
| **08-22 morning** | 07:56 → 15:24 | **~7.5 h** | V10-b, soak start, P7/P8 prompts, census fix, PROGRESS, SR&ED doc, Pi 5 plans, Rule −1 |
| **08-22 afternoon** | 16:40 → 18:06+ | **~1.5 h+** | G3 archive, C0 (#96/#97), C0 review, Rule 0.5, mechanism 5 |

**08-22 calendar day total: ~13 h hands-on** — largest day in the project (not 8–10 h). Early block 00:04–04:15 (~4 h) belongs to this push, not a separate "08-21 day."

---

## Non-commit work (by phase)

### Phase 1 — Build-out

| category | Mitch's recall |
|---|---|
| Physical bench | A couple of hours |
| Listening / live playing | Yes — live playing sessions |
| **08-06 / 08-07** | Unsure — could have been mostly off |

### Phase 2 — First fault isolation (08-12 → 08-18)

| category | Mitch's recall / git |
|---|---|
| Physical bench | Screen setup, GPIO power — couple of hours |
| Listening / live playing | Not much on crackle; **lots** testing 1024/512 feasibility live |
| AI-assisted review | **A lot this week** |
| Instrument building (cat. 1) | **Major component — not "some."** Two native C instruments in ~48 h; harness foundation. Commits undercount (see git sequence). |

**SR&ED narrative:** The defect was **introduced by the same work that later hunted it** — `jack_lsp` as graph owner (08-12/13) → crackle diagnostic (08-14) → resolution (08-18). Self-inflicted fault found by systematic investigation.

**Mitch's narrative:** Crackle predates window; voice governor attempted fix; looper built for compute path; looper pushed latency → deeper investigation.

**Git sequence (crackle + instruments):**

| date | commit | event |
|---|---|---|
| 08-12 | `bb9dea8` | Phase 1 JACK engine — **jack_lsp enters codebase** |
| 08-13 | `fb7a48c` | `jack_lsp` as graph owner under sudo — **fault introduced** |
| 08-14 | `73d2d67` | Crackle diagnostic script |
| 08-17 | `0dc9e5b` | Python meter stalling jackd RT cycle — **observer effect** |
| 08-18 | `dd130a5` | **Resolution:** crackle was the graph probe |
| 08-18 | `2607286` | **`mpe-peak-meter`** compiled C — rewrite to stop perturbing what it measures |
| 08-19 | `e2ae996` | **`measure-latency-run.sh`** + cyclictest floor — harness everything since depends on |
| 08-19 | `d6d0fa4` | **`mpe-xrun-probe`** C, µs delays |
| 08-19 | `c33b52e` | Latency tap v1 fix — criterion 42, OSC client not `_send` (**Rule −1 #3**) |

### Phase 3 — Looper cost (08-18 → 08-19)

| category | Mitch's recall |
|---|---|
| Listening | ~3 h Tue 08-19 extended live-load test |
| Soaks | ~20% monitoring average |
| AI review | More than Phase 2 |
| Instrument (cat. 1–2) | Harness + probe land here; tap failures diagnosed in commits |

### Phase 4 — Jitter hunt (refuted)

| category | Mitch's recall |
|---|---|
| Scarlett swap | ~30 min |
| Soaks | ~20% monitoring |
| Reading / interpretation | Some — cyclictest, cushion model, Steps 0–4 |
| AI review | ~same as Phase 3 |
| Instrument (cat. 2) | Less effective results 08-20/21; not days lost |

### Phase 5 — E1 (refuted)

See table row. `26f940c`: x86 probe binary removed — **repo hygiene** (fail-loud), not Rule −1 unless confirmed run on Pi.

### Phases 6–8 — start of 08-21→22 push

Evening 08-21 (13:16→22:59). End burst 21:32–22:09 ≠ 37 min of work. Continues 00:04→04:15 (V9 arc).

- **`8e7e1e9` MEASUREMENT-DISCIPLINE 21:38** before W1 verdict 21:54 — doctrine mid-investigation (cat. 3).
- Peak-meter shutdown ~18:34 **before** W1; V8-b auto-pick **08-22 00:04**.
- AI review **peak day** (#88–#91, review loop).

### Phase 9 — V9 / V10-b (08-22)

| category | Mitch's recall / git |
|---|---|
| Hours | Within **08-22 ~13 h** block (see push table) |
| Gate 1 soak | **Fully unattended** — `7b8032c` 11:06 "soak started"; commits continue immediately at 11:11, 11:14… Started soak → back to other work. **Not a lapse:** normal for 8 h run; header-only failure undetected 5 h → **mechanism 5** cause→effect pair. |
| Retractions (cat. 2) | **11:00–12:30** — V10-b fixes 11:00/11:03, validate PASS 11:06; census handover `ac110cb` 12:14; parser `e8b91ed` 12:25. High interpretation density (XML vs three patches); low commit count. |
| AI review | Concentrated same window |

### Phase 10 — Ceiling / multithreading analysis

Written on `docs/v9-review`; merged **`2b78339` 12:37**. Sits in **07:56–12:37** block, interleaved with V10-b and P7/P8 prompts. Analysis hours not separately quantified — embedded in morning block.

### Phase 11 — V11 (in flight)

| category | Mitch's recall / git |
|---|---|
| Hours | **~2 h** — plan `5c18da8` 13:01; harness `6e98af0`/`1cae0fb` 14:41–14:42; run 14:42→15:07 (~25 min of wall time) |
| Instrument (cat. 1) | Peak meter blind graph recovery — most of the 2 h is harness repair, not the run |

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

*Open — ranges preserved; instrument-building column incomplete (TBD where marked).*

---

## Open questions

- Phase 1: 08-06/07 off or partial?
- Phase 2: Day count for 3–4 h/day total; **instrument-building hours** for 08-12→19 block
- Continuous push: total hands-on across full ~30 h wall clock (only 08-22 ~13 h quantified so far)
- `26f940c`: confirm x86 probe never measured on Pi

---

## Ongoing capture (proposal)

One dated line per work session: calendar date, phase, **measurement h**, **instrument-building h**, **review h**, one-line note. Same habit as recording measurement conditions. Prevents reconstructing G1 from memory again.

---

*Last updated: 2026-08-22 (America/Toronto) — Phases 9–11 + continuous push*
