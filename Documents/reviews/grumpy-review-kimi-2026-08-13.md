# Grumpy Dev review — MPE-Module (JACK audio engine, post-Phase-1)

*Review date: 2026-08-13 (America/Toronto)*
*Reviewer: Kimi (grumpy senior dev mode), full-codebase pass*
*Branch under review: `yolo/jack-drop-alsa-fallback` @ `1ab9f55`, one commit on top of `dev` @ `daac891` (PR #49, JACK Phase 1 merge).*
*Scope: this repo only. `mpe-cli` excluded per instructions. No code modified.*

**What I read:** both audio specs in full (`Documents/specs/jack-audio-engine-spec.md`, `looper-jack-client-spec.md`), the three touch specs (browse spec in full, browse-UX and favorites specs skimmed), `AGENTS.md`, `README.md`, `docs/GIT-WORKFLOW.md`, `docs/PATHS.md`, `docs/STABLE-SETUP.md`, `CHANGELOG.md`, git history (`dev`, `main`, branch topology). Code: all of `scripts/lib/audio-engine.sh`, `engine-guard.sh`, `paths.sh`, `mpe-services.sh`; `start-surge-cli.sh`, `start-jackd.sh`, `jackd-prestart.sh`, `restart-audio-graph.sh`, `surge-watchdog.sh`, `uac2-stall-watchdog.sh`, `mic-to-uac2-bridge.sh`, `set-audio-profile.sh`, `set-surge-audio.sh`, `configure-pi-paths.sh`, `detect-audio-device.sh`, `detect-jack-device.sh`, `start-uac2-watchdog-if-needed.sh`; systemd units (`mpe-jackd`, `surge-xt-cli`, `surge-watchdog`) and `99-usb-audio.rules`; Python side: `audio_engine.py`, `engine_state_monitor.py`, `surge_audio.py`, `audio_profile.py`, `session_capture.py`, `surge_monitor.py`, `mixer.py`, `mixer_controls.py`, `touch_browser_mixer.py`, `json_store.py`, `looper_clock_monitor.py`, the touch app orchestrator and HUD draw paths; `tests/test_audio_engine.py` structure; `.github/workflows/test.yml`; `config/mpe.env.example`.
**What I sampled rather than read line-by-line:** the remaining ~40 `patch_browser/` modules (calibration, scanner, sidecars, pressure, WiFi, MIDI clock — read headers/greps only), the 60-odd other test files (structure and names, not bodies), `patch_browser_ui.py` (1456-line legacy encoder UI — headers only), deploy/setup scripts not on the audio path. Full-repo greps for `MPE_AUDIO_ENGINE`, `degraded`, `arecord`/`aplay`/`snd-aloop`, merge conflict markers, and duplicate function definitions back the drift claims.

---

## 1. First Impressions (The Gut Check)

This does not look like a codebase that "got twisted up." It looks like a codebase that survived a twist and *documented the scar tissue*. The phase structure is intact, the ALSA removal is genuinely complete (I grepped for every deleted symbol — they're gone, and there are static guard tests keeping them gone), the test suite is green (440 tests, 0 failures), and the spec amendment is unusually honest engineering writing — `RETIRED 2026-08-13` markers instead of rewriting history, with the soak log explicitly quarantined as evidence for a design that no longer exists.

The twist is real, but it's in the *branch topology and the plan's forward path*, not in the merged tree: `yolo/looper-phase0` (PR #48) is 76 commits ahead / 1 behind `dev` with a merge-base *before* Phase 1, the Phase 2 spec depends on files that only exist there, and the replacement spec (`looper-jack-client-spec.md`) is sitting **untracked in the working tree** while contradicting the amendment its sibling spec landed yesterday. The merged code is coherent. The *pipeline* is where the risk lives.

## 2. Architecture & Structure

The Phase 1 architecture is sound and, frankly, better argued than most commercial embedded audio work I've reviewed:

- **Single-engine state machine** (`ok | recovering | failed`) with state in `/run/mpe` tmpfs, atomic writes, and `RuntimeDirectoryPreserve=yes` — the spec's reasoning about *why* state can't live in the watchdog's memory (the watchdog is `BindsTo` the service it restarts) is correct and is implemented exactly as reasoned. `scripts/lib/audio-engine.sh:88-122`, `config/surge-xt-cli.service:13-14`.
- **Supervisor cooldown math is done against systemd's actual knobs** — 90 s cooldown / max 3 restarts sized to stay under `StartLimitBurst=5 / StartLimitIntervalSec=300`. Somebody did the arithmetic instead of picking a number. `patch_browser/audio_engine.py:52-76` with a bash parity twin at `scripts/lib/audio-engine.sh:369-392`, and parity *tests* proving they agree.
- **D2's "restart jackd, not Surge" rule is actually followed** in the callers I read: `set-audio-profile.sh:57`, `set-surge-audio.sh:129`, `uac2-stall-watchdog.sh:64-68`, `99-usb-audio.rules:26-27` all route through `mpe_restart_audio_graph`.
- **The `BindsTo` rejection rationale** (surge must start to *report* failure) is the kind of reasoning that separates a state machine from a pile of shell.

Structural concerns:

- The `mpe_jackd.service` ↔ `surge-xt-cli.service` ↔ `surge-watchdog.service` triangle is now the single most load-bearing thing in the product, and its correctness rests on bash sourced libraries with stringly-typed state files. It works, it's tested, and it's one distracted refactor away from subtle breakage. The `NoAlsaPathTests` grep-guards are the right instinct — extend that pattern, don't relax it.
- `main` is **42 commits behind `dev`** (`git rev-list --count main..dev`), meaning the entire JACK engine, the governor work, and months of touch UI are absent from the release line the "stable appliance" tracks. That's expected mid-phase, but the gap is now large enough that a emergency gig rollback to `main` would be a *different product*. Plan the `dev`→`main` promotion as its own tested event.
- Phase 2's entire dependency chain runs through PR #48, which predates Phase 1 and touches the three files Phase 1 rewrote most (`start-surge-cli.sh`, `detect-audio-device.sh`, `99-usb-audio.rules`). The looper spec's Task 0 (rebase) is correctly identified as the gate. Until it lands, "sound module + analog-esque mixer" is a one-client graph — a mixer with one channel.

## 3. Code Quality

The good, concretely:

- `patch_browser/json_store.py:24-43` — atomic write with `fsync` on file *and* directory. Most projects half-do this. This one doesn't.
- `scripts/lib/audio-engine.sh:160-179` — `mpe_engine_state_write` uses `${2:-unknown}` instead of `${2:?}` with a comment explaining that a status publisher killed its caller and left the instrument unsupervised. That's a bug someone *found*, and the fix carries its own rationale.
- `patch_browser/audio_engine.py` is 140 lines, readable, and every constant carries its provenance (why 5 s, why 90 s, why 3).
- Bash/Python parity is enforced by tests (`BashReconcileParityTests`), not by hope.
- Shell quoting is disciplined throughout the scripts I read; `set -uo pipefail` (and `set -e` where safe) is consistent; enum validation on every env knob (`mpe_jack_period`, `mpe_jack_rate`, `set-surge-audio.sh`) keeps the sudo/NOPASSWD surface sane.

The mediocre:

- The touch UI is a **19-mixin Frankenclass** (`patch_browser/touch_browser_app.py:67-87`). It works and it's split along sensible seams, but the mixins reach into each other's attributes freely, which is why `touch_browser_draw.py:485-489` needs defensive `getattr(self, "engine_hud_rect", Rect(0,0,0,0))` chains — the compiler of last resort is runtime attribute roulette. Not urgent; worth a "who owns which attrs" pass before the next big UI feature.
- Stringly-typed state files (`engine.state`, `surge.state`, `jack.state`, `engine-reconcile.state`) parsed by grep/cut in bash and `str.partition` in Python. Two parsers, one format, zero schema. It has held so far; the day someone writes a value containing `=` or a newline, both parsers degrade differently.

## 4. Code Smells (The Hall of Shame)

**🔴 1. jackd's duplex open almost certainly breaks the usb-host-session mic bridge — and nobody can know, because the soak row is BLOCKED.**

`scripts/start-jackd.sh:43-44`:

```bash
exec jackd -R -P"$JACK_PRIO" -s \
    -d alsa -d "$HW_DEV" -r "$JACK_RATE" -p "$JACK_BUFFER" -n "$JACK_PERIODS"
```

No `-P` (playback-only) flag. jackd's ALSA backend opens the device **duplex** by default, which means it holds the Sound Blaster's *capture* stream too. Meanwhile `scripts/mic-to-uac2-bridge.sh:54` — the entire point of the `usb-host-session` profile ("Surge on Sound Blaster; mic → USB when PC captures", per `config/mpe.env.example:39`) — does:

```bash
arecord -D "$CAP_DEV" -f S16_LE -r "$RATE" -c 2 -t raw --buffer-size="$BUF" |
```

where `resolve_blaster_mic_capture_device()` (`patch_browser/session_capture.py:66-89`) resolves `plughw:CARD=<Play3>,DEV=0` — the same card jackd now holds. ALSA `hw` opens are exclusive: pre-JACK this worked because Surge's JUCE ALSA open was playback-only ("Front output"); post-JACK the capture side is busy and the bridge will fail with `Device or resource busy`, likely in a restart loop (`mic-to-uac2-bridge.service`). Criteria 5b and 14's session-capture half are both marked **BLOCKED — physical rewire** in the Gate B log, so this regression window has never been exercised. *Fix direction:* add `-P` (playback-only) to the jackd line — or explicitly split `-P/-C` — then actually run the blocked 5b/14 soak rows; if capture on the same card is ever wanted by a future client, that decision belongs in the spec, not in a default.

**🟡 2. The touch UI sells you a buffer size the engine doesn't use.**

`patch_browser/surge_audio.py:16-19`:

```python
# Must stay in sync with the fallback in scripts/start-surge-cli.sh.
# 768 drops voices under heavy MPE polyphony on Pi 4; 512 choked outright.
DEFAULT_BUFFER = 1024
```

There is no fallback in `start-surge-cli.sh` anymore — the comment is a ghost, and worse, the whole module treats `MPE_SURGE_BUFFER_SIZE` as the buffer. The graph actually runs `MPE_JACK_BUFFER` (default 256). A fresh appliance displays "Audio buffer — 1024 · 21 ms" while playing at 256×3 = 16 ms. The seeder makes it official — `scripts/configure-pi-paths.sh:70-74`:

```bash
if [ -n "$_preserved_surge_buffer" ]; then
    echo "MPE_SURGE_BUFFER_SIZE=$_preserved_surge_buffer"
else
    echo "MPE_SURGE_BUFFER_SIZE=1024"
fi
```

writes the legacy key into every fresh `/etc/mpe/mpe.env` and never writes the JACK keys. (The Gate B soak already saw this: `mpe test pi audio` reported 12 "touch buffer label / env drift" failures, dismissed as "pre-existing.") `set-surge-audio.sh:125-128` doubles down by writing *both* keys on `--buffer`, coupling the two knobs D6 explicitly separated. *Fix:* point `surge_audio.py`'s read-back and labels at `MPE_JACK_BUFFER`/`MPE_JACK_PERIODS` (and multiply by periods in the latency label), stop seeding `MPE_SURGE_BUFFER_SIZE` in `configure-pi-paths.sh`, and have `set-surge-audio.sh --buffer` write the JACK key only.

**🟡 3. The watchdog's "bounded" wait isn't, when jackd hangs instead of dying.**

`scripts/surge-watchdog.sh:73-86`:

```bash
if ! mpe_jack_server_ready; then
    waited=0
    while [ "$waited" -lt "$RECONCILE_BUDGET" ]; do
        if mpe_jack_server_ready; then
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done
```

`mpe_jack_server_ready` internally runs `timeout 3 jack_lsp` (`scripts/lib/audio-engine.sh:208-216`). When jackd is *running but hung* (process exists, never answers), each probe burns up to 3 s, so the "15 s budget" loop is really up to ~15 × (3 + 1) = **60 s** of watchdog blockage — during which the crash-recovery arm at the top of the main loop doesn't run. The same probe is also executed up to three times per reconcile pass (`_reconcile_engine` calls `mpe_surge_on_jack_graph` → `mpe_jack_server_ready`, then `mpe_jack_server_ready` again twice). *Fix:* probe once per pass into a variable, and count wall-clock (`SECONDS` or epoch deltas) instead of loop iterations.

**🟡 4. The Phase 2 spec contradicts the amendment — and isn't committed.**

`Documents/specs/looper-jack-client-spec.md` is **untracked** (`git status`: `?? Documents/specs/looper-jack-client-spec.md`) and stale in at least four places against the 2026-08-13 amendment: §D.5 still scopes "keep `MPE_AUDIO_ENGINE=alsa` as an explicit operator choice" (the amendment deleted the variable outright); criterion 11's verification boots "with `MPE_AUDIO_ENGINE=jack` unset (default)" — a variable that no longer exists; §D.5.2 describes the guard message advising `mpe engine set alsa` (the message was rewritten); §D.5.3 analyzes `looper_guard_blocked` keying on the configured engine (the parameter was dropped). This is exactly how "the spec said so" bugs get reintroduced by a future session reading the newest-looking document. *Fix:* amend the looper spec's §D.5/criterion 11 to the JACK-only reality and commit the file — a spec that isn't in git isn't a spec, it's a rumor.

**🟡 5. One predicate, three implementations.**

The looper-guard decision — "is `MPE_LOOPER_ENABLED=1`" — exists as `mpe_looper_engine_blocked()` (`scripts/lib/engine-guard.sh:24-26`), `mpe_looper_state_label()` (`scripts/lib/audio-engine.sh:189-195`), and `looper_guard_blocked()` (`patch_browser/audio_engine.py:36-38`). The spec's own D5 rationale was "a guard per call site would rot," and then the guard itself got three homes. Today they're trivially identical; after Phase 2 lifts the guard they become three places to half-delete (criterion 11 even greps for `LOOPER_GUARD` — it won't catch `mpe_looper_state_label`). *Fix:* make `engine-guard.sh` the single bash source (have `mpe_looper_state_label` call it or move both into `audio-engine.sh`), and keep the Python twin only because the HUD needs it — with the parity test extended to cover all three.

**🟢 6. Stale comment parade.** `config/99-usb-audio.rules:1-5` still says "Automatically restart Surge XT CLI when USB audio devices are connected/disconnected" (it restarts the graph now); `scripts/uac2-stall-watchdog.sh:4-6` still says "restart Surge on the gadget" (it calls `restart_audio_graph`); `scripts/lib/engine-guard.sh:7` dates the ALSA removal "2026-08-12" (the amendment is 2026-08-13); `scripts/set-surge-audio.sh:72` assigns `_old_buffer` and never uses it. *Fix:* one comment-hygiene pass; these are the lies the next incident report will trip over.

## 5. Logic & Business Rules

- **The hard-failure design is correctly wired end to end.** `start-surge-cli.sh:147-154` publishes `state=failed` with a named reason and exits non-zero; `surge-xt-cli.service` retries on its own; the supervisor's budget caps the loop and escalates once (`surge-watchdog.sh:39-47` announces `failed` a single time, not every 5 s poll). The `BindsTo` rejection logic in the spec is implemented as specified — I verified the unit graph rather than trusting the doc.
- **The failed-state reason gets overwritten.** When the supervisor escalates with reason `supervisor-exhausted`, Surge's own `Restart=on-failure` keeps re-running `start-surge-cli.sh`, which re-publishes `state=failed reason=no-server` (`start-surge-cli.sh:151`). The HUD shows the right state but the journal/status reason loses the escalation history. Cosmetic; one line in `mpe_engine_state_write` callers ("don't downgrade reason") fixes it.
- **Rule-serving edge case done right:** `start-jackd.sh:33-41` only publishes `recovering` if nothing more specific (`ok`/`failed`) is already published — a jackd restart must not clobber Surge's state. That's the kind of multi-writer discipline state files usually don't get.
- **Profile-switch flag lifecycle is sound but subtle:** `set-audio-profile.sh:54-60` marks the flag, and it persists until Surge next starts — which, post-Phase-1, happens on the *supervisor's* schedule, not the operator's. The comment acknowledges it. Behavior is correct; the UX consequence (MIDI wait skipped possibly minutes later) is worth one sentence in `docs/PATHS.md`.
- **Business-rule drift, minor:** `set-surge-audio.sh` accepts only 44100/48000 sample rates while `mpe_jack_rate()` also accepts 96000 (`scripts/lib/audio-engine.sh:60-65`). Either 96k is a supported rate (let the UI offer it) or it isn't (remove it from the validator). Pick one.
- **HUD label vs. retired states:** `engine_hud_label` (`patch_browser/audio_engine.py:110-125`) would render a stale pre-upgrade state file carrying `active=alsa state=degraded` as an "ALSA" badge — `degraded` is correctly unrecognized (tested), but the `active` value is passed through untrusted. Harmless (tmpfs clears on reboot), one `active not in ("jack", "none")` check fixes it.

## 6. Test Strategy & Execution

**Actual run:** `python3 -m unittest discover -s` is hook-blocked on this machine; ran the project's own harness, `mpe test local all` (same discover + shell suites):

```
Ran 440 tests in 42.076s
OK
OK test_gadget_persist.sh
All prepare-dsi-display shell tests passed
```

440 tests, **0 failures**. (CHANGELOG claims 438 for the amendment commit; two have been added since or the count drifted — immaterial, but the doc and the suite should agree.)

What's genuinely good:

- **`tests/test_audio_engine.py` (704 lines) is the model test file**: pure-decision unit tests, bash-parity tests that execute the real shell functions, static `NoAlsaPathTests` that grep the tree to make the ALSA removal *irreversible-by-accident*, and unit-file content assertions (RuntimeDirectoryPreserve, StartLimit keys in `[Unit]` not `[Service]` — a bug class they actually hit).
- CI (`.github/workflows/test.yml`) runs the same discover plus the two shell harnesses on `dev`/`main` pushes and PRs, and the pygame-dependent tests mock the display, so the minimal CI environment (`pip install python-osc` only) is sufficient. Local green == CI green is plausible here.

The gaps:

- **Criterion 16 has no executable test.** `mpe_jack_period` / `mpe_jack_periods` / `mpe_jack_rate` — the enum validators guarding the server's period — have zero test coverage (`grep MPE_JACK_BUFFER tests/` → nothing). The D6 separation is asserted in docs and violated in practice by the UI layer (Hall of Shame #2), which is exactly what a test would have caught.
- The heavy UI surface (1400-line draw mixin, 19 mixins) is covered by smoke/wiring tests, not behavior tests. Acceptable for a single-user appliance; don't let it grow further without a seam.
- The integration tests that matter most (2\*, 2b/2b2 at the new 5 s settle, 15-from-`failed`, 12 stale-env) are **Pi-only by design and currently unrun** — see Spec Conformance below. The unit suite cannot substitute for them; the branch is correctly labeled REQUIRES PI SOAK BEFORE MERGE. Hold that line.

## 7. Security & Performance

**Security** — this is a single-user appliance with no network surface (OSC bound to `127.0.0.1:53280`, confirmed in `patch_browser/patch_loader.py` and the spec), and the review found no holes:

- Privilege boundaries are sane: units run as the appliance user, `LimitRTPRIO=95` *permits* RT without forcing it, `sudo -n` is used everywhere in libraries, and every sudo-exposed script (`set-surge-audio.sh`, `set-audio-profile.sh`) validates against fixed enums before touching `/etc/mpe/mpe.env`. The `_update_env_var` sed writes only validated values. `paths.sh` sources env files as root in some unit contexts — standard risk, mitigated by root-owned `/etc/mpe`.
- No secrets in the repo; `.gitignore` covers SSH keys explicitly.

**Performance:**

- The reconcile-probe worst case (Hall of Shame #3) is the one real perf defect: up to ~60 s of watchdog blockage against a hung jackd, which also delays the crash-recovery arm.
- Otherwise the hot paths are thoughtfully cheap: HUD reads `engine.state` via a 0.5 s cached background monitor (`patch_browser/engine_state_monitor.py`), not per-frame disk hits; patch scanning is indexed; the state writers are O(lines) greps on tiny tmpfs files.
- One free win: `start-surge-cli.sh` invokes `"$SURGE_CLI" --list-devices` up to three times per boot (JACK index, MIDI index, and detection downstream). Each Surge CLI invocation is seconds of boot time on a Pi 4. Cache one listing per boot and parse it three ways.

## 8. Developer Experience

- **Documentation is a genuine strength.** `AGENTS.md`, `COMMANDS.md`, `docs/PATHS.md`, `docs/GIT-WORKFLOW.md`, and `docs/STABLE-SETUP.md` are current, cross-linked, and opinionated ("Pi runs from git clone only — no loose copies"). The spec cites `file:line` for its claims; I spot-checked a dozen citations and they resolved.
- The spec's self-flagellation sections (Falsification Analysis, "this threshold is a decision on incomplete data," honest BLOCKED rows in the soak log) are the best thing in the repo. Keep that culture; it's rarer than any code here.
- **The twist shows in DX, not code:** an untracked governing spec, a 76-commit unmerged prerequisite branch, and a same-day amendment landing on a fresh branch mean the next session's orientation cost is high. The `CHANGELOG.md` discipline (dated, per-session, with branch names) is what's saving you.
- Minor housekeeping: `ENCODER_BUTTON_REVIEW.md` sits at repo root instead of `docs/`; `README.md:75` says reference stack is "Raspberry Pi 5" while both specs say Pi 4B (two appliances may exist — then say so); `logs/shutdown-trace.jsonl` is an untracked runtime artifact in the working tree (fine, but consider ignoring `logs/`).

---

## Spec Conformance: Phase One vs Plan

Mapping each `jack-audio-engine-spec.md` Phase 1 commitment to the merged state (`dev` @ `daac891` + amendment branch `1ab9f55`):

| # | Criterion (as amended) | Status | Evidence |
|---|---|---|---|
| 1 | Cold boot → Surge a JACK client | **Landed, soaked PASS** | `config/mpe-jackd.service` + `jackd-prestart.sh` + `surge-xt-cli.service:3-4`; Gate B log |
| 2a/2c/2d | ALSA fallback states | **Retired by amendment; removal verified** | symbols gone (grep), `NoAlsaPathTests` guard reintroduction |
| 2\* | jackd fails → silent + `state=failed`, promote on unmask | **Landed in code, NOT soak-verified** | `start-surge-cli.sh:147-154`, watchdog promote arm `surge-watchdog.sh:88-90`; Gate C soak pending |
| 2b / 2b2 | jackd death recovery / repeated-death budget | **Landed; soaked PASS at 15 s settle; 5 s settle UNVERIFIED** | `surge-watchdog.sh:62-91`, `audio_engine.py:52-76`; Gate C item 2 |
| 3 | ALSA regression path | **Retired** | — |
| 4 | Engine survives reboot, readable | **Landed (trivially — static constant)** | `MPE_ENGINE_NAME="jack"`, `scripts/lib/audio-engine.sh:18` |
| 5a | Profile switch restarts jackd | **Landed, soaked PASS** | `set-audio-profile.sh:57` → `restart_audio_graph` |
| 5b | UAC2 host capture re-points graph | **BLOCKED (rewire) + likely-broken dependency found** | Hall of Shame #1 — mic bridge vs jackd duplex open |
| 6 | Surge audio thread SCHED_FIFO via jackd, no `chrt` | **Landed, soaked PASS** | no `chrt` anywhere in `start-surge-cli.sh` |
| 10 | Looper guard at one chokepoint | **Partially landed by design** | policy in `engine-guard.sh` + `audio_engine.py` (unit-tested); the authoritative `mpe-looper.py main()` guard lives on unmerged `yolo/looper-phase0` — deferred per spec |
| 12 | Stale `MPE_AUDIO_ENGINE=alsa` is inert | **Landed in code; boot path unverified** | variable read nowhere (grep-verified); Gate C item 4 |
| 13 | HUD surfaces engine state + `looper=guarded` | **Landed; partial soak PASS** | `audio_engine.py:99-139` + `touch_browser_draw.py:392-405`; full guarded badge needs an `MPE_LOOPER_ENABLED=1` boot (still pending) |
| 14 | Direct-ALSA consumers coexist with jackd | **Half: calibration PASS, session capture BLOCKED** | Gate B log; same duplex-open concern as #1 applies to `session_capture.py` |
| 15 | DAC replug restarts jackd from any prior state | **Landed; soaked PASS from `ok`; from `failed` UNVERIFIED** | `99-usb-audio.rules:26-27` → `restart-audio-graph.sh` (does `reset-failed` first — correct); Gate C item 3 |
| 16 | `MPE_JACK_BUFFER`/`PERIODS` independent of Surge key | **DRIFTED** | bash side correct; but `set-surge-audio.sh:125-128` writes both keys, UI reads the Surge key, `configure-pi-paths.sh` seeds it; no tests (Hall of Shame #2) |
| 17 | `mpe engine status` documented interface | **Landed (mpe-cli side, out of scope here), soaked PASS** | spec + Gate B log |

**Surprises:**

- *Specced-but-missing:* nothing in the phase-one code scope is missing outright — the gaps are all **verification** gaps (Gate C soak: 2\*, 2b/2b2 at 5 s, 15-from-`failed`, 12) plus the criterion 16 UI drift and the untested JACK buffer validators.
- *Landed-but-unspecced:* `mpe_restart_audio_graph` resets the supervisor budget on every graph restart (`scripts/lib/audio-engine.sh:311-314`) — sensible, but it's a behavioral rule (operator action clears escalation) that exists only in a code comment; and the `NoAlsaPathTests` static guards go beyond anything the spec asked for (good surprise).
- *Cross-spec:* `looper-jack-client-spec.md` (untracked) still plans around `MPE_AUDIO_ENGINE=alsa` as an operator escape hatch that the amendment abolished yesterday (Hall of Shame #4).

---

## Verdict

The merged phase-one state **is** coherent with the spec's phase structure — impressively so, because the spec was amended in lockstep with the code and the ALSA removal is genuinely total (grep-verified, guard-tested, 440/440 green). Whatever felt "twisted up" in the merge did not leave damage in the tree: no conflict artifacts, no dead fallback arms, no duplicate engine paths. The real risks to the "sound module + analog-esque mixer" goal are forward-looking, not residual: the hard-failure redesign has never been soaked (and must not merge without Gate C); jackd's default duplex open very likely breaks the `usb-host-session` mic bridge and session capture — a shipped profile regression hiding behind a BLOCKED soak row; the touch UI still presents the retired Surge buffer key as truth; and the entire mixer half of the product vision is gated on rebasing a 76-commit branch that predates the architecture it must join, culminating in a Python-callback kill criterion that may yet answer "Phase 1 + HAT." The codebase isn't at risk from what the merge did. It's at risk from what hasn't been verified since.

## Priority backlog (🔴 items)

1. **Verify and fix the jackd duplex-open conflict with the `usb-host-session` mic bridge and session capture** — add playback-only (`-P`) or an explicit `-P/-C` split to `scripts/start-jackd.sh:43-44` if capture must stay free, then run the physically-blocked 5b/14 soak rows. This is a probable shipped-profile regression (mic return to host dies with `EBUSY`) introduced by Phase 1 and never exercised.
2. **Do not merge `yolo/jack-drop-alsa-fallback` without the Gate C soak** — the branch converts every jackd failure into a silent instrument by design; 2\* (mask-at-boot → `state=failed` → unmask promotes), 2b/2b2 at the new 5 s settle, DAC replug from `state=failed` (15), and stale-`MPE_AUDIO_ENGINE` inertness (12) are all unverified on hardware. The spec already mandates this; treat any pressure to skip it as the actual incident.

---

*Finding tally: 🔴 2 · 🟡 5 · 🟢 6+ (minor nits grouped). Test suite: 440 pass / 0 fail (`mpe test local all`, 2026-08-13).*
