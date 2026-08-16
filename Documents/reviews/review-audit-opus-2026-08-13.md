# Review Audit — `grumpy-review-opus-2026-08-13.md`

*Audit date: 2026-08-13 (America/Toronto)*

**Review audited:** `Documents/reviews/grumpy-review-opus-2026-08-13.md`
**Codebase:** `/home/mitch/Documents/GitHub/MPE-Module` @ `yolo/jack-drop-alsa-fallback` `1ab9f55`
**Method:** every claim taken back to the file, with `git`, `grep`, and the `mpe` CLI run against the working tree. No files modified except this one.

**Tally: 48 ✅ · 12 ⚠️ · 3 ❌ · 2 🔍**

---

## Work Queue

Claims extracted from the review, grouped by artifact.

**Git / process (A1–A7)**
A1 Phase 2 spec untracked · A2 branch identity `1ab9f55` = `dev` + 1 · A3 HEAD reverses goal 2 and deletes the soaked fallback arm · A4 Gate C's five scenarios unrun; four Gate B PASS rows tested deleted code · A5 440 tests / 42s / exit 0 · A6 CHANGELOG says 438 · A7 `mpe test coverage` output as quoted

**`config/99-usb-audio.rules` (B1–B5)**
B1 `ATTR{}` cannot match on `remove` · B2 all four skip guards leak; every remove hits the generic rule · B3 `modprobe -r snd_aloop` in `calibration_teardown.py:35` fires it every calibration · B4 cited line numbers `:19, 27` / `:16-19` · B5 proposed fix "filter on kernel-supplied `ENV{}`"

**`scripts/surge-watchdog.sh` (C1–C8)**
C1 corrupt-defaults arm at `:97-105` · C2 `start-surge-cli.sh` exits 1 by design `:147-154` · C3 unit is `Restart=on-failure` / `RestartSec=10` / `StartLimit*` in `[Unit]` · C4 ~100s to `failed` · C5 `.corrupted_*` accumulate, nothing cleans them · C6 `grep -rn corrupted` returns exactly two lines · C7 `RECONCILE_BUDGET` busy-wait stretches the outer cadence to ~20s · C8 the 15s `RECONCILE_BUDGET` was left behind when settle went 15s → 5s

**Tests / CI (D1–D7)**
D1 `StartSurgeCliFailureTests` tests its own copy of the script · D2 `:524` assertion depends on a trailing quote · D3 `NoAlsaPathTests` is string-absence only · D4 no shellcheck anywhere · D5 CI installs one hand-picked package and hardcodes two shell tests · D6 `bash -n` on exactly one script · D7 nothing tests the udev rules

**Touch UI (E1–E6)**
E1 19 mixins · E2 133 `self.*` assignments · E3 `getattr` chains at `:484-489` and `:503-505` · E4 stated cause ("draw cannot know whether layout ran") · E5 dead per-frame import at `:450`, second at `:478` · E6 20 `threading.Thread` hits

**Monitors (F1–F3)**
F1 `EngineStateMonitor` 0.5s on tmpfs · F2 `LooperClockMonitor` 0.2s on `$HOME` (SD card) · F3 `looper_clock_monitor.py` misnamed; only surviving `looper_*` file

**Architecture / spec conformance (G1–G7)**
G1 tmpfs rationale and `BindsTo` reasoning · G2 bash↔Python parity pinned by tests · G3 D2 inventory honoured; nothing restarts Surge on a device-changing path · G4 two front-ends share one model layer by import · G5 `degraded` retired in both languages with a stale-file migration test · G6 the "analog-esque mixer" half of the product goal has no spec · G7 `Documents/specs/` inventory

**Security (H1–H6)**
H1 no shell-injection surface · H2 `nmcli` argv + `sudo -n` · H3 "five `sudo` call sites in four files; all use `sudo -n` or fixed argv" · H4 no network surface, all OSC on loopback · H5 Wi-Fi password visible in `/proc/<pid>/cmdline` · H6 RT privilege permitted, not forced

**Performance (I1–I3)** · **DX (J1–J5)** · **Retry storm (K1)**

---

## Claim Verification

### Git and process state

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| A1 | `Documents/specs/looper-jack-client-spec.md` is untracked | ✅ | `git status --porcelain` → `?? Documents/specs/looper-jack-client-spec.md` (plus `?? Documents/reviews/` and ` M .cursor/permissions.json`). 53,273 bytes on disk, in no commit. |
| A2 | Branch `yolo/jack-drop-alsa-fallback` @ `1ab9f55` = `dev` @ `daac891` + 1 commit | ✅ | `git log --oneline dev..HEAD` → exactly `1ab9f55 refactor(audio-engine): remove ALSA entirely as a product audio path`. `daac891` is the PR #49 merge. |
| A3 | HEAD reverses Phase 1 goal 2 and deletes the Gate-B-proven fallback arm | ✅ | `jack-audio-engine-spec.md:142-145`: *"~~The instrument never boots silent…~~ **RETIRED 2026-08-13.** Reversed by design."* `start-surge-cli.sh:9-14` states the same in the script header. No fallback branch survives (`NoAlsaPathTests` + my own read of `start-surge-cli.sh:114-154`). |
| A4 | Gate C's five scenarios unrun; four Gate B PASS rows tested deleted code | ✅ (documentary) | Gate C list is verbatim at `jack-audio-engine-spec.md:452-459`, headed **"Required before merge — Gate C soak (not yet run)"**. Gate B rows `2a` (`:465`), `2d` (`:466`), `3` (`:469`), `2c` (`:472`) all describe `active=alsa` / `state=degraded` / `release-alsa-for-jackd` — none of which exist in the tree. 🔍 on *whether* Gate C ran: unverifiable from a laptop; the spec's own status line is the only evidence either way. |
| A5 | 440 tests, 0 failures, ~42s, exit 0 | ✅ | I re-ran it: `mpe test local all` → `Ran 440 tests in 42.277s` / `OK`, plus both shell tests green. Matches the review's `42.113s` run. |
| A6 | `CHANGELOG.md` claims 438 | ✅ | `CHANGELOG.md:71` — *"Full suite: 438 tests, 0 failures (`mpe test local all`)."* Actual 440. |
| A7 | `mpe test coverage` output as quoted | ✅ | Reproduced verbatim: 60 modules, 16 registered-but-absent on unmerged branches, 2 shell tests, `OK`. |

