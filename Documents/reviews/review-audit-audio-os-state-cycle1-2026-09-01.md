# Review Audit — Grumpy Dev Code Review: MPE harness & appliance "OS" state

**Audited artifact:** `Documents/reviews/grumpy-review-audio-os-state-2026-09-01.md`
**Codebase:** MPE-Module, branch `dev` @ `898b160` (verified — matches the review's stated HEAD)
**Related repo:** `mpe-cli` (commands/jack.sh, as cited by the review)
**Audit cycle:** 1 of 5
**Method:** every claim traced to source, line numbers checked against the live file, git history checked where cited, tests re-run, one CI behavior empirically reproduced in a scratch sandbox.

---

## Work Queue

Extracted 34 distinct, checkable claims, grouped by section:

**§1 First Impressions** — commit 305dc31 self-retraction; commit 2b7e094 global→scoped revert; install-units.sh ExecStart guard + five-day outage; mpe-jackd.service 92/60/44 line counts.

**§2 Architecture** — five virtual-card exclusion lists disagreeing; snd-dummy update hit 2/5; ALSA truth derived via JUCE round-trip; JUCE binary launched inside udev `RUN+=`; state-machine shape praised (jackd owns device, supervisor reconciles, card-ID comparison, `RuntimeDirectoryPreserve=yes`).

**§3 Code Quality** — naming/prefix consistency; **DEVICE_TIER dead code** (P0 #2); shell-hygiene matrix (`set -e/-u/pipefail` per file); causal link between missing `set -u` and DEVICE_TIER being silent; `surge_audio.py` quality + one false comment.

**§4 Code Smells** — the four P0s (4.1 idle-sink blindness, 4.2 DEVICE_TIER, 4.3 unauditable state, 4.4 SIGKILL-defeats-trap) plus three P1s (4.5 udev forks JUCE, 4.6 string-amputation device ID, 4.7 hardcoded tier-1 + no `MPE_PREFERRED_DAC`) and three P2/P3s (4.8 comment-as-changelog, 4.9 false doc claim, 4.10 hand-maintained enable list).

**§5 Logic & Business Rules** — state-machine praise (reconcile decision, stuck-failed sweep, planned-promote flag, debounce asymmetry); boot-vs-udev race; "relevance cannot see audibility" edge case; rollback is best-effort (no generation counter/snapshot).

**§6 Test Strategy** — file/line counts (154 vs 164 internal inconsistency); "more test code than production code"; `test_detect_jack_device.py` parameterization claim; APC-mini re-assertion in 02f2898; zero tests / four call sites for `mpe_physical_playback_card_present`; no composition tests; no audibility assertion; **SC2154-would-have-caught-it claim**.

**§7 Security & Performance** — gitleaks configured; sudoers scoped to named units; `set-surge-audio.sh` allowlist validation; no-forks doctrine honored in watchdog, violated in udev path.

**§8 Developer Experience** — docs-as-asset claim; CI-can't-execute-the-real-layer claim; `patch_browser_ui.py` size callout.

---

## Claim Verification

### §1 — First Impressions

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | `305dc31` "retracts its own previous three commits because the measurement could not distinguish the two outcomes" | ✅ Confirmed | `git log -1 --format=%B 305dc31`: *"Correction to the three commits above. I picked snd-aloop on a measurement that could not tell the two outcomes apart..."* — near-verbatim match. |
| 2 | `2b7e094` "reverts a global setting because it was 'a global answer to a one-device fault'" | ✅ Confirmed | Subject line: `audio: 64x2 is a Sound Blaster problem, not everyone's problem` — consistent with the paraphrase; scope (a per-device vs. global setting) matches the commit's stated intent. |
| 3 | `install-units.sh` refuses to enable a unit whose `ExecStart` doesn't exist, and names a five-day outage | ✅ Confirmed | `scripts/install-units.sh:192-194`: *"An enabled unit whose ExecStart does not exist is the worst of both worlds... That is how mpe-looper.service skipped every boot for five days unnoticed."* Exact wording. |
| 4 | `mpe-jackd.service` is 92 lines, 60 comment; 44 lines byte-identical in `surge-xt-cli.service` | ⚠️ Partially True | `wc -l config/mpe-jackd.service` = 92 (exact). `grep -c '^\s*#'` = 60 (exact). The "Audio cores" (E1) block is confirmed byte-identical between the two units (`diff` → no output) but is **42 lines**, not 44 — a minor overcount, not material to the finding. |

### §2 — Architecture & Structure

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 5 | Five disagreeing virtual-card exclusion lists (table) | ✅ Confirmed | Verified every cell against the live file: `detect-jack-device.sh:163` → `grep -viE 'Loopback\|Dummy\|vc4hdmi\|UAC2'` (exact line, exact pattern). `audio-engine.sh:444` → `vc4hdmi* \| UAC2Gadget \| UAC2 \| Loopback` (exact). `audio-engine.sh:461` → `grep -viE 'Loopback\|vc4hdmi\|UAC2'` (exact). `mpe-cli/commands/jack.sh:74` → `grep -viE ' (Headphones\|vc4hdmi[0-9]*\|UAC2Gadget\|Loopback)$'` (exact line; review's `vc4hdmi*` is a light paraphrase of `vc4hdmi[0-9]*`). `detect-audio-device.sh:251` → `grep -viE 'Default Audio Device\|Dummy'` plus a preceding `$GADGET_GREP` exclusion (exact). Every line number the review cited for this table is correct to the line. |
| 6 | "When snd-dummy arrived 2026-08-30, two of five got updated" | ✅ Confirmed | `detect-jack-device.sh:163` and `detect-audio-device.sh:251` both include `Dummy`. `audio-engine.sh:444` and `audio-engine.sh:461` and `mpe-cli/commands/jack.sh:74` do not. That is 2 of 5 — matches exactly. |
| 7 | ALSA truth derived from a JUCE round-trip; `detect-jack-device.sh` string-amputates a display string to recover a fact `/proc/asound/cards` states directly | ✅ Confirmed | `scripts/detect-audio-device.sh` calls `surge-xt-cli --list-devices` and greps `"Output Audio Device"` lines (lines 32-46). `scripts/detect-jack-device.sh:76-85` (`_device_name_hint`) does exactly the described chain: strip everything before the last `" on "`, strip `ALSA.` prefix, strip `, USB Audio*` and `;*` suffixes — four sequential string amputations, as claimed. |
| 8 | A full JUCE binary launch runs inside a udev `RUN+=` | ✅ Confirmed | `config/99-usb-audio.rules` → `restart-audio-graph.sh` → `mpe_graph_restart_is_relevant` → `detect-jack-device.sh` → `detect-audio-device.sh`, which runs `timeout 5 "$SURGE_CLI" --list-devices`. Chain confirmed by direct read of all four files. |
| 9 | Lifecycle-shape praise (jackd owns device, supervisor reconciles, card-ID comparison not `hw:N`, `RuntimeDirectoryPreserve=yes`) | ✅ Confirmed | `restart-audio-graph.sh:66-77` explicitly documents and implements ID-based comparison ("Compare by card ID, never by hw:N... ALSA frees and reuses card indices"). `config/mpe-jackd.service` sets `RuntimeDirectoryPreserve=yes` with a comment explaining why. `mpe_engine_reconcile_decision` (audio-engine.sh:704) implements cooldown/settling/exhaustion states as described. |

### §3 — Code Quality

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 10 | Naming good, `mpe_` prefix consistent | 🔍 Can't Verify (accepted on spot-check) | Every function read during this audit (`mpe_physical_playback_card_present`, `mpe_engine_state_write`, `mpe_bound_card_id`, etc.) follows the convention. A full repo-wide audit of every symbol was out of scope here; no counter-example was found. |
| 11 | `DEVICE_TIER` dead code at `start-surge-cli.sh:54`, assigned nowhere, writer died in `5b4f24b` | ✅ Confirmed | `scripts/start-surge-cli.sh:54`: `if [ "$DEVICE_TIER" = "0" ]; then` — exact line. Repo-wide grep (`grep -rn DEVICE_TIER .`) returns exactly this one line plus the review document itself. `git show 5b4f24b -- scripts/start-surge-cli.sh` shows the diff deleting `DEVICE_TIER=$(echo "$result" \| grep "^TIER=" \| cut -d= -f2)` and every other reference, as part of removing ALSA entirely. Confirmed with primary evidence, not just the reviewer's `git log -S` claim. |
| 12 | Shell-hygiene matrix: `set -euo pipefail` in `detect-audio-device.sh`/`set-surge-audio.sh`; `set -uo pipefail` in `detect-jack-device.sh`/`jackd-prestart.sh`/`restart-audio-graph.sh`; nothing in `start-surge-cli.sh`/`surge-watchdog.sh` | ✅ Confirmed | Direct `grep -n "^set "` on all six files reproduces this exact matrix. `start-surge-cli.sh` and `surge-watchdog.sh` have no `set` line at all. |
| 13 | "That is why the DEVICE_TIER bug is silent instead of loud" | ✅ Confirmed | Logical consequence of #12: under `set -u`, referencing an unset `$DEVICE_TIER` would abort the script with an error; without it, it silently expands to `""` and the `[ "" = "0" ]` test is always false. Both conditions independently verified above. |
| 14 | `surge_audio.py` clean/typed; one false comment | ✅ Confirmed | See §4.4 below — the comment at line 31 is demonstrably false. |

### §4 — Code Smells (the four P0s, verified in depth)

#### 4.1 — `mpe_physical_playback_card_present` blind to the idle sink

| Claim | Verdict | Evidence |
|---|---|---|
| Function omits `Dummy` from its exclusion list | ✅ Confirmed | `scripts/lib/audio-engine.sh:461`: `grep -viE 'Loopback\|vc4hdmi\|UAC2'` — no `Dummy`. |
| `snd-dummy` loads at `sysinit.target`, before USB enumeration | ✅ Confirmed | `config/modules-load.d/mpe-idle-sink.conf` contains `snd-dummy`. `systemd-modules-load.service` (the consumer of `modules-load.d`) is `WantedBy=sysinit.target`, `Before=sysinit.target` — one of the earliest units in boot, well before USB device probing (which is asynchronous and can take seconds for composite/hub-attached devices) has necessarily finished. `config/mpe-jackd.service` itself documents this exact hazard: *"sound.target only means the sound subsystem is up, not that the USB DAC has enumerated."* |
| Prestart wait loop (`jackd-prestart.sh:42`) is defeated — exits immediately (`waited=0`) whether or not a real DAC is present | ✅ Confirmed | Read the loop directly: `while ! mpe_physical_playback_card_present; do ... done`. Since the predicate returns true the instant `snd-dummy` (or any HDMI-only tree) is visible in `/proc/asound/cards`, the loop body never executes on any boot where a card already exists in `/proc/asound/cards` regardless of whether it's a real DAC. |
| Tier 3 (`detect-audio-device.sh`) resolves to `Dummy` when no real DAC has been matched by tiers 1/2 | ✅ Confirmed | `detect-audio-device.sh:213`: `DEVICE=$(echo "$DEVICE_LIST" \| grep -iE 'Dummy' \| head -1 \|\| true)`. `detect-jack-device.sh:135-136` deliberately matches `Dummy` at tier 3 with an explicit comment: *"Here it is not an accident, it is what tier 3 asked for."* A dedicated test (`test_tier_3_resolves_the_dummy_card_as_the_pi5_idle_sink`) confirms this is intentional, tested behavior — the **bug is not that tier 3 selects Dummy**, it's that the boot-gate predicate can't tell "Dummy because nothing better exists yet" from "Dummy because nothing better exists, period." |
| No downstream rescue exists (watchdog, `mpe_engine_stuck_failed_maybe_sweep`, udev relevance check) | ✅ Confirmed, and worse than the review states — see "What the Review Missed" below. |
| Misdiagnosis: `jackd-prestart.sh:69` reports `no-card-resolved` when truth is `no-device` | ✅ Confirmed (logically sound, narrower than stated) | `jackd-prestart.sh:65-72` branches on `mpe_physical_playback_card_present` to choose between `no-card-resolved` and `no-device` as the failure reason. Since the predicate misclassifies Dummy-only as "physical," a boot with nothing but the idle sink and a hard detection failure (rare — tier 3 would normally match Dummy first) would report the wrong reason. The review's framing ("reintroduced by the fix for it," tying it to `6ca9b5a`) is more a thematic callback than a literal reuse of the same code path — `6ca9b5a`'s stated aim was eliminating a different misleading message ("no ALSA card matches tier '4'"). The underlying mechanism claimed here is real; the historical attribution is rhetorical. |

**Severity: Critical (P0), agree with reviewer.** This is the mechanism that keeps a cold boot with a DAC attached silent by construction, with no independent path that self-corrects.

#### 4.2 — `DEVICE_TIER` read and never written

| Claim | Verdict | Evidence |
|---|---|---|
| `start-surge-cli.sh:54` reads `$DEVICE_TIER`; no writer exists anywhere in the repo | ✅ Confirmed | Repo-wide `grep -rn DEVICE_TIER` returns exactly one line (the read) plus the review doc. `lib/uac2-lazy-route.sh` (the sourced file immediately above the read) defines no such variable — read in full, confirmed. |
| Writer died in `5b4f24b` | ✅ Confirmed | Diff shows `DEVICE_TIER=$(echo "$result" \| grep "^TIER=" \| cut -d= -f2)` deleted along with the rest of the ALSA-selection block, in the commit that made JACK the sole audio engine. |
| Appliance unconditionally writes route `analog` | ✅ Confirmed | Since `$DEVICE_TIER` is always empty (unset, no `set -u`), `[ "$DEVICE_TIER" = "0" ]` is always false, so `surge_audio_route_write analog` always runs. |
| Fix — read `TIER=` from `/run/mpe/jack-device`, which `jackd-prestart.sh` already writes | ✅ Confirmed, and the fix is immediately actionable | `jackd-prestart.sh:78-83` writes `JACK_DEVICE=`, `JACK_CARD_ID=`, **and** `TIER=$JACK_TIER` into `$DEVICE_FILE` (`/run/mpe/jack-device`) today. The reviewer's proposed fix requires no new plumbing — the field already exists and is already correct at write time. |

**Severity: Critical (P0), agree with reviewer.** This is a real, currently-dead code path (UAC2 gadget routing at tier 0), confirmed by primary git evidence rather than the reviewer's summary of it.

#### 4.3 — Published state cannot distinguish "playing" from "silent"

| Claim | Verdict | Evidence |
|---|---|---|
| `engine.state` fields are `engine/active/state/reason/looper` (+`updated`) | ✅ Confirmed | `mpe_engine_state_write` (audio-engine.sh:216-261) writes exactly `engine=`, `active=`, `state=`, `reason=`, `looper=`, `updated=`. No card/tier field. |
| `jack.state` fields are `started/device/period/periods/rate`, `device` is `hw:N` | ✅ Confirmed | `mpe_jack_state_write` (audio-engine.sh:373-389) writes exactly those fields. `start-jackd.sh:39` calls it with `$HW_DEV`, which is `JACK_DEVICE` from `/run/mpe/jack-device` — literally `hw:8` for the idle sink, per the doc comment on the idle sink's `index=8`. |
| Neither file records card ID, tier, or audibility; Python-side readers (`patch_browser/audio_engine.py`, `session_snapshot.py`) add no derived audibility field | ✅ Confirmed | Read `patch_browser/audio_engine.py` in full — it parses the same KEY=value schema with no additional fields. `grep -in "card\|tier\|Dummy\|audibl" patch_browser/session_snapshot.py scripts/session-snapshot-publisher.py` returns nothing. |
| DECISIONS.md doctrine quote, and `install-units.sh` quoting it | ✅ Confirmed | `Documents/DECISIONS.md:324`: `## 2026-08-15 — A reading that looks the same whether or not it means anything`. `scripts/install-units.sh:50`: `# (Documents/DECISIONS.md 2026-08-15: a state that reads the same broken or fine).` Both exist exactly as cited. |

**Severity: Critical (P0), agree with reviewer.** Confirmed independently — this is the most structurally important finding in the review, because it means every "the appliance says it's fine" signal (systemd, HUD, `mpe engine status`) is provably unable to distinguish the exact failure mode in 4.1 from a working instrument. Directly on-doctrine per `AGENTS.md`'s "measurement integrity" framing.

#### 4.4 — Rollback trap defeated by SIGKILL

| Claim | Verdict | Evidence |
|---|---|---|
| `trap _restore_env_on_death EXIT INT TERM HUP` at `set-surge-audio.sh:136` | ✅ Confirmed | Exact line, exact text. |
| `subprocess.run(timeout=...)` in Python delivers SIGKILL on timeout | ✅ Confirmed (documented CPython behavior) | CPython's `subprocess.run` catches `TimeoutExpired` from `communicate()` and calls `process.kill()`, which on POSIX sends `SIGKILL`. This is standard, version-stable behavior, not appliance-specific — no live hardware needed to confirm it. |
| SIGKILL cannot be added to the trap list and cannot be caught | ✅ Confirmed | Basic POSIX fact, independent of any branch analysis: SIGKILL is never deliverable to a signal handler. This alone is sufficient to confirm the core claim regardless of which of the two branches below is live. |
| False comment at `surge_audio.py:31` ("The script now traps its own death and rolls back, so a kill is survivable") | ✅ Confirmed | `patch_browser/surge_audio.py:31` reads exactly that (review cited lines 26-32 as the block; the specific false sentence is on line 31). |
| Two-branch consequence analysis (sudo execs the script vs. sudo forks a monitor) | 🔍 Can't Verify which branch is live on the appliance | Both branches are plausible under standard Linux `sudo` semantics, and I could not test empirically: this sandbox has no passwordless `sudo` (`sudo -n true` → "interactive authentication is required"), and the Pi itself is explicitly off-limits per this task's constraints. The review itself hedges with conditional language ("If sudo exec'd... If sudo forked...") rather than asserting one — that hedge is appropriate given the code doesn't resolve it and I couldn't either. **This does not weaken the P0**: as shown above, the core claim (trap cannot catch SIGKILL) needs no branch resolution to be true, and the code comment's confident, false "a kill is survivable" statement is independently confirmed regardless of branch. |

**Severity: Critical (P0), agree with reviewer.** The measured incident cited in the script's own comment (`MEASURED 2026-09-01: --buffer 64... The rig booted dead`) is real, in-repo, dated evidence of a bad env value surviving a kill — confirming this isn't theoretical.

### §4 — P1/P2/P3 items

| # | Claim | Verdict | Evidence |
|---|---|---|
| 4.5 | udev forks a JUCE synth on every DAC plug via `99-usb-audio.rules` → ... → `surge-xt-cli --list-devices` | ✅ Confirmed | Full chain read and traced (see #8 above); `timeout 5` wrapper around the JUCE launch confirmed at `detect-audio-device.sh` invocation site. |
| 4.6 | Device policy is string-matching against a JUCE display format instead of `/sys/class/sound/cardN/id` | ✅ Confirmed | Same evidence as #7. `/sys/class/sound/card${idx}/id` is in fact used elsewhere in the codebase (`restart-audio-graph.sh:83-84`, `mpe_bound_card_id`) — proving a direct, non-lossy source of truth already exists in the repo and simply isn't used for this path. |
| 4.7 | Tier 1 hardcodes "Sound Blaster Play! 3"; tier 2 is `grep -i "usb" \| head -1` of an unordered list; no `MPE_PREFERRED_DAC` anywhere | ✅ Confirmed (line number is approximate) | `detect-audio-device.sh:99` (review cites :106 — off by ~7 lines, same statement block, not material) hardcodes the product name. Tier 2 selector at line 146 is exactly `grep -i "usb"` with `head -1` at the end of a five-line filter chain. Repo-wide `grep -rn MPE_PREFERRED_DAC` (excluding the review doc) returns nothing. |
| 4.8 | Comment-as-changelog; 305dc31 updated prose everywhere `snd-aloop` appeared and missed the two `grep -viE` lists | ✅ Confirmed | Comment ratios independently confirmed (§1 #4). `305dc31`'s diff is a documentation/behavior correction commit; it does not touch `audio-engine.sh:444` or `:461`, consistent with the claim that those two lists were missed. |
| 4.9 | `docs/USB-AUDIO-HOST.md:168` falsely claims idle sink "loaded on every deploy"; only caller is `bootstrap-pi5-looper.sh`; `deploy-all.sh` doesn't call it | ✅ Confirmed | Exact text at that exact line. `grep -rln install-idle-sink` returns only `bootstrap-pi5-looper.sh` (caller), `detect-jack-device.sh` (comment reference only), and the script itself. `deploy-all.sh` has zero references to `install-idle-sink` or `snd-dummy`. |
| 4.10 | `install-units.sh` enable-state is a hand-maintained list | ✅ Confirmed | `ENABLED=(...)` array at line 29, with an explicit maintenance-burden comment at line 14 ("update ENABLED/DISABLED below or the next restore silently reverts it"). |

### §5 — Logic & Business Rules

| # | Claim | Verdict | Evidence |
|---|---|---|
| 15 | State-machine praise: `mpe_engine_reconcile_decision`, `mpe_engine_stuck_failed_maybe_sweep`, planned-promote flag, `(card, action)` debounce with asymmetry argument | ✅ Confirmed, with one important addendum | All four mechanisms exist as described (`audio-engine.sh:704`, `:825`, `mpe_planned_promote_flag_set`, `restart-audio-graph.sh` debounce block with its explicit "unplug→replug inside the window would drop the add" comment). **However**, `mpe_engine_stuck_failed_maybe_sweep` — which the review cites here as a *strength* — is itself silently defeated by the exact 4.1 bug. See "What the Review Missed." |
| 16 | Boot-vs-udev race: cold-boot `add` event can fire before `mpe-jackd` is up; `mpe_bound_card_id` fails; relevance says "restart (fail loud)"; `systemctl restart --no-block` races the unit systemd is concurrently starting | ✅ Confirmed (code-path exists; timing/frequency needs hardware) | Traced directly: `mpe_bound_card_id()` (`restart-audio-graph.sh:83-91`) returns 1 when `ps -o args= -C jackd` finds no jackd process — true before the unit has started. `mpe_graph_restart_is_relevant` then returns 0 with reason `"jackd binding unresolved — restarting (fail loud)"` (line 99-101). The subsequent `systemctl restart --no-block` against a concurrently-starting unit is a real, traceable code path. The actual probability of this race firing on a given boot is a hardware-timing question — 🔍 Can't Verify without the Pi. |
| 17 | "Relevance cannot see audibility" — bound-to-Dummy + later DAC plug + detection still mid-enumeration → relevance says "not relevant" | ⚠️ Partially True | The code path is real: `mpe_graph_restart_is_relevant` compares `desired` (from a fresh `detect-jack-device.sh` call) against `bound`; if both resolve to `Dummy` at that instant, it returns "not relevant." But by the time a `pcmC*D*p add` udev event fires for a *new* card, that card is already registered in `/proc/asound/cards`, and `detect-audio-device.sh` calls a **fresh** `surge-xt-cli --list-devices` (not a cached JUCE session) — so the race window where JUCE itself hasn't yet seen the new device is narrow and timing-dependent. The review is honest that this requires "if detection is still mid-enumeration" — a real but unquantified condition. 🔍 the exact window size can't be verified without hardware. |
| 18 | Rollback is best-effort: no generation counter, no known-good snapshot, no boot-time validation; `_prev_buffer` read from the file being mutated, so failure compounds | ✅ Confirmed | Directly visible in `set-surge-audio.sh:90` (`_prev_buffer` read from `$ENV_FILE` via `mpe_source_appliance_env`, the same file about to be mutated) and the surrounding comment block (lines 93-112) which documents this exact compounding failure and the 2026-09-01 measured incident. |

### §6 — Test Strategy & Execution

| # | Claim | Verdict | Evidence |
|---|---|---|
| 19 | "164 test files, ~30,000 lines... 104 audio-lifecycle tests pass while the instrument is silent" | ⚠️ Partially True — internal inconsistency in the review itself | `find tests -name "*.py" -o -name "test_*.sh" \| wc -l` = **164** (exact) and total lines = **29,966** (matches "~30,000" exactly). But the review's own **Coverage** section at the top states *"most of the 154 test files"* — 154 vs. 164 is an internal contradiction within the review document, not a codebase error. Minor, but the kind of detail a "meticulous" review should catch in itself. Re-ran the cited command exactly: `python3 -m unittest tests.test_audio_engine tests.test_detect_jack_device tests.test_detect_audio_device tests.test_engine_lifecycle_ownership` → **"Ran 104 tests in 7.036s / OK"**. Confirmed exactly. |
| — | "more test code than production code" | ❌ Incorrect | Measured directly: `scripts/` + `patch_browser/` + `native/` (all `.sh`/`.py`) = **69,005 lines**. `tests/` = **29,966 lines**. Production code is more than **2.3×** the size of the test suite, not smaller than it. This claim does not survive a `wc -l`. |
| 20 | `test_detect_jack_device.py` parameterizes the card tree and pins the real 2026-08-30 Pi 5 card list | ✅ Confirmed | Verified by running the suite (`Pi5IdleSinkResolutionTests`, `Pi5IdleSinkTests` test classes exist and pass, with docstrings referencing the exact scenario). |
| 21 | `02f2898` re-asserted the APC-mini exclusion "so a fix that worked by loosening the last-resort filter would fail instead of pass" | ✅ Confirmed | `git log -1 --format=%B 02f2898`: *"...with the APC-mini exclusion re-asserted against it so a fix that worked by loosening the last-resort filter would fail instead of pass."* Verbatim match — the reviewer quoted the commit message directly and accurately. |
| 22 | `mpe_physical_playback_card_present` has zero tests; "four call sites" | ⚠️ Partially True | `grep -rn physical_playback_card tests/` → no matches, confirmed zero tests. Call-site count: `grep -rn mpe_physical_playback_card_present scripts/ config/` finds the **definition** (line 457) plus **three** call sites (`jackd-prestart.sh:42`, `jackd-prestart.sh:69`, `audio-engine.sh:833`). "Four call sites" appears to count the definition as a site — minor imprecision, doesn't change the severity. |
| 23 | No composition tests across modules-load → prestart → detect → start-jackd | ✅ Confirmed | No test file exercises this multi-unit sequence; every test I ran or found operates on a single script/function in isolation with synthetic inputs (e.g., `MPE_ASOUND_CARDS` pointing at a fixture file), not the actual boot ordering. |
| 24 | Nothing asserts audibility / bound card is not virtual | ✅ Confirmed | Consistent with #22 and the state-schema finding in 4.3 — no test references `physical_playback_card`, and no state field exists for a test to assert against even if one were written. |
| 25 | "CI runs shellcheck; SC2154 would flag it [DEVICE_TIER]. Check whether it is disabled or the file excluded." | ❌ Incorrect (conclusion happens to be right; stated mechanism is wrong) | Read `.github/workflows/test.yml` — CI runs `shellcheck -S error -x "${_sh[@]}"`. The review implies the `-S error` severity floor is the reason SC2154 (a "warning"-level check) doesn't fire, and suggests checking for a disable/exclude. **That is not what's happening.** I reproduced the exact scenario in a scratch sandbox (dynamic `SCRIPT_DIR`-based `source` with a `# shellcheck source=` directive, mirroring the real files exactly) and found shellcheck **never flags SC2154 for a bare uppercase variable name at any severity level, with or without `-x`**, because shellcheck has a built-in heuristic that treats `SCREAMING_SNAKE_CASE` variables as presumed-external/environment variables and specifically suppresses "referenced but not assigned" for them. I confirmed this by testing the identical construct with a lowercase name (`$device_tier`), which SC2154 **does** flag at default severity. So `DEVICE_TIER` was never going to be caught by shellcheck at all, regardless of the CI's severity flag, and there is no "disabled or excluded" setting to go find. The `-S error` flag is a real, independent gap (it does suppress genuine warning-level findings), but it is not the cause here. |

### §7 — Security & Performance

| # | Claim | Verdict | Evidence |
|---|---|---|
| 26 | Gitleaks configured; sudoers grants in `provision-mpe-agent.sh` scoped to named units | ✅ Confirmed | `.gitleaks.toml` exists at repo root. `scripts/pi/provision-mpe-agent.sh:92`: `echo "$AGENT_USER ALL=(root) NOPASSWD: /usr/bin/systemctl $verb $u.service"` — scoped to a specific verb + unit, not a blanket grant. (Note: the file lives at `scripts/pi/provision-mpe-agent.sh`, not `scripts/provision-mpe-agent.sh` — the review doesn't give a path, so this is not an error on its part.) |
| 27 | `set-surge-audio.sh` validates with a strict allowlist before writing | ✅ Confirmed | `is_valid_buffer`/`is_valid_periods`/`is_valid_sample_rate` (lines 45-65) are closed `case` statements over fixed value sets, checked before any `_update_env_var` call. |
| 28 | No-forks doctrine honored in `surge-watchdog.sh` | 🔍 Can't Verify precisely, plausible on read | `JACK_PROBE_INTERVAL_S` and a batched `systemctl is-active` pattern are present in the file; confirming the *absence* of any per-cycle fork requires a full read of the watchdog's main loop, which was not fully re-audited line-by-line here. Nothing found contradicts the claim. |
| 29 | No-forks doctrine violated in the udev path (4.5), event-driven so invisible in steady-state CPU census | ✅ Confirmed | Same evidence as 4.5 — a full JUCE process launch per DAC plug/unplug event is confirmed, and being event-triggered (not a periodic loop) it would not appear in a steady-state CPU sampling window, consistent with `AGENTS.md`'s framing that the CPU doctrine specifically targets *periodic* forks. |

### §8 — Developer Experience

| # | Claim | Verdict | Evidence |
|---|---|---|
| 30-31 | Docs-as-asset; `install-units.sh --diff` affordance; `mpe-cli` well-shaped | 🔍 Can't Verify exhaustively / plausible | Consistent with everything read during this audit — docs were extensive, accurate in the specific places checked (DECISIONS.md, AGENTS.md), and `install-units.sh` does have a `--diff` mode (confirmed by reading the `MODE` handling in that file). Not independently re-verified against every doc file. |
| 32 | "Docs have started lying... once one entry lies the rest are claims rather than facts" | ✅ Confirmed as rhetoric grounded in a real finding (4.9) | The specific factual claim underneath (docs/USB-AUDIO-HOST.md:168) is confirmed false (see 4.9). The broader inference ("the rest are claims rather than facts") is reviewer editorializing, not a separate checkable claim — reasonable given AGENTS.md's own "Repo false-assertion debt" framing, but I did not audit all 45 doc files to confirm no other doc claims are false. |
| 33 | CI can't execute the layer that keeps breaking (no ALSA, no systemd, no Pi) | ✅ Confirmed | `.github/workflows/test.yml` runs on `ubuntu-latest` with `pip install python-osc`, `unittest discover`, and shellcheck — no ALSA device simulation, no systemd unit execution, no hardware. Every test that exercises `mpe_physical_playback_card_present`-adjacent logic (had there been any) would need to mock `/proc/asound/cards` via `MPE_ASOUND_CARDS`, which is exactly the fixture pattern the existing tests use for tier detection — but as confirmed in #22, that pattern was never applied to this specific predicate. |
| 34 | `patch_browser_ui.py` at 63KB "worth naming on principle" | 🔍 Can't Verify substantively | File size check confirms a large file exists at that path; "worth naming on principle" is an editorial judgment, not a factual claim to verify. |

---

## Severity Re-Assessment

| # | Issue | Reviewer Rating | My Rating | Delta | Reasoning |
|---|---|---|---|---|---|
| 1 | 4.1 idle-sink blindness | P0 | **Critical / P0** | = | Agree fully. Confirmed no downstream rescue exists, and found the rescue mechanism the review itself praises (`mpe_engine_stuck_failed_maybe_sweep`) is *also* inert for this exact failure — see below. If anything the review under-states how alone this bug is. |
| 2 | 4.2 DEVICE_TIER dead | P0 | **High, not Critical** | ↓ | This is a real, silent bug, but its blast radius is narrower than the other three P0s: it only matters when `MPE_AUDIO_PROFILE=usb-host` *and* tier resolves to 0 (host actively capturing over the UAC2 gadget). On the standalone profile (the primary gigging configuration, per AGENTS.md's framing of "Sound Blaster Play! 3" as the default hardware output) this code path is never reached at all — `surge_audio_route_write analog` is also the *correct* answer whenever tier isn't 0. It is genuinely dead/wrong code that should be fixed, but it is not on the critical path to "why is the appliance dead right now" the way 4.1/4.3/4.4 are. I'd ship it same sprint, not stop-the-line. |
| 3 | 4.3 unauditable state | P0 | **Critical / P0** | = | Agree fully, and would rank this the *most* structurally important of the four — it's the reason 4.1 and any future silent-binding bug are undetectable by any existing observability surface. Fixing this one change (add `card=`/`tier=` to `jack.state`) also gives 4.1's fix a way to be tested for regression going forward. |
| 4 | 4.4 SIGKILL defeats trap | P0 | **Critical / P0** | = | Agree fully. Confirmed via basic POSIX signal semantics (SIGKILL is untrappable, full stop) independent of the exec-vs-fork ambiguity the review appropriately declines to resolve. Backed by a real, dated, in-repo incident. |
| 5 | 4.5 udev forks JUCE | P1 | **P1, agree** | = | Real CPU-doctrine violation on an already-busy path (replug), correctly scoped as "not P0" since it's a performance/reliability risk during a specific event, not an always-silent appliance. |
| 6 | 4.6 string-amputation device ID | P1 | **P2, slightly inflated** | ↓ | This is real technical debt and the fragility is legitimate (two "broken twice this week" incidents cited), but it is a *maintainability* risk, not a currently-manifesting defect independent of 4.1/4.2. I'd bundle it into the same refactor as 4.5 rather than call it out as an equally urgent, separately-scheduled P1 — it's correctly the same underlying root cause as 4.5 (JUCE-mediated resolution), and the fix for one substantially is the fix for the other. |
| 7 | 4.7 hardcoded tier-1 / no MPE_PREFERRED_DAC | P1 | **P2, agree it's real, disagree on urgency** | ↓ | Confirmed real and confirmed there's truly no override. But this is a "the appliance behaves unpredictably with two DACs" gap, not a "the appliance is silent with the one DAC we ship" gap. Given the stated context (solo dev, one gigging rig, Sound Blaster Play! 3 is the shipped hardware), I'd treat multi-DAC ordering as backlog until a second DAC is actually part of the product, not this sprint. |
| 8 | 4.8 comment-as-changelog | P2 | **P2, agree** | = | Correctly scoped — real, causally linked to why 4.1 happened (confirmed: `305dc31` touched prose but missed the two `grep -viE` lists), but not itself a bug. |
| 9 | 4.9 false doc claim | P3 | **P3, agree, but flag it now not later** | = | Confirmed false. Low severity as code risk, but AGENTS.md's own "Repo false-assertion debt" doctrine says false docs compound — worth a five-minute fix in the same PR as anything else touching that doc, rather than truly deferred. |
| 10 | §5 boot-vs-udev race | *(not separately prioritized by reviewer)* | **P1** | new | This is a real, traced code path with a plausible mechanism for silence independent of 4.1, and it interacts with 4.1 (the review itself notes "with 4.1, boot outcome depends on that interleaving"). Deserves its own backlog line, not just a paragraph in the Logic section. |
| 11 | §6/§7 "CI can't execute the layer that keeps breaking" | *(observation, not prioritized)* | **P1** | new | This is arguably the single highest-leverage process fix available: a hermetic test harness with a fixture `/proc/asound/cards` for the *composition* (modules-load→prestart→detect→start-jackd) would have caught 4.1 before it shipped, and the existing test suite already has the fixture-injection pattern (`MPE_ASOUND_CARDS`) needed to build it cheaply. |

---

## What the Review Missed

**1. The state-machine mechanism the review praises as "genuinely good" recovery is itself silently defeated by the same 4.1 bug — doubly so.**

In §5, the review lists `mpe_engine_stuck_failed_maybe_sweep` as a strength: "handles terminal-state-plus-hardware-returned." The task brief for this audit specifically asked whether this function could rescue 4.1's failure mode. It cannot, for two independent reasons, neither mentioned by the review:

```bash
# scripts/lib/audio-engine.sh:830-834
card=0
if mpe_physical_playback_card_present; then
    card=1
fi
```

and, from `mpe_engine_stuck_failed_decision` (audio-engine.sh:788-793):
```bash
if [ "$state" != failed ]; then
    printf 'idle'
    return 0
fi
```

- **It only fires when `state=failed`.** In the 4.1 scenario, jackd binds `hw:8` successfully, Surge connects to it successfully, and `state=ok` is published — never `failed`. The sweep never triggers because nothing about the silent-Dummy boot looks like a stuck failure to this function; it looks like success.
- **Even if it did fire, it reuses the exact same broken predicate.** `card=1` would be set whenever Dummy is present, meaning this "hardware returned" check would say hardware is present and available even when only the idle sink exists — inheriting 4.1's blind spot rather than catching it.

This matters for prioritization: the review's own text (§5) implicitly offers `mpe_engine_stuck_failed_maybe_sweep` as evidence the state machine has a safety net; in fact there is no safety net anywhere in the reconciliation code for this specific failure. That raises my confidence in the review's P0 ranking of 4.1, not lowers it — but it should be stated plainly rather than left adjacent to a "genuinely good" compliment that reads, on a careless pass, as slightly reassuring.

**2. `surge-watchdog.sh` never re-evaluates device tier on a timer — only on udev events.**

Grepping the watchdog's main loop (`grep -n "detect-jack-device\|detect_audio\|while true" scripts/surge-watchdog.sh`) turns up zero calls to either detection script. The watchdog's periodic reconciliation checks only whether jackd/Surge *processes* are alive and responsive (`mpe_jack_server_ready`), never whether they're bound to the *right* device. Combined with finding #1, this means: once bound to Dummy at boot, the **only** path back to a real DAC is a udev `add`/`remove` event for that exact card, evaluated by `mpe_graph_restart_is_relevant` at that exact instant (which itself has the timing edge case the review notes in §5, item "relevance cannot see audibility"). There is no periodic self-healing at all. This should be named as its own finding, not buried as a side-comment under the udev-relevance discussion.

**3. `install-idle-sink.sh`'s own comment misstates its module's index.**

```
# install-idle-sink.sh, lines ~19-20:
# snd-dummy, not snd-aloop -- see config/modprobe.d/mpe-idle-sink.conf for the
# measurement. ...
# The options file above supplies index=7; ...
```
But `config/modprobe.d/mpe-idle-sink.conf` actually reads `options snd-dummy index=8 pcm_substreams=2`, with its own comment explicitly stating *"index=8 keeps it clear of the real cards and of the calibration loopback (index=7)."* So `install-idle-sink.sh`'s comment cites the *loopback's* index, not the idle sink's own index. Harmless (a stale/copy-pasted comment, not functional code), but it's exactly the "discipline stored in prose, not in code" pattern the review's own §1 calls out — a small, concrete instance of the same disease, in a file the review says it read in full.

**4. The "more test code than production code" claim is measurably false, and worth correcting because it undercuts an otherwise sound point.**

Reviewer's §6 opens with this line to set up the "coverage followed convenience, not risk" argument. Measured: `scripts/` + `patch_browser/` + `native/` = 69,005 lines; `tests/` = 29,966 lines. Production code is 2.3× the test suite, not smaller. The review's underlying point (elaborate test suite, wrong things tested) stands on its own without this claim — it just shouldn't be asserted as measured fact when a `wc -l` contradicts it.

**5. The review's own Coverage section is internally inconsistent (154 vs. 164 test files).**

Minor, self-contained, worth a one-line fix in the review document itself before it's used to drive further work.

**Nothing found in security.** I looked specifically for auth bypass, unvalidated input reaching a shell command, and injection risk around `_update_env_var`'s `sed` substitution (the review flags this exact spot as correctly-guarded) — confirmed the guard is real and sufficient for the current input surface (fixed value sets via `case` statements, no free-form value ever reaches `_update_env_var`). No additional security gaps found beyond what the review already covered.

---

## What the Review Got Right (And Why It Matters)

**4.3 (unauditable state) is the finding to fix first, not fourth.** The review lists it third among the P0s and gives it equal weight to the other three, but it's structurally prior to all of them: fixing 4.1 without also fixing 4.3 means the *next* silent-binding bug (there will be one — 4.6/4.7's fragility guarantees it) ships with the exact same blindness. Fixing 4.3 first means 4.1's own fix can carry a regression test that actually asserts the appliance is audible, not just that a card exists — which today, per confirmed finding #24, is not something any test in the repo can assert, because the field to assert against doesn't exist yet.

**4.1 and 4.4 compound in a way the review states but doesn't fully spell out.** A cold boot that binds Dummy (4.1) publishes `state=ok` (4.3), so an operator (or an agent) troubleshooting via `mpe engine status` sees a healthy appliance. If that same operator then tries to fix it by changing the buffer size via the touch UI, and that operation times out (a plausible scenario if it's simultaneously fighting the Dummy/real-DAC boot race in §5), they now hit 4.4 on a system that already looked fine. Each P0 individually is bad; the four together describe an appliance that can present as fully green from every currently-available signal while transitioning through multiple independent failure modes at once. That is the strongest possible argument for the review's own priority order (fix all four before doing anything else), even stronger than the review's own verdict paragraph states it.

**The `305dc31` self-retraction citation is a genuinely good piece of evidence**, and I traced it to primary source rather than trusting the paraphrase — it holds up exactly as characterized, including the specific measurement methodology (`aplay -D hw:7` timing) that led to the wrong conclusion the first time. This is a rare case of a review citing commit history *accurately enough to use as courtroom evidence*, not just color commentary.

---

## Prioritized Action Matrix

| Priority | Issue | Verdict | Effort | Depends On |
|---|---|---|---|---|
| P0 | Add `Dummy` (and consolidate all virtual-card matching) into one shared `mpe_card_is_virtual()` predicate; all 5 call sites use it; add a composition test asserting `mpe_physical_playback_card_present` is false when only Dummy exists | ✅ Confirmed | Half-day | — |
| P0 | Add `card=`/`tier=` to `jack.state`; publish `state=degraded reason=idle-sink` when the bound card is virtual | ✅ Confirmed | Half-day | Should land before or with the item above, so the fix above can be tested against real state output |
| P0 | Make settings rollback crash-safe: write-ahead `/run/mpe/audio-settings.pending`, reconcile via `ExecStartPre` on `mpe-jackd`; correct the false comment at `surge_audio.py:31` | ✅ Confirmed | Multi-day | — |
| P0 | `mpe_engine_stuck_failed_maybe_sweep` must not treat Dummy as "hardware returned" — feed it the corrected `mpe_card_is_virtual()` predicate as part of the P0 #1 fix, and add a test asserting the sweep does not fire (or does not count Dummy as recovery) when only Dummy is bound | new finding | Quick fix (bundle with P0 #1) | P0 #1 |
| P1 | Fix or delete `DEVICE_TIER` at `start-surge-cli.sh:54`: read `TIER=` from `/run/mpe/jack-device` (plumbing already exists); add `set -uo pipefail` to `start-surge-cli.sh` and `surge-watchdog.sh` | ✅ Confirmed | Quick fix | — |
| P1 | Add a composition/integration test harness (fixture `/proc/asound/cards`, fixture systemd-unit ordering) covering modules-load → prestart → detect → start-jackd, so this class of bug fails in CI, not on the gig | new finding (review names the gap, doesn't prioritize the fix) | Multi-day | — |
| P1 | Get `surge-xt-cli` out of device resolution: select from `/proc/asound/cards` + `/sys/class/sound/*/id` directly; call Surge only to translate an already-chosen card into a JUCE index. Removes JUCE-in-udev (4.5) and the string-amputation layer (4.6) together, and gives `MPE_PREFERRED_DAC` somewhere to live (4.7) | ✅ Confirmed, merged 3 review items into one refactor | Multi-day | — |
| P2 | Correct the false doc claim at `docs/USB-AUDIO-HOST.md:168` (idle sink is not loaded on every deploy — only via `bootstrap-pi5-looper.sh`); also correct the stray "index=7" comment in `install-idle-sink.sh` (should read index=8) | ✅ Confirmed (+ 1 new instance) | Quick fix | — |
| P3 | Trim/relocate the 44-line duplicated E1 comment block between `mpe-jackd.service` and `surge-xt-cli.service` into a shared doc reference | ✅ Confirmed | Quick fix | — |
| P3 | Fix the review document's own internal inconsistency (154 vs. 164 test files) and the "more test code than production" claim before this review is used to drive further planning | new finding (about the review, not the code) | Quick fix | — |

---

## Disagreements and Judgment Calls

**Disagree: 4.2 (`DEVICE_TIER`) does not belong at the same P0 tier as 4.1/4.3/4.4.** All three of the others are live, present-tense contributors to "the appliance is currently silent." 4.2 only matters under `MPE_AUDIO_PROFILE=usb-host` with the host actively capturing — a narrower, secondary configuration per the product's own docs (Sound Blaster Play! 3 standalone is the primary gigging setup). It's real dead code that produces a wrong (if currently harmless-by-coincidence, since `analog` is also correct off-tier-0) result, and it should absolutely be fixed — but calling it P0 alongside a bug that silences cold boot with the *primary* hardware config overstates its urgency. I'd keep it in the same PR/sprint (the fix is trivial and the plumbing already exists) without letting it consume "stop everything" attention.

**Disagree: the SC2154 claim as stated would have sent someone down the wrong path.** "Check whether it is disabled or the file excluded" directs an engineer to go hunting for a shellcheck config knob that doesn't exist for this reason. The actual, verifiable mechanism (shellcheck systematically exempts `SCREAMING_SNAKE_CASE` names from SC2154, regardless of severity threshold or `-x`) is a more useful thing to know, because it means **no CI severity change will ever catch this class of bug** — the real fix is either renaming the convention (not realistic here) or, more practically, adding `set -u` everywhere (which the review already correctly recommends for other reasons) so a genuinely unassigned variable fails loudly at runtime instead of relying on static analysis that structurally cannot see it.

**Disagree, mildly: grouping 4.6 (string-amputation) as a same-priority-tier P1 alongside 4.5 (udev-forks-JUCE) somewhat double-counts one root cause.** Both stem from routing device identity through `surge-xt-cli --list-devices` instead of `/proc/asound/cards` / `/sys/class/sound/*/id` directly. The Priority Backlog's own item 5 ("Get surge-xt-cli out of device resolution") already fixes both at once — I'd present 4.5/4.6/4.7 as three symptoms of one refactor in the backlog, not as three independently-schedulable P1s, so nobody accidentally ships a narrow fix for 4.6 alone and calls the udev-forking problem (4.5) done.

**Agree, with emphasis: the review's choice not to apply enterprise standards is correct and should be reinforced, not just noted.** This is a solo-developer appliance with one primary hardware target; the review correctly does not demand a device-abstraction plugin architecture, a formal state machine DSL, or CI hardware-in-the-loop infrastructure disproportionate to the team size. The one place I'd push back toward *more* rigor than the review asks for is exactly where AGENTS.md itself sets the bar higher than generic small-team practice: the "no in-band failures / positive control / negative control" measurement-discipline doctrine. None of the four P0 fixes as scoped by the review currently include a negative control (deliberately break the fix, assert the harness catches it) — per AGENTS.md's own standing rule ("Do not weaken an assertion to make a test pass" / Rule -1), every fix in this backlog should ship with one, not just a positive-path composition test.

**No disagreement on scope or coverage.** The review's own "Not read" list (`patch_browser_ui.py`, the APC/looper stack, ~40 `measure-*.sh`, `native/`, `scripts/yolo/`) is honest and appropriately scoped for a cycle-1 pass triggered by an active outage — I did not find evidence that skipping those files caused the review to miss anything material to the audio-silence bug being investigated.
