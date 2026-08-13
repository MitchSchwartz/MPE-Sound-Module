# Grumpy Dev review — MPE-Module (full codebase, JACK Phase 1 post-merge)

*Review date: 2026-08-13 (America/Toronto)*

**Repo:** `/home/mitch/Documents/GitHub/MPE-Module`
**Branch reviewed:** `yolo/jack-drop-alsa-fallback` @ `1ab9f55` (= `dev` @ `daac891` + 1 commit)
**Scope:** this repo only. The separate `mpe-cli` repo and all CLI tooling are **out of scope** by instruction.
**Reviewer stance:** grumpy, fair, evidence-only. No code was modified.

## What I read, and what I didn't

**Read in full:** `Documents/specs/jack-audio-engine-spec.md` (559 lines), `Documents/specs/looper-jack-client-spec.md` (869 lines), `patch_browser/audio_engine.py`, `patch_browser/engine_state_monitor.py`, `patch_browser/looper_clock_monitor.py`, `patch_browser/calibration_teardown.py`, `patch_browser/mixer.py`, `scripts/lib/audio-engine.sh`, `scripts/lib/engine-guard.sh`, `scripts/start-surge-cli.sh`, `scripts/start-jackd.sh`, `scripts/jackd-prestart.sh`, `scripts/detect-jack-device.sh`, `scripts/surge-watchdog.sh`, `scripts/restart-audio-graph.sh`, `config/mpe-jackd.service`, `config/surge-xt-cli.service`, `config/surge-watchdog.service`, `config/99-usb-audio.rules`, `tests/test_audio_engine.py`, `.github/workflows/test.yml`, `CHANGELOG.md` (top 80 lines).

**Skimmed / sampled:** `README.md`, `AGENTS.md`, `docs/GIT-WORKFLOW.md`, `docs/PATHS.md`, `docs/refactor-touch-browser-plan.md`, `patch_browser/touch_browser_draw.py` (lines 380–520 of 1419), `patch_browser/touch_browser_app.py` (constructor), `patch_browser/wifi_manager.py`, `config/mpe.env.example`, `requirements.txt`, git history (last 40 commits, `dev..HEAD`, `main..dev`), full file inventory of `patch_browser/`, `scripts/`, `config/`, `tests/`.

**Did NOT read:** the other three touch-browser specs beyond their titles and grep hits; `patch_browser_ui.py` beyond its import/class structure (1456 lines, OLED front-end); the bulk of `touch_browser_draw.py`, `touch_browser_input.py`, `touch_browser_settings.py`; ~50 of the 60 test modules individually (I ran them all instead); most of `scripts/` (deploy, setup, UAC2, MIDI-clock, pedal, DSI); the APC control-surface layer (spec declares it out of scope and in good shape); `patch_browser/calibration_*.py` beyond teardown and loopback grep hits.

**Cannot verify from a laptop:** anything requiring the Pi — jackd behaviour, udev event delivery, RT scheduling, xruns. Where I reason about those, I say so and name the mechanism.

---

## 1. First Impressions (The Gut Check)

The specs in this repo are better than the specs in most funded companies I have reviewed. `jack-audio-engine-spec.md` contains a falsification table that names the conditions under which the whole plan is wrong, a cooldown design that anticipates the supervisor being worse than the fault it responds to, and — the part I genuinely did not expect — an amendment that retires half its own acceptance criteria with inline `RETIRED 2026-08-13` markers rather than rewriting history, explicitly so a soak log stays honest. Someone here understands that a spec whose criteria get quietly reinterpreted is a spec that has stopped meaning anything.

So my gut check is not "is this a mess." It isn't. My gut check is: **the engineering discipline is concentrated in the documents, and the verification has not caught up to the code.**