### `config/99-usb-audio.rules`

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| B1 | `ATTR{id}` reads sysfs, which is already unlinked on `remove`, so `ATTR{}` matches cannot succeed | ✅ | Standard udev semantics, not a repo fact — `ATTR{}` resolves against `/sys/$DEVPATH`, which is gone by the time the `remove` uevent is processed. Only `ENV{}` (restored from the udev database) survives. Nothing in the rules file or `install-udev-rules.sh` works around it. |
| B2 | All four skip guards filter `add` only; every `remove` falls through to the generic rule | ✅ | ```ACTION=="add\|remove", SUBSYSTEM=="sound", KERNEL=="card[0-9]*", ATTR{id}=="Loopback", GOTO="mpe_usb_audio_end"``` (`:21`) followed by ```ACTION=="remove", … RUN+="…/restart-audio-graph.sh"``` (`:27`). The `remove` half of each guard is inert; the generic rule is unguarded. |
| B3 | `unload_snd_aloop_if_idle()` → `modprobe -r snd_aloop` restarts the graph at the end of every calibration | ✅ | `calibration_teardown.py:35` `subprocess.run(["sudo", "modprobe", "-r", "snd_aloop"], check=False)`, called from `restore_mpe_audio_services()` at `:69`. Module removal deletes the Loopback card → `remove` uevent → line 27 → `restart-audio-graph.sh` → `mpe_restart_audio_graph()`. The invariant it violates is written on `:20` of the rules file. 🔍 on hardware confirmation — the mechanism is sound but nobody has watched it happen on the Pi. |
| B4 | Cited as `:19, 27` in §4.3 and `:16-19` in the backlog | ⚠️ | Off by two. The `Loopback` guard is line **21**, not 19; line 19 is the bare `UAC2` guard. The four guards span **17–21**, not 16–19. The quoted rule text is correct; only the anchors drift. |
| B5 | Fix: "filter on kernel-supplied `ENV{}` properties (available on `remove`) instead of `ATTR{}`" | ⚠️ | Half right. `ENV{}` does survive `remove` (udev restores db properties), but **no kernel-supplied property carries the ALSA card `id`** — the `sound/cardN` uevent gives `DEVPATH`, `SUBSYSTEM`, `ACTION`, and little else, and this repo runs no rule that stamps `ID_*` onto sound devices earlier. As written, option 1 would need a *new* `add`-time rule to plant `ENV{MPE_SKIP_CARD}=1` first. The review's own option 2 (move the identity check into `restart-audio-graph.sh` against `/proc/asound/cards`) is the one that actually works, and the review is right that it collapses four filters into one testable place. Take option 2; do not attempt option 1 as stated. |

### `scripts/surge-watchdog.sh`

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| C1 | Corrupt-defaults arm at `:97-105` moves user defaults aside on `is-failed` | ✅ | Exact quote matches `:97-104`: `if systemctl is-failed "$SURGE_SERVICE"` → `log "ALERT: Surge service failed, cleaning user defaults"` → `mv "$USER_DEFAULTS" "$BACKUP"`. |
| C2 | `start-surge-cli.sh` exits 1 by design with no graph server | ✅ | `start-surge-cli.sh:147-154` — `ENGINE_STATE=failed`, two `CRITICAL:` log lines, `mpe_engine_state_write … failed "$ENGINE_REASON"`, `mpe_surge_state_write none ""`, `exit 1`. |
| C3 | `Restart=on-failure`, `RestartSec=10`, `StartLimitBurst=5` / `StartLimitIntervalSec=300` | ✅ (line refs ⚠️) | All four keys present and correctly placed: `StartLimitBurst=5` / `StartLimitIntervalSec=300` at `surge-xt-cli.service:5-6` in `[Unit]`; `Restart=on-failure` / `RestartSec=10` at `:24-25`. The review cites `:6-7, 27-28` — off by one/two in both pairs. Substance intact. |
| C4 | "Roughly 100s into any jackd outage, `is-failed` becomes true" | ⚠️ | Directionally right, arithmetic unverified. Each failed start costs the bounded JACK wait (`MPE_JACK_READY_TIMEOUT_DEFAULT=10`, `audio-engine.sh:32`) plus `RestartSec=10`, so the fifth attempt lands ~80–100s in — but `wait-for-usb-midi.sh` runs before the engine block (`start-surge-cli.sh:69-79`) and can add unbounded time. "~100s" is a reasonable floor, not a measured number. Nothing turns on the exact figure. |
| C5 | `.corrupted_<ts>` files accumulate; nothing cleans them | ✅ | Repo-wide `grep -rn corrupted` returns exactly `surge-watchdog.sh:101` and `:103` (plus the review itself). No cleanup path, no `find -mtime`, no logrotate anywhere in `scripts/` or `config/`. |
| C6 | `grep -rn corrupted` returns exactly those two lines | ✅ | Reproduced. |
| C7 | The `RECONCILE_BUDGET` busy-wait stretches the `is-failed` arm to ~20s | ✅ | `RECONCILE_BUDGET="${MPE_RECONCILE_BUDGET_SEC:-15}"` (`:13`); `_reconcile_engine` sleeps 1s per iteration up to the budget (`:75-81`); the outer loop then adds `sleep 5` (`:117`). Both live in the same single-threaded `while true`. |
| C8 | The 15s `RECONCILE_BUDGET` was left behind when settle moved 15s → 5s | ✅ | `MPE_ENGINE_JACKD_SETTLE_DEFAULT=5` with an explicit "was 15s" comment (`audio-engine.sh:336-341`) and the mirrored `JACKD_SETTLE_SEC = 5` in `audio_engine.py:32`. `RECONCILE_BUDGET` is still 15 with no comment and no mention in the spec's §Backlog. The review is also right that they are different knobs — this is a legibility trap, not a bug. |

