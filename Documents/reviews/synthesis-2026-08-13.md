# MPE-Module Codebase Review Synthesis

*Synthesis date: 2026-08-13 (America/Toronto)*  
*Branch reviewed: `yolo/jack-drop-alsa-fallback` @ `1ab9f55`*  
*Sources: four artifacts from the 2026-08-13 dual-model review + dual-audit pass*

---

## 1. Executive Summary

The merged Phase 1 JACK audio engine on `yolo/jack-drop-alsa-fallback` is **architecturally coherent and remarkably clean for a post-merge tree**. Both independent reviews searched for the usual wreckage — surviving ALSA branches, duplicate engine paths, merge conflict markers, orphaned state machines — and found almost none. The D2 inventory ("restart jackd, not Surge on device-changing paths") is honoured in code; `degraded` is retired in both bash and Python with a stale-state migration test; 440 tests pass in ~42 s; security on the appliance surface is clean. Whatever felt "twisted up" in the merge lives in branch topology and forward planning, not in the tree itself.

The central risk is **verification debt, not architecture**. The 2026-08-13 amendment reversed Phase 1's second-highest goal — "the instrument never boots silent" became "the instrument is silent and says so" — which deleted the exact fallback arm that Gate B spent a day proving. Five Gate C soak scenarios (`jack-audio-engine-spec.md:452-459`) that would validate the replacement behaviour have **not run**, and the spec blocks merge in bold. The instrument's behaviour on its most important failure path is currently a hypothesis defended by unit tests and prose, including one test that re-implements the script it claims to cover (`tests/test_audio_engine.py:486-504`). Two real bugs compound this: a udev `remove`-event blind spot in `config/99-usb-audio.rules` (Opus review + audit) and a probable jackd duplex-open regression against the `usb-host-session` mic bridge (Kimi review + audit). Both are amendment side-effects on code that was correct the day before.

**Headline verdict on phase-one state vs plan:** Phase 1 **landed in code** and matches the spec's phase structure as amended — criteria 2a/2c/2d/3 are cleanly retired, criterion 2* is wired end-to-end, D6 buffer split is implemented on the bash side, and the supervisor cooldown math is correct. Phase 1 is **not verified** for the amendment's new failure semantics, and two falsification-table assumptions (5b UAC2 capture, session-capture half of 14) remain open while Phase 2 planning proceeds. The "sound module" half of the product is on a credible, measured path; the "analog-esque mixer" half has no spec in the repository and is a product conversation, not a branch defect. **Do not merge to `dev` without Gate C soak; fix udev and watchdog issues before soaking, not after.**

---

## 2. High-Confidence Findings (Both Reviews + Both Audits Agree)

These items appear in both reviews and were confirmed by both audits with ✅ verdicts (or documentary ✅ where hardware-only).

- **Merge cleanliness — no structural damage from Phase 1 + amendment**
  - No surviving ALSA fallback branch; `MPE_AUDIO_ENGINE` read nowhere in consumers (`NoAlsaPathTests`, grep-verified).
  - No merge conflict markers; no duplicate engine paths; `VALID_ENGINE_STATES = {"ok", "recovering", "failed"}` in both languages.
  - D2 call-site inventory honoured: `set-audio-profile.sh:57`, `set-surge-audio.sh:129`, `uac2-stall-watchdog.sh:67`, `99-usb-audio.rules:26-27` all route through graph restart.
  - Evidence: both reviews §First Impressions / §Spec Conformance; Kimi audit claims 52, 50; Opus audit G3, G5.

- **Gate C soak is the merge blocker — hard-failure path never exercised on hardware**
  - Spec states **REQUIRES PI SOAK BEFORE MERGE** (`jack-audio-engine-spec.md:102-108`); five scenarios listed at `:452-459`, zero run.
  - Gate B PASS rows for 2a, 2d, 3, and half of 2c tested code paths that no longer exist.
  - Criterion 2* (`start-surge-cli.sh:147-154` → `state=failed`, promote on unmask) is landed in code only.
  - Both audits rate this Critical/High and agree: green unit tests are not hardware evidence.
  - Evidence: Opus 4.2, A4; Kimi Spec Conformance row 2*; both audit P0 rows.

