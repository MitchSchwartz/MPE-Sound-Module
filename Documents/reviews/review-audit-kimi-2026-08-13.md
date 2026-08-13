# Review Audit — Kimi grumpy review of MPE-Module JACK Phase 1

*Audit date: 2026-08-13 14:09 (America/Toronto)*
*Auditor: review-audit pass (staff-engineer verification, read-only)*
*Subject under audit: [`grumpy-review-kimi-2026-08-13.md`](grumpy-review-kimi-2026-08-13.md)*
*Codebase state verified against: `yolo/jack-drop-alsa-fallback` @ `1ab9f55` (same commit the review covered; tree unchanged since).*

**Headline:** the review is overwhelmingly accurate. Of 54 distinct claims checked, **48 confirmed ✅, 3 partially true ⚠️, 2 incorrect ❌, 1 unverifiable 🔍** (out-of-scope `mpe-cli` claim). Every load-bearing claim — the jackd duplex-open regression risk, the Gate C soak gate, criterion 16 drift, the untracked/stale looper spec, the watchdog budget blowout — checks out against the code with line-level evidence. The errors are secondary: a stale-copied divergence count ("1 behind" vs actual 32), an already-gitignored file flagged as unignored, a two-file claim stated as three (twice), and one fix prescription ("stop seeding `MPE_SURGE_BUFFER_SIZE`") that would break live consumers of that key.

---

## Work Queue

Claims extracted from the review, grouped by file/component:

**A. `scripts/start-jackd.sh`** — (1) no playback-only flag; jackd opens the DAC duplex and holds the capture stream [🔴]. (2) 33-41 publishes `recovering` only when nothing more specific exists [positive].

**B. Mic bridge / session capture** — (3) `mic-to-uac2-bridge.sh:54` + `session_capture.py:66-89` capture from the same Sound Blaster card jackd holds → EBUSY. (4) failure implies a bridge restart loop. (5) soak rows 5b/14 BLOCKED, so the window was never exercised.