### Tests and CI

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| D1 | `test_jack_failure_exits_nonzero_and_publishes_failed` asserts against the test's own copy of the failure branch | ✅ | `tests/test_audio_engine.py:486-504` builds a bash `body` that re-declares `mpe_wait_for_jack_server() { return 1; }` and then hand-writes the three lines of the failure branch. `start-surge-cli.sh` is never executed. Renaming, reordering, or dropping `mpe_surge_state_write` in the real script leaves this test green. |
| D2 | `:524` matches only when the path is followed by a double quote | ✅ | `self.assertNotIn("detect-audio-device.sh\"", text)` at `:524`. `bash $SCRIPT_DIR/detect-audio-device.sh` or a trailing space would slip straight past it. |
| D3 | `NoAlsaPathTests` is ~15 string-absence assertions across five files | ✅ | `:423-481`. Six tokens for `start-surge-cli.sh`, seven for `audio-engine.sh`, four for the watchdog, two for the guard, three for `set-surge-audio.sh`. All `assertNotIn`. |
| D4 | No shellcheck anywhere | ✅ | Absent from `.github/workflows/test.yml`, from `.cursor/`, and from every script; the only shellcheck strings in the repo are `# shellcheck source=` / `disable=` directives inside the scripts themselves. |
| D5 | CI installs one hand-picked package; two hardcoded shell tests | ✅ | `.github/workflows/test.yml:19` `pip install python-osc`; `:29-30` names `test_gadget_persist.sh` and `test_prepare_dsi_display.sh` literally. `requirements.txt` exists and is unused by CI. |
| D6 | `bash -n` runs on exactly one script | ✅ | `test_set_surge_audio_is_valid_bash` at `:473-480` is the only syntax check in the suite. |
| D7 | Nothing tests the udev rules | ✅ | No test file references `99-usb-audio.rules`; `mpe test coverage` counts two shell tests, neither of which parses or simulates udev matching. |

### Touch UI

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| E1 | 19 mixins | ✅ | `sed -n '67,87p' … \| grep -c 'Mixin,'` → **19**. |
| E2 | 133 `self.*` assignments in the constructor | ✅ | `sed -n '60,300p' … \| grep -c '^        self\.'` → **133**, exactly the review's number and window. (Whole `__init__` is 131 by a different cut — the shape of the finding is unaffected.) |
| E3 | Six `getattr` defaults and three cascading fallbacks at `:484-489`; same pattern at `:503-505` | ✅ | Quote is byte-accurate against `touch_browser_draw.py:484-489`, and `:503-505` repeats it (`self._draw_engine_hud(getattr(self, "engine_hud_rect", Rect(0, 0, 0, 0)))`). |
| E4 | Cause: "the draw mixin cannot know whether the layout mixin has run" | ⚠️ | Stronger than stated, for a different reason. `self._layout()` is called from `__init__` at `touch_browser_app.py:248`, before any frame is drawn, and it unconditionally assigns `status_title_x` (`touch_browser_layout.py:101`), `looper_hud_rect` (`:127/135/137` — every branch), `engine_hud_rect` (`:142/150`), and `audio_profile_badge_rect` (`:153`). So the ordering hazard the review names **does not exist**; the `getattr` defaults are unconditionally unreachable. That makes the recommended deletion safer than the review implies, not riskier. |
| E5 | Dead `is_recovering` import at `:450`; second function-local import at `:478` | ✅ | `is_recovering` appears exactly once in the whole package — the import itself. `:478` is `from patch_browser.usb_audio_recovery import status_subtitle`, called on the next line, inside `_draw_status_bar`. |
| E6 | `grep -rn 'threading.Thread' patch_browser/` → 20 hits | ⚠️ | Actual **22** across 11 modules (`calibration_loader` 4, `touch_browser_prefs` 3, then pairs). Undercount; the point (many threads on a deadline-bound box) stands. |

### Monitors

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| F1 | `EngineStateMonitor` polls tmpfs at 0.5s | ✅ | `POLL_INTERVAL_S = 0.5` (`engine_state_monitor.py:10`); `read_engine_state()` targets `/run/mpe/engine.state` (`audio_engine.py:24`). |
| F2 | `LooperClockMonitor` polls `$HOME` at 0.2s — 5 Hz on the SD card | ✅ | `POLL_INTERVAL_S = 0.2` (`looper_clock_monitor.py:10`); `CLOCK_STATE_FILE = Path.home() / ".mpe_midi_clock_state.json"` (`midi_clock.py:19`). |
| F3 | `looper_clock_monitor.py` is the only surviving `looper_*` file and is misnamed | ✅ | `git ls-files \| grep -i looper` → one path. It reads the `midi-clock-in` daemon's state, is imported at `touch_browser_app.py:28`, and drives a BPM badge. Nothing looper about it. |