- **Phase 2 looper spec is untracked and stale**
  - `git status` → `?? Documents/specs/looper-jack-client-spec.md` (869 lines, ~53 KB).
  - Stale against 2026-08-13 amendment: §D.5 still scopes `MPE_AUDIO_ENGINE=alsa`; criterion 11 references unset `MPE_AUDIO_ENGINE=jack`; §D.5.2 quotes old guard message; §D.5.3 analyzes removed `resolve_audio_engine` parameter.
  - Opus audit MISSED-5: committing verbatim publishes the newest-looking stalest document.
  - Evidence: Opus 4.1, A1; Kimi Hall of Shame #4, audit claims 15–19; Opus audit 4.1 + MISSED-5.

- **440 tests pass; suite is a genuine gate**
  - `mpe test local all` → 440 tests, 0 failures, ~42 s (reproduced by both audits).
  - `test_audio_engine.py` (704 lines) is the model file: bash parity, `NoAlsaPathTests`, unit-content assertions.
  - Evidence: Opus A5; Kimi audit claim 36; Opus audit A5, A7.

- **Supervisor cooldown design is correct and parity-pinned**
  - `mpe_engine_reconcile_decision()` (`audio-engine.sh:369-392`) ↔ `reconcile_cooldown_decide()` (`audio_engine.py:52-76`); `BashReconcileParityTests` runs both.
  - tmpfs state rationale for `BindsTo` hazard is implemented as spec'd.
  - Evidence: both reviews §Architecture; Opus audit G1, G2; Kimi audit claims 30–31, 38.

- **Branch identity**
  - `yolo/jack-drop-alsa-fallback` @ `1ab9f55` = `dev` @ `daac891` + 1 commit (ALSA removal amendment).
  - Evidence: Opus audit A2; Kimi audit (same commit verified).

---

## 3. Unique Findings by Source

### Opus Grumpy Review — findings Kimi did not raise

| Finding | Audit status | Notes |
|---------|--------------|-------|
| **4.3 — udev `remove` blind spot** (`99-usb-audio.rules:17-27`): `ATTR{id}` cannot match on `remove`; all four skip guards leak; `modprobe -r snd_aloop` in `calibration_teardown.py:35` restarts graph after every calibration | ✅ verified (Opus audit B1–B3) | Kimi audit did not evaluate udev rules |
| **4.4 — Watchdog discards Surge user defaults** (`surge-watchdog.sh:97-105`): post-amendment, routine jackd outage → `is-failed` → `.corrupted_*` misfiling | ✅ verified (Opus audit C1–C6); ⚠️ amplified by MISSED-2 (repeats per cycle) | Unique high-value catch |
| **4.5 — Test re-implements `start-surge-cli.sh` failure branch** (`test_audio_engine.py:486-504`) | ✅ verified (Opus audit D1) | Opus audit re-rates 🟡 → **High** (automated half of 4.2) |
| **4.8 — 5 Hz SD-card poller** (`looper_clock_monitor.py:10`, `$HOME/.mpe_midi_clock_state.json`) | ✅ verified (Opus audit F2) | Kimi noted 0.5 s engine monitor only |
| **4.9 — CI weaker than local** (no shellcheck, hardcoded shell tests, `requirements.txt` unused) | ✅ verified (Opus audit D4–D6) | |
| **4.10 — Calibration teardown / jackd race** (`calibration_teardown.py:67-72`) | ✅ verified; meaningful only composed with 4.3 | |
| **MISSED-1 — UI Surge restart bypasses cooldown** (`surge_monitor.py:167`, touch + OLED) | ✅ verified (Opus audit) | Not in Opus review; found by Opus audit only |
| **MISSED-3 — Unbounded `$HOME/surge-watchdog.log` on SD card** | ✅ verified (Opus audit) | |
| **🔴 Mixer has no spec** (20-line `MixerChannel` dataclass vs product goal) | ⚠️ partial (Opus audit G6): code facts ✅; phrase "analog-esque mixer" **not in repo** | Product question, not code defect |

### Kimi Grumpy Review — findings Opus did not raise