The specific shape of the problem: Phase 1 merged to `dev` (PR #49) after a real Pi soak. Then, one commit later, the team reversed Phase 1's second-highest goal — "the instrument never boots silent" became "the instrument is silent and says so." That reversal deleted the entire fallback arm that the soak log had spent a day proving. What is on `HEAD` right now is a *design-level reversal of a soaked design, verified by unit tests and prose*. The spec says so itself, in bold, twice: **REQUIRES PI SOAK BEFORE MERGE**. It is right, and the fact that it says so is the strongest signal in the repo that this team is not fooling itself.

Was the merge "twisted up"? Less than you fear. I went looking for orphans and duplicated engines and found remarkably little: one misnamed file, no surviving ALSA branch, no dual state machine, the D2 call-site inventory actually honoured in code. The debris from that merge is not the risk here.

The risks are three, and none of them is the merge:

1. The most detailed design document in the repo — 869 lines of Phase 2 architecture — **is not committed to git.**
2. The failure path that the appliance now depends on has never run on hardware.
3. Half of the stated product goal ("analog-esque mixer") has no spec, no code, and no plan.

And one honest-to-goodness bug in `99-usb-audio.rules` that has been sitting there since before this work started.

---

## 2. Architecture & Structure

**The audio engine is well shaped.** The Phase 1 + amendment design is a single graph server (`config/mpe-jackd.service`), a client (`config/surge-xt-cli.service`), and a reconciler (`scripts/surge-watchdog.sh`), with all shared state in tmpfs (`scripts/lib/audio-engine.sh:88-141`) rather than in shell variables. The reason given for tmpfs is the correct one and is written down: `surge-watchdog.service` is `BindsTo=surge-xt-cli.service`, so the supervisor is restarted by the very event its rate-limiter exists to count (`scripts/lib/audio-engine.sh:9-12`). That is a class of bug most teams ship twice before noticing.

**`BindsTo` is rejected for the right reason, and the reason was updated when the design changed.** The original rationale ("it makes `degraded` unreachable") went moot with the amendment; the spec replaced it with a reason that survives — `BindsTo` would stop Surge from starting, and Surge *must* start in order to detect the absent server and publish `state=failed` (`Documents/specs/jack-audio-engine-spec.md:298-310`). The units match: `Wants=` + `After=`, no `Requires`.

**The bash↔Python duplication is deliberate and pinned.** `mpe_engine_reconcile_decision()` (`scripts/lib/audio-engine.sh:369-392`) and `reconcile_cooldown_decide()` (`patch_browser/audio_engine.py:52-76`) implement the same table twice, in two languages, because one runs in the supervisor and one runs in the touch UI. Normally I'd call that a maintenance trap. Here it is covered by `BashReconcileParityTests` (`tests/test_audio_engine.py:172-186`), which runs the same five cases through both. That is the correct answer to unavoidable duplication.

**The D2 inventory is real, not aspirational.** The spec claims six call sites that must restart jackd rather than Surge, and says the rule needed an inventory because three were missed in the first draft. I checked all of them. `set-audio-profile.sh:57`, `set-surge-audio.sh:129`, `uac2-stall-watchdog.sh:67`, and `config/99-usb-audio.rules:26-27` (via `restart-audio-graph.sh`) all restart the graph. Nothing in `scripts/` or `patch_browser/` restarts `surge-xt-cli` on a device-changing path. This is the single most common place a design document diverges from a codebase, and it hasn't.

**Where the architecture is weak: the touch UI.** `TouchPatchBrowser` composes **19 mixins** (`patch_browser/touch_browser_app.py:67-87`) and its `__init__` performs **133 `self.*` assignments**. `docs/refactor-touch-browser-plan.md` describes this as a deliberate choice — *"Mixin composition keeps method bodies identical to pre-refactor behavior; no protocol/lazy-import indirection."* That was a defensible call for a mechanical split of a 2656-line file. But the refactor moved the lines and did not reduce the coupling: every mixin can still read and write every attribute, and the file layout now *hides* that fact instead of making it obvious. Section 4 has the receipt.

**Two front-ends, one model layer — correctly done.** `patch_browser_ui.py` (OLED + rotary encoder, 1456 lines, repo root) and `touch_patch_browser.py` → `patch_browser/` (5" touch panel) are two separate hardware UIs. I went in expecting duplicated `PatchScanner`/`PatchLoader` implementations, because `docs/TOUCH_PATCH_BROWSER.md:47` says the touch UI "borrowed patterns" from the OLED one. It didn't borrow — it imports: `patch_browser_ui.py:30-40` pulls `PatchLoader`, `PatchScanner`, and `SurgeMonitor` from the shared package. Credit where due; that is the outcome you want and rarely get.

---

## 3. Code Quality

**Shell quality is high, which is not a sentence I write often.** Every state write goes through `mpe_state_write_atomic()` (`scripts/lib/audio-engine.sh:102-122`) — temp file, chmod, `mv -f`, with a warning and non-zero return on failure rather than a silent partial write. Every environment variable read is validated through a `case` allowlist with a documented default and a `WARNING:` on garbage (`mpe_jack_period`, `mpe_jack_periods`, `mpe_jack_rate`, `mpe_engine_cooldown_seconds` — `audio-engine.sh:36-79, 344-363`). Readiness is a bounded poll loop, never `sleep N` (`mpe_wait_for_jack_server`, `:219-237`), and the spec explicitly demands that (`D3` boot ordering).

**The comments explain decisions, not mechanics.** `config/mpe-jackd.service:6-13` does not say "disable the start limit"; it says *why* — jackd exits immediately when its card is absent, so the default burst would put the unit in permanent start-limit failure within ~15s of an unplugged DAC and then refuse to start when the DAC came back. That is a comment written by someone who got bitten and wanted the next reader not to be.

**Two comments are load-bearing bug postmortems, and I want them preserved verbatim.** `scripts/lib/audio-engine.sh:161-165`: *"a status publisher must never kill its caller"* — `${2:?}` in `mpe_engine_state_write` was exiting `surge-watchdog.sh` mid-restart, leaving Surge crashed and unsupervised. And `scripts/start-surge-cli.sh:41-42`: Surge may exit non-zero while still printing a usable device list, so a noisy exit must not be read as "no JACK device." Both are covered by tests.

**Python quality is uneven by layer.** `patch_browser/audio_engine.py` is 140 lines of pure functions with no I/O in the decision path, `Literal` return types, and a docstring per function. `patch_browser/engine_state_monitor.py` and `looper_clock_monitor.py` are correct little threaded pollers with proper `Event`-based shutdown and lock-guarded snapshots. Then `patch_browser/touch_browser_draw.py` (1419 lines) and `touch_browser_app.py`'s constructor are the opposite end. The good code and the bad code are in the same package, which suggests the difference is *when* it was written, not who wrote it.

**Type hints are present and meaningful where they matter** (`ReconcileAction = Literal[...]`, `patch_browser/audio_engine.py:13`) and absent in the UI layer. There is no mypy in CI; `docs/refactor-touch-browser-plan.md` lists "typed host protocol for mixin `self` if mypy is added later" under *Deferred*, which is at least honest bookkeeping.

---

## 4. Code Smells (The Hall of Shame)

### 🔴 4.1 — The Phase 2 spec is not in git

```
$ git status --short Documents/
?? Documents/specs/looper-jack-client-spec.md
```

869 lines. It contains the `audioop` bit-exactness analysis measured from `_audioop.c` (the `floor()`-not-`rint()` trap, the clip-at-every-pairwise-add fold), the decision that acceptance criterion 9 **cannot pass as written** because `JACK-Client`'s `get_array()` allocates per callback, the replacement 9a–9e criteria, the kill-criterion table with the reasoning for 20% and 50% of period, and a 13-task breakdown with human gates. It is the highest-value artifact produced in this effort and it exists only in one working tree, unversioned, one `git clean -fd` from oblivion. No other session, agent, or reviewer can see it. CI cannot see it. The `dev` branch has never heard of it.

**Fix:** `git add Documents/specs/looper-jack-client-spec.md` and commit it, today, before anything else in this document gets acted on.

### 🔴 4.2 — The appliance's silent-failure behaviour has never run on hardware

`Documents/specs/jack-audio-engine-spec.md:102-108` states it plainly:

> **Pi soak required before merge.** §Gate B soak log below verified the *retired* fallback design (2a, 2d, 3, and the `degraded` half of 2c). None of it verifies the hard-failure behaviour this amendment introduces […] This branch is marked **REQUIRES PI SOAK BEFORE MERGE** for exactly this reason.

The Gate B log has four PASS rows (`2a`, `2d`, `3`, half of `2c`) that tested code paths which no longer exist in the tree. What replaced them — criterion `2*`, "jackd fails at boot → appliance is silent and says so" — is covered by `StartSurgeCliFailureTests` (see 4.5 for why that coverage is thinner than it looks) and by nothing else. Five named Gate C scenarios are listed at `:452-459`; zero have run.

This is not a code defect. It is the review's central risk finding: **the instrument's behaviour on its most important failure is currently a hypothesis.**

**Fix:** run Gate C's five scenarios before this branch merges to `dev`. The spec already lists them in order; no new thinking required.

### 🔴 4.3 — `99-usb-audio.rules` skip guards silently do not apply to `remove` events

```udev
# Skip snd-aloop (calibration loads/unloads this; must not restart production Surge mid-run)
ACTION=="add|remove", SUBSYSTEM=="sound", KERNEL=="card[0-9]*", ATTR{id}=="Loopback", GOTO="mpe_usb_audio_end"
...
ACTION=="remove", SUBSYSTEM=="sound", KERNEL=="card[0-9]*", RUN+="@MPE_MODULE_REPO@/scripts/restart-audio-graph.sh"
```
— `config/99-usb-audio.rules:19, 27`

`ATTR{id}` reads sysfs. On a `remove` event the sysfs node is already unlinked, so `ATTR{}` matches cannot succeed — this is standard udev behaviour, not a quirk of this rule. All four skip guards (`vc4hdmi*`, `UAC2Gadget`, `UAC2`, `Loopback`) therefore filter `add` correctly and filter `remove` **not at all**. Every `remove` falls through to the generic rule and restarts jackd.

The concrete path: `unload_snd_aloop_if_idle()` runs `modprobe -r snd_aloop` (`patch_browser/calibration_teardown.py:35`) at the end of every calibration run. That fires a sound-card `remove`, which restarts the graph. The comment on line 19 states the exact invariant being violated. Gate B recorded criterion 14 (calibration with jackd up) as PASS, but the restart happens during *teardown*, after the calibration result is already written — precisely where nobody was looking. The same blind spot fires on UAC2 gadget unbind during profile switches.

Cost per occurrence is a jackd restart plus supervisor reconcile — the same path Gate B measured at ~30–39s of silence.

**Fix:** filter on kernel-supplied `ENV{}` properties (available on `remove`) instead of `ATTR{}`, or move the card-identity check into `restart-audio-graph.sh`, which can consult `/proc/asound/cards` and decline. The second option keeps one filter instead of four and is testable from a laptop.

### 🔴 4.4 — A jackd outage makes the watchdog throw away Surge's user defaults

```bash
if systemctl is-failed "$SURGE_SERVICE" &>/dev/null; then
    log "ALERT: Surge service failed, cleaning user defaults"

    if [ -f "$USER_DEFAULTS" ]; then
        BACKUP="${USER_DEFAULTS}.corrupted_$(date +%Y%m%d_%H%M%S)"
        mv "$USER_DEFAULTS" "$BACKUP"
        log "Backed up corrupted file to: $BACKUP"
    fi
```
— `scripts/surge-watchdog.sh:97-105`

This arm assumes `surge-xt-cli` reaching `failed` means a corrupt defaults file. That assumption held before the amendment, when an ALSA fallback meant Surge almost always started. It does not hold now. Post-amendment, `start-surge-cli.sh` **exits 1 by design** when there is no graph server (`:147-154`), the unit is `Restart=on-failure` with `RestartSec=10` and `StartLimitBurst=5` / `StartLimitIntervalSec=300` (`config/surge-xt-cli.service:6-7, 27-28`) — so roughly 100s into any jackd outage, `is-failed` becomes true and this arm fires, attributing a missing DAC to file corruption and moving the user's accumulated Surge defaults aside. `start-surge-cli.sh:60-66` then writes a fresh empty skeleton in its place.

Two consequences: the diagnostics actively mislead (a log line saying "corrupted" during what is actually a cable problem), and `.corrupted_<timestamp>` files accumulate in the defaults directory with no cleanup anywhere in the repo — `grep -rn corrupted` returns exactly these two lines and nothing that ever removes them.

This is the clearest instance of the amendment changing a precondition that a distant piece of code was silently relying on.

**Fix:** gate the corrupt-defaults arm on the engine state — skip it when `mpe_engine_state_get state` is `failed` with `reason=no-server` / `no-jack-device`, since that is a known-good explanation for the failure that has nothing to do with the file.

### 🟡 4.5 — A test that re-implements the script it claims to test

```python
def _run_start_surge_cli(self, *, env: dict[str, str], stubs: str) -> ...:
    # start-surge-cli.sh is a script, not a sourceable library, and needs a
    # real SURGE_CLI binary + USER_DEFAULTS setup to run end-to-end. This
    # exercises the exact state-publishing sequence its failure branch
    # runs (spec D3 hard failure), matching the real script line-for-line;
    # NoAlsaPathTests statically confirms the real script has no other branch.
    body = f"""
...
mpe_engine_state_write "$MPE_ENGINE_NAME" none failed "$ENGINE_REASON" "$(mpe_looper_state_label)"
mpe_surge_state_write none ""
exit 1
"""
```
— `tests/test_audio_engine.py:486-504`

`test_jack_failure_exits_nonzero_and_publishes_failed` asserts that *this test's copy* of the failure branch works. Change the argument order in `start-surge-cli.sh:151`, drop the `mpe_surge_state_write` call, or swap `none` for `unknown`, and the test still passes green. The docstring is honest about the compromise, which is why this is 🟡 and not 🔴 — but honesty in a docstring does not add coverage. Criterion `2*`'s only automated evidence is this plus the string-absence assertions in `NoAlsaPathTests` (`:423-481`), several of which are fragile in their own right: `assertNotIn('detect-audio-device.sh"', text)` (`:524`) matches only when the path happens to be followed by a double quote.

**Fix:** make the script testable rather than copied — extract the engine-resolution + state-publish sequence into a sourceable function in `scripts/lib/audio-engine.sh` and have `start-surge-cli.sh` call it, then test the real function.

### 🟡 4.6 — 19 mixins, 133 constructor assignments, one namespace

```python
class TouchPatchBrowser(
    TouchBrowserEvdevMixin,
    TouchBrowserPrefsMixin,
    TouchBrowserSettingsMixin,
    TouchBrowserBrightnessModalMixin,
    # … 15 more …
    TouchBrowserInputMixin,
):
```
— `patch_browser/touch_browser_app.py:67-87`

`sed -n '60,300p' … | grep -c '^        self\.'` → **133**. Nineteen classes share one flat attribute namespace with no declared interface between them. The refactor plan is candid that this was chosen to keep method bodies byte-identical during the split, which was the right risk trade for the split itself. It is not a resting place: nothing here is checkable, and the next symptom is directly below.

**Fix:** give the mixins a `Protocol` for the attributes they read off `self` (already listed as Deferred in the plan) and start moving cohesive state into small owned objects — `engine_monitor`, `looper_monitor`, `cpu_monitor` already show the pattern working.

### 🟡 4.7 — Defensive `getattr` chains standing in for an interface

```python
title_x = getattr(self, "status_title_x", self.status_rect.x + 12)
widget_left = getattr(self, "engine_hud_rect", getattr(self, "looper_hud_rect", self.audio_profile_badge_rect)).x
if widget_left <= title_x or getattr(self, "engine_hud_rect", Rect(0, 0, 0, 0)).w <= 0:
    widget_left = getattr(self, "looper_hud_rect", self.audio_profile_badge_rect).x
if widget_left <= title_x:
    widget_left = self.audio_profile_badge_rect.x
```
— `patch_browser/touch_browser_draw.py:484-489`

Six `getattr` defaults and three cascading fallbacks in six lines, because the draw mixin cannot know whether the layout mixin has run. This is 4.6's bill arriving. It is also unreadable: nobody can tell from this code which of the three widgets is *supposed* to be there. Same pattern again at `:503-505`.

**Fix:** initialise all HUD rects to `Rect(0, 0, 0, 0)` in `__init__` (some already are, at `:154-157`) and delete every `getattr` default here; a zero-width rect is already the "not present" signal that `_draw_engine_hud` checks at `:393`.

### 🟡 4.8 — Three header badges, three polling threads, an SD card underneath

`EngineStateMonitor` polls `/run/mpe/engine.state` every **0.5s** (`patch_browser/engine_state_monitor.py:10`) — tmpfs, fine. `LooperClockMonitor` polls `~/.mpe_midi_clock_state.json` every **0.2s** (`patch_browser/looper_clock_monitor.py:10`) — five reads per second, forever, of a file in `$HOME` on the SD card, to drive one BPM badge. `SurgeCpuMonitor` adds a third thread. `grep -rn 'threading.Thread' patch_browser/` returns 20 hits across the package.

On a Pi 4B whose whole purpose is now holding a 256-frame JACK deadline, an unaudited 5 Hz filesystem poller is the kind of thing that shows up later as an unexplained xrun and takes a day to find.

**Fix:** move the MIDI clock state file to `/run/mpe/` alongside `engine.state` (it is ephemeral runtime state, not user data), and drop the poll to 0.5s to match its sibling.

### 🟡 4.9 — CI is materially weaker than the local gate

```yaml
      - name: Install test dependencies
        run: pip install python-osc
      - name: Run unit tests
        run: python3 -m unittest discover -s tests
...
      - name: Run shell tests
        run: |
          bash tests/test_gadget_persist.sh
          bash tests/test_prepare_dsi_display.sh
```
— `.github/workflows/test.yml:18-31`

Three problems. Dependencies are one hand-picked package rather than `requirements.txt`. The two shell tests are hardcoded, so `mpe test coverage`'s reassuring *"CI runs every shell test"* holds only by the accident that there are currently two. And there is **no shellcheck anywhere** — not in CI, not in `.cursor/`, nowhere — despite the entire audio engine being bash and the scripts themselves carrying `# shellcheck disable=` directives that imply somebody once ran it locally.

The evidence that this gap is live: the ALSA-removal commit shipped `set-surge-audio.sh` still calling the just-deleted `mpe_audio_engine()`. It was caught by a hand-written grep assertion added in the same commit (`tests/test_audio_engine.py:464-471`, whose docstring says *"a real bug caught by this sweep, not a hypothetical"*). Catching it was good. Needing a bespoke test per deleted function is not a strategy.

**Fix:** add a shellcheck job over `scripts/` and `config/*.service`, glob the shell tests (`for f in tests/test_*.sh`), and install from `requirements.txt`.

### 🟡 4.10 — Calibration teardown restarts Surge without restarting jackd

```python
subprocess.run(["sudo", "pkill", "-f", "surge-xt-cli"], check=False)
time.sleep(0.5)
unload_snd_aloop_if_idle()
subprocess.run(["sudo", "systemctl", "start", "mpe-pressure-remap.service"], check=False)
subprocess.run(["sudo", "systemctl", "start", "surge-poly-governor.service"], check=False)
subprocess.run(["sudo", "systemctl", "start", "surge-xt-cli.service"], check=False)
```
— `patch_browser/calibration_teardown.py:67-72`

`mpe-jackd.service` is never started or verified here. Standing alone that is defensible — calibration doesn't stop jackd. Composed with 4.3 it is not: line 69 triggers the udev `remove` that restarts jackd, and line 72 then races Surge's 10s readiness wait (`mpe_jack_ready_timeout`, default 10) against jackd's measured ~6s startup. Two fixed `sleep`s (0.5s, and 1s in `stop_mpe_audio_services`) are the only sequencing.

**Fix:** after fixing 4.3, add an explicit `systemctl start mpe-jackd.service` plus a bounded readiness wait before starting Surge, rather than relying on timing.

### 🟢 4.11 — Dead import executed on every rendered frame

```python
def _draw_audio_profile_badge(self, rect: Rect) -> None:
    label = header_badge_label()
    from patch_browser.usb_audio_recovery import is_recovering

    recovering = label == "Sync"
```
— `patch_browser/touch_browser_draw.py:448-452`

`is_recovering` is imported and never called; the check is done by string comparison on the next line. Module caching makes the cost near-zero, so this is cosmetic — but it is a function-local import inside a per-frame draw path, and there is a second one at `:478`. **Fix:** delete line 450; hoist `:478` to module scope.

### 🟢 4.12 — `looper_clock_monitor.py` monitors the MIDI clock, not the looper

```python
"""Read looper MIDI clock state for the touch patch browser header HUD."""
...
class LooperClockMonitor:
    """Background reader for ~/.mpe_midi_clock_state.json (midi-clock-in daemon)."""
```
— `patch_browser/looper_clock_monitor.py:1, 13-14`

This is the **only** `looper_*` file that survived Phase 1's looper strip (`git ls-files | grep -i looper` → one result), which makes it read like merge debris on first inspection. It isn't — it is live, imported at `touch_browser_app.py:28`, and it reads the `midi-clock-in` daemon's state. The name costs every future reader the same five minutes it cost me, and it will cost more once real looper modules land from `yolo/looper-phase0`.

**Fix:** rename to `midi_clock_monitor.py` / `MidiClockMonitor` before phase0 merges and adds genuinely-looper-named siblings.

### 🟢 4.13 — CHANGELOG test count is stale

`CHANGELOG.md:71` — *"Full suite: 438 tests, 0 failures."* Actual on this branch: **440**. Trivial, but this file is the chronological index of record and the number is the kind of thing someone will later use to reason about whether tests were dropped. **Fix:** update, or stop quoting exact counts.

### 🟢 4.14 — Unbounded retry storm when no DAC is present

`jackd-prestart.sh` waits up to 15s for a card (`:25, 41-48`), exits 1 if none resolves (`:57-61`); the unit is `Restart=always` with `RestartSec=3` and `StartLimitIntervalSec=0` (`config/mpe-jackd.service:15, 45-46`). With no DAC attached that is a permanent ~18s loop writing to the journal forever. This is the *intended* consequence of the spec's "keep retrying rather than wedge in start-limit failure" reasoning, and I agree with the trade — but nothing bounds the log growth or backs the interval off. **Fix:** back off `RestartSec` after N consecutive prestart failures, or log the repeat at a lower frequency.

---

## 5. Logic & Business Rules

**The cooldown logic is correct and correctly ordered.** `mpe_engine_reconcile_decision()` (`scripts/lib/audio-engine.sh:369-392`) evaluates in the order: budget exhausted → `failed`; jackd still settling → `jackd-settling`; no prior restart → `restart`; else compare against cooldown. That ordering matters — checking cooldown before the settle window would let the supervisor fight a jackd that has `Restart=always` and is mid-bringup. Both implementations agree, and `BashReconcileParityTests` pins them.

**The looper guard's exit-code split is subtle and right.** `looper_guard_exit_code()` (`patch_browser/audio_engine.py:41-49`) returns 0 for the systemd path and non-zero for interactive callers, because `mpe-looper.service` is `Restart=on-failure` and a non-zero guard would produce a restart storm — the opposite of the clean refusal intended. The context signal is an explicit exported `MPE_LOOPER_SERVICE=1` with systemd's `INVOCATION_ID` as backstop, rather than inferring context. The spec explains why `ConditionEnvironment=` cannot be used (it reads the manager environment, not the unit's `EnvironmentFile`). Every part of this reasoning is written down and tested (`tests/test_audio_engine.py:82-89`).

**The guard's authoritative enforcement point does not exist on this branch, by design.** `scripts/lib/engine-guard.sh:12-15` says so outright: *"The looper itself lives on yolo/looper-phase0, not on dev, so its authoritative guard — main() of scripts/mpe-looper.py, which every start path crosses — must be added on that branch."* So what ships on `dev` is guard *policy* (message text, blocked predicate, exit-code split) with unit tests, and no enforcement, because there is nothing to enforce against. This is coherent — but it means criterion 10 is currently a promise held in two files that no runtime path reaches, and the D5 analysis that found the real chokepoint (correcting an earlier draft's false claim that `mpe-looper-service.sh` was the only path) is the kind of finding that decays when nothing exercises it.

**Two ordering hazards worth naming.**

`_reconcile_engine` (`scripts/surge-watchdog.sh:62-91`) busy-waits up to `RECONCILE_BUDGET` (default **15s**, `:13`) inside a loop whose outer cadence is `sleep 5` (`:117`). During a jackd outage the watchdog's *other* responsibility — the `is-failed` crash-recovery arm at `:97` — therefore runs every ~20s rather than every 5s. Separately: when the amendment moved `MPE_ENGINE_JACKD_SETTLE_DEFAULT` from 15s to 5s, this sibling 15s constant was left alone. It governs the "jackd never comes back" arm specifically, so it is not the same knob — but the spec's §Backlog note reasons about recovery latency without mentioning it, and anyone tuning recovery will find one 15 and not the other.

`start-jackd.sh:35-41` refuses to clobber a more specific state, publishing `recovering` only when the current state is not already `ok | failed | recovering`. Correct instinct. Note the consequence: a jackd restart arriving while `state=failed` leaves `failed` published until the supervisor's next poll promotes it, so `mpe engine status` can read `failed` for up to 5s after recovery has begun. Acceptable; worth knowing before someone debugs it as a bug.

**The `degraded` retirement is complete, and tested as such.** `VALID_ENGINE_STATES = frozenset({"ok", "recovering", "failed"})` (`patch_browser/audio_engine.py:21`), and `test_degraded_no_longer_a_valid_state` (`tests/test_audio_engine.py:673-681`) asserts that a state file carrying `state=degraded` — which a pre-upgrade appliance will have — renders as a bare `JACK` badge rather than an unrecognised status. Thinking about the *stale state file on the upgraded device* is the detail that separates a real migration from a rename.

---

## 6. Test Strategy & Execution

### Actual results

`python3 -m unittest discover -s tests -q` could not be run directly: a workspace hook blocks direct `python`/`python3` invocation, and the global agent rules require project CLIs instead. I used the sanctioned equivalent, which wraps unittest discovery plus every shell test:

```
$ mpe test local all
Ran 440 tests in 42.113s

OK
--- tests/test_gadget_persist.sh ---
OK test_gadget_persist.sh
--- tests/test_prepare_dsi_display.sh ---
All prepare-dsi-display shell tests passed
```

**440 tests, 0 failures, 0 errors, 42.1s. Exit code 0.** 60 Python test modules + 2 shell tests.

```
$ mpe test coverage
test modules in checkout: 60
registered but absent here (16, on unmerged branches — not a failure): test_apc_led test_apc_mini
  test_apc_session_midi test_clip_matrix test_control_surfaces test_looper_alsa_stderr
  test_looper_bar_clock test_looper_devices test_looper_engine test_looper_health test_looper_hud
  test_looper_period_debug test_looper_session test_looper_timing_publisher
  test_looper_timing_state test_looper_xruns
shell tests in checkout: 2

OK: every test module is reachable from a suite, and CI runs every shell test.
```

Non-fatal noise: several `ResourceWarning: unclosed file` from `tests/test_uac2_stall_watchdog.py:228`, and repeated `Warning: python-osc not installed, patch loading disabled` — the latter meaning a chunk of the patch-loading suite runs in degraded mode locally, and `.github/workflows/test.yml:19` installs `python-osc` precisely so CI doesn't. Worth knowing that laptop-green and CI-green are not the same green.

### Strategy assessment

**The suite-registry gate is the best thing in this test setup, and I have not seen it elsewhere.** `mpe test coverage` fails if any test module belongs to no named suite, and it distinguishes *absent because the feature lives on an unmerged branch* (16 modules, exit 3) from *absent because someone forgot*. That is a genuinely original answer to the "we have suites and nobody knows what's in them" problem, and it is what let me confirm in one command that the looper strip was clean rather than lossy.

**Bash is tested, which almost never happens.** `tests/test_audio_engine.py` runs real bash through `subprocess` with function stubs injected (`_run_bash_script`, `:56-67`), exercising `mpe_engine_reconcile_decision`, `mpe_restart_audio_graph`'s `reset-failed`-then-`restart` ordering (`:280-297`), the reconcile arms via stubbed `mpe_surge_on_jack_graph` (`:541-576`), and the `/run` fallback path. `test_empty_active_engine_does_not_kill_caller` (`:336-351`) is a regression test for a specific production incident and its docstring names the incident.

**Failure-path testing is present, not just happy-path.** Unwritable state directory (`:353-372`), state-publish failure not aborting the supervisor (`:578-599`), `jack_lsp` missing while `jackd` is running treated as not-ready (`:393-420`). Also unit-file *assertions* — `StartLimit*` must be in `[Unit]` because systemd ignores it under `[Service]` (`:244-254`), a bug that was actually shipped and fixed in `3cb7de7`.

**Where the strategy is thin.**

1. **Deletion is verified by grepping source text.** `NoAlsaPathTests` (`:423-481`) asserts absence of ~15 identifier strings across five files. Correct-ish for a one-time removal, but these tests will pass forever regardless of behaviour, will break on innocent renames, and one of them (`:524`) depends on a trailing quote character.
2. **The one behavioural test of criterion 2\* tests a copy of the code** — see 4.5. The single most important failure path in the current design has no test that executes the real script.
3. **No shellcheck, no bash coverage measurement, no mypy.** ~60 shell scripts carry the audio engine; `bash -n` is run on exactly one of them (`test_set_surge_audio_is_valid_bash`, `:473-480`).
4. **Nothing tests the udev rules.** 4.3 is a logic bug in a `.rules` file that no test touches. `mpe test coverage` counts shell tests but nothing parses or simulates udev matching, and the rules file is where the invariant was violated.
5. **Hardware-gated criteria stay gated.** `5b` (UAC2 host capture) and the `session_capture.py` half of `14` are both BLOCKED on a physical rewire, and have been since Gate B. The spec's own falsification table lists "jackd's exclusive device open doesn't break calibration/session capture" as an assumption to be resolved *before Phase 1 merge* (`:433`). Phase 1 merged. That assumption is still open.

---

## 7. Security & Performance

### Security — genuinely clean, and I looked hard

**No shell injection surface.** `grep -rn 'shell=True\|os.system\|eval ' patch_browser/ scripts/` returns **nothing**. Every subprocess call in the Python layer is an argv list.

**The one interesting attack surface is handled correctly.** Wi-Fi SSIDs and passwords arrive from an on-screen touch keyboard and reach `nmcli` — the classic place an appliance eats a shell metacharacter. `patch_browser/wifi_manager.py:1` opens with *"NetworkManager Wi-Fi helpers for the touch appliance (nmcli, no shell)"*, and `_run_nmcli` (`:26-41`) builds `["sudo", "-n", "nmcli", …]` as a list with `-n` (never prompt). The password is passed as an argv element (`:294-295`), not interpolated. Correct.

**Privilege is narrow and enumerable.** Five `sudo` call sites across the whole Python layer, in four files (`wifi_manager.py`, `calibration_loopback.py`, `touch_browser_app.py`, `audio_profile.py`). All use `sudo -n` or fixed argv. `mpe_systemctl` (`scripts/lib/audio-engine.sh:292-298`) checks `id -u` and falls back to `sudo -n systemctl`, with a `WARNING:` and non-zero return rather than a hang when passwordless sudo is unavailable (`mpe_restart_audio_graph`, `:307-310`).

**Realtime privilege is permitted, never forced.** `LimitRTPRIO=95` / `LimitMEMLOCK=infinity` on both `mpe-jackd.service` and `surge-xt-cli.service`, with a comment explaining why the unit needs them at all (systemd bypasses PAM, so `/etc/security/limits.d/audio.conf` from the `jackd2` package does not apply). jackd runs as the appliance user, not root. Priority is a fixed constant (`MPE_JACK_RT_PRIORITY_DEFAULT=70`), and even the user-overridable path validates through a numeric `case` (`:67-72`) — a runaway FIFO thread at 70 could starve the touch UI and SSH, so keeping this non-arbitrary matters.

**No network surface.** Every OSC binding is `127.0.0.1` — `surge_monitor.py:82`, `surge_cpu_monitor.py:60, 149`, `patch_loader.py:46`, `surge_playback.py:182`, `calibrate-patch-normalization.py:105`. Nothing binds `0.0.0.0`. Matches the spec's stated model.

**🟢 The one nit:** the Wi-Fi password is an argv element to `sudo nmcli`, so it is briefly visible in `/proc/<pid>/cmdline` and to `ps` for any local user. On a single-user appliance whose threat model is explicitly physical access this is proportionate — noting it only so the choice is deliberate rather than accidental. `nmcli` can read secrets from stdin if that ever changes.

### Performance

**The measurements are real and the conclusions follow from them.** The spec's §Evidence table records 512×3 at 32ms / 0 xruns, 256×3 at 16ms / 0 xruns idle with mild crackle under load, 128×3 at 8ms with 0 xruns but audible crackle — and correctly concludes the artifact is *downstream of JACK*, because the graph stayed up. From there it identifies the DAC (USB Full Speed, 12 Mbit/s, shared with two MIDI devices, `S16_LE`/`S24_3LE` only) as a **hardware** ceiling and declines to chase sub-256 in software. That is the right call, made from data, and it is why the falsification analysis can honestly say a HAT plus Phase 1 is an acceptable end state.

**D6's buffer split is implemented, not just specified.** `MPE_JACK_BUFFER` / `MPE_JACK_PERIODS` drive the server (`mpe_jack_period`, `mpe_jack_periods`, `audio-engine.sh:36-56`) and `--buffer-size=` is gone from Surge's argv — asserted at `tests/test_audio_engine.py:435`. Sample rate deliberately stays a single appliance-wide key so the UAC2 gadget and the graph cannot disagree, with the reason in the comment (`:58-59`).

**`jackd -s` (softmode) is kept deliberately** so a single xrun does not tear down the graph — right for a live instrument, and flagged in §Technical Notes. The Phase 2 spec then notices the consequence nobody would have caught later: softmode may *hide* looper-caused xruns from `set_xrun_callback`, which would make criterion 9e's "0 xruns" clause unverifiable as written. It is logged as OQ-4 with a concrete falsification test. That is the level of care I want to see and rarely do.

**Where I'd push back.** The recovery latency is the honest weak spot and the spec says so: criterion 2b budgeted a 15s audible gap; Gate B measured **~39s** for `pkill jackd` and **~55–60s** for the double-failure case, with Mitch describing DAC replug recovery as *"very slow."* The §Backlog names three candidate fixes and defers them. The amendment's 15s→5s settle reduction will help, but the remaining terms — jackd's own ~6s start, the `RECONCILE_BUDGET` wait, and a full `systemctl restart surge-xt-cli` — are untouched, and the in-process JACK reconnect (the candidate that would actually collapse this) is still on the backlog. Calling the settle reduction an improvement is fair; treating it as *the* fix would not be, and the spec is careful not to.

Then 4.8: a 5 Hz SD-card poll thread on the box that has to hold a 256-frame deadline.

---

## 8. Developer Experience

**Onboarding is unusually good.** `AGENTS.md` gives a `mpe`-CLI-first policy with an explicit agent-safe/read-only vs. writes split and a standing instruction to *propose a new subcommand rather than improvise remote shell* — which is why this review used `mpe test local all` instead of fighting a hook. `docs/` carries 30 files including `BUILD-FROM-ZERO.md`, `PATHS.md`, `PI-BOOT-RECOVERY.md`, and `GIT-WORKFLOW.md`. `docs/GIT-WORKFLOW.md` states the branch policy in one table plus one rule worth quoting: *"Do not push to `dev` and `main` in the same testing pass."*

**The docs told the truth every time I checked them.** `docs/PATHS.md:43` records `MPE_AUDIO_ENGINE` as retired; `config/mpe.env.example:56` says the same; `README.md` describes the JACK graph architecture. The D2 inventory matched the code. `mpe test coverage`'s claim matched reality. I went in expecting the usual doc rot and found approximately none.

**Two places where the honesty is exemplary and I want it named.** First, `Documents/specs/jack-audio-engine-spec.md:118-127` — a *citation note* admitting that `docs/AUDIO-ENGINE-FOUNDATION.md`, cited throughout both specs, **does not exist on this branch or on `dev`**; it lives only on the unmerged `yolo/looper-phase0`, was read via `git show`, and is deliberately not duplicated because that would fork a document PR #48 owns. Second, `looper-jack-client-spec.md:312-318` — *"The fixture set does not exist,"* with the `git ls-tree` command that proves criterion 8's "existing fixture set" refers to something not in the repository. Most specs would have papered over both.

**Where DX degrades.**

- **The uncommitted Phase 2 spec (4.1) is a DX failure before it is anything else.** Every collaborator, agent session, and CI run is working from a strictly poorer picture of the plan than the one person with this working tree.
- **`docs/AUDIO-ENGINE-FOUNDATION.md` being cross-branch-only** is documented honestly but is still a real cost: two specs' core reasoning (Part 8's option D, Part 3's NumPy sketch, the Appendix's 0.090ms mixer measurement) is unreadable without `git show` against an unmerged branch. Anyone reviewing on `dev` hits dead references.
- **Repo-root clutter.** `ENCODER_BUTTON_REVIEW.md`, `REFERENCE_BOM.md`, `FAQ.md`, `COMMANDS.md`, `boot_animation.py`, `shutdown_animation.py`, `touch_boot_splash.py`, `touch_shutdown_splash.py`, `touch_patch_browser.py`, `patch_browser_ui.py`, `setup-i2c-early.sh` all sit at top level alongside `patch_browser/`, `scripts/`, `docs/`, `Documents/`. And there are two documentation trees — `docs/` (30 files) and `Documents/specs/` (5) — with no stated rule for which gets what. `Documents/reviews/` (this file) is now a third.
- **`config/` mixes templates with data.** `patch_normalization.json` plus two dated `patch_normalization.pi-backup-*.json` files are committed next to systemd unit templates. `.gitignore`'s closing comment — *"Everything else (binaries, patches, configs) GOES IN GIT for backup!"* — explains the choice, but using the source repo as a device backup target is why `config/` is hard to read.
- **`logs/`, `data/`, `.pytest_cache/` are committed directories** in a repo that is also an appliance deploy target.

---

## Spec Conformance: Phase One vs Plan

Phase 1 criteria from `Documents/specs/jack-audio-engine-spec.md` §Acceptance Criteria, as amended 2026-08-13. **Landed** = implemented in code on `HEAD`. Hardware verification is tracked separately because the amendment invalidated much of the Gate B evidence.

| # | Criterion | Code | Hardware | Evidence |
|---|---|---|---|---|
| 1 | Cold boot leaves Surge a JACK client wired to the DAC | **Landed** | Gate B PASS | `config/mpe-jackd.service`, `scripts/jackd-prestart.sh`, `start-jackd.sh`, `start-surge-cli.sh:122-133` |
| 2a | jackd fails at boot → sound anyway | **Retired, cleanly** | n/a | No fallback branch survives; `tests/test_audio_engine.py:426-436` asserts absence of `select_alsa_device`, `ENGINE-FALLBACK`, `--buffer-size=` |
| **2\*** | jackd fails at boot → **silent and says so**; unmask → auto-promote | **Landed** | ❌ **NEVER SOAKED** | `start-surge-cli.sh:147-154` publishes `state=failed`/`active=none` and exits 1; promote arm at `surge-watchdog.sh:88-90`. Spec `:452-459` lists this as Gate C item 1, not run |
| 2b | jackd dies mid-set → sound returns | **Landed** | Gate B PASS **at old 15s settle** | `Restart=always` (`mpe-jackd.service:44`) + promote. Retest at 5s = Gate C item 2, not run. Measured ~39s vs 15s budget — **missed** |
| 2b2 | Repeated jackd death does not wedge Surge | **Landed** | Gate B PASS **at old settle** | Cooldown table `audio-engine.sh:369-392`; `StartLimit*` correctly in `[Unit]` |
| 2c | Double failure → loud not silent | **Retired, subsumed into 2\*** | n/a | Consistent with code |
| 2d | Promotion after degraded boot | **Retired, folded into 2\*** | n/a | `release-alsa-for-jackd` gone; `tests/…:456` asserts it |
| 3 | `MPE_AUDIO_ENGINE=alsa` reproduces old behaviour | **Retired, cleanly** | n/a | Variable read **nowhere**; only comments/docs/tests mention the name |
| 4 | `engine=jack` reads back identically after reboot | **Landed** | not re-soaked | Static `MPE_ENGINE_NAME="jack"` (`audio-engine.sh:18`) / `ENGINE_NAME` (`audio_engine.py:20`) |
| 5a | Profile switch restarts **jackd**; supervisor reconciles | **Landed** | Gate B PASS | `set-audio-profile.sh:57` → `restart_audio_graph` |
| 5b | UAC2 host capture open/close re-points the graph | **Landed** | ❌ **BLOCKED** (physical rewire) | `uac2-stall-watchdog.sh:67`. Falsification table `:430` says verify *"before building Phase 2"* — Phase 2 spec is written and Tasks 1–6 declared startable. **Drift.** |
| 6 | Surge audio **thread** `SCHED_FIFO`, no `chrt` on the process | **Landed** | Gate B PASS (spot check) | No `chrt` anywhere in `start-surge-cli.sh`; D4 note at `:134-137` |
| 10 | Looper guard refuses via one shared message; no restart loop | **Partial by design** | ❌ deferred twice | Policy + tests in `engine-guard.sh` and `audio_engine.py:36-49`. **Authoritative chokepoint (`mpe-looper.py main()`) does not exist on `dev`** — `engine-guard.sh:12-15` says so |
| 12 | `engine=jack` on fresh install **and** with stale `MPE_AUDIO_ENGINE=alsa` | **Landed** | ❌ not soaked | Nothing reads the variable, so a stale line is inert. Gate C item 4 |
| 13 | Looper regression **surfaced** — HUD shows `looper=guarded` | **Landed** | Gate B **PARTIAL** | `engine_state_monitor.py` → `touch_browser_draw.py:392-412`; `L⛔` at `audio_engine.py:124`. Never booted with `MPE_LOOPER_ENABLED=1` |
| 14 | Direct-ALSA consumers work with jackd holding the device | **Landed (cal)** | PASS (cal) / ❌ **BLOCKED** (session capture) | Falsification table `:433` requires this audit *before Phase 1 merge*; merged with `session_capture.py` unaudited. See also 4.3/4.10 — teardown is **not** clean |
| 15 | DAC unplug/replug restarts jackd **from any prior state** | **Landed** | Partial — soaked from `ok` only | `99-usb-audio.rules:26-27`; `mpe_restart_audio_graph` does `reset-failed` first (`audio-engine.sh:306`) and resets the supervisor budget (`:314`). Amendment widened to "from `failed`" — Gate C item 3 |
| 16 | JACK buffer keys independent of `MPE_SURGE_BUFFER_SIZE` (D6) | **Landed** | not re-soaked | `mpe_jack_period`/`periods`; `--buffer-size=` absence asserted |
| 17 | `mpe engine status` is the documented interface | out of scope | Gate B PASS | Lives in `mpe-cli` |

### Surprises

**Specced but missing:**

1. **Gate C soak — 5 named scenarios, zero run.** The spec blocks its own merge on this. This is the finding.
2. **Criterion 10's authoritative guard** — the chokepoint the D5 analysis worked hard to correctly identify does not exist on `dev`; only policy + unit tests do.
3. **`docs/AUDIO-ENGINE-FOUNDATION.md`** — cited throughout both specs (Part 3, Part 8, Part 9, §Appendix), present on neither `dev` nor `HEAD`. Documented honestly at `:118-127`; still a dead reference for anyone reviewing on `dev`.
4. **`docs/measurements/`** — named as criterion 9's destination by the governing spec; does not exist (Phase 2 Task 6).
5. **The Phase 2 spec itself** is untracked (4.1).
6. **5b and session-capture (14)** — two falsification-table assumptions that were to be closed *before* Phase 1 merge / *before* Phase 2 work, both still open while Phase 2 is being planned.

**Landed but unspecced:**

1. **`mpe test coverage`** — the suite-registry gate is not in any spec and is one of the best things here.
2. **`mpe_restart_audio_graph`'s `reset-failed` + supervisor-budget reset** (`audio-engine.sh:300-316`) — goes beyond D2, and is what makes criterion 15's recovery-from-`failed` actually work. Earned via a real bug (`0cc6763`, `9258b68`).
3. **`start-jackd.sh:35-41`'s state-clobber guard** — a race the spec never names.
4. **The 4.3 udev `remove` blind spot** — a pre-existing bug in a file both Phase 1 and the amendment touched, invisible to every spec and every test.
5. **🔴 The "analog-esque mixer" half of the product goal has no spec at all.** `Documents/specs/` holds four specs: the JACK engine, the looper JACK client, and three touch-browser docs. **None covers a mixer.** What exists in code is `patch_browser/mixer.py` — a **20-line `MixerChannel` dataclass** — plus `mixer_controls.py` (340 lines) and `touch_browser_mixer.py` (128 lines), which together implement per-patch **Vol / Tail / Touch** faders on the patch detail pane (`README.md:57`). Those are *parameter* controls, not audio mixing: no buses, no summing, no per-source gain staging, nothing analog-esque. The only actual audio-mixing work anywhere in the plan is the looper's NumPy mix kernel, which is Phase 2, which is gated on a measurement whose own kill criterion includes *"Stop. Phase 1 + an I2S HAT."* Whatever "analog-esque mixer" means as a product, it currently has **zero design surface**, and the naming collision with the existing per-patch fader UI will make that gap easy to keep overlooking.

---

## Verdict

The merged Phase-one state **is coherent with the spec's phase structure** — considerably more so than the "things got twisted up" framing suggests. I went looking for the usual post-merge wreckage and found almost none: no surviving ALSA branch, no dual-engine state machine, no duplicated audio path, the D2 six-call-site inventory honoured in code, `degraded` genuinely retired in both languages with a migration test for stale state files, and exactly one misnamed file (`looper_clock_monitor.py`) as debris. 440 tests pass in 42s, security is clean in a way most appliance codebases are not, and the specs are the best-reasoned engineering documents I have reviewed in this repo class — they falsify themselves, retire their own criteria in place rather than rewriting history, and twice admit in writing that a document they cite does not exist. That discipline is the asset here and it should be protected. What is at risk is not the architecture but the **verification ledger**: `HEAD` reverses Phase 1's second goal ("never boots silent" → "fail loud and legible"), which deleted the exact fallback arm that a full day of Gate B soaking had proven, and the five Gate C scenarios that would validate the replacement have not run — so the instrument's behaviour on its single most important failure is currently a hypothesis defended by a test that re-implements the script it tests. Two real bugs compound this: udev `ATTR{}` matches cannot fire on `remove` events, so all four skip guards in `99-usb-audio.rules` leak and every calibration teardown restarts jackd in violation of the invariant its own comment states; and the amendment made "Surge exits non-zero" the routine jackd-outage path, which now routinely trips a watchdog arm that blames a missing DAC on a corrupt config file and moves the user's Surge defaults aside. As for the product goal: the "sound module" half is on a credible, measured, honestly-bounded path. The **"analog-esque mixer" half has no spec, no buses, and no plan** — the only thing named "mixer" in the tree is a 20-line dataclass driving three per-patch parameter faders, and the sole genuine audio-mixing work sits behind a Phase 2 gate whose own kill criterion permits the answer "stop." Fix the 🔴s, run Gate C, and write a mixer spec before more engine work lands on top of an unverified reversal.

**Findings: 5 🔴 · 6 🟡 · 5 🟢**

🔴 — 4.1 untracked Phase 2 spec · 4.2 unsoaked hard-failure design · 4.3 udev `remove` blind spot · 4.4 watchdog discards user defaults · §Spec Conformance mixer has no spec
🟡 — 4.5 self-testing test · 4.6 19-mixin god object · 4.7 `getattr` layout chain · 4.8 5 Hz SD-card poller · 4.9 CI weaker than local gate · 4.10 teardown/jackd race
🟢 — 4.11 dead per-frame import · 4.12 `looper_clock_monitor` misnamed · 4.13 stale CHANGELOG count · 4.14 unbounded no-DAC retry storm · §7 Wi-Fi password in argv

---

## Priority backlog

1. **🔴 Commit `Documents/specs/looper-jack-client-spec.md`.** 869 lines of the repo's most detailed design work is untracked (`git status` → `??`) — invisible to CI, to every other session, and to `dev`, and one `git clean -fd` from gone. This is a five-second fix and it is first for a reason.
2. **🔴 Run the Gate C soak before merging `yolo/jack-drop-alsa-fallback` to `dev`.** The spec blocks its own merge on this in bold, and lists the five scenarios in order (`jack-audio-engine-spec.md:452-459`): criterion 2\* masked-jackd boot → `state=failed` + promote-on-unmask; 2b/2b2 retest at the new 5s settle; criterion 15 replug from `state=failed`; criterion 12 with a stale `MPE_AUDIO_ENGINE=alsa` line; D5 guard boot. Every Gate B PASS row for the failure paths tested code that no longer exists.
3. **🔴 Fix the udev `remove`-event blind spot in `config/99-usb-audio.rules:16-19`.** `ATTR{}` cannot match on `remove` (sysfs is already unlinked), so the `Loopback` / `UAC2` / `UAC2Gadget` / `vc4hdmi` skips apply to `add` only, and `modprobe -r snd_aloop` in `calibration_teardown.py:35` restarts jackd after every calibration run — the precise scenario line 19's comment forbids. Filter on kernel-supplied `ENV{}` properties, or move the card-identity check into `restart-audio-graph.sh` where it is one filter instead of four and testable from a laptop.
4. **🔴 Stop the watchdog from destroying Surge's user defaults during a jackd outage** (`scripts/surge-watchdog.sh:97-105`). Post-amendment, `start-surge-cli.sh` exits 1 by design when there is no graph server, so `surge-xt-cli` reliably reaches `failed` ~100s into any DAC problem, and this arm then moves the user's accumulated defaults to `.corrupted_<timestamp>` — misattributing a cable fault to file corruption and accumulating junk files that nothing ever cleans up. Gate the arm on `mpe_engine_state_get state` / `reason`: skip it when the failure is already explained by `no-server` or `no-jack-device`.
5. **🔴 Write a spec for the "analog-esque mixer" before more engine work lands.** Half the stated product goal has no design surface: the only "mixer" in the tree is a 20-line `MixerChannel` dataclass plus per-patch Vol/Tail/Touch parameter faders, and the sole real audio-mixing work is Phase 2's NumPy kernel — gated on a measurement whose kill criterion permits "stop, Phase 1 + a HAT." Decide now whether the mixer rides on the JACK graph (which would make it a Phase 2 dependency and change the looper's insert topology in `looper-jack-client-spec.md` §B.3) or is a separate product surface. This is the question that will invalidate the most work if answered late.