### Architecture and spec conformance

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| G1 | tmpfs rationale and the `BindsTo` rejection are correct and current | ✅ | `surge-watchdog.service:4` is `BindsTo=surge-xt-cli.service`, which is exactly the hazard `audio-engine.sh:9-12` names. `surge-xt-cli.service:3-4` carries `Wants=`/`After=` with no `Requires`, matching the amended rationale at spec `:298-310`. |
| G2 | The bash/Python cooldown duplication is pinned by parity tests | ✅ | `mpe_engine_reconcile_decision()` (`audio-engine.sh:369-392`) and `reconcile_cooldown_decide()` (`audio_engine.py:52-76`) evaluate the same four rules in the same order; `BashReconcileParityTests` runs both. |
| G3 | D2 honoured: nothing in `scripts/` or `patch_browser/` restarts `surge-xt-cli` on a device-changing path | ⚠️ | The device-changing half is true and I re-verified every site: `set-audio-profile.sh:57` and `uac2-stall-watchdog.sh:67` call `restart_audio_graph`, `set-surge-audio.sh:129` calls `mpe_restart_audio_graph`, and the udev rule routes through `restart-audio-graph.sh`. But the sweeping phrasing hides two direct Surge restarters the review never mentions: `surge_monitor.py:167` (`sudo systemctl restart surge-xt-cli.service`, user-triggered — see MISSED-1) and `mpe-services.sh:121` (deploy helper). Neither is device-driven, so D2 holds; the inventory as stated does not. |
| G4 | The two front-ends share the model layer by import, not duplication | ✅ | `patch_browser_ui.py:30-42` imports `PatchLoader`, `PatchScanner`, and `SurgeMonitor` from `patch_browser/`. No second implementation. |
| G5 | `degraded` retired in both languages with a stale-state-file test | ✅ | `VALID_ENGINE_STATES = frozenset({"ok", "recovering", "failed"})` (`audio_engine.py:21`); watchdog asserted free of the token (`test_audio_engine.py:456`). |
| G6 | The "analog-esque mixer" half of the product goal has no spec, no code, no plan | ⚠️ | Every *code* fact checks out: `mixer.py` is a 20-line `MixerChannel` dataclass; `mixer_controls.py` is 340 lines; `touch_browser_mixer.py` is 128; `README.md:57` describes them as per-patch Vol/Tail/Touch faders; no spec in `Documents/specs/` covers buses, summing, or gain staging. But **"analog-esque mixer" appears nowhere in the repository** — `grep -ri` finds it only inside the two review documents. `README.md:3` and `AGENTS.md:5` state the product as "MPE sound module," full stop. This is a gap between Mitch's verbal framing and the repo, which is worth naming, but the review presents it as a documented product goal that the codebase abandoned. It is a planning question, not a code defect, and it cannot be adjudicated against this repo at all. |
| G7 | "`Documents/specs/` holds four specs: the JACK engine, the looper JACK client, and three touch-browser docs" | ⚠️ | Self-contradicting sentence: it says four and lists five. `ls` confirms **five** files. Trivial, but it appears inside the 🔴 mixer finding. |

### Security

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| H1 | No shell-injection surface — `shell=True`, `os.system`, `eval` all absent | ✅ | Reproduced across `patch_browser/`: zero matches. Every subprocess call I read is an argv list. |
| H2 | Wi-Fi credentials reach `nmcli` as argv with `sudo -n`, never interpolated | ✅ | `wifi_manager.py:32` `prefix = ["sudo", "-n", "nmcli"] if use_sudo else ["nmcli"]`; password passed as a list element. |
| H3 | "Five `sudo` call sites across the whole Python layer, in four files (`wifi_manager.py`, `calibration_loopback.py`, `touch_browser_app.py`, `audio_profile.py`). All use `sudo -n` or fixed argv." | ❌ | Wrong on both counts. `sudo` appears in **nine** files — `wifi_manager.py` (20), `calibration_teardown.py` (8), `dsi_splash.py` (4), `audio_profile.py` (2), `calibration_loopback.py` (2), and one each in `touch_browser_app.py`, `surge_audio.py`, `midi_sync_settings.py`, `surge_monitor.py` — with well over a dozen distinct invocation sites (`calibration_teardown.py` alone has seven at `:35, 48, 50, 67, 70, 71, 72, 74`). And only `wifi_manager.py` uses `-n`; every other site is a bare `sudo`, which on an appliance without passwordless sudo blocks rather than failing fast — the exact hazard `mpe_systemctl` (`audio-engine.sh:292-298`) was written to avoid on the shell side. The **conclusion** survives: every one of those sites is a fixed argv list, so there is still no injection surface. The enumeration that made the conclusion feel audited does not. |
| H4 | No network surface; every OSC binding is loopback | ✅ | Every `SimpleUDPClient` / `sock.bind` in `patch_browser/` and `scripts/` uses `127.0.0.1`; zero `0.0.0.0` in the repo. |
| H5 | Wi-Fi password visible in `/proc/<pid>/cmdline` | ✅ | Consequence of H2's argv approach; correctly rated 🟢 for a single-user appliance. |
| H6 | RT privilege permitted, not forced; jackd priority validated | ✅ (with a gap — see MISSED-6) | `LimitRTPRIO=95` / `LimitMEMLOCK=infinity` on both units with the PAM-bypass comment; `MPE_JACK_RT_PRIORITY_DEFAULT=70`; no `chrt` anywhere in `start-surge-cli.sh`. |