| Finding | Audit status | Notes |
|---------|--------------|-------|
| **🔴#1 — jackd duplex open breaks mic bridge** (`start-jackd.sh:43-44` lacks ALSA `-P` after `-d alsa`; `mic-to-uac2-bridge.sh:54` + `session_capture.py:66-89` EBUSY) | ✅ verified (Kimi audit A1, B3–B5) | Kimi's best unique catch; Phase 1 *introduced* capture-side hold |
| **🟡#2 — Criterion 16 UI drift** (`surge_audio.py:16-19`, `configure-pi-paths.sh:70-74`, touch shows "1024 · 21 ms" while graph runs 256×3 = 16 ms) | ✅ verified (Kimi audit C6–C10, C12) | Gate B logged 12 drift failures, dismissed pre-existing |
| **🟡#3 — Watchdog budget blowout** (15 iterations × `timeout 3 jack_lsp` + sleep ⇒ ~60 s vs 15 s nominal) | ✅ verified (Kimi audit D13–D14); M1 adds unguarded `jack_lsp` at `audio-engine.sh:432` | |
| **🟡#5 — Triple looper predicate** (`engine-guard.sh`, `audio-engine.sh:189-195`, `audio_engine.py:36-38`) | ✅ verified (Kimi audit F21); audit deflates 🟡 → Low | |
| **Branch topology** (`main` 42 behind `dev`; `yolo/looper-phase0` 76 ahead) | ⚠️ partial: counts ✅; "1 behind" ❌ (actual 32 behind, Kimi audit claim 34) | |
| **96 kHz enum drift** (`set-surge-audio.sh` vs `mpe_jack_rate()`) | ✅ verified (Kimi audit H27) | |
| **`--list-devices` invoked 3× per boot** | ⚠️ partial (Kimi audit claim 49): 2 in `start-surge-cli.sh`, 3rd in `detect-audio-device.sh` via different unit | |

### Audit-only additions (neither review's headline list)