**C. Buffer-key drift (Hall of Shame #2 / criterion 16)** — (6) `surge_audio.py:16-19` ghost comment + `DEFAULT_BUFFER=1024`. (7) UI label shows "1024 · 21 ms" while graph runs 256×3 = 16 ms. (8) `configure-pi-paths.sh:70-74` seeds `MPE_SURGE_BUFFER_SIZE`, never JACK keys. (9) Gate B saw 12 "touch buffer label / env drift" failures, dismissed as pre-existing. (10) `set-surge-audio.sh:125-128` writes both keys on `--buffer`. (11) *Prescription:* stop seeding the Surge key; write JACK key only. (12) criterion 16 has no executable test.

**D. Watchdog (Hall of Shame #3)** — (13) 15-iteration budget loop × `timeout 3 jack_lsp` + 1 s sleep ⇒ up to ~60 s blocked against a hung jackd. (14) probe runs up to 3× per reconcile pass.

**E. Looper spec (Hall of Shame #4)** — (15) untracked in git. (16) §D.5 keeps `MPE_AUDIO_ENGINE=alsa` as operator choice. (17) criterion 11 boots "with `MPE_AUDIO_ENGINE=jack` unset". (18) §D.5.2 cites the old guard message advising `mpe engine set alsa`. (19) §D.5.3 analyzes `looper_guard_blocked` keying on configured engine via `resolve_audio_engine`. (20) criterion 11's `grep -r LOOPER_GUARD` misses `mpe_looper_state_label`.

**F. Guard triple implementation (Hall of Shame #5)** — (21) one predicate, three homes: `engine-guard.sh:24-26`, `audio-engine.sh:189-195`, `audio_engine.py:36-38`.

**G. Stale comments (#6)** — (22) `99-usb-audio.rules:1-5`. (23) `uac2-stall-watchdog.sh:4-6`. (24) `engine-guard.sh:7` dates removal 2026-08-12. (25) `set-surge-audio.sh:72` unused `_old_buffer`.

**H. Logic & business rules** — (26) failed-reason overwritten (`supervisor-exhausted` → `no-server`). (27) sample-rate enum drift (44100/48000 vs 96000). (28) HUD `active` passed through untrusted. (29) profile-switch flag persists until supervisor-scheduled Surge start. (30) failed publish path correct end-to-end [positive]. (31) watchdog announces `failed` once [positive]. (32) `mpe_restart_audio_graph` resets supervisor budget, unspecced.

**I. Git / topology** — (33) `main` 42 behind `dev`. (34) `yolo/looper-phase0` "76 ahead / 1 behind", merge-base before Phase 1. (35) PR #48 touches the three files Phase 1 rewrote most.

**J. Tests / DX / security positives** — (36) 440 tests, 0 failures. (37) CHANGELOG says 438. (38) `test_audio_engine.py` structure (parity tests, `NoAlsaPathTests`, unit-content assertions). (39) README says Pi 5 vs specs' Pi 4B. (40) `ENCODER_BUTTON_REVIEW.md` at root. (41) `logs/shutdown-trace.jsonl` untracked, "consider ignoring `logs/`". (42) `json_store.py` fsyncs file + dir. (43) `${2:-unknown}` rationale comment. (44) OSC bound to 127.0.0.1:53280. (45) HUD reads via 0.5 s cached monitor. (46) 19-mixin class at `touch_browser_app.py:67-87`. (47) defensive `getattr` chains at `touch_browser_draw.py:485-489`. (48) HUD draw at `touch_browser_draw.py:392-405`. (49) `--list-devices` invoked "up to three times per boot" by `start-surge-cli.sh`. (50) D2 routing at four call sites. (51) no `chrt` in `start-surge-cli.sh`. (52) no conflict markers / dead fallback arms. (53) `mpe engine status` landed in mpe-cli, soaked PASS. (54) Gate B soak-log citations (PASS/BLOCKED rows) quoted faithfully.

---

## Claim Verification

### A. `scripts/start-jackd.sh`

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | No `-P` playback-only flag; jackd ALSA backend opens duplex and holds the Sound Blaster capture stream → mic bridge/session capture EBUSY | ✅ | `start-jackd.sh:43-44`: `exec jackd -R -P"$JACK_PRIO" -s \` / `-d alsa -d "$HW_DEV" -r "$JACK_RATE" -p "$JACK_BUFFER" -n "$JACK_PERIODS"`. Note: the `-P"$JACK_PRIO"` *before* `-d alsa` is jackd's **core** realtime-priority option — the ALSA backend's playback-only flag (also spelled `-P`, but positioned *after* `-d alsa`) is genuinely absent, so the backend defaults to duplex. The review did not confuse these. Hardware confirmation still pending — the review itself flags this ("almost certainly", "nobody can know") and the fix direction is correct. |
| 2 | 33-41 publishes `recovering` only when nothing more specific is published | ✅ | `start-jackd.sh:35-41`: `case "$current_state" in ok \| failed \| recovering) ;; *) mpe_engine_state_write ... recovering jackd-starting ...` — multi-writer discipline as described. |

### B. Mic bridge / session capture

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 3 | Bridge captures from the same card jackd holds | ✅ | `mic-to-uac2-bridge.sh:54`: `arecord -D "$CAP_DEV" -f S16_LE ...`; `session_capture.py:66-89` resolves `plughw:CARD=<Play3>,DEV=0` (fallback `return plug_dev or hw_dev or f"plughw:CARD={card_id},DEV=0"`). `config/mpe.env.example:39` confirms the profile's purpose: "usb-host-session — Surge on Sound Blaster; RC-5 return (mic in) → USB when PC captures". ALSA `hw` capture opens are exclusive — mechanism is sound. |
| 4 | Failure implies a restart loop | ✅ (sharper than stated) | `config/mic-to-uac2-bridge.service:12-13`: `Restart=on-failure` / `RestartSec=1`. With no explicit `StartLimitBurst`, the default (5/10 s) wedges the unit into `failed` after ~5 s of EBUSY — not an infinite loop but a dead bridge requiring manual `reset-failed`. See *What the Review Missed*, M3. |
| 5 | Soak rows 5b/14 BLOCKED — regression window never exercised | ✅ | Spec Gate B log: `5b UAC2 host capture | **BLOCKED** | Needs physical rewire` (line 473); `14 calibration/session | **PASS** (cal) / **BLOCKED** (session) | ... session_capture.py still blocked (same rewire as 5b)` (line 475). |

### C. Buffer-key drift (Hall of Shame #2 / criterion 16)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 6 | Ghost comment + `DEFAULT_BUFFER = 1024` | ✅ | `surge_audio.py:16-18`: `# Must stay in sync with the fallback in scripts/start-surge-cli.sh.` — no fallback exists in `start-surge-cli.sh` (full read; the string `MPE_SURGE_BUFFER_SIZE` appears nowhere in that script). Module docstring ("Surge ALSA buffer size…") is also stale. |
| 7 | Fresh appliance displays "1024 · 21 ms" while playing at 256×3 = 16 ms | ✅ | `surge_audio.py:50-55` reads `MPE_SURGE_BUFFER_SIZE` (seeded 1024); `buffer_latency_ms` (66-71) computes `buf*1000/rate` with **no ×periods**; the graph runs `mpe_jack_period`/`mpe_jack_periods` defaults 256×3 (`audio-engine.sh:23-24`). Label is wrong on both axes (key and math). |
| 8 | `configure-pi-paths.sh:70-74` seeds the legacy key, never the JACK keys | ✅ | Lines 70-74 write `MPE_SURGE_BUFFER_SIZE` (preserved or 1024); the whole write block (59-80) contains no `MPE_JACK_*` line. JACK keys fall through to library defaults. |
| 9 | Gate B saw 12 "touch buffer label / env drift" failures, dismissed as pre-existing | ✅ | Spec line 469: "`mpe test pi audio`: 12 failures (touch buffer label / env drift — pre-existing on branch, not jack-specific)". Quoted exactly. |
| 10 | `set-surge-audio.sh:125-128` writes both keys on `--buffer` | ✅ | Line 90 `_update_env_var MPE_SURGE_BUFFER_SIZE "$BUFFER"`; lines 125-128 `if [ -n "$BUFFER" ]; then _update_env_var MPE_JACK_BUFFER "$BUFFER" ...`. Both keys written; the D6-separated knobs are re-coupled through this path. |
| 11 | *Prescription:* stop seeding `MPE_SURGE_BUFFER_SIZE`; `--buffer` should write the JACK key only | ❌ (prescription) | `MPE_SURGE_BUFFER_SIZE` is **not** a dead key. Live consumers: `scripts/calibrate-patch-normalization.py:422,457`, `patch_browser/midi_sync.py:58`, and the `MPE_MIDI_OUTPUT_OFFSET_AUTO` default ("-buffer_ms from MPE_SURGE_BUFFER_SIZE", `mpe.env.example:142`). `mpe.env.example:66-71` explicitly documents it as "legacy knob — no effect on JACK playback. Still read by calibration and the MIDI clock offset auto-derivation." Removing the seed forces those consumers onto their hardcoded 1024 fallbacks and contradicts the shipped env contract. The correct fix is the review's *first* half (repoint UI read-back/labels at `MPE_JACK_BUFFER`/`MPE_JACK_PERIODS`, ×periods in the label) — the seeding should stay, possibly with a renamed comment. See Disagreements. |
| 12 | Criterion 16 has no executable test; `grep MPE_JACK_BUFFER tests/` → nothing | ✅ | Zero matches for `MPE_JACK_BUFFER|MPE_JACK_PERIODS` anywhere under `tests/` (grep run this session). The enum validators `mpe_jack_period`/`mpe_jack_periods`/`mpe_jack_rate` have no coverage. |

### D. Watchdog (Hall of Shame #3)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 13 | "15 s budget" loop is really up to ~60 s against a hung jackd | ✅ | `surge-watchdog.sh:13` `RECONCILE_BUDGET=15`; loop at 73-86 counts **iterations**, each costing `timeout 3 jack_lsp` (`audio-engine.sh:215`) + `sleep 1` when the server hangs ⇒ 15 × ~4 s ≈ 60 s, during which the crash-recovery arm (main loop, 97-113) does not run. |
| 14 | Same probe executed up to 3× per reconcile pass | ✅ | Per pass: line 67 `mpe_surge_on_jack_graph` → `mpe_jack_server_ready` (`audio-engine.sh:428`); line 73 `mpe_jack_server_ready`; loop line 76; line 82; line 88 calls both again. Worst case is worse than "3×" — but the claim's direction and fix (probe once, count wall-clock) are right. |

### E. Looper spec (Hall of Shame #4)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 15 | `Documents/specs/looper-jack-client-spec.md` is untracked | ✅ | `git status --porcelain`: `?? Documents/specs/looper-jack-client-spec.md`. |
| 16 | §D.5 still scopes `MPE_AUDIO_ENGINE=alsa` as an operator choice | ✅ | Looper spec lines 79-81 (Non-Goals: "remove auto-fallback and keep `MPE_AUDIO_ENGINE=alsa` as an explicit operator choice only") and 691-694 (§D.5 same). The amendment (`jack-audio-engine-spec.md:20-24`) deleted the variable outright: "`MPE_AUDIO_ENGINE` is retired — removed from `config/mpe.env.example` and every consumer." |
| 17 | Criterion 11 verification boots "with `MPE_AUDIO_ENGINE=jack` unset (default)" | ✅ | Looper spec line 100; also §D.4 step 2 (line 675): "Leave `MPE_AUDIO_ENGINE` unset so the default `jack` applies." Variable no longer exists. |
| 18 | §D.5.2 describes the guard message advising `mpe engine set alsa` | ✅ | Looper spec 699-704 quotes `engine-guard.sh:34` telling the user "Switch with: sudo mpe engine set alsa". Current `engine-guard.sh:20`: `MPE_LOOPER_GUARD_MESSAGE="looper is unavailable until the JACK callback client ships (spec Phase 2) — there is no ALSA route to run it through."` — message rewritten; the cited line 34 is now an `if` statement. Stale quote *and* stale line ref. |
| 19 | §D.5.3 analyzes `looper_guard_blocked` keying on the configured engine via `resolve_audio_engine` | ✅ | Looper spec 705-712. Current `audio_engine.py:36-38`: `def looper_guard_blocked(*, looper_enabled: str \| int \| None = None) -> bool: ... return str(looper_enabled or "0").strip() == "1"` — no engine parameter; `resolve_audio_engine` does not exist anywhere in `patch_browser/` (grep). |
| 20 | Criterion 11's `grep -r LOOPER_GUARD` won't catch `mpe_looper_state_label` | ✅ (stronger than stated) | `LOOPER_GUARD` matches `LOOPER_GUARD_MESSAGE` (both languages) and `LOOPER-GUARDED` only with case-insensitive/-e tricks; it misses `mpe_looper_state_label` (`audio-engine.sh:189`) **and** lowercase `looper_guard_blocked` under a case-sensitive grep. The removal checklist in looper-spec Task 12 does name the symbols explicitly, mitigating this in practice. |

### F. Guard triple implementation (Hall of Shame #5)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 21 | One predicate, three implementations | ✅ | `engine-guard.sh:24-26` `mpe_looper_engine_blocked() { [ "${MPE_LOOPER_ENABLED:-0}" = "1" ] }`; `audio-engine.sh:189-195` `mpe_looper_state_label` re-tests the same var; `audio_engine.py:36-38` `looper_guard_blocked`. Nuance: the Python twin takes the value as a *parameter* rather than reading the env, so it's a pure function of caller input — still a third policy home, but the cleanest of the three. The spec's own D5 rationale ("a guard per call site would rot") makes the triplication a fair smell callout. |

### G. Stale comments (#6)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 22 | `99-usb-audio.rules:1-5` says "restart Surge XT CLI" | ✅ | Lines 1-5 unchanged from pre-JACK wording; actual actions at 26-27 run `restart-audio-graph.sh` (jackd). |
| 23 | `uac2-stall-watchdog.sh:4-6` says "restart Surge on the gadget" | ✅ | Header unchanged; actual path is `_uac2_restart_graph` (64-68) → `restart_audio_graph`. |
| 24 | `engine-guard.sh:7` dates the ALSA removal "2026-08-12" | ✅ | Line 7: "(ALSA removed entirely, 2026-08-12)"; the amendment is dated 2026-08-13 everywhere else. Off-by-one-day stale. |
| 25 | `set-surge-audio.sh:72` assigns `_old_buffer`, never uses it | ✅ | Line 72 assigns; only `_old_rate` is consumed (line 102). Dead assignment. |

### H. Logic & business rules

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 26 | Failed-state reason gets overwritten (`supervisor-exhausted` → `no-server`) | ✅ | Supervisor escalation writes reason `supervisor-exhausted` once (`surge-watchdog.sh:42-45`), but `surge-xt-cli.service:24` `Restart=on-failure` keeps re-running `start-surge-cli.sh`, which re-publishes `state=failed reason=no-server` at line 151 on every retry until start-limit wedges the unit. Mechanism confirmed; cosmetic as rated. |
| 27 | Rate enum drift: UI 44100/48000, validator also 96000 | ✅ | `set-surge-audio.sh:47-52` accepts only 44100/48000; `audio-engine.sh:60-65` `mpe_jack_rate()` also accepts 96000; `surge_audio.py:14` `SAMPLE_RATE_PRESETS = (44100, 48000)`. Two policies, pick one. |
| 28 | HUD passes `active` through untrusted | ✅ | `audio_engine.py:114-120`: `active = state.get("active") or ...; parts.append(str(active)[:4].upper())` — a stale `active=alsa` renders "ALSA"; `state` is filtered against `VALID_ENGINE_STATES` (line 121) but `active` is not. Harmless (tmpfs clears on reboot), as rated. |
| 29 | Profile-switch flag persists until the *supervisor's* next Surge start | ✅ | `set-audio-profile.sh:54-60`: marks flag, calls `restart_audio_graph`, comment acknowledges "the flag persists until Surge is next started — e.g. manual restart or supervisor reconcile." |
| 30 | Hard-failure design wired end-to-end [positive] | ✅ | `start-surge-cli.sh:147-154` publishes `state=failed` + non-zero exit; unit retries via `Restart=on-failure`; supervisor caps and escalates once. Verified against units, not just docs. |
| 31 | Watchdog announces `failed` once, not every poll [positive] | ✅ | `surge-watchdog.sh:42`: `if [ "$(mpe_engine_state_get state)" != failed ]` gates the log+write. |
| 32 | `mpe_restart_audio_graph` resets the supervisor budget — landed but unspecced | ✅ | `audio-engine.sh:311-314`: `mpe_engine_reconcile_reset` after restart, rationale only in the code comment. Full read of the spec: the D3 cooldown table (334-339) and the amendment never state the rule "operator/device action clears escalation." Amended criterion 15 (line 195) *implies* the outcome (replug recovers from `state=failed`) but the behavioral rule itself is code-only. |

### I. Git / topology

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 33 | `main` is 42 commits behind `dev` | ✅ | `git rev-list --count main..dev` → `42`. |
| 34 | `yolo/looper-phase0` is "76 commits ahead / 1 behind `dev`", merge-base before Phase 1 | ⚠️ | Measured this session: **76 ahead / 32 behind** (`git rev-list --count` both directions); merge-base `da70ee5` (Aug 10), which does predate the Phase 1 merge `daac891` ✅. The "1 behind" figure is lifted from the looper spec's own 2026-08-12 measurement (its lines 628/836) without re-running — a day stale and materially different: the branch is *further* behind than reported, which strengthens the rebase-gate point but misreports the fact. |
| 35 | PR #48 "touches the three files Phase 1 rewrote most (`start-surge-cli.sh`, `detect-audio-device.sh`, `99-usb-audio.rules`)" | ⚠️ | `git diff --name-only $(merge-base)..yolo/looper-phase0` (69 files total, matching the spec's count) includes `scripts/start-surge-cli.sh` and `scripts/detect-audio-device.sh` but **not** `config/99-usb-audio.rules`. Two of three; the rebase-conflict argument is unaffected. (The looper spec's §D.4 makes the same three-file claim — the review inherited it.) |

### J. Tests / DX / security positives

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 36 | 440 tests, 0 failures | ✅ | Reproduced this session via the project's own harness: `mpe test local all` → `Ran 440 tests in 42.236s / OK`, plus `OK test_gadget_persist.sh` and `All prepare-dsi-display shell tests passed`. Static count corroborates: 440 `def test_` methods under `tests/`. |
| 37 | CHANGELOG claims 438 | ✅ | `CHANGELOG.md:71`: "Full suite: 438 tests, 0 failures". Two tests added since; immaterial, as the review said — but the doc/suite mismatch is real. |
| 38 | `test_audio_engine.py` structure (parity tests, `NoAlsaPathTests`, unit-content assertions) | ✅ | 704 lines; classes include `BashReconcileParityTests` (172), `NoAlsaPathTests` (423), `RuntimeDirectoryPreserveTests` (189), `SurgeStartLimitUnitTests` (244), `JackdStartLimitTests` (257). |
| 39 | `README.md:75` says Pi 5; specs say Pi 4B | ✅ | README:75 "Reference stack: Raspberry Pi 5…"; `jack-audio-engine-spec.md:508` "Pi 4B, Debian 13 trixie"; looper spec:809 "Pi 4B Rev 1.5". Either two appliances exist (then say so) or one doc is wrong. |
| 40 | `ENCODER_BUTTON_REVIEW.md` sits at repo root | ✅ | File exists at root. |
| 41 | `logs/shutdown-trace.jsonl` is untracked; "consider ignoring `logs/`" | ❌ | The file is **already gitignored**: `git check-ignore -v logs/shutdown-trace.jsonl` → `.gitignore:34:logs/shutdown-trace.jsonl`. That's why `git status --porcelain` is clean for it. The recommendation is already implemented; the review apparently inferred "untracked" from the file's on-disk presence without checking ignore rules. |
| 42 | `json_store.py:24-43` atomic write, fsync file + directory | ✅ | Lines 34-43: `handle.flush(); os.fsync(handle.fileno())`, `os.replace`, then `os.open(path.parent)` + `os.fsync(dir_fd)`. |
| 43 | `audio-engine.sh:160-179` `${2:-unknown}` with found-bug rationale | ✅ | Comment at 162-165 documents the watchdog-killed-by-`${2:?}` incident exactly as quoted. |
| 44 | OSC bound to 127.0.0.1:53280, no network surface | ✅ | `patch_browser/patch_loader.py:46-47`: `osc_host="127.0.0.1", osc_port=53280`. |
| 45 | HUD reads via 0.5 s cached background monitor | ✅ | `engine_state_monitor.py:10`: `POLL_INTERVAL_S = 0.5`; draw path calls `self.engine_monitor.snapshot()` (`touch_browser_draw.py:395`), no per-frame disk hits. |
| 46 | 19-mixin class at `touch_browser_app.py:67-87` | ✅ | Exactly 19 mixin base classes, lines 68-86. |
| 47 | Defensive `getattr` chains at `touch_browser_draw.py:485-489` | ✅ | Quoted lines match: `getattr(self, "engine_hud_rect", getattr(self, "looper_hud_rect", self.audio_profile_badge_rect)).x` etc. |
| 48 | HUD draw at `touch_browser_draw.py:392-405` | ✅ | `_draw_engine_hud` reads monitor snapshot → `engine_hud_should_show`/`engine_hud_label`/`engine_hud_semantic`. |
| 49 | "`start-surge-cli.sh` invokes `--list-devices` up to three times per boot" | ⚠️ | Two invocations live in `start-surge-cli.sh` (line 42 JACK index, line 97 MIDI index). The third is `detect-audio-device.sh:32`, run from `jackd-prestart.sh:50` — a **different unit** (`mpe-jackd.service` ExecStartPre). So: 3 per boot across the boot path, 2 attributable to the named script. The perf point (Surge CLI invocations cost seconds on a Pi 4) stands; the suggested fix (cache one listing) would need to cross the unit boundary, making it slightly harder than implied. |
| 50 | D2 routing at four call sites [positive] | ✅ | `set-audio-profile.sh:57` → `restart_audio_graph`; `set-surge-audio.sh:129` → `mpe_restart_audio_graph`; `uac2-stall-watchdog.sh:64-68` → `restart_audio_graph`; `99-usb-audio.rules:26-27` → `restart-audio-graph.sh`. All four verified. |
| 51 | No `chrt` anywhere in `start-surge-cli.sh` [positive] | ✅ | Full read; no `chrt`. Comment at 134-137 explains why. |
| 52 | No merge-conflict artifacts / dead fallback arms / duplicate engine paths [positive] | ✅ | Repo-wide grep for `^<<<<<<< ` across py/sh/md: none. `MPE_AUDIO_ENGINE` appears only in comments; `resolve_audio_engine` is gone from `patch_browser/`. |
| 53 | Criterion 17 landed mpe-cli-side, soaked PASS | 🔍 | `mpe-cli` is a separate repo explicitly outside this review's scope; the Gate B log row (`17 ... PASS | Shipped in mpe-cli 85dad3c`) is internally consistent, but I did not verify the mpe-cli repo. |
| 54 | Gate B soak-log citations faithful [positive] | ✅ | Every quoted row (5b BLOCKED, 14 half-BLOCKED, 2b2 PASS, 12-failure drift note, 39 s recovery, etc.) matches `jack-audio-engine-spec.md:460-475` verbatim. |

---

## Severity Re-Assessment

| # | Issue | Reviewer Rating | My Rating | Delta | Reasoning |
|---|-------|-----------------|-----------|-------|-----------|
| 1 | jackd duplex open vs mic bridge/session capture | 🔴 Critical | **High** | Slightly deflated | Mechanism verified; it is a real shipped-profile regression *in waiting*. But the affected profile is already BLOCKED on a physical rewire, so nothing regresses in the field until that unblocks — which makes this a "fix before/at the rewire" gate rather than a drop-everything fire. Fix itself is one flag (`-P` after `-d alsa`). |
| 2 | Do not merge without Gate C soak | 🔴 Critical | **High** | Agree | Process gate, spec-mandated (spec lines 102-108, 452-458). The branch converts every jackd failure into a silent instrument *by design*; shipping that unsoaked is the real incident risk. |
| 7/8/10/12 | Criterion 16 drift: UI sells the retired-for-graph buffer key; no tests | 🟡 Medium | **Medium** | Agree | User-facing mislabel (21 ms vs 16 ms) plus a knob whose read-back doesn't match the graph. No audio harm — `set-surge-audio.sh --buffer` writes both keys, so the picker *works*; only the display lies. |
| 13/14 | Watchdog budget blowout on hung jackd | 🟡 Medium | **Medium** | Agree | Up to ~60 s of supervisor blindness in a rare-but-real failure mode (hung, not dead, jackd), delaying the crash-recovery arm. Compounds with my M1 (unguarded `jack_lsp` at `audio-engine.sh:432`). |
| 15-19 | Looper spec untracked + 4 stale sections | 🟡 Medium | **Medium** | Agree | Process rot, not code rot — but exactly the kind that re-injects "the spec said so" bugs into Phase 2. One commit + four small edits. |
| 21 | Triple predicate | 🟡 Medium | **Low** | Deflated | All three implementations are trivially identical today and two are one-liners reading the same env var. For a solo-maintainer appliance this is a cleanup task, not a defect; it only bites during the Phase 2 guard removal, which has its own checklist (Task 12 names the symbols). |
| 26 | Failed-reason overwrite | 🟢 Cosmetic | **Low** | Agree | Journal/status loses escalation history; HUD unaffected. One-line guard ("don't downgrade reason") in the state writer's callers. |
| 27 | 96 kHz enum drift | 🟢 Minor | **Negligible** | Agree-ish | No user path reaches the inconsistency (UI presets exclude 96k; env can set it and the validator honors it). Pick one when convenient. |
| 28 | HUD `active` passthrough | 🟢 Minor | **Negligible** | Agree | tmpfs clears on reboot; worst case is a 4-char stale badge until then. |
| 32 | Budget-reset unspecced | 🟢 (surprise) | **Low** | Agree | Behavior is *correct* and matches amended criterion 15's intent; the gap is that a behavioral rule lives only in a code comment. One spec sentence fixes it. |
| 33/34/35 | Branch topology warnings | 🟡 (structural) | **Medium** | Agree | Direction right; numbers stale in the review's favor (32 behind, not 1). The rebase gate (Task 0) is correctly identified as load-bearing. |

---

## What the Review Missed

Honest account: the review's coverage of the audio path was thorough — my independent read of the same files produced only four net-new items, all minor:

- **M1 — One `jack_lsp` call lacks the `timeout 3` guard.** `audio-engine.sh:432` (`mpe_surge_on_jack_graph`): `jack_lsp 2>/dev/null | grep -qi 'surge'` — every other probe goes through `timeout 3 jack_lsp` (`:215`), but this one doesn't. It's reached only after a ready-probe passed, so the window is narrow (server hangs between the two calls), but if it trips, the watchdog blocks **indefinitely**, not 60 s — strictly worse than Hall of Shame #3's worst case. Same fix pass: probe once, reuse the result.
- **M2 — `start-surge-cli.sh` publishes `state=ok` optimistically.** Lines 171-172 write `ok` immediately after backgrounding Surge; `sleep 2` (line 174) is not a liveness check. A Surge that dies on connect (bad index, late jackd death) shows a false `ok` until the watchdog's next 5 s poll. Self-healing; Low.
- **M3 — The EBUSY blast radius is a wedge, not a loop.** The review said the mic bridge would fail "likely in a restart loop." `mic-to-uac2-bridge.service:12-13` (`Restart=on-failure`, `RestartSec=1`, default burst limits) means ~5 rapid retries then the unit lodges in `failed` — the bridge stays dead even if jackd later frees the device, until manual `reset-failed`. Sharpens 🔴#1's consequence; doesn't change its fix.
- **M4 — Stale inventory number in `engine-guard.sh:9`.** "MPE_LOOPER_ENABLED is read in nine files" — the current tree has 4 (`grep -rl` over py/sh/service, excluding docs/reviews). Same class as the #6 comment parade; one more data point that the guard's own documentation is drifting.

Nothing missed on the security axis — the review's "no holes" conclusion for a loopback-only, single-user appliance with enum-validated sudo entry points matches my read.

## What the Review Got Right (And Why It Matters)

1. **The duplex-open catch (🔴#1) is the review's best find, and it's the kind static reading is for.** No test could have caught it — the soak rows that would exercise it (5b/14) are physically blocked, and the failure only exists when two correct-on-paper components (jackd's default duplex open; the bridge's exclusive `hw` capture) meet on hardware. The added depth I'd attach: the review undersold *why now* — pre-JACK, Surge's JUCE ALSA open was playback-only, so Phase 1 *introduced* the capture-side hold. It's a regression created by the merge, not a pre-existing condition, and the BLOCKED soak row is exactly where it would have surfaced. Fixing it is one flag; the cost of not fixing it is the entire `usb-host-session` feature dead-on-arrival after the rewire Mitch is presumably about to do.
2. **Gate C as a hard stop (🔴#2) is correct and correctly framed as "pressure to skip it is the actual incident."** The amendment converts jackd failure into designed silence. Every mechanism of that (2\*, promotion from `failed`, 5 s settle retest, stale-env inertness) is verified in code and unverified on hardware — and the review resisted treating the green 440-test suite as evidence about hardware behavior, which is exactly right.
3. **Criterion 16 drift (🟡#2) matters more than a label bug.** The touch UI is the *only* instrument-facing surface for audio settings. Right now it displays 21 ms while the graph runs 16 ms — meaning any future "latency feels wrong" report will be debugged against numbers the UI invented. The Gate B log already recorded 12 drift failures and they were waved through as "pre-existing"; the review correctly identified that dismissal as the real process failure.
4. **The untracked looper spec (🟡#4) — the review caught that the *newest-looking* document is the stalest.** Four concrete contradictions, each verified above (§D.5, criterion 11, D.5.2, D.5.3). Left uncommitted, Phase 2 Task 0 starts from a spec whose guard analysis describes code that no longer exists.
5. **The watchdog probe math (🟡#3)** — verified, and the fix (probe once per pass, count wall-clock) is the right shape.

## Prioritized Action Matrix

| Priority | Issue | Verdict | Effort | Depends On |
|----------|-------|---------|--------|------------|
| **P0** | Hold `yolo/jack-drop-alsa-fallback` merge until Gate C soak runs (2\*, 2b/2b2 @ 5 s settle, 15-from-`failed`, 12 stale-env) — spec-mandated, unverified hard-failure path | ✅ | Pi bench session (~half-day with Mitch) | Mitch + hardware |
| **P0** | jackd duplex open: add playback-only `-P` (after `-d alsa`) to `start-jackd.sh:43-44`, then run the blocked 5b/14 soak rows | ✅ | Quick fix + Pi verification | Physical rewire (already the 5b/14 blocker); decide whether any future client needs capture on the same card — record in spec |
| **P1** | Commit + amend `Documents/specs/looper-jack-client-spec.md`: §D.5 (no ALSA operator choice), criterion 11 (drop `MPE_AUDIO_ENGINE` from verification; fix grep to catch all guard symbols), D.5.2 (current guard message), D.5.3 (current `looper_guard_blocked` signature) | ✅ | Quick fix (one commit, four edits) | None |
| **P1** | Criterion 16 UI drift: point `surge_audio.py` read-back/labels at `MPE_JACK_BUFFER`/`MPE_JACK_PERIODS` with ×periods latency math; keep seeding `MPE_SURGE_BUFFER_SIZE` (calibration + MIDI offset still read it) but stop presenting it as the playing latency; add tests for the JACK enum validators | ✅ | Half-day | None |
| **P1** | Watchdog probes: probe once per reconcile pass, count wall-clock not iterations, and wrap the bare `jack_lsp` at `audio-engine.sh:432` in `timeout 3` (M1) | ✅ + M1 | Quick fix | None |
| **P2** | Failed-reason downgrade guard in `mpe_engine_state_write` callers (keep `supervisor-exhausted`) | ✅ | Quick fix | None |
| **P2** | Comment-hygiene pass: `99-usb-audio.rules:1-5`, `uac2-stall-watchdog.sh:4-6`, `engine-guard.sh:7` date + "nine files" (M4), `set-surge-audio.sh:72` dead `_old_buffer`; CHANGELOG 438→440; README Pi 5 vs Pi 4B disambiguation | ✅ | Quick fix | None |
| **P2** | Spec the budget-reset rule (one sentence in the D3 cooldown section) and the 96 kHz enum decision | ✅ | Quick fix | None |
| **P2** | Consolidate the looper predicate to one bash home + Python twin with extended parity test | ✅ | Quick fix | Naturally lands with Phase 2 Task 12 |
| **P3** | Mixin attribute-ownership pass on `touch_browser_app.py` (19 mixins); document who owns `engine_hud_rect` etc. | ✅ | Half-day | Before next big UI feature |
| **P3** | Cache one Surge `--list-devices` listing per boot across jackd-prestart + start-surge-cli (crosses unit boundary — hand a file, not a variable) | ⚠️ | Half-day | Optional; boot-time win only |
| **P3** | `start-surge-cli.sh` optimistic `ok` publish (M2): verify SURGE_PID survived the first 2 s before writing `state=ok` | Missed | Quick fix | None |

## Disagreements and Judgment Calls

1. **"Stop seeding `MPE_SURGE_BUFFER_SIZE`" is wrong as prescribed (claim 11, ❌).** The key is retired *as the graph period* but is a live input to calibration (`calibrate-patch-normalization.py:422,457`) and the MIDI output-offset auto-derivation (`midi_sync.py:58`, `mpe.env.example:142`), and `mpe.env.example:66-71` documents exactly that contract. Deleting the seed forces silent fallback to hardcoded 1024s in those paths. The right fix keeps the seed (re-commented as a calibration/MIDI knob) and repairs the UI's read-back. This is the one place the review's "delete it" instinct outran the repo's own documentation.
2. **"`--buffer` should write the JACK key only" is debatable.** Writing both keys keeps the touch buffer picker functional for its remaining consumers (MIDI offset) while driving the graph — arguably the *desired* one-knob UX for an appliance, at the cost of formally re-coupling what D6 separated. I'd frame the fix as "relabel + repoint read-back" and leave the dual-write, rather than the review's "write the JACK key only." Reasonable engineers could land either way; the review's version is more spec-pure, mine is more UX-coherent.
3. **"76 ahead / 1 behind" (claim 34, ⚠️→❌ on the number).** The review presented a day-old number from the spec it was auditing as a fresh measurement. Direction and conclusion survive (the branch is actually *more* diverged — 32 behind), but in a review whose core virtue is verified numbers, this one wasn't verified. Same inheritance pattern as claim 35's three-file list (actually two).
4. **`logs/` ignore suggestion (claim 41, ❌)** — already implemented at `.gitignore:34`. Minor, but it's the second instance of the review asserting git state without running the check.
5. **Big-team vs solo-appliance calibration.** The review's structural warnings (19-mixin class, stringly-typed state files, triple predicate) are all *true* and all correctly rated as non-urgent — I'd go further and call them acceptable end-states for a single-maintainer instrument, not debt requiring a plan. Where the review aimed its big-team instincts correctly: test parity between bash and Python decision functions, and treating the systemd triangle as the load-bearing risk. Where they misfired slightly: the "one predicate, one home" push (🟡#5) — with a guard deletion already specced as Task 12 with an explicit symbol checklist, the triplication has a scheduled funeral.
6. **Severity framing on 🔴#1.** I'd call it High rather than Critical only because the affected profile is already blocked on a physical rewire — nothing ships broken that isn't already blocked. If the rewire lands before the fix, it becomes Critical instantly. Sequencing matters more than the label.

---

*Audit method note: every code claim above was verified by direct read of the cited file at `1ab9f55`; every git claim by running the command (`git status --porcelain`, `git rev-list --count` both directions, `git merge-base`, `git diff --name-only`, `git check-ignore -v`); the test-suite claim by re-running `mpe test local all` (440/440 OK). One claim (53) is out of scope by construction (`mpe-cli` repo).*