### Performance

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| I1 | `jackd -s` softmode kept deliberately | ✅ | `start-jackd.sh:43` — `exec jackd -R -P"$JACK_PRIO" -s …`. |
| I2 | Recovery measured ~39s (`pkill jackd`) and ~55–60s (double failure) against a 15s budget | ✅ | Gate B rows at spec `:463` and `:472`; `:464` records Mitch calling DAC replug "very slow". The spec's §Backlog concedes the miss. |
| I3 | D6 buffer split implemented; `--buffer-size=` gone from Surge argv | ✅ | `mpe_jack_period` / `mpe_jack_periods` feed `start-jackd.sh:43-44`; `SURGE_AUDIO_ARGS` (`start-surge-cli.sh:129-132`) carries only `--audio-interface` and `--sample-rate`; absence asserted at `test_audio_engine.py:434`. |

### Developer experience

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| J1 | "`logs/`, `data/`, `.pytest_cache/` are committed directories" | ❌ | `git ls-files logs data .pytest_cache` returns exactly one path: `data/patch_metadata_baseline.json` — a legitimate test fixture. `logs/` contents are gitignored (`.gitignore` names `logs/shutdown-trace.jsonl` and the marker); `.pytest_cache/` self-ignores via the `.gitignore` pytest writes into it, which is why it never shows in `git status`. Three directories exist on disk; **none of them is a committed directory in the sense the finding implies**. |
| J2 | `docs/` carries 30 files | ⚠️ | 31. |
| J3 | `config/` mixes unit templates with device data | ✅ | `config/patch_normalization.json` plus two tracked `pi-backup-2026-07-31/-08-02` snapshots sit beside the `.service` templates; `.gitignore`'s closing line explains the choice. |
| J4 | Repo-root clutter; two documentation trees, now three | ✅ | Root holds `ENCODER_BUTTON_REVIEW.md`, `REFERENCE_BOM.md`, `FAQ.md`, `COMMANDS.md`, four animation/splash scripts, `touch_patch_browser.py`, `patch_browser_ui.py`, and `setup-i2c-early.sh` alongside the package dirs. `docs/` (31), `Documents/specs/` (5), `Documents/reviews/` (2). |
| J5 | `docs/AUDIO-ENGINE-FOUNDATION.md` is cross-branch only; `docs/measurements/` does not exist | ✅ | `git ls-files \| grep AUDIO-ENGINE-FOUNDATION` → nothing on this branch; `git ls-tree -r --name-only yolo/looper-phase0` → `docs/AUDIO-ENGINE-FOUNDATION.md`. `docs/measurements` does not exist. Both admitted honestly in the spec at `:118-127`. |

### Retry storm

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| K1 | With no DAC, a permanent ~18s prestart/restart loop writes to the journal forever | ✅ | `jackd-prestart.sh:25` `WAIT_SECONDS=15`, loop at `:41-48`, `exit 1` at `:57-61`; `mpe-jackd.service:15` `StartLimitIntervalSec=0`, `:40-41` `Restart=always` / `RestartSec=3`. The review correctly endorses the trade and asks only for backoff. |

---

## Severity Re-Assessment

| # | Issue | Reviewer | Mine | Delta | Reasoning |
|---|---|---|---|---|---|
| 4.1 | Phase 2 spec untracked | 🔴 | **High** | agree | 869 lines of design in one working tree with no second copy. Genuinely a five-second fix — but see MISSED-5: committing it *as-is* lands a spec that is already stale against yesterday's amendment. |
| 4.2 | Hard-failure design never soaked | 🔴 | **Critical** | agree, and I'd go further | This is the finding. The spec blocks its own merge in bold; the only automated evidence is a test of a copy of the code (4.5). Everything else in this document is secondary to it. |
| 4.3 | udev `remove` blind spot | 🔴 | **High** | agree | Real, mechanically certain, silently violates a stated invariant, and fires on a routine user workflow. Not Critical only because the failure mode is ~30s of silence during calibration teardown, not data loss. |
| 4.4 | Watchdog discards user defaults | 🔴 | **High** | agree | Real user-data destruction on a routine cable fault, and worse than described (MISSED-2). Not Critical because Surge defaults are small and regenerable, and the "destroyed" file is preserved under a `.corrupted_*` name — it is misfiling, not deletion. |
| — | Mixer has no spec | 🔴 | **Low (as a repo finding)** | **inflated** | The code observations are accurate but the goal being measured against is not in the repository. This is a product conversation for Mitch, not a defect on this branch, and it should not sit in a 🔴 list beside an unsoaked failure path. Reclassify as an open product question. |
| 4.5 | Test re-implements the script | 🟡 | **High** | **deflated** | The review rates this 🟡 for docstring honesty and then, in §4.2 and the Verdict, leans on it as the *reason* criterion 2\* is unverified. It cannot be both a minor style issue and the load-bearing gap in the repo's most important failure path. It is the automated half of 4.2 and should carry 4.2's weight. |
| 4.6 | 19 mixins / 133 assignments | 🟡 | **Low** | **inflated for this project** | Accurate measurement, real long-term cost, zero current defect. On a solo appliance project with 440 green tests and a candid refactor plan that already lists the Protocol work as Deferred, this is backlog. Big-team standard applied to a one-person tree. |
| 4.7 | `getattr` layout chain | 🟡 | **Low** | agree on rating | Confirmed, and E4 shows it is provably dead code rather than a live ordering risk — which makes it a safe, satisfying 20-minute cleanup rather than a hazard. |
| 4.8 | 5 Hz SD-card poller | 🟡 | **Medium** | agree | The one finding whose cost lands on the thing the whole branch exists to protect (a 256-frame deadline). Fix is trivial (`/run/mpe/` + 0.5s) and compounds with MISSED-3. |
| 4.9 | CI weaker than local gate | 🟡 | **Medium** | agree | The missing shellcheck matters most: ~60 shell scripts carry the audio engine and the repo's own ALSA-removal commit shipped a call to a deleted function. |
| 4.10 | Teardown/jackd race | 🟡 | **Medium** | agree | Only dangerous composed with 4.3; the review says so explicitly and orders the fix correctly. |
| 4.11 | Dead per-frame import | 🟢 | **Negligible** | agree | And there are two more of the same class the review missed (MISSED-4). |
| 4.12 | `looper_clock_monitor` misnamed | 🟢 | **Low** | agree | Cheap now, expensive after `yolo/looper-phase0` lands real `looper_*` siblings. Rename before that merge. |
| 4.13 | Stale CHANGELOG count | 🟢 | **Negligible** | agree | 438 vs 440. Confirmed. |
| 4.14 | Unbounded no-DAC retry | 🟢 | **Low** | agree | Correct call on the trade; the ask is only backoff. Compounds with MISSED-3 on SD-card writes. |
| §7 | Wi-Fi password in argv | 🟢 | **Negligible** | agree | Proportionate to a physical-access threat model, and named deliberately. |

