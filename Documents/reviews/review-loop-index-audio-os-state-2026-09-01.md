# Review loop — audio / appliance "OS" state

**Scope:** system bring-up, part supervision, lifecycle + state across the Pi, DAC plug/unplug, multiple DACs.
**Trigger:** recent pushes regressed the device — audio output largely stopped.
**Branch:** `fix/card-identity-and-audible-state` (from `dev` @ 898b160). NOT committed, NOT merged.
**Date:** 2026-09-01 · **Cycles:** 3 of a 5 max · **Stopped because:** final audit reported 0 P0.

| Cycle | Review | Audit | Fixes applied |
|---|---|---|---|
| 1 | [grumpy](grumpy-review-audio-os-state-2026-09-01.md) (manager) | [audit](review-audit-audio-os-state-cycle1-2026-09-01.md) (subagent) | 4 P0 + 1 P1 + 2 P2 |
| 2 | [grumpy](grumpy-review-audio-os-state-cycle2-2026-09-01.md) (subagent) | [audit](review-audit-audio-os-state-cycle2-2026-09-01.md) (subagent, covers cycles 2+3) | 2 P0 + 5 P1 |
| 3 | — (audit-driven) | same artifact as cycle 2 | 3 P1 |

Independence: the manager was Grumpy for cycle 1 only, then became the builder and never reviewed its own
work again. Cycle 2's audit was skipped at the time and run retrospectively as cycle 3 — noted rather than
hidden, because acting on a review without an independent audit is the thing this loop exists to prevent.

## Test record

| Point | Tests | Result |
|---|---|---|
| Baseline (`dev` @ 898b160) | 1806 | OK (3 skipped) |
| After cycle 1 | 1847 | OK |
| After cycle 2 | 1858 | OK |
| After cycle 3 | 1869 | OK |

`lint-jack-only-paths`, `lint-systemd-units`, `shellcheck -S error` clean throughout.

## The bug

`snd-dummy` became the Pi 5 idle sink on 2026-08-30, loaded at `sysinit` by
`config/modules-load.d/mpe-idle-sink.conf` — long before USB enumeration finishes. Five hand-maintained
"which cards are virtual" lists existed across four files; only two learned about Dummy. One that did not
was `mpe_physical_playback_card_present`, the gate gating the bounded DAC-enumeration wait in
`jackd-prestart.sh`. So on every cold boot: gate sees Dummy -> calls it real -> `waited=0` -> detection runs
before the DAC enumerates -> tier 3 matches Dummy -> jackd binds an inaudible card. systemd green,
`mpe jack status` green, xruns 0, no sound.

## What changed

- **One card-identity predicate.** `mpe_card_is_virtual()` in `scripts/lib/audio-engine.sh`; six call sites
  across two repos now call it, including the `mpe-cli` snippet, which sources the appliance's own copy over
  SSH rather than mirroring the list. Patterns are anchored, so a real `DummyPlug` DAC is not silenced.
- **State that knows whether sound is possible.** `jack.state` gains `card=`/`tier=`/`audible=`;
  `reason=idle-sink`; a journal warning at bind; the touch HUD no longer says "Audio ready" on a
  Dummy-bound graph.
- **Crash-safe settings changes.** `/etc/mpe/mpe.env.pending` (persistent, NOT `/run` — tmpfs is wiped by
  the reboot this must survive) written before any mutation, reconciled by a `-+` `ExecStartPre` on
  `mpe-jackd`, keyed on `(boot_id, pid)`. `flock` serialises changes; `set-audio-profile.sh` — a second door
  into the same failure that no review caught — now shares the lock and gained a rollback it never had.
- **`DEVICE_TIER`**, read but never assigned since `5b4f24b`, now sourced from `/run/mpe/jack-device`;
  `set -uo pipefail` on `start-surge-cli.sh` and `surge-watchdog.sh` so the next one fails loudly.
  shellcheck could never have caught it — SC2154 structurally exempts `SCREAMING_SNAKE_CASE`.
- **`install-units.sh`'s missing-path guard** now checks every `Exec*` line, not just `ExecStart`, honouring
  the `-` prefix — and has tests that run the actual shell.

## Still open — the honest list

**Untouched across all three cycles** (`scripts/restart-audio-graph.sh`, `config/99-usb-audio.rules` have
zero diff):

1. **Boot-vs-udev race.** On cold boot the `add` event for `pcmC*D*p` can fire before `mpe-jackd` is up;
   relevance then reports "binding unresolved — restarting (fail loud)" against a unit systemd is
   concurrently starting.
2. **Relevance cannot see audibility.** Bound to Dummy with a DAC plugged in later, if detection is still
   mid-enumeration `desired` also returns Dummy, relevance says "not relevant", and it exits. **There is no
   periodic self-heal** — recovery depends entirely on a udev event landing correctly.
