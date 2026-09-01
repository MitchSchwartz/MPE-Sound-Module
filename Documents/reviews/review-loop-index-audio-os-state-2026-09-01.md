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