---

## What the Review Missed

Six items. Two of them are consequential.

**MISSED-1 — A second, uncoordinated Surge restarter, reachable from a button on the touch screen. (High.)**
The entire D3 cooldown design — 90s spacing, a 3-restart budget, tmpfs state that survives `BindsTo` — exists so that no supervisor can burn `StartLimitBurst=5` and leave Surge dead. `SurgeMonitor.restart_surge()` participates in none of it:

```163:171:patch_browser/surge_monitor.py
    def restart_surge(self):
        try:
            print("Attempting to restart Surge XT CLI...")
            result = subprocess.run(
                ["sudo", "systemctl", "restart", "surge-xt-cli.service"],
                capture_output=True,
                text=True,
                timeout=5,
            )
```

It is wired to a UI action in both front-ends — `touch_browser_input.py:318` (`elif hit == "surge_restart":`) and `patch_browser_ui.py:1036` — and gated only on `can_restart`, which `get_status_summary()` sets to `True` whenever Surge is not live (`surge_monitor.py:199`). Post-amendment that is precisely the state a jackd outage produces. So during a DAC problem the appliance shows the user a Restart button, and each tap consumes one of the five starts the cooldown table is budgeted against, with no record in `/run/mpe/engine-reconcile.state`. The review checked that nothing restarts Surge on a *device-changing* path and concluded the inventory was clean; it never asked who else restarts Surge for any other reason.

**MISSED-2 — 4.4 is worse than described: the `mv` happens before the cooldown decision, so it repeats. (Amplifies a confirmed 🔴.)**
In `surge-watchdog.sh:97-106` the defaults file is moved aside *unconditionally* on `is-failed`, and only then is `_supervisor_restart_surge` consulted — which may return `cooldown`, `jackd-settling`, or `failed` and do nothing. Meanwhile `start-surge-cli.sh:59-67` writes a fresh empty skeleton on every start. The cycle is therefore: unit fails → move defaults → restart → skeleton created → fails again → move the skeleton → …, producing one `.corrupted_<timestamp>` file per supervisor cycle rather than one per outage, plus an `ALERT: Surge service failed, cleaning user defaults` line on every 5s poll for the duration. The review's fix (gate on `mpe_engine_state_get state`/`reason`) still resolves it, but the finding should be written as repeated destruction, not a single misfiling.

**MISSED-3 — `$HOME/surge-watchdog.log` is unbounded, on the SD card, with no rotation anywhere in the repo. (Medium.)**
`LOG_FILE="${MPE_WATCHDOG_LOG:-$HOME/surge-watchdog.log}"` (`:12`), and `log()` (`:15-18`) writes to that file *and* stdout (→ journal, since the unit is `StandardOutput=journal` by default). A repo-wide grep for `logrotate`, `SizeMax`, or any `find -mtime` cleanup returns nothing. During the outage described in 4.4 that is roughly 12 file appends per minute forever. The review raised SD-card write pressure (4.8) and journal growth (4.14) as two separate small findings and missed the third instance, which is the one that runs on the failure path the branch is built around.

**MISSED-4 — Two more dead imports, same class as 4.11.**
`engine_state_monitor.py:6` and `looper_clock_monitor.py:6` both `import time`; neither module contains a single `time.` reference (both use `Event.wait()`). The review read both files closely enough to quote their poll intervals and their `Event`-based shutdown, and praised them as "correct little threaded pollers."

**MISSED-5 — The untracked Phase 2 spec is also *stale*, which changes the top recommendation.**
The review's P1 is "commit it today, before anything else in this document gets acted on," and it praises the document at length. But `looper-jack-client-spec.md` predates the 2026-08-13 amendment and still plans around the variable that amendment abolished: `:80` scopes the work as "remove auto-fallback and keep `MPE_AUDIO_ENGINE=alsa` as an explicit operator choice"; criterion 11 at `:100` verifies a boot "with `MPE_AUDIO_ENGINE=jack` unset (default)"; `:694` analyses the effects of retaining it. Committing it verbatim puts a stale plan on `dev` carrying the newest timestamp in `Documents/specs/` — the exact failure mode the review warns about elsewhere. Commit it (the loss risk is real), but amend §D.5 and criterion 11 in the same commit.

**MISSED-6 — Two smaller correctness gaps in code the review praised.**

