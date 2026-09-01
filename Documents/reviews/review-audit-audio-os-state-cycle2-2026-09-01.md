# Review Audit — Cycle 2: Audio / OS-State Spine (Review + Applied Fixes)

**Audited review:** `Documents/reviews/grumpy-review-audio-os-state-cycle2-2026-09-01.md`
**Codebase:** MPE-Module, branch `fix/card-identity-and-audible-state` (uncommitted, cut from `dev` @ 898b160)
**Second repo:** `mpe-cli`, branch `main` (uncommitted, `commands/jack.sh`)
**Method:** every code claim re-traced to the live file with line/quote evidence; every applied fix scrutinised for correctness/completeness/new defects, including two scratch-copy revert tests and two synthetic-harness probes (install-units.sh guard, flock failure modes). Full suite run: **1858 tests, OK (skipped=3)** — matches the task brief, +11 over cycle-2's own count of 1847 (consistent with more tests landing after cycle-2 was written).

Everything in this repo is uncommitted, so there is no git boundary between "as cycle-2 reviewed it" and "now." Where the review quotes exact code, I compare that quote to the live file: a match confirms the claim was accurate when written; a mismatch means a fix landed since. I note both explicitly per item.

---

## HALF A — Auditing Cycle-2's Claims

### Claim Verification

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | P0: audibility signal never reached the player — `audio_engine.py:126` returns before reading `reason`; zero consumers of `audible=` | ✅ Confirmed (accurate as written; since fixed — see Half B §2) | Review's quoted pre-fix snippet (`if state == "ok" and active == "jack": return "Audio restored", "Audio ready", 2.0` with no `reason` check) is the exact shape a bare 4-arg `mpe_jack_state_write` call and the pre-fix `audio_engine.py` would have produced. Live file now has the `if reason == "idle-sink":` guard inserted before that return (`patch_browser/audio_engine.py:129-139`), confirming the described defect existed and was subsequently patched. The "only surface the player looks at" framing is somewhat overstated, though — see Severity Re-Assessment. |
| 2 | P0: `ExecStartPre=+...reconcile-audio-settings.sh` lacks `-`; `install-units.sh`'s guard greps `^ExecStart=` only, so a missing script thrashes `mpe-jackd` forever | ✅ Confirmed | `git show dev:scripts/install-units.sh` (pre-fix baseline) contains exactly `exec_line="$(sed -n 's/^ExecStart=//p' "$RENDER_TMP/$u.service" \| head -1)"` — verbatim match to the review's Probe G. `config/mpe-jackd.service` (current, pre this diff having landed against `dev`) confirms `Restart=always`, `RestartSec=3`, `StartLimitIntervalSec=0` are all present on the unit, so the thrash-forever mechanics are real. Both the `-` and the widened guard are now present (Half B §1). |
| 3 | Concurrent settings changes clobber the known-good marker (Probe D) | ✅ Confirmed | Matches the pre-fix design directly: `_prev_buffer`/`_prev_periods`/`_old_rate` were read straight from `mpe.env` with no serialisation and no liveness check on any prior in-flight writer — exactly the shape needed to reproduce Probe D. Now closed by two independent layers (Half B §3). |
| 4 | `mpe_pending_reconcile` logs "restored" even when `install` fails | ✅ Confirmed | Review's quote (`install -m 0644 "$tmp" "$env_file" && restored=1` / `rm -f "$tmp"` / unconditional `echo "...restored..."`) puts the echo *outside* the `&&`. Live `scripts/lib/audio-settings-pending.sh:118-125` now has the echo **inside** `if install ...; then ... else ...; fi` — fixed (Half B §4). |
| 5 | `SingleSourceOfTruthTests` bypassable (case-glob copy, wrapped grep chain); "6 lists → 1" was really 4 consolidated, 2 left (`detect-audio-device.sh`, `mpe-cli/jack.sh`) | ✅ Confirmed, and both remaining gaps have since been closed | The consolidation-table claim matches my own read of the pre-fix call sites. As of the *current* tree, `detect-audio-device.sh` gained a parallel, explicitly-synced `VIRTUAL_GREP` for the JUCE-string namespace (not literally "one list," but documented and now catches Dummy/Loopback at tier 2), and `mpe-cli/commands/jack.sh` no longer keeps its own list at all — it sources `audio-engine.sh` on the Pi and calls `mpe_card_is_virtual` directly. See Half B §5/§6. The guard itself was also rewritten with negative-control tests for exactly the two bypass shapes Probe E found. |
| 6 | `mpe_card_is_virtual`'s globs were open prefixes (`Dummy*`, `UAC2*` would swallow `DummyPlug`, `UAC2Audio`) | ✅ Confirmed | This describes the state before this predicate existed in its final anchored form (the review is reviewing the branch's own newly-introduced predicate, mid-flight). Current `scripts/lib/audio-engine.sh:501-509` anchors every token exactly, allowing only an ALSA `_[0-9]*` dedup suffix. Verified fixed and not over-corrected (Half B §5). |
| 7 | The `set -u` change is SAFE (executed both scripts in a stub sandbox) | ✅ Confirmed | Independently verified by direct read rather than re-running the sandbox: `start-surge-cli.sh` initialises `DEVICE_TIER=""` before conditionally overwriting it (no bare unset read survives), `surge-watchdog.sh`'s one bare `${MPE_MODULE_REPO}` reference is unconditionally set by `paths.sh`, and every other library reference I traced (`mpe_run_dir`, `mpe_pending_file`, etc.) is either defaulted with `${x:-}` or assigned before use. No counter-example found. |
| 8 | The two modified tests were NOT weakened | ✅ Confirmed, and the one precision-loss noted in the review's own 4.8 has since also been fixed | `test_set_surge_audio_rollback.py` now anchors on `self.src.index("# Proven: the graph came up")` rather than the ambiguous `rindex` search the review flagged — matches the review's own suggested fix verbatim (`tests/test_set_surge_audio_rollback.py:62-71`, `:95-96`). |