3. **Multi-DAC.** Tier 1 hardcodes `Sound Blaster Play! 3`; tier 2 is `head -1` of an unordered list. With
   two USB DACs attached, which one you get is whatever JUCE enumerated first. No `MPE_PREFERRED_DAC`.
4. **Composition-test harness** for the real `modules-load -> prestart -> detect -> start-jackd` sequence.
   The new tests pin the seam's logic; they do not execute the sequence. This is the thing that would have
   caught the outage in CI rather than on stage.
5. **`surge-xt-cli` in device resolution** — still forks a JUCE binary inside a udev `RUN+=`, and still
   recovers card identity by string-amputating a display name.

**Open decision for Mitch:** `state=` still has no value meaning "running but inaudible". The truth lives in
`jack.state` + `reason=idle-sink` instead, because reintroducing `degraded` would fail
`lint-jack-only-paths.sh:56` and re-overload a token deliberately retired. Whether
`ok | recovering | failed` needs a fourth member is a vocabulary decision, not a fix.

**Not verified on hardware.** Everything here is reasoned from source and exercised against synthetic card
trees and a real SIGKILL. None of it has run on the Pi.

---

## Cycle findings (carried in 2026-09-23 before the cut)

### `grumpy-review-audio-os-state-2026-09-01.md`


1. **P0 — Add `Dummy` to the virtual-card exclusion via one shared `mpe_card_is_virtual()` predicate**, not
   five greps. `mpe_physical_playback_card_present` returns true on a Pi 5 with nothing plugged in, killing the
   DAC-enumeration wait and letting jackd bind the inaudible idle sink on every cold boot. Ship the predicate
   and the composition test in one commit.
2. **P0 — Publish audibility in `jack.state`.** Add `card=`/`tier=`; a virtual bound card publishes
   `state=degraded reason=idle-sink`. Until this exists nothing can tell a working appliance from a dead one.
3. **P0 — Make the settings rollback crash-safe.** Write-ahead `/run/mpe/audio-settings.pending` reconciled by
   an `ExecStartPre` on `mpe-jackd`; SIGKILL cannot be trapped. Correct the false comment at
   `surge_audio.py:26`.
4. **P0 — Fix or delete `DEVICE_TIER` at `start-surge-cli.sh:54`**; add `set -uo pipefail` there and in
   `surge-watchdog.sh`. Check why shellcheck SC2154 did not catch it.
5. **P1 — Get `surge-xt-cli` out of device resolution.** Select from `/proc/asound/cards` +
   `/sys/class/sound/`; call Surge only to translate an already-chosen card into a JUCE index. Removes the JUCE
   fork from udev, deletes the string-amputation layer, and gives `MPE_PREFERRED_DAC` / multi-DAC ordering
   somewhere sane to live.

### `grumpy-review-audio-os-state-cycle2-2026-09-01.md`


1. **🔴 Consume the audibility signal at the HUD.** `patch_browser/audio_engine.py:126` — check
   `reason == "idle-sink"` (or `jack["audible"] != "yes"`) *before* returning "Audio ready", and
   add the test that asserts what the player sees. Without this, cycle-1 P0 #2 is not fixed.
2. **🔴 Make the new `ExecStartPre` unable to brick `mpe-jackd`.** Use
   `ExecStartPre=-+…/reconcile-audio-settings.sh`; widen `install-units.sh`'s existence guard to
   `ExecStartPre` lines; relax
   `test_jackd_unit_runs_the_reconciler_as_root_before_device_selection`, which currently pins the
   hazard.
3. **🟡 Serialise settings changes.** `flock` around `set-surge-audio.sh` and refuse to start while
   `mpe_pending_status` is `inflight` — otherwise the second change records the first's untested
   value as "known good" (reproduced) and the compounding failure the header claims to have
   solved is still live.
4. **🟡 Stop the reconciler reporting a restore it did not perform.** Move the `echo` inside
   `install … &&`; keep the marker and log a WARNING when `restored != 1` instead of clearing it.
5. **🟡 Tighten `mpe_card_is_virtual` and its guard.** Exact-match `Dummy`, `UAC2`, `UAC2Gadget`;
   keep prefixes only for `vc4hdmi*`/`vc4-hdmi*`/`Loopback*`; add boundary ids to the real-card
   test. Rewrite `SingleSourceOfTruthTests` as a definition-count + allowlisted-occurrence check —
   a `case`-glob copy and a line-wrapped grep chain both pass it today (verified), and
   `detect-audio-device.sh` tier 2 still has no `Dummy`/`Loopback` exclusion at all.

### `review-audit-audio-os-state-cycle1-2026-09-01.md`


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

### `review-audit-audio-os-state-cycle2-2026-09-01.md`


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