- *Read-modify-write on `engine.state` with no lock.* The review credits `mpe_state_write_atomic()` for atomic whole-file installs, which is true. But `surge-watchdog.sh:44` and `:52` do `mpe_engine_state_write "$MPE_ENGINE_NAME" "$(mpe_engine_state_get active)" …` — read one field, then rewrite the whole file — while `start-jackd.sh:35-41` performs its own read-then-write of the same file. Each *write* is atomic; the *sequence* is not, so a jackd restart landing between the watchdog's read and its write silently reverts `active`. The window is milliseconds and the blast radius is a wrong HUD badge for one poll, so this is Low — but it is the shared-state mutation hazard the review's atomicity praise implies does not exist.
- *`mpe_jack_rt_priority()` validates digits but not range.* `audio-engine.sh:67-72` accepts any all-digit string, so `MPE_JACK_RT_PRIORITY=99` passes validation and then dies against `LimitRTPRIO=95` at exec time, and `MPE_JACK_RT_PRIORITY=94` would quietly place jackd above the ceiling the review says "matters" for keeping SSH and the touch UI schedulable. Every sibling validator in the file (`mpe_jack_period`, `mpe_jack_periods`, `mpe_jack_rate`) uses an explicit value allowlist. Low, one-line fix.

**Checked and clean — no finding:** authentication/authorization (none exists; single-user appliance), unvalidated external input (Wi-Fi is the only external string and it is argv-safe), exposed secrets (`.gitignore` blocks key material; `config/mpe.env` is ignored while `mpe.env.example` is tracked), circular imports (`patch_browser_ui.py` imports downward into the package only), off-by-one in the cooldown table (both implementations agree with the spec table and with each other, and the `>=`/`<` polarities are correct in all four rules).

---

## What the Review Got Right (And Why It Matters)

**4.2 — the unsoaked reversal — is correctly identified as the central risk, and the review's framing of it is better than a defect list.** The compound shape is what makes it dangerous: the amendment deleted the fallback arm, the Gate B log's four PASS rows for failure behaviour now describe code that does not exist, the replacement criterion's only automated evidence is a test of a copy of the script, and the branch is one merge away from `dev`. Each of those is defensible alone. Together they mean the appliance's behaviour on the failure a gigging musician actually cares about — the DAC dies mid-set — is currently inferred from prose. If this merges unsoaked, the first real evidence arrives on stage.

**4.3 and 4.4 are the two findings a spec review could never have produced.** Both are amendment side-effects on code that was correct the day before. 4.3 is a pre-existing udev bug that only became load-bearing once "restart the graph" started meaning "restart the only audio path"; 4.4 is a precondition change — "Surge exits non-zero" went from anomaly to routine — quietly invalidating a distant heuristic. Left alone they compound with each other and with 4.10: a calibration teardown fires a `remove`, the graph restarts, Surge races a 10s readiness wait against a ~6s jackd start, and if it loses often enough the watchdog eventually starts filing the user's defaults as corrupt. That's four confirmed findings on one workflow, and only 4.3 needs to be fixed for the chain to break.

**The `RECONCILE_BUDGET` observation (C7/C8) is the kind of thing that only comes from reading the loop rather than the functions.** Two constants that look like the same knob, one of which was tuned and one of which wasn't, inside a single-threaded loop whose outer cadence silently changes during the exact fault the tuning was for. Nothing tests it and no spec mentions it.

**The review's restraint is worth as much as its findings.** It declined to call `jackd -s`, the `Restart=always` retry loop, the bash/Python duplication, or the argv Wi-Fi password defects, and in each case named the reasoning that makes them right. It said out loud what it could not verify from a laptop. A review that flagged everything would have buried 4.2.

---

## Prioritized Action Matrix

| Priority | Issue | Verdict | Effort | Depends On |
|---|---|---|---|---|
| **P0** | Run the Gate C soak (5 scenarios, `jack-audio-engine-spec.md:452-459`) before `yolo/jack-drop-alsa-fallback` merges to `dev` (4.2) | ✅ | multi-day (Pi time) | Nothing — the scenarios are already written in order |
| **P0** | Fix the udev `remove` blind spot — move the card-identity check into `restart-audio-graph.sh` against `/proc/asound/cards`; do **not** use the `ENV{}` option as written (4.3 / B5) | ✅ | half-day | Nothing |
| **P0** | Gate the watchdog's corrupt-defaults arm on `mpe_engine_state_get state`/`reason`, and move the `mv` *after* the cooldown decision (4.4 + MISSED-2) | ✅ | quick fix | Nothing |
| **P1** | Commit `Documents/specs/looper-jack-client-spec.md` **and** amend its §D.5 / criterion 11 off the retired `MPE_AUDIO_ENGINE` in the same commit (4.1 + MISSED-5) | ✅ | quick fix | Nothing |
| **P1** | Route `SurgeMonitor.restart_surge()` through the supervisor cooldown, or disable the UI Restart action when `engine.state` is `failed` with `reason=no-server`/`no-jack-device` (MISSED-1) | ✅ | half-day | Shares the state-read helper with the P0 watchdog fix |
| **P1** | Make criterion 2\*'s failure path testable for real — extract the engine-resolution + state-publish sequence into a sourceable function in `audio-engine.sh` and test that (4.5) | ✅ | half-day | Best done before Gate C so the soak and the test check the same code |
| **P1** | Add shellcheck over `scripts/` + `config/*.service` to CI, glob the shell tests, install from `requirements.txt` (4.9) | ✅ | half-day | Nothing |
| **P2** | Move the MIDI clock state file to `/run/mpe/` and drop the poll to 0.5s (4.8) | ✅ | quick fix | Coordinate with the `midi-clock-in` daemon's writer |
| **P2** | Bound `$HOME/surge-watchdog.log` — rotate, cap, or drop the file and rely on the journal (MISSED-3) | ✅ | quick fix | Nothing |
| **P2** | Explicit `systemctl start mpe-jackd.service` + bounded readiness wait in `restore_mpe_audio_services()` (4.10) | ✅ | quick fix | 4.3 first |
| **P2** | Back off `RestartSec` or throttle logging after N consecutive prestart failures (4.14) | ✅ | quick fix | Nothing |
| **P2** | Rename `looper_clock_monitor.py` → `midi_clock_monitor.py` (4.12) | ✅ | quick fix | Land before `yolo/looper-phase0` merges |
| **P2** | Decide whether the mixer rides on the JACK graph or is a separate surface, and write it down somewhere in the repo (mixer finding, reframed) | ⚠️ | half-day (decision, not code) | Mitch — this is a product call, not an engineering one |
| **P3** | Delete the dead `getattr` chain at `touch_browser_draw.py:484-489` and `:503-505` (4.7) | ✅ | quick fix | None — `_layout()` provably runs first |
| **P3** | Delete the three dead imports: `touch_browser_draw.py:450`, `engine_state_monitor.py:6`, `looper_clock_monitor.py:6`; hoist `:478` (4.11 + MISSED-4) | ✅ | quick fix | Nothing |
| **P3** | Give `mpe_jack_rt_priority()` a bounded allowlist like its siblings (MISSED-6) | ⚠️ | quick fix | Nothing |
| **P3** | Reconcile `RECONCILE_BUDGET=15` with the 5s settle, or comment why they differ (C8) | ✅ | quick fix | Post-Gate-C, when real recovery numbers exist |
| **P3** | Update `CHANGELOG.md:71` to 440, or stop quoting exact counts (4.13) | ✅ | quick fix | Nothing |
| **P3** | Protocol for the mixin `self` surface; start extracting cohesive state (4.6) | ✅ | refactor project | Only worth starting if mypy lands |