### Severity Re-Assessment

| # | Issue | Reviewer Rating | My Rating | Delta | Reasoning |
|---|---|---|---|---|---|
| 1 | Audibility signal not consumed by the player | 🔴 (implied P0, headline finding) | **High, not the Critical the framing implies** | ↓ (framing only, not the underlying defect) | The review's own text says "the touch HUD... the only surface the player looks at — still prints 'Audio ready'." I traced the call graph: `patch_browser/touch_browser_layout.py`'s "Engine HUD retired" comment confirms there is **no persistent status HUD at all** any more (it was removed for reading "JACK" permanently and being useless). `audio_switch_progress_message`'s "ok" branch is reachable **only** as a transient toast while a buffer/rate/profile switch is actively polling (`_poll_surge_audio_switch`/`_poll_audio_profile_switch`, gated on `self._surge_audio_switching`/`self._audio_profile_switching`). So the defect is real and worth fixing exactly as done, but it is not an always-visible "everything looks green, mid-gig" signal — it is a narrower, still-important, moment-of-settings-change signal. The fix that landed is correctly scoped to that narrower reality; see Half B §2 for what it still misses even there. |
| 2 | ExecStartPre thrash risk | 🔴 P0 | **Critical, agree** | = | `Restart=always` + `StartLimitIntervalSec=0` is a genuine forever-thrash mechanism, and AGENTS.md's own documented soak workflow (`git checkout main` after a soak) is exactly the trigger the review describes. No disagreement. |
| 3 | Concurrent settings changes | 🟡 P1-ish in the review's own numbering | **Agree** | = | Correctly scoped; the fix that landed (flock + independent liveness check in `mpe_pending_write`) is a proportionate two-layer answer. |
| 4 | mpe_pending_reconcile logs false "restored" | 🟡 | **Agree** | = | Rule -1 violation exactly as described; fixed cleanly. |
| 5 | SingleSourceOfTruthTests bypassable | 🟡 | **Agree, and worth noting the rewrite is good but not complete** | = | The two demonstrated bypasses (case-glob, wrapped-grep) are now closed with dedicated negative controls. An `awk`-based or variable-hoisted reimplementation would still slip past the rewritten guard (see Half B §7) — narrower than before, not closed. |
| 6 | mpe_card_is_virtual open prefixes | 🟡 | **Agree** | = | Correctly identified as "latent, not live" (neither of Mitch's known interfaces collides) — appropriate severity given solo-dev/one-gigging-rig context. |

---

## HALF B — Auditing the Applied Fixes

### 1. `ExecStartPre=-+` and the rewritten `install-units.sh` guard

**The unit file fix is correct.** `config/mpe-jackd.service:47`: `ExecStartPre=-+@MPE_MODULE_REPO@/scripts/reconcile-audio-settings.sh`. systemd treats leading modifier characters (`-`,`@`,`:`,`+`,`!`) as independent flags regardless of order, so `-+` and `+-` are equivalent; the `-` makes a missing/failing script non-fatal to the unit, which is exactly the property being restored.

**The guard rewrite** (`scripts/install-units.sh:203-240`) was scrutinised with a standalone reproduction of its exact `case`/`sed`/heredoc logic against nine synthetic exec lines (script in `/tmp/.../guardtest/check.sh`), plus enumeration of all 30 real `Exec*` lines across every unit in `config/*.service`:

- **Directive coverage**: the sed alternation `Exec\(Start\|StartPre\|StartPost\|Stop\|StopPost\|Reload\)=` is anchored on the trailing `=`, which disambiguates `Start` vs `StartPre` vs `StartPost` correctly (POSIX leftmost-longest match plus the anchor means only one alternative can ever match a given line) — verified this covers all six real systemd `Exec*` directives (there is no `ExecStopPre`).
- **Multiple Exec lines per unit**: confirmed working — `mpe-jackd.service` alone has three `ExecStartPre` lines; the `while IFS= read -r exec_line; do ... done <<EOF_EXEC_LINES` heredoc iterates all of them independently (verified `mkdir`/`sed` produces one line per match and the loop consumes each).
- **`@`/`:`/`+` prefixes without `-`**: correctly still checked. `surge-xt-cli.service:20`'s `ExecStartPost=+.../start-uac2-watchdog-if-needed.sh` (root-only, no failure tolerance) is still validated for existence — confirmed via the harness (`@`-only and `:`-only prefixes do not trigger the "-`-detected" skip).
- **Paths containing a hyphen** (e.g. `/opt/my-tool/run.sh`, or the repo's own `MPE-Module` in every rendered `@MPE_MODULE_REPO@` path): safe. The `-`-detection only inspects `${exec_line%%/*}` — everything **before the first slash** — and every real absolute path in this repo begins with `/` immediately after any modifiers, so path-internal hyphens (including the one in "MPE-Module" itself) never reach the check. Verified with the harness (`case 3`, `case 9`).
- **Genuine false negative found**: an `Exec*` line whose **first token is not an absolute path but contains a literal hyphen** — either a bare relative command (`some-missing-command`) or a relative command followed by an absolute-path argument (`sudo-wrapper /nonexistent/foo.sh`) — is misclassified as `-`-prefixed and **skipped entirely**, including any absolute-path arguments that follow (harness `case 7`/`case 8`, reproduced deterministically). This is because `${exec_line%%/*}` degrades to the whole line (or everything before the argument's `/`) when there is no leading absolute path, and the outer/inner hyphen check doesn't distinguish "genuine modifier prefix" from "a hyphen that happens to be there." **None of the repo's actual 30 `Exec*` lines trigger this** — every one begins with either a bare `/` or a single-character modifier stack immediately followed by `/`. This is a latent, currently-dormant correctness gap, not a live bug. The cleanest fix is to compute the "-` present" check from the **same** character-by-character modifier-stripping loop that already exists two lines below (which is provably correct), rather than a separate substring heuristic.
- **Zero regression-test coverage of the guard itself.** `tests/test_systemd_units.py::test_every_exec_path_in_the_repo_exists` checks the *same underlying fact* (do referenced scripts exist) but via an entirely independent Python re-implementation (`token.lstrip("-@:+!")` on every token, unconditionally) that never invokes `install-units.sh` as a subprocess and does not replicate its `-`-tolerance semantics. A grep across all test files found no test that executes `install-units.sh`'s missing-exec guard directly. **This means the false negative above — and any future regression in the guard's own parsing (e.g. reverting to `head -1`, or the sed pattern) — has no automated tripwire.**
- **Could it now MISS a genuinely missing path (false negative) worse than before?** For every line shape that exists in the current unit files: no — coverage strictly increased (all `ExecStart*`/`ExecStop*`/`ExecReload` lines are now checked, not just the first `ExecStart=`). The one theoretical regression (hyphenated relative-command lines) does not exist anywhere in this repo today.

**Revert test (empirical):** copied `config/`, `scripts/`, `tests/`, `patch_browser/` into a scratch dir, removed the `-` from `mpe-jackd.service`'s `ExecStartPre`, and ran `test_jackd_unit_runs_the_reconciler_as_root_before_device_selection`. Baseline: pass. Reverted: **fails** with `AssertionError: '-' not found in '+@' : reconciler must be failure-tolerant (-)...` — the test is a genuine, working regression guard for this specific fix.

### 2. `patch_browser/audio_engine.py` idle-sink branch — the fix is real but incomplete

The literal line the review cited is fixed:

```python
if reason == "idle-sink":
    return (
        "No audio output — connect a DAC",
        "Running on the idle sink — nothing is audible",
        6.0,
    )
return "Audio restored", "Audio ready", 2.0
```

But it checks **only `reason`**, not `jack.get("audible")`, even though the function already receives a `jack` dict and uses it elsewhere in the same function (`jack_started = _parse_epoch(jack.get("started"))`). The reviewer's own proposed patch was `if reason == "idle-sink" or (jack.get("audible") or "") == "no":` — the second clause was dropped in what shipped.

**Traced whether `reason` is reliably populated at that point — it is not, always:**

- `scripts/start-surge-cli.sh:148,200` — `ENGINE_REASON=""` on the success path, **never** set via `mpe_engine_sink_reason`; `mpe_engine_state_write ... "$ENGINE_REASON"` publishes `state=ok active=jack reason=""` the instant Surge comes up on the graph, virtual card or not. This line was **not touched** by this fix pass (confirmed via `git diff dev -- scripts/start-surge-cli.sh` — only the `set -u`/`DEVICE_TIER` block changed).
- `scripts/lib/audio-engine.sh:695` (`mpe_promote_surge_planned`, the "settings change validated" success path used by both `set-surge-audio.sh` and `set-audio-profile.sh`) also writes `mpe_engine_state_write "$MPE_ENGINE_NAME" jack ok "" "$looper_label"` — empty reason.
- The **only** place that ever corrects this is `surge-watchdog.sh:100`'s periodic `_reconcile_engine`, which calls `mpe_engine_state_write ... "$(mpe_engine_sink_reason)"` — but only once every `JACK_PROBE_INTERVAL_S` (default **10s**, `surge-watchdog.sh:136`), and only after the short-circuit steady-state check (`state=ok && active=jack && elapsed<10s → skip`) lets a fresh probe through.

So there is a real, traceable **up-to-10-second window after every Surge start or successful promote** in which `engine.state` reads `reason=""` on a Dummy-bound graph, and the shipped `if reason == "idle-sink":` check cannot catch it, because it never consults the already-available `audible` field as a backstop.

**A second, more consequential gap: the completion toast never goes through this function at all.** Traced the touch UI's call graph (`patch_browser/touch_browser_prefs.py`):
- `audio_switch_progress_message`'s "ok" branch fires **only** as a transient progress toast, reached solely through `_poll_surge_audio_switch`'s `except queue.Empty:` branch — i.e., **while the settings-change subprocess is still running.**
- The moment `set-surge-audio.sh` (or `apply_profile`) actually finishes, `_poll_surge_audio_switch` takes the `ok, message = self._surge_audio_result_queue.get_nowait()` branch instead and calls `_finish_surge_audio_switch(ok, message)`, which does `self._toast(message, 3.0)` — where `message` is built entirely inside `patch_browser/surge_audio.py`'s `_run_set_script` (`return True, f"{success} (~{ms:.0f} ms graph latency)"`), a code path with **zero knowledge of `reason`, `audible`, or idle-sink** and untouched by this fix pass.
- This is the toast a user is most likely to actually see (it is the definitive, always-shown-on-success completion message, distinct from the transient progress overlay), and a settings change completed on a Dummy-bound graph will show a fully confident "Buffer set to 128 (~X ms graph latency)" with no warning whatsoever — the exact "reads the same broken or fine" failure this whole two-cycle review exists to eliminate, one call-path over from the one that got fixed.

**Verdict:** the specific line cited by cycle-2 is fixed, correctly and minimally. But neither the reviewer's own full proposed fix (the `audible` OR-clause) nor the completion-toast path were addressed, so a musician can still get a confidently-wrong "it worked" message immediately after changing a setting on a silent rig.

### 3. `flock` in `set-surge-audio.sh` + the inflight refusal in `mpe_pending_write`

**Ordering verified correct.** The flock block (`scripts/set-surge-audio.sh`, new) is inserted immediately after the `is_valid_buffer`/`is_valid_periods` argument checks and **before** `mpe_source_appliance_env` / `_prev_buffer="${MPE_JACK_BUFFER:-256}"` — i.e., before the exact read Probe D exploited. Confirmed by direct diff read.

**`exec 9>` failure mode — empirically tested (not just read):**
- If the lock **file descriptor fails to open** (permission/path failure on both `/run/mpe` and `$TMPDIR` fallback), `flock -n 9` itself fails with "Bad file descriptor," so `! flock -n 9` is true and the script **refuses and exits 1** — fail-**closed**, verified with a scratch reproduction (`flocktest.sh`, Test B). The error message printed ("another audio settings change is already running") is misleading in this specific case (the real cause is a filesystem problem, not contention) but the safety property holds.
- If the **`flock` binary itself is absent**, `command -v flock` fails and the entire `if ... fi` block is skipped — the script proceeds with **no locking at all and no warning printed**, verified with a scratch reproduction (Test C: `PATH=/nonexistent` and the script sails through to "acquired lock OK"). This is a genuine fail-**open** path. On real Raspberry Pi OS this is low-probability (`flock` ships in `util-linux`, present by default), and it is significantly mitigated by the second, independent layer below — but it is a silent, undiagnosed degradation with zero log line if it ever does happen.
- **Refusal leaves the caller clean**: both refusal points (flock contention, and separately `mpe_pending_write`'s own inflight check) occur before any mutation and before the `trap _restore_env_on_death` is installed, so a refused run exits with no side effects.

**Second, independent layer holds even if flock is somehow bypassed**: `mpe_pending_write` (`scripts/lib/audio-settings-pending.sh:50-58`) unconditionally checks `mpe_pending_status` for `inflight` (pid still alive, same boot) before it will overwrite the marker, regardless of whether the caller took any lock. This means even in the (unlikely) flock-binary-absent scenario, a second concurrent `set-surge-audio.sh` invocation would still be refused by this second check — the design has genuine defense in depth, and I could not construct a scenario where both layers fail simultaneously short of removing `mpe_pending_write`'s own check.

**A gap the fix didn't cover**: `scripts/set-audio-profile.sh` mutates the *same* `/etc/mpe/mpe.env` (writing `MPE_AUDIO_PROFILE`) and calls the same `mpe_promote_surge_planned`, but has **no flock and does not call `mpe_pending_write` at all** — confirmed by direct read, no reference to either mechanism anywhere in the file. A profile-change racing a buffer/rate change is not covered by either new layer, and a profile change killed mid-flight has zero crash-safety (same failure shape as the original 2026-09-01 incident, just for a different variable).

### 4. `mpe_pending_reconcile` failure handling

`failed=1` path (`scripts/lib/audio-settings-pending.sh:94-139`): confirmed the `echo "...restored..."` now sits inside the `install ... ; then` success branch, with an `else` branch logging `FAILED to restore` instead — closes cycle-2's Rule -1 finding. On `failed=1`, the function returns 1 **and skips `mpe_pending_clear`**, so the marker is retained — verified against `tests/test_audio_settings_pending.py::test_marker_is_kept_when_nothing_could_be_restored`.

**Cannot loop forever harmfully**: the marker is only consulted once per `mpe-jackd` (re)start via `ExecStartPre`, not in a tight retry loop, so a persistently-unrestorable marker (e.g. `/etc` genuinely read-only) just means the appliance keeps booting on the untested value until the underlying filesystem problem is fixed — a "free retry" as the header comment states, bounded by however often jackd restarts, not a livelock.

**Partial restore (key 1 ok, key 2 fails)**: traced the loop — `restored` and `failed` are independent flags, both possibly set to 1 within the same pass. On any `failed=1`, the **entire** marker (all `restore:` lines, not just the failed key) is kept, so the next reconcile attempt harmlessly re-applies the already-succeeded keys (idempotent — writing the same known-good value twice is a no-op) while retrying the one that failed. This is sane, but **no test exercises the mixed case directly** — `test_a_failed_restore_is_reported_as_failed` and `test_marker_is_kept_when_nothing_could_be_restored` both fail *all* keys together (chmod 0444 on the whole env file, or a nonexistent directory), not a 2-of-3 split. Behavior is correct by code inspection; coverage of it is not.

### 5. `mpe_card_is_virtual` anchored globs — enumerated against real ALSA card ids

```bash
mpe_card_is_virtual() {
    case "${1:-}" in
        Loopback | Loopback_[0-9]* ) return 0 ;;
        Dummy | Dummy_[0-9]* ) return 0 ;;
        UAC2 | UAC2_[0-9]* | UAC2Gadget | UAC2_Gadget ) return 0 ;;
        vc4hdmi | vc4hdmi[0-9]* | vc4-hdmi | vc4-hdmi[0-9]* ) return 0 ;;
        *) return 1 ;;
    esac
}
```

- **snd-dummy** registers id `Dummy` exactly (no `id=` override in `config/modprobe.d/mpe-idle-sink.conf`, which only sets `index=8 pcm_substreams=2` — substreams live under one card, not multiple card ids) — caught by the exact `Dummy` arm.
- **snd-aloop** registers `Loopback` by default, with ALSA's kernel-side dedup convention appending `_1`, `_2`, ... on a collision (this is the actual ALSA uniquification format, matching the code's own comment) — caught by `Loopback | Loopback_[0-9]*`.
- **vc4-hdmi**: both the hyphenated (`vc4-hdmi`) and non-hyphenated, per-connector-numbered (`vc4hdmi0`, `vc4hdmi1`) forms seen across cycle-1's and cycle-2's own boundary tables are covered by the four-way `vc4hdmi | vc4hdmi[0-9]* | vc4-hdmi | vc4-hdmi[0-9]*` — belt-and-suspenders against whichever naming the kernel driver actually emits.
- **UAC2 gadget**: covers both `UAC2Gadget` (cycle-1's cited exact id) and `UAC2_Gadget` (the underscore form some `g_audio`/`f_uac2` driver builds use), plus a bare `UAC2`/`UAC2_[0-9]*` for older matching behavior.
- No real-world id for any of these four device classes was found uncaught. Cross-checked against the live `tests/test_card_identity.py::AnchoredPredicateTests`, which independently exercises `test_lookalike_real_cards_are_not_virtual` (`DummyPlug`, `LoopbackPro`, `UAC2Audio`, `UAC20`, `vc4hdmiX` must all read REAL) and `test_alsa_duplicate_id_suffixes_are_still_virtual` (`Loopback_1`, `Dummy_1`, `vc4hdmi0`, `vc4hdmi1` must all read VIRTUAL) via real subprocess calls into the actual bash function — not a re-implementation. **Not too narrow.**

### 6. `mpe-cli/commands/jack.sh` — sourcing `audio-engine.sh` over SSH

The rewrite eliminates the hand-maintained list entirely (confirmed: no more `grep -viE` card-exclusion regex in `jack.sh`) and sources `$_repo/scripts/lib/audio-engine.sh` on the Pi, calling `mpe_card_is_virtual` directly.

- **Repo path absent/moved**: guarded — `[ -z "$_repo" ] || [ ! -r "$_repo/scripts/lib/audio-engine.sh" ]` catches both "no `MPE_MODULE_REPO=` line" and "path exists in mpe.env but the file isn't there," refusing with a clear message before attempting to source anything.
- **mpe.env unreadable**: guarded by the same check (`_repo` stays empty if the `[ -r /etc/mpe/mpe.env ]` test fails).
- **Sourcing under `set -e` in the calling heredoc**: `cmd_jack_start`'s remote script has `set -e` (verified, `commands/jack.sh:164`) and calls `DAC=\$(_jack_pick_dac)` as a bare assignment. **Empirically confirmed** (`bash -c 'set -e; f(){ return 1; }; DAC=$(f); echo after'` → "after" never prints, exit 1) that a refusal inside `_jack_pick_dac` aborts the entire remote script immediately, **skipping** the script's own intended `if [ -z "\$DAC" ]; then echo "ERROR: no DAC found..."; fi` fallback. In practice this is a wash, not a regression: `_jack_pick_dac`'s own stderr message ("cannot read scripts/lib/audio-engine.sh... refusing to guess") is printed before the `return 1` and is more accurate than the generic message it preempts.
- **Asymmetry found**: `cmd_jack_caps`'s heredoc has **no `set -e`** at all (confirmed, no such line between its `<<EOF` and `_jack_pick_dac` call). The same refusal there instead leaves `DAC=""`, and the script limps forward with `CARD=""` producing degraded output (`cat /proc/asound/card/id`, missing number) rather than a clean error. Pre-existing shape, not introduced by this fix, but the two commands now diverge in how they fail.
- **`| head -1` after a `while` loop in a pipeline**: the `while read -r _idx _id; do ...; done` is not the pipeline's last stage, so it necessarily runs in a subshell. `mpe_card_is_virtual` (sourced into the parent function's scope) is correctly inherited into that subshell (functions are copy-on-fork). `break`/`echo` behave as expected; a full non-match correctly yields empty output, which callers already check via `[ -z "$DAC" ]` (in `cmd_jack_start`) — no bug found here.

### 7. New/modified tests — Rule -1 and revert verification

- No weakened assertion found among the new/modified test files. `SingleSourceOfTruthTests` was materially strengthened: it now joins backslash-continued lines and checks both a `grep`-chain shape and a `case`-glob shape, with **dedicated negative-control tests naming the exact two bypasses** Probe E demonstrated (`test_negative_control_guard_catches_a_case_glob_copy`, `test_negative_control_guard_catches_a_wrapped_grep_chain`), plus a "does not false-positive on prose" control. It also now scans the `mpe-cli` repo (`Path("/home/mitch/Documents/GitHub/mpe-cli")` is in `_sources()`'s roots), closing cycle-2's cross-repo blind spot — and correctly finds nothing to flag there now that `jack.sh`'s own list is gone.
- **Still bypassable, narrower than before**: an `awk`-based reimplementation without parentheses (e.g. `awk '/Loopback|Dummy/ {next}'`) or a list hoisted into a shell variable (`VIRTUAL_CARDS="Loopback|Dummy|UAC2"`) would both still slip past the rewritten guard's `"grep" in line or (")" in line and ("|" in line or "case" in line))` heuristic — verified by hand-tracing both shapes against the actual predicate. No test names or covers either residual shape.
- **Revert test performed** (see §1 above): removing `-` from `ExecStartPre` in a scratch copy makes `test_jackd_unit_runs_the_reconciler_as_root_before_device_selection` fail with a precise, on-topic message. This is a real, working regression test, not decoration.
- `test_commit_happens_only_after_the_graph_is_proven`'s precision loss (cycle-2's 4.8) has itself been fixed since the review (now anchors on the unique `"# Proven: the graph came up"` comment) — confirmed by direct read.

---

## Step 4 — What Both Cycles Still Missed

Both reviews, and the two fix passes that followed, worked almost entirely on **card identity** (is this id virtual?) and **crash-safety of settings writes**. The boot/lifecycle/hotplug **timing** spine — flagged by cycle-1 §5 and cycle-1's own audit, and never on either review's headline P0/P1 list — is untouched by either fix cycle:

1. **Boot-vs-udev race is completely unaddressed.** `scripts/restart-audio-graph.sh` and `config/99-usb-audio.rules` have **zero diff** against `dev` (confirmed via `git diff --stat`). A cold-boot `add` event for the DAC's PCM node can still fire before `mpe-jackd.service` finishes starting; `mpe_bound_card_id()` still returns non-zero in that window, `mpe_graph_restart_is_relevant` still says "jackd binding unresolved — restarting (fail loud)," and `systemctl restart --no-block` still races a unit systemd is concurrently starting. Card-identity being correct now doesn't touch this — it's an orthogonal timing bug.

2. **"Relevance cannot see audibility" hotplug race is unaddressed.** Bound to Dummy, a real DAC plugged in later: if `detect-audio-device.sh`'s JUCE-mediated detection is still mid-enumeration at the exact instant the udev `add` event fires, `desired` also resolves to `Dummy`, and `mpe_should_skip_graph_restart_for_card` (now correctly delegating to the anchored `mpe_card_is_virtual`) says "not relevant" — a *more precisely classified* no-op, but still a no-op. The underlying timing window is untouched.

3. **No periodic self-healing exists at all.** Traced `surge-watchdog.sh`'s main loop in full: `_reconcile_engine` only ever asks "is Surge on *a* JACK graph," never "is the JACK graph bound to the *best available* card." Once bound to Dummy, the only path back to a real DAC is a udev event for that exact card at that exact moment (subject to #2). A boot sequence where the appliance is powered on before the DAC is plugged in — an entirely ordinary real-world order of operations for a gigging musician — has no periodic fallback that would ever re-evaluate and correct the binding on its own.

4. **Multi-DAC ordering is still unowned.** `detect-audio-device.sh`'s tier-2 selector is still `grep -i "usb" | ... | head -1` over an unordered list — "whichever JUCE enumerated first" — with no `MPE_PREFERRED_DAC` anywhere in the repo (confirmed, `grep -rn MPE_PREFERRED_DAC` returns nothing). Identical to cycle-1's original finding; neither fix cycle touched it. Correctly triaged as backlog-not-urgent for a solo rig with one shipped DAC (per cycle-1's own audit), but worth naming again since this audit's brief explicitly asked about it.

5. **`detect-audio-device.sh` tier 4's exclusion (`grep -viE 'Default Audio Device|Dummy'`) still has no `Loopback` exclusion** — a narrow, low-likelihood gap in the last-resort fallback path, inherited unchanged from before either review cycle.

6. **`set-audio-profile.sh` carries the original vulnerability shape, untouched.** It mutates the same `mpe.env`, drives the same `mpe_promote_surge_planned` restart, and can be killed by the same `subprocess.run(timeout=...)` mechanism from the touch UI's `apply_profile()` — with **no flock and no crash-safe marker**. Neither review examined this sibling script closely enough to flag it; it is the same class of bug this entire two-cycle process exists to close, just one script over.

7. **The completion-toast gap (Half B §2)** is a genuinely new finding from this audit, not previously named: cycle-2 correctly found and fixed the *transient progress* toast but the *actual completion* toast (`patch_browser/surge_audio.py`'s `_run_set_script` success message) never routes through any idle-sink/audible check.

8. **`install-units.sh`'s guard has a latent false-negative and zero direct test coverage** (Half B §1) — a new finding from this audit's own scrutiny of the rewrite, not previously named by either cycle.

---

## Prioritized Action Matrix

| Priority | Issue | Verdict | Effort | Depends On |
|---|---|---|---|---|
| P1 | Close the completion-toast gap: route `_finish_surge_audio_switch`'s / `_finish_audio_profile_switch`'s success message through an idle-sink/`audible` check, and add `jack.get("audible") != "yes"` as a backstop OR-clause in `audio_switch_progress_message`'s "ok" branch (closing the ≤10s `reason=""` producer-gap window too) | ✅ Confirmed new finding | Half-day | — |
| P1 | Give `set-audio-profile.sh` the same `flock` + `mpe_pending_write`/crash-safe-marker treatment as `set-surge-audio.sh` | ✅ Confirmed new finding | Multi-day (touches a second script + its tests) | Pattern already exists in `set-surge-audio.sh` |
| P1 | Address the boot-vs-udev race and the "relevance cannot see audibility" hotplug race — e.g. a debounce-and-re-probe-after-settle on the udev path, or a periodic re-detection tick in `surge-watchdog.sh` | ✅ Confirmed, carried over from cycle-1, still fully open | Multi-day / refactor | — |
| P2 | Fix `install-units.sh`'s `-`-detection false negative (anchor it to the existing correct modifier-stripping loop) and add a subprocess-based test that actually executes the guard against a synthetic broken unit | ✅ Confirmed new finding | Half-day | — |
| P2 | `MPE_PREFERRED_DAC` / deterministic multi-DAC tier-2 ordering | ✅ Confirmed, carried over from cycle-1 | Multi-day | Backlog per cycle-1-audit's own judgment (no second DAC in the current rig) |
| P2 | Harden `SingleSourceOfTruthTests` against an `awk`-without-parens or variable-hoisted reimplementation | ⚠️ Partially True (real, narrower than before) | Half-day | — |
| P2 | Add `Loopback` to `detect-audio-device.sh` tier 4's exclusion list | ✅ Confirmed, low-likelihood | Quick fix | — |
| P3 | Log a warning (or fail closed) when `command -v flock` fails in `set-surge-audio.sh`, instead of silently proceeding unprotected | ✅ Confirmed new finding, low probability on real Raspberry Pi OS | Quick fix | — |
| P3 | Add a dedicated test for `mpe_pending_reconcile`'s mixed partial-restore case (key 1 ok, key 2 fails) | ⚠️ Partially True (behavior correct, coverage missing) | Quick fix | — |
| P3 | Update stale docs: `docs/CODE-MAP.md:512` still shows the 4-arg `mpe_jack_state_write` call; `docs/PATHS.md:55` and `Documents/specs/session-control-plane-spec.md:65` still show the old 4-field `jack.state` schema | ✅ Confirmed still open (re-verified `docs/CODE-MAP.md:512` directly) | Quick fix | — |
| P3 | Rewrite the duplicate "negative control" test (`test_negative_control_without_reconcile_the_bad_value_survives` == `test_sigkill_mid_change_leaves_the_untested_value_behind`) or delete one | ✅ Confirmed, cosmetic | Quick fix | — |

---

## Disagreements and Judgment Calls

**Disagree (mildly) with cycle-2's framing of finding 4.1 as "the only surface the player looks at."** The underlying code defect is real and correctly fixed at the cited line, but the persistent "Engine HUD" was retired specifically because it was uninformative (`touch_browser_layout.py`'s own comment), so there is no always-visible status a musician glances at mid-set. The actual exposure is two transient toasts around an active settings change — one now fixed, one (the completion toast) still not. This changes *which* fix matters most, not whether the original finding was worth fixing.

**Agree with cycle-1-audit's judgment that `MPE_PREFERRED_DAC` and the boot/udev races are correctly P1-not-P0** given the solo-dev, one-shipped-DAC context — but flag that neither has moved at all across two full review-and-fix cycles focused entirely on card identity. If a third cycle happens, the boot/hotplug timing spine is the highest-leverage place to point it, per this audit's brief.

**Disagree, mildly, with treating the `install-units.sh` guard rewrite as "done."** It is a substantial, correct improvement over the pre-fix state and closes the specific Probe G scenario cleanly — but a rewrite of this shape (restructuring a `for` loop into a `while read` fed by a heredoc, with a hand-rolled prefix-modifier heuristic) is exactly the kind of change that benefits from a subprocess-level test exercising the shell code itself, and none exists. The existing Python re-implementation tests a different, weaker property (do the files exist in this checkout) and provides no protection against a regression in the guard's own logic.

**No disagreement found with any of cycle-2's severity ratings** on the four headline items (audibility, ExecStartPre, concurrent-writes, false-restored-log) — all four were real, all four are now fixed, and none of the fixes introduced a new problem of comparable severity to what it closed. The residual gaps found in this audit (completion-toast, `set-audio-profile.sh`, the guard's false negative) are each narrower in scope than the originals they sit beside.