| Finding | Source audit | Status |
|---------|--------------|--------|
| Opus audit MISSED-2: 4.4 repeats `.corrupted_*` per supervisor cycle | Opus audit | ✅ |
| Opus audit MISSED-6: read-modify-write race on `engine.state`; unbounded RT priority validator | Opus audit | ✅ (Low) |
| Kimi audit M2: optimistic `state=ok` publish before Surge liveness check | Kimi audit | ✅ (Low) |
| Kimi audit M3: mic bridge wedges in `failed`, not infinite loop | Kimi audit | ✅ (sharpens 🔴#1) |

---

## 4. Corrections — Do Not Implement As Originally Written

### Udev `ENV{}` fix (Opus review 4.3 / backlog item 3)

**Do not** implement "filter on kernel-supplied `ENV{}` properties" as stated. Opus audit claim B5: `ENV{}` survives `remove`, but **no kernel property carries the ALSA card `id`** — option 1 would need a new `add`-time rule to stamp `ENV{MPE_SKIP_CARD}=1` first. Writing four `ENV{}` rules that match nothing is strictly worse than today.

**Correct fix:** move card-identity check into `restart-audio-graph.sh` against `/proc/asound/cards` — one filter, testable from a laptop. Opus audit and Kimi audit both endorse this path.

### Criterion 16 — do not delete `MPE_SURGE_BUFFER_SIZE` seed (Kimi review Hall of Shame #2 prescription)

**Do not** stop seeding `MPE_SURGE_BUFFER_SIZE` in `configure-pi-paths.sh:70-74`. Kimi audit claim 11 ❌: the key is retired as the **graph period** but remains live for `calibrate-patch-normalization.py:422,457`, `midi_sync.py:58`, and `MPE_MIDI_OUTPUT_OFFSET_AUTO` (`mpe.env.example:142`). Removing the seed forces hardcoded 1024 fallbacks and contradicts the shipped env contract.

**Correct fix:** repoint `surge_audio.py` read-back and latency labels at `MPE_JACK_BUFFER` / `MPE_JACK_PERIODS` (multiply by periods in the ms calculation); keep seeding the Surge key with a re-commented purpose; add tests for JACK enum validators. Dual-write on `set-surge-audio.sh --buffer` is debatable (spec-pure vs one-knob UX) — do not delete without deciding.

### Soak sequencing (Opus review priority backlog vs Opus audit disagreement #5)

**Do not** run Gate C first, then fix udev and watchdog. Opus audit: fix **4.3 and 4.4 before Gate C** — both are cheap, both sit on failure paths Gate C exercises, and soaking a branch you are about to change means either soaking twice or shipping a soak log that does not describe what merges. Spec commit (5 seconds) can happen anytime; hardware soak should be last among P0s.

**Recommended P0 order:** (1) udev fix in `restart-audio-graph.sh`, (2) gate watchdog corrupt-defaults arm + move `mv` after cooldown decision, (3) optional: route UI Surge restart through supervisor (MISSED-1), (4) **then** Gate C soak, (5) merge.

### Mixer as product decision, not code defect (Opus audit disagreement #1)

**Do not** treat "write a spec for the analog-esque mixer" as 🔴 alongside unsoaked failure paths. Opus audit G6: the phrase does not appear in the repository (`README.md` / `AGENTS.md` say "MPE sound module"). The observation about naming collision with per-patch Vol/Tail/Touch faders is valid; the 🔴 severity is inflated. File as an **open product question for Mitch** — decide whether mixer rides on the JACK graph (Phase 2 dependency) or is a separate surface — not as a branch-blocking engineering defect.

---

## 5. Prioritized Action Plan

Deduplicated P0/P1 from both audits. Effort labels from audit matrices.

### Quick fixes (laptop)

1. **P0 — Udev remove blind spot:** card-identity check in `restart-audio-graph.sh` via `/proc/asound/cards` (not raw `ENV{}`). *(Opus 4.3; Opus audit P0; half-day → quick fix)*
2. **P0 — Watchdog corrupt-defaults arm:** gate on `mpe_engine_state_get state`/`reason` (`no-server`, `no-jack-device`); move `mv` **after** cooldown decision *(Opus 4.4 + MISSED-2; Opus audit P0; quick fix)*
3. **P1 — Commit + amend looper spec:** `git add Documents/specs/looper-jack-client-spec.md` **and** fix §D.5, criterion 11, D.5.2, D.5.3 in same commit *(Opus 4.1 + Kimi #4 + both audits P1; quick fix)*
4. **P1 — jackd playback-only:** add ALSA `-P` after `-d alsa` in `start-jackd.sh:43-44` *(Kimi 🔴#1; Kimi audit P0 code fix — Pi verify at rewire)*
5. **P1 — Criterion 16 UI drift:** repoint `surge_audio.py` labels; keep `MPE_SURGE_BUFFER_SIZE` seed; add JACK validator tests *(Kimi #2; Kimi audit P1; half-day)*
6. **P1 — Watchdog probes:** probe once per reconcile pass; wall-clock budget; wrap bare `jack_lsp` at `audio-engine.sh:432` in `timeout 3` *(Kimi #3 + M1; Kimi audit P1; quick fix)*
7. **P1 — Make criterion 2* testable:** extract failure-path sequence to sourceable function in `audio-engine.sh`; test real code, not copy *(Opus 4.5; Opus audit P1; half-day — before Gate C)*
8. **P1 — CI hardening:** shellcheck on `scripts/` + `config/*.service`; glob shell tests; install from `requirements.txt` *(Opus 4.9; Opus audit P1; half-day)*
9. **P1 — UI Surge restart through cooldown** or disable when `engine.state=failed` + known reason *(Opus audit MISSED-1; P1; half-day)*
10. **P2 — Move MIDI clock state to `/run/mpe/`; poll 0.5 s** *(Opus 4.8; quick fix)*
11. **P2 — Bound `$HOME/surge-watchdog.log`** *(Opus audit MISSED-3; quick fix)*
12. **P2 — Calibration teardown:** explicit `systemctl start mpe-jackd` + readiness wait after 4.3 fix *(Opus 4.10; quick fix)*
13. **P2 — Comment hygiene, CHANGELOG 438→440, README Pi 4B/5 disambiguation** *(Kimi #6; Kimi audit P2)*

### Gate C soak (Pi — Mitch + hardware)

14. **P0 — Run five Gate C scenarios** before merge (`jack-audio-engine-spec.md:452-459`):
    - 2*: masked jackd boot → `state=failed` → unmask promotes
    - 2b/2b2 at 5 s settle (retest)
    - 15: DAC replug from `state=failed`
    - 12: stale `MPE_AUDIO_ENGINE=alsa` line inert
    - D5 guard boot with `MPE_LOOPER_ENABLED=1`
    - *(Both reviews 🔴; both audits P0; ~half-day Pi bench)*

15. **P0 — Run blocked 5b/14 soak rows** after jackd `-P` fix and physical rewire *(Kimi 🔴#1; Kimi audit P0)*

### Merge

16. **Merge `yolo/jack-drop-alsa-fallback` → `dev`** only after Gate C PASS and P0 laptop fixes landed.

### Product decision (mixer spec)

17. **Decide mixer product surface** — JACK-graph client vs separate UI; write spec before more engine work. Not a merge blocker. *(Opus review 🔴 reframed; Opus audit P2 "decision, not code")*

---

## 6. Artifact Index

| Artifact | Path |
|----------|------|
| Opus Grumpy Dev review | [`Documents/reviews/grumpy-review-opus-2026-08-13.md`](grumpy-review-opus-2026-08-13.md) |
| Kimi Grumpy Dev review | [`Documents/reviews/grumpy-review-kimi-2026-08-13.md`](grumpy-review-kimi-2026-08-13.md) |
| Opus review audit | [`Documents/reviews/review-audit-opus-2026-08-13.md`](review-audit-opus-2026-08-13.md) |
| Kimi review audit | [`Documents/reviews/review-audit-kimi-2026-08-13.md`](review-audit-kimi-2026-08-13.md) |
| Governing spec (Phase 1) | [`Documents/specs/jack-audio-engine-spec.md`](../specs/jack-audio-engine-spec.md) |
| Phase 2 spec (untracked) | [`Documents/specs/looper-jack-client-spec.md`](../specs/looper-jack-client-spec.md) |

---

## 7. Model Comparison — Which Review & Audit Was Better?

### Review quality comparison (Opus 5 vs Kimi K3)

| Dimension | Opus 5 review | Kimi K3 review |
|-----------|---------------|----------------|
| **Finding tally** | 5 🔴 · 6 🟡 · 5 🟢 (16 numbered) | 2 🔴 · 5 🟡 · 6+ 🟢 |
| **Claim breadth** | 65 auditable claims (Opus audit queue) | 54 auditable claims (Kimi audit queue) |
| **Depth vs breadth** | Deeper on process, udev, watchdog amendment side-effects, test strategy, DX clutter, spec conformance table (17 criteria) | Deeper on hardware-path regression (duplex open), env/key drift, branch topology, business-rule edge cases |
| **Unique high-value catches** | udev `remove`/`ATTR{}` bug (4.3); watchdog defaults destruction (4.4); test-copy gap as load-bearing on 2* (4.5); mixer naming trap (reframed later) | jackd duplex vs mic bridge (🔴#1) — only finding that explains a *shipped-profile regression in waiting*; criterion 16 UI drift with Gate B evidence; watchdog ~60 s budget math |
| **Errors / exaggerations** | Mixer 🔴 measured against phrase not in repo; `sudo` inventory "five sites in four files" (actual 9+ files); `logs/`/`.pytest_cache/` "committed directories" ❌; `ENV{}` fix overspecified; 4.5 rated 🟡 while cited as reason 2* is unverified | "Stop seeding `MPE_SURGE_BUFFER_SIZE`" ❌; "76 ahead / 1 behind" (actual 32 behind); three-file PR #48 conflict list (actual two); `logs/` "consider ignoring" (already gitignored); mic bridge "restart loop" (actually wedges in `failed` per M3) |
| **Trust for acting on code changes** | **Higher for amendment/post-merge regression hunting** — 4.3 and 4.4 are the findings a spec review could never produce; D2 inventory verification is exhaustive | **Higher for profile/hardware-path analysis** — 🔴#1 is the session's only finding that rewrites understanding of 5b/14; buffer drift is user-facing and test-gappable |

**Verdict:** Neither review alone is sufficient. Opus found the udev and watchdog bombs Kimi missed entirely; Kimi found the jackd duplex regression and criterion 16 drift Opus never mentioned. For **code changes on this branch**, trust **Opus for supervisor/udev/calibration teardown paths** and **Kimi for jackd device-open semantics and touch UI env contract**.

### Audit quality comparison

| Dimension | Opus 5 auditing Opus review | Kimi K3 auditing Kimi review |
|-----------|------------------------------|------------------------------|
| **Verdict tally** | **48 ✅ · 12 ⚠️ · 3 ❌ · 2 🔍** / 65 claims (~74% exact) | **48 ✅ · 3 ⚠️ · 2 ❌ · 1 🔍** / 54 claims (~89% exact) |
| **Errors in praise vs criticism** | 3 ❌ all in **praise/background**: sudo count (H3), committed `logs/` (J1), mixer goal in repo (G6). Criticism claims (4.2–4.4, B1–B3, D1) overwhelmingly ✅ | 2 ❌ in **secondary/prescription**: seed deletion (C11), logs ignore (J41). Core 🔴#1 and Gate C: ✅ |
| **"What the review missed"** | **6 items (MISSED-1–6)** — two High (UI restart bypasses cooldown; 4.4 repeats per cycle). Substantially expanded action matrix | **4 items (M1–M4)** — all Low/Medium; sharpens 🔴#1 consequence (wedge not loop) |
| **Severity calibration** | Aggressive: mixer 🔴 → Low; 4.5 🟡 → High; 4.6 🟡 → Low; `ENV{}` fix flagged ⚠️ | Moderate: 🔴#1 Critical → High (blocked profile); triple predicate 🟡 → Low; preserved fix-prescription challenge on seed deletion |
| **Process rigor** | Full claim queue A–K; severity re-assessment table; explicit soak sequencing disagreement | Tighter hit rate; fewer ⚠️; stronger prescription auditing (claim 11 ❌) |

**Verdict:** **Opus audit** is better at **process rigor and expanding the finding set** (6 missed items vs 4, severity re-assessment, P0 sequencing). **Kimi audit** is better at **fix-prescription accuracy** — caught the one dangerous "delete the seed" recommendation and verified jackd `-P` vs core `-P` distinction (claim A1 footnote). Kimi's higher ✅ ratio (89% vs 74%) reflects narrower scope and fewer background DX claims, not necessarily deeper verification of hard bugs.

### Cross-model pattern: better at audit than review?

**Opus:** **Yes — better at auditing than reviewing**, marginally. The review's 3 ❌ errors sit in praise/inventory (H3 sudo count, J1 committed dirs, G6 mixer phrase); the audit caught MISSED-1 (UI cooldown bypass) the review never raised, corrected B5 (`ENV{}`), reframed mixer severity, and upgraded 4.5. Review strength: narrative framing of 4.2 as *the* central risk and amendment side-effect reasoning (4.3+4.4 compound chain). Audit strength: claim-by-claim discipline and 6 net-new items.

**Kimi:** **Audit ≈ review, with audit winning on prescriptions.** The review's 🔴#1 (duplex open) is the highest-value unique finding in the whole session and survived audit ✅ with only severity deflation (Critical → High). The audit's claim 11 ❌ ("stop seeding") prevents a real foot-gun the review prescribed. Audit missed udev entirely (out of Kimi review scope). Review strength: hardware-path regression intuition. Audit strength: env-contract reading (`mpe.env.example:66-71`) and git number verification (32 vs 1 behind).

**Overall winner:** **Split the work — no single model wins all roles.**

| Role | Recommended model | Evidence |
|------|-------------------|----------|
| Full codebase / architecture review | **Opus** for breadth, spec conformance, amendment side-effects | 16 findings vs 13; only source of udev + watchdog + test-strategy gaps |
| Hardware-path / profile regression review | **Kimi** | 🔴#1 duplex open; criterion 16 + Gate B drift citation |
| Review audit (process rigor) | **Opus audit** | 65-claim queue, 6 MISSED items, severity table, soak sequencing |
| Review audit (fix prescription) | **Kimi audit** | Seed deletion ❌; jackd flag disambiguation; mic bridge wedge sharpening |
| Implementation | **`composer-2.5-fast`** (per project AGENTS.md) off locked audit P0/P1 list | N/A to this session |

If forced to pick one pair for a repeat pass: **Opus review + Kimi audit** — Opus for finding count and amendment archaeology, Kimi audit for catching dangerous fixes and env-contract mistakes. The inverse (Kimi review + Opus audit) would miss udev entirely unless Opus audit's scope expanded beyond the Kimi review text.

---

## 8. Recommended Model Routing (Going Forward)

- **Full codebase review (milestone / release-scope):** Opus thinking model — better at exhaustive inventories (D2 call sites, spec conformance tables), amendment side-effect chains, and test-strategy critique. Pair with Kimi pass on audio hardware paths if `$` allows.
- **Targeted regression review (audio engine, profiles, udev):** Kimi K3 — duplex-open class of bugs and env-key drift; verify every prescription against `mpe.env.example` and live consumers.
- **Review audit:** Opus for claim extraction, severity re-assessment, and "what did the review miss"; Kimi (or second Opus pass) specifically on **fix prescriptions** and env/schema contracts.
- **Implementation / P0 fixes:** `composer-2.5-fast` from deduplicated audit action matrix — udev check in `restart-audio-graph.sh`, watchdog gating, `-P` flag, UI label repoint, sourceable test helper.
- **Human-only:** Gate C soak and 5b/14 rewire — no model substitutes for Pi bench time.

---

*This synthesis was produced from the four source artifacts listed in §6. No code was modified.*