---

## Disagreements and Judgment Calls

**1. The mixer finding does not belong in the 🔴 list.** The review's five 🔴s are four verifiable code/process defects and one product-strategy question measured against a phrase that exists nowhere in the repository. Mixing them costs the other four: a reader triaging five "stop what you're doing" items will discount all of them when one turns out to be "we should decide what the product is." Keep the observation — it is a good one, and the naming collision with the per-patch fader UI is a real trap — but file it as an open product question for Mitch, not as a branch defect.

**2. 4.5 is under-rated and 4.6 is over-rated, and swapping them would improve the document.** The review's own argument makes 4.5 the automated half of its Critical finding, then rates it 🟡 because the docstring is honest. Honesty is a reason to trust the author, not a reason to downgrade the gap. Conversely 4.6 (19 mixins) is a measurement without a symptom on a solo project whose refactor plan already lists the fix as Deferred; 🟡 imports a big-team review standard into a one-person appliance repo. The immediate consequence 4.6 cites — 4.7's `getattr` chain — turns out to be dead code that runs after `_layout()` has already assigned every rect.

**3. Do not implement 4.3's first proposed fix.** "Filter on kernel-supplied `ENV{}` properties (available on `remove`)" reads as though the card identity is sitting in the uevent. It is not — `ENV{}` on `remove` returns what udev previously *stored in its database*, and nothing in this repo stamps a card-id property at `add` time. Anyone who takes option 1 at face value will write four `ENV{}` rules that match nothing and conclude the bug is fixed, which is strictly worse than today, where at least the failure is loud. Option 2 (the check inside `restart-audio-graph.sh`) is correct, is one filter instead of four, and is testable from a laptop — which also closes D7.

**4. "Commit the spec today, before anything else" needs one amendment attached.** The urgency is right — an untracked 869-line design document is one `git clean -fd` from gone. But the review spent several paragraphs praising that document and never diffed it against the amendment it postdates by a day. Committing it unchanged publishes a stale plan with the freshest mtime in `Documents/specs/`, and the next session will read the newest-looking file. Commit and amend in one go; it adds ten minutes.

**5. I'd sequence the P0s differently than the review's backlog.** Its order is spec-commit → Gate C → udev → watchdog → mixer spec. I'd fix 4.3 and 4.4 *before* running Gate C, not after. Both are cheap, both are on the failure paths Gate C exercises, and soaking a branch you already know you are about to change means either soaking it twice or shipping a soak result that does not describe what merges. The spec commit is genuinely five seconds and can happen at any point.

**6. Where I'd defend the codebase against the review's implied standard.** The 🟢 items — the argv Wi-Fi password, the unbounded no-DAC retry, the function-local import — are all correctly rated and all correctly *reasoned*, and I want to be explicit that I checked each and agree they are not defects. On an appliance whose threat model is physical access and whose operator is its author, `sudo nmcli` with the password in argv is the proportionate choice, and `Restart=always` with no start limit is the right trade for a jackd that must recover when the DAC comes back. The review says so. That restraint is why the 🔴s are worth acting on.

---

## Bottom Line

The Opus review is trustworthy. Of 65 discrete claims I could check, 48 verified exactly, 12 were true with an overstatement or a drifted line number, 3 were wrong, and 2 could not be settled from a laptop. Every 🔴 that is a code or process claim — the untracked spec, the unsoaked reversal, the udev `remove` blind spot, the watchdog's defaults destruction — is real, and the two bugs are of a kind that only a careful post-amendment read produces. The three ❌s are all in the "praise" sections rather than the findings: the `sudo` inventory is off by a factor of three, `logs/`/`.pytest_cache/` are not committed, and the mixer finding measures the repo against a goal the repo never states. That pattern is worth noting for next time — the review audited its criticisms harder than it audited its compliments — but it does not undermine the action items. Fix 4.3 and 4.4, then run Gate C.
