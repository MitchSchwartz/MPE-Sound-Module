# JACK audio engine — permanent graph server + looper as callback client

**Issue:** untracked
**Status:** Approved, Phase 1 **written but unverified** — code on `yolo/jack-audio-engine-phase1`, not yet run on hardware. Not "Implemented": 9 of the 17 Phase 1 criteria are hardware-only and none have been executed. Gate B is the Pi soak.
**Created:** 2026-08-12
**Last updated:** 2026-08-12 11:36 (America/Toronto)

**Gate A decisions:** default engine on boot is `jack`. The Phase 1 looper
regression is **accepted knowingly** — looper off during Phase 1, refused by the
D5 guard, surfaced by a touch HUD indicator (criterion 13).

## Problem Statement

The appliance runs five processes and four buffer stages to get a note from key
to speaker: Surge → `snd-aloop` → `arecord` → pipe → Python looper → pipe →
`aplay` → DAC. That costs ~40 ms round trip and creates three independent clocks
(`snd-aloop`, the DAC crystal, Python's monotonic clock) that nothing reconciles.
Mitch's acceptance test is *"the playing experience is subpar right now."*

`docs/AUDIO-ENGINE-FOUNDATION.md` Part 8 selects **option D**: run a graph server
so Surge and the looper are clients processed in the same tick, removing
`snd-aloop`, the pipes, and the multi-clock problem together.

**Proven on the appliance 2026-08-12** (see §Evidence):

- Surge has JUCE's JACK backend compiled in — dlopens `libjack.so.0`, no rebuild.
- `jackd2` runs Surge on the graph at 256 frames × 3 periods, 24-bit, **16 ms**,
  zero xruns, auto-connected to the DAC.
- Mitch, playing it: *"it sounds the best so far."*

That was a manual bring-up outside systemd. **It does not survive a reboot.**

## Goals

1. JACK is the appliance's **default audio engine on boot**, surviving reboot.
2. The instrument never boots silent — if jackd fails, audio still works.
3. The looper becomes a JACK client so looping works in the default configuration.
4. `mpe` CLI can inspect and switch the engine without raw SSH.

## Non-Goals

- PipeWire. **This overrides `AUDIO-ENGINE-FOUNDATION.md` Part 9 decision 2
  ("D, leaning PipeWire").** Headless PipeWire needs session/lingering plumbing or
  hand-rolled system units and carries a documented socket-activation boot race,
  while `jackd` is a single daemon with a plain system unit. The *direction* — one
  clock, one graph — is unchanged. Reversible: `pipewire-jack` also provides
  `libjack.so.0`, so swapping engines never touches Surge. Part 9 has been updated
  to record the reversal.
- A compiled mix kernel. Deferred until the callback exists and the GC tail is
  measured rather than assumed (Part 8).
- Latency below 256 frames. Measured as a **hardware** ceiling, not software —
  see §Evidence.
- Replacing the APC control surface layer, which is in good shape.
- **Looping in JACK mode during Phase 1.** Explicitly out of scope and shipped as
  a known regression (D5), lifted by Phase 2.
- **Looping in `usb-host` / `usb-host-session` profiles.** Not supported today and
  not added here; Phase 2 targets `standalone` only.
- **Seamless (gapless) recovery from jackd death.** Phase 1 budgets an audible
  gap of up to 15 s (D3 `recovering`). Gapless failover is not attempted.
- **Migrating other direct-ALSA consumers onto the graph** — calibration and
  session capture keep their current device access; they are only *audited* for
  conflicts with jackd's exclusive open.

## Acceptance Criteria

### Phase 1 — permanence

| # | Criterion | Verification |
|---|-----------|--------------|
| 1 | Cold boot with no login leaves Surge a JACK client wired to the DAC | `mpe jack status` after reboot shows jackd + surge threads `SCHED_FIFO`, and `jack_lsp -c` lists `Surge XT:out_1 → system:playback_1` |
| 2a | **jackd fails at boot → sound anyway.** Inject: `sudo systemctl mask mpe-jackd.service`, reboot | Surge runs on an ALSA tier device; `mpe engine status` prints `engine=jack state=degraded reason=no-server`; journal has `ENGINE-FALLBACK` |
| 2b | **jackd dies mid-set → sound returns.** Inject: `sudo pkill -KILL jackd` while playing | Audio returns within 15 s with no manual action; `mpe engine status` reports the recovery; gap is logged |
| 2c | **Double failure → loud, not silent.** Inject: mask jackd *and* unplug the DAC | `mpe engine status` shows `state=failed`; journal names both causes. Sound is impossible here; the criterion is diagnosability |
| 2b2 | **Repeated jackd death does not wedge Surge.** Inject: `pkill -KILL jackd` five times inside one 300 s window | Surge never hits `StartLimitBurst`; `systemctl status surge-xt-cli` is not `failed`; supervisor honours the 90 s cooldown and escalates to `state=failed` rather than looping. Guards the case where the supervisor is worse than the fault |
| 2d | **Promotion after a degraded boot.** Inject: boot masked (as 2a), then `unmask` + `start` jackd | Supervisor restarts Surge onto the graph without manual action; `mpe engine status` returns to `state=ok`. This case is only reachable because D3 uses a supervisor rather than `BindsTo` |
| 3 | `MPE_AUDIO_ENGINE=alsa` reproduces today's behaviour on the defined regression set: device tier chosen, buffer size, `MPE_SURGE_RT_PRIORITY` honoured, looper route available | Run `mpe test pi` + manual play; no jackd process present |
| 4 | Engine choice survives reboot, readable via `mpe engine status` | Set, reboot, read |
| 5a | Profile switch (`standalone` ↔ `usb-host` ↔ `usb-host-session`) restarts **jackd**, and the supervisor reconciles Surge onto the new server within the 15 s budget | `mpe engine status` shows the expected `hw:N` per the D1 tier table; zero xruns after settle |
| 5b | **UAC2 host capture open/close mid-session** re-points the graph | With `MPE_AUDIO_PROFILE=usb-host`, start/stop capture on the host; jackd restarts on the new device, supervisor reconnects Surge, audio resumes |
| 6 | Surge's audio **thread** is `SCHED_FIFO` below jackd's; no `chrt` wrapper on the Surge process in JACK mode | `mpe jack status` (reads `/proc/<pid>/task/*`, not process policy) |
| 10 | **Deferred to `yolo/looper-phase0` merge.** With `MPE_LOOPER_ENABLED=1` and `MPE_AUDIO_ENGINE=jack`, every looper entry point refuses with one shared message; `mpe-looper.service` must not restart-loop. Phase 1 strips looper scripts from this branch — guard policy lives in `engine-guard.sh` + `patch_browser/audio_engine.py` (unit-tested); full criterion verifies when phase0 lands | Unit test on guard helpers now; boot test + `journalctl -u mpe-looper` when phase0 merges |
| 12 | **Default engine is `jack`** on a fresh install and after upgrade of an appliance with no `MPE_AUDIO_ENGINE` set | Fresh `/etc/mpe/mpe.env` + upgrade path; `mpe engine status` reports `jack` in both |
| 13 | The looper regression is **surfaced, not discovered** — the touch HUD reads `/run/mpe/engine.state` and shows engine/state/`looper=guarded` via `patch_browser/audio_engine.py` + `touch_browser_draw.py` | Boot with both flags; HUD displays guarded/degraded state |
| 14 | Direct-ALSA consumers still work with jackd holding the device | Run `calibrate-patch-normalization.py` and `session_capture.py` with jackd up; either they succeed, or the failure is documented and the workflow states "stop jackd first" |
| 15 | USB DAC **unplug/replug** (`99-usb-audio.rules`) restarts jackd, not just Surge, and the graph returns | Pull and reseat the DAC mid-session; audio resumes; zero xruns after settle |
| 16 | `MPE_JACK_BUFFER` / `MPE_JACK_PERIODS` drive the server independently of `MPE_SURGE_BUFFER_SIZE` (D6) | Set JACK keys only; `mpe jack status` reports the requested period; Surge key has no effect in JACK mode |
| 17 | `mpe engine status` / `mpe engine set alsa\|jack` exist and are the documented interface | CLI subcommand tests; `mpe engine set` round-trips through reboot (criterion 4) |

### Phase 2 — looper as callback client

| # | Criterion | Verification |
|---|-----------|--------------|
| 7 | Looper records and overdubs with Surge on the graph — no `snd-aloop`, no `arecord`/`aplay` | Manual play; `lsmod` shows no `snd_aloop`; no child processes |
| 8 | NumPy mixer is sample-identical to the current `audioop` path | Unit test asserting equality across the existing fixture set, both backends in-process |
| 9 | Zero allocation in the callback, and the GC tail is measured | `tracemalloc` delta == 0 across N callbacks; `gc.get_stats()` pause distribution recorded to `docs/measurements/` with p99 stated. **Threshold set after first measurement** (Open Q1) — this criterion gates on the number existing and being under one period, not on a number guessed now |
| 11 | Guard from #10 is removed and both run together | Boot with both flags; looper records |

## Phasing

**Phase 1 — permanence (small, shippable, reversible).** systemd units, engine
flag, fallback, device selection, CLI. Looper is *guarded off* in JACK mode.

**Phase 2 — looper as callback client (large, "a week+" per Part 8).** NumPy
mixer behind a swappable interface, JACK callback, retire `snd-aloop` and the
subprocess pipeline, GC discipline. Lifts the Phase 1 guard.

Phase 1 alone leaves the looper unavailable in the default engine, which is why
Phase 2 is in the same spec rather than a separate effort.

## Design Decisions

These resolve the questions `AUDIO-ENGINE-FOUNDATION.md` Part 9 lists as open.

**D1 — Device selection moves from Surge to jackd.** jackd owns the hardware, so
the existing tier logic in `scripts/detect-audio-device.sh` picks jackd's `-d hw:N`
instead of Surge's `--audio-interface`. Surge then always asks for the JACK device.
One selection policy, not two.

**D2 — systemd propagates device changes; callers are not patched individually.**
jackd binds one device at start, so anything that can change the device must
restart **jackd**, not Surge. Six call sites currently restart
`surge-xt-cli.service` directly, and `uac2-stall-watchdog.sh` does so *mid-session*
when the host opens or closes capture. Patching all six invites drift.

Ordering is expressed in the unit graph; **liveness is not** (see D3 for why
`BindsTo` is rejected):

- `surge-xt-cli.service` gains `Wants=mpe-jackd.service` and
  `After=mpe-jackd.service`. Ordering only — Surge still starts if jackd does not.
- `mpe-jackd.service` runs device selection in `ExecStartPre` via the **same**
  `detect-audio-device.sh` tier logic (D1), so it re-evaluates on every start.
- Restarting jackd therefore does *not* automatically restart Surge. The
  supervisor in D3 reconciles them, which is also what recovers the case
  `BindsTo` could never handle: Surge fell back to ALSA at boot and jackd came up
  afterwards.

Callers split by intent — a rule, backed by a verified inventory:

| Intent | Restart | Sites (all verified in repo) |
|---|---|---|
| Device may change | `mpe-jackd.service` | `set-audio-profile.sh`; `set-surge-audio.sh`; `uac2-stall-watchdog.sh`; `looper-audio-route.sh` *(when `yolo/looper-phase0` lands — not on `dev` today)*; **`config/99-usb-audio.rules`** (udev → `restart-audio-graph.sh`); **`mpe restart surge`**; **`mpe looper buffer`** |
| Device unchanged | `surge-xt-cli.service` | `surge-watchdog.sh`; `deploy-patches.sh`; `setup-pi-symlinks.sh`; `mpe-services.sh`; `patch_browser/surge_monitor.py` (touch UI restart); `mpe rt surge` (`mpe-cli/commands/rt.sh`) |

Three of these were missed in the first draft and are the reason this is a rule
plus an inventory rather than a rule alone. The udev one matters most: the
pre-gig checklist includes unplugging the DAC, and that rule fires on exactly
that event.

`set-surge-audio.sh` restarts on **any** change today, not only rate. Under JACK,
period size is a server property (D6), so it restarts jackd for both rate and
buffer changes.

`restart_surge()` in the UAC2 watchdog is renamed `restart_audio_graph()` and
restarts jackd when the engine is JACK, Surge when it is ALSA. This resolves the
Part 9 question *"what happens to the `usb-host` UAC2 gadget?"*. `usb-host-session`
is unaffected: Surge stays on the Sound Blaster and only the mic→gadget bridge
starts and stops.

**D3 — Degraded-state machine, not a single startup check.** Default-on makes this
load-bearing: a startup-only check does not cover a jackd crash mid-set. States:

| State | Entry | Behaviour |
|---|---|---|
| `jack` | jackd up, Surge connected | Normal. |
| `degraded` | `MPE_AUDIO_ENGINE=jack`, no server after `ExecStartPre` wait **or** Surge fell back to ALSA while jackd was running | `start-surge-cli.sh` stops `mpe-jackd.service` (non-blocking) before opening the tier ALSA device, logs `ENGINE-FALLBACK` with `action=stopped-jackd`, and starts Surge on ALSA. jackd stays stopped until reboot or manual start — sound with worse latency, appliance rests degraded. |
| `recovering` | jackd died while running, or came up after Surge fell back | Supervisor restarts Surge onto the correct engine. Audible gap budget: **≤ 15 s**. |
| `failed` | No server *and* no usable ALSA device | `start-surge-cli.sh` must **not** `exit 1` silently as it does today — it logs both causes and surfaces `state=failed` to `mpe engine status`. |

**`BindsTo` is rejected, and this was a real design error caught in review.**
`BindsTo=mpe-jackd.service` on Surge makes the `degraded` state unreachable: if
jackd is masked or fails, `BindsTo` (which implies `Requires`) prevents Surge
from starting at all, so the boot fallback can never run. `Wants=` + `After=`
gives ordering without coupling liveness, which is what boot fallback needs.

**Liveness is reconciled by the existing supervisor instead.**
`surge-watchdog.sh` already supervises Surge and is already `BindsTo=surge-xt-cli`.
It gains an engine reconciliation check on its existing cadence:

| Observed | Action |
|---|---|
| `engine=jack`, server up, Surge connected | none |
| `engine=jack`, server down | wait for `Restart=always` on jackd; if it does not return within the budget, restart Surge to land `degraded` |
| `engine=jack`, server up, Surge on ALSA (fell back earlier) | restart Surge to **promote** it back onto the graph |
| `engine=alsa` | ignore jackd entirely |

This covers both criterion 2a and 2b with one mechanism, and adds the promotion
case that `BindsTo` could not express.

**Cooldown — specified, because the naive version wedges the instrument.** The
watchdog polls every 5 s (`surge-watchdog.sh:40`) while Surge has `RestartSec=10`,
`StartLimitBurst=5`, `StartLimitIntervalSec=300` (`surge-xt-cli.service:20-23`). An
uncooled supervisor restarting on every poll exhausts the burst limit in ~25 s and
leaves Surge **dead until manual intervention** — worse than the fault it responds
to. Rules:

| Rule | Value | Why |
|---|---|---|
| First reconcile restart | immediate | Meets the 15 s gap budget |
| Subsequent restarts | ≥ **90 s** since the last supervisor-initiated one | ≤ 3 per 300 s window, under `StartLimitBurst=5`, leaving headroom for Surge's own `on-failure` restarts |
| jackd restarted < 15 s ago | do not restart Surge | jackd has `Restart=always`; let it settle rather than fight it |
| 3 supervisor restarts without reaching `ok` | stop; `state=failed`, log loudly | Prevents an unbounded slow loop |

**State lives in `/run/mpe/`, not memory.** The watchdog is
`BindsTo=surge-xt-cli.service` (`surge-watchdog.service:3-4`), so it is itself
restarted every time it restarts Surge — an in-memory timestamp would be wiped on
exactly the event it exists to rate-limit. `/run` is tmpfs, so it also clears on
reboot, which is the correct lifetime.

Boot ordering is explicit: `mpe-jackd.service` is `After=sound.target` and its
`ExecStartPre` waits for the selected device to exist; Surge is `After=` jackd with
a bounded readiness wait for the socket, not a `sleep`.

**D4 — No `chrt` on Surge in JACK mode.** JACK assigns the client audio thread its
own priority (measured: jackd 70, Surge 65). Wrapping the process in `chrt` fights
the server and elevates non-audio threads. `MPE_SURGE_RT_PRIORITY` is ignored when
the engine is JACK, and that is stated in the log line.

**D5 — Looper and JACK are mutually exclusive until Phase 2, enforced at one
chokepoint.** `MPE_LOOPER_ENABLED` is read in nine files, so a guard per call site
would rot. One shared helper — `scripts/lib/engine-guard.sh`, exposing
`mpe_guard_looper_engine()` — carries the message and the decision.

The first draft called `mpe-looper-service.sh` "the only path by which the looper
runs." That is **false**: `mpe restart looper`, `mpe looper restart`, and a direct
`python3 scripts/mpe-looper.py` (documented in `looper-audio-route.sh` status
output) all bypass it. The guard therefore goes at the real chokepoint —
**`main()` in `scripts/mpe-looper.py`** — which every path must cross, with the
shell helper used only for early, friendlier refusals:

| Site | Role | Exit |
|---|---|---|
| `mpe-looper.py` `main()` | Authoritative — no path bypasses it | see below |
| `looper-audio-route.sh on` | Refuses to arm the route before a reboot into a broken state | non-zero |
| `mpe looper …` / `mpe restart looper` | Fails on the laptop, before touching the appliance | non-zero |

**Exit code splits by who is watching.** `mpe-looper.service` has
`Restart=on-failure` with a 2 s delay, so a guard exiting non-zero produces a
restart storm and journal spam — the opposite of the clean refusal intended.
So: when guarded, `main()` logs `LOOPER-GUARDED` and **exits 0**, which
`Restart=on-failure` will not restart. Interactive callers exit non-zero, because
a human is reading the result. `ConditionEnvironment=` is not usable here — it
reads the manager environment, not the unit's `EnvironmentFile`.

**How `main()` knows which it is:** `mpe-looper-service.sh` exports
`MPE_LOOPER_SERVICE=1` before its `exec python3 … mpe-looper.py`. Present → exit 0;
absent → exit non-zero. Explicit and greppable, rather than inferring context.
systemd's `INVOCATION_ID` is the backstop for any future unit that reaches the
Python entry without going through the wrapper. A direct `python3 scripts/mpe-looper.py`
from a shell has neither, so it correctly refuses non-zero.

`mpe engine status` reports `looper=guarded` so the zero exit is never mistaken
for a running looper.

Message names the reason and the exit: *"looper requires MPE_AUDIO_ENGINE=alsa
until the JACK callback client ships (spec Phase 2)."*

**This is a shipped regression, not a hidden one.** Phase 1 with default-on JACK
means an appliance currently running `MPE_LOOPER_ENABLED=1` loses looping until
Phase 2. That is a product decision, taken knowingly, and must appear in release
notes and the touch HUD rather than being discovered on stage.

**D6 — Separate buffer keys.** `MPE_JACK_BUFFER` / `MPE_JACK_PERIODS` are distinct
from `MPE_SURGE_BUFFER_SIZE`, because under JACK the period is a property of the
server, not of Surge. Defaults 256 / 3.

## Falsification Analysis

**The strongest case that this is the wrong move:** we are adding a second
realtime daemon, a new boot dependency, and a shipped looper regression to an
instrument that already worked — in exchange for latency that is *already
bounded by the USB DAC*, not by the architecture. If the 16 ms keeper had come
from a HAT instead, we would have the same playing improvement with no new
software. A reasonable engineer could say: buy the HAT, keep direct ALSA.

**Why we are proceeding anyway:** the graph also fixes the multi-clock drift
(foundation Part 6, problem 2), which no DAC change addresses, and it was
measured better by the person who plays it. But if Phase 2 proves painful, the
honest fallback is **Phase 1 only + a HAT**, not pushing through.

**Assumptions that would invalidate the plan if wrong:**

| Assumption | If false | Detect by |
|---|---|---|
| Surge reconnects cleanly after jackd restarts | Every profile switch needs manual intervention; D2's whole propagation model collapses | Criterion 5b, before building Phase 2 |
| A Python callback can hold a 256-frame deadline | Phase 2 is unshippable in Python; compiled kernel moves from "later" to "required" | Criterion 9, measured not assumed |
| Supervisor reconcile completes inside 15 s, and its cooldown never trips Surge's `StartLimitBurst` | Mid-set jackd death is an unacceptable gap — or worse, the supervisor wedges Surge entirely | Criterion 2b kill test, plus repeated kills inside one 300 s window |
| jackd's exclusive device open doesn't break calibration/session capture | `calibrate-patch-normalization.py` and session record silently fail | Audit direct-ALSA consumers before Phase 1 merge |

**Pre-gig manual checklist** (this is an instrument; run before relying on it):
cold boot with no network; unplug/replug the DAC; profile switch mid-set;
`pkill jackd` while playing; boot once with the looper flag set.

## Security Considerations

- **Data flow:** Local audio only. No network surface. OSC stays on `127.0.0.1`.
- **Trust boundaries:** N/A — single-user appliance, physical access.
- **Auth model:** jackd runs as the existing appliance user, not root. `@audio`
  group already grants `rtprio 95` / `memlock unlimited` via
  `/etc/security/limits.d/audio.conf` installed by the `jackd2` package.
- **Privilege:** No new sudo paths. systemd units use `LimitRTPRIO`, matching the
  existing Surge unit; RT is *permitted*, never forced.
- **Failure modes:** jackd death is the main one — covered by D3 fallback plus
  `Restart=` on the unit. A runaway FIFO thread at priority 70 could starve the
  touch UI and SSH; priority stays a fixed constant, not user input.
- **Input validation:** All CLI arguments remain fixed enums (buffer sizes,
  engine names) so the Cursor allowlist boundary holds. No free-form interpolation
  into remote shell.
- **RLS:** N/A.

## Assumptions & Constraints

- Pi 4B, Debian 13 trixie, kernel 6.18 `PREEMPT` (not `PREEMPT_RT`).
- `jackd2` 1.9.22 and `jack-example-tools` installed (done 2026-08-12).
- Output DAC is USB **Full Speed**, sharing the bus with LUMI and APC MINI.
- Surge is a separate process regardless of the looper's language; its only
  requirement is reaching the graph server.
- Git workflow: feature work targets `dev`; `main` only after Pi soak
  (`docs/GIT-WORKFLOW.md`).

## Evidence (measured 2026-08-12, this appliance)

| Finding | Measurement | Consequence |
|---|---|---|
| Surge JACK support | Binary dlopens `libjack.so.0`, resolves 4 `jack_*` symbols; JUCE types `ALSA JACK` | No rebuild needed |
| Graph works | `Audio driver type: [JACK]`, `Surge XT:out_{1,2} → system:playback_{1,2}` | Auto-connect, no `jack_connect` |
| RT topology | jackd audio thread FIFO 70; Surge audio thread FIFO 65 | Better than blanket `chrt` 20 on the whole process |
| 512×3 24-bit | 32 ms, 0 xruns | *"sounds the best so far"* |
| 256×3 24-bit | 16 ms, 0 xruns idle; mild crackle on heavy patch + 8 notes | Current keeper |
| 128×3 | 8 ms, **0 xruns**, audible crackle | Graph kept up — artifact is downstream of JACK |
| DAC ceiling | `S16_LE` / `S24_3LE` only; USB **Full Speed** 12 Mbit/s shared with 2 MIDI devices | 24-bit is the max and already past audible relevance; sub-256 needs an I2S HAT, not software |
| Live resize hazard | `jack_bufsize` renegotiates ALSA format to 16-bit; fresh start gets 24-bit | A/B must use a fresh server start, not live resize |

## Open Questions

1. **GC tail at 1–2 periods.** Determines whether the compiled kernel is optional
   or required (Part 8). Measure after the Phase 2 callback exists.
2. **Does the looper need JACK transport,** or is its own transport sufficient once
   it shares a clock? Affects whether other clients can sync later.
3. **`snd-aloop` retirement.** Phase 2 should remove it from the Surge start path;
   confirm nothing else (UAC2 bridge, session record) depends on it first.

## Rollback

- Phase 1: set `MPE_AUDIO_ENGINE=alsa` and restart, or `mpe jack disable`. Units
  remain installed but inert.
- Manual experiment: `mpe jack stop` restores systemd Surge.
- Full: `git revert` on `dev`; the Pi runs `main` until promotion.

## Technical Notes

- `surge-watchdog.service` is `BindsTo=surge-xt-cli.service`, so it follows Surge
  automatically and needs no separate ordering against jackd.
- jackd `-s` (softmode) prevents an xrun from tearing down the graph. Appropriate
  for a live instrument; keep it in the unit.
- jackd's main thread is `SCHED_OTHER` by design — only the audio thread is RT.
  Any check that reads process-level scheduling policy will report a false
  negative; verification must read `/proc/<pid>/task/*`.
