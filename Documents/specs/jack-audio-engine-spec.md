# JACK audio engine — permanent graph server + looper as callback client

**Issue:** untracked
**Status:** Approved, Phase 1 on `yolo/jack-audio-engine-phase1` @ `4d93fe2`, **amended 2026-08-13** (`yolo/jack-drop-alsa-fallback`) — ALSA removed entirely as a product audio path, see §Amendment. **Gate B soak** below verifies the *pre-amendment* fallback design and is retained as history; the amendment requires a fresh Pi soak before merge (not yet run).
**Created:** 2026-08-12
**Last updated:** 2026-08-13 00:33 (America/Toronto)

**Gate A decisions:** default engine on boot is `jack`. **Amended 2026-08-13: `jack` is now the *only* engine — there is nothing else to default away from.** The Phase 1 looper
regression is **accepted knowingly** — looper off during Phase 1, refused by the
D5 guard, surfaced by a touch HUD indicator (criterion 13).

## Amendment 2026-08-13 (America/Toronto) — ALSA removed entirely as a product audio path

**This section is additive.** Sections below that it contradicts are left in
place with inline `RETIRED 2026-08-13` markers rather than rewritten or
deleted — the Gate B soak log (§Gate B soak log) is a factual record of real
Pi behaviour under the design this amendment retires, and rewriting history to
match the new design would make that log dishonest.

**Decision.** JACK is the only audio engine. There is no `MPE_AUDIO_ENGINE=alsa`
operator option, no automatic ALSA fallback, and no dual-engine state machine.
`MPE_AUDIO_ENGINE` is retired — removed from `config/mpe.env.example` and every
consumer in `scripts/` and `patch_browser/audio_engine.py`. It had exactly one
purpose (selecting JACK vs. ALSA), and that selection no longer exists.

**Why now.** Governing principle (Mitch): *"If it wasn't already in there,
would we consider adding it now? I'm certain the answer is no, so we're just
having sunk cost fallacy."* A second audio architecture that nothing exercises,
that the looper guard had to special-case (D5), that doubled the state
vocabulary (D3), and that no test suite covered as a primary path would not be
proposed from a blank slate. It also loses its last practical justification:
an audio HAT has been ordered, so the USB DAC the ALSA path served is
transitional hardware, and a USB-DAC-specific escape hatch is not long-term
product direction.

**The trade-off this reverses (Goal 2).** Phase 1 shipped *"the instrument
never boots silent"* as a hard goal, met by falling back to a direct ALSA
device when jackd would not start (D3 `degraded`). That fallback is exactly
what is being removed. Its replacement: if jackd will not start, the appliance
is **silent and says so** — `state=failed` in `/run/mpe/engine.state`, the
touch HUD, and the journal — rather than silently degrading to a
worse-latency, untested path. **"Fail loud and legible" replaces "never
silent"** as the goal for this failure mode. This is a deliberate regression in
one dimension (uptime-with-sound) traded for a large reduction in surface area
(one state machine, one engine, one code path).

**What collapses:**

- **State vocabulary:** `ok | degraded | recovering | failed` → **`ok | recovering | failed`**. `degraded` is retired outright — nothing publishes it anymore, in bash or Python. A state file left over from a pre-amendment appliance carrying `state=degraded` is not treated as a recognised status by the HUD reader (tested in `tests/test_audio_engine.py`).
- **Engine selection:** `mpe_audio_engine()`, `mpe_engine_is_jack()`, and every read of `MPE_AUDIO_ENGINE` are deleted, not deprecated. `mpe_audio_graph_unit()` no longer branches — it is unconditionally `mpe-jackd.service`.
- **`scripts/start-surge-cli.sh`:** no ALSA branch. Surge is started as a JACK client, period; on failure it publishes `state=failed` and exits non-zero instead of opening a tier ALSA device.
- **`scripts/jackd-engine-condition.sh`** (the `ExecCondition=` gate that skipped jackd under `MPE_AUDIO_ENGINE=alsa`): **deleted**, including its reference in `config/mpe-jackd.service`. jackd always attempts to start.
- **The looper guard (D5) no longer keys on engine.** `looper_guard_blocked()` in `patch_browser/audio_engine.py` dropped its `engine` parameter; the guard fires whenever `MPE_LOOPER_ENABLED=1`, full stop — there is no ALSA route left to run the looper through even as a workaround, so the condition that used to matter (which engine is active) cannot vary.
- **`mpe_release_audio_device_for_alsa()`, `mpe_jack_release_timeout()`, `MPE_JACK_RELEASE_TIMEOUT_DEFAULT`, `mpe_surge_active_engine()`, `mpe_jackd_unit_masked()`, `mpe_jackd_unit_seeking_start()`, `MPE_AUDIO_GRAPH_ACTION`:** all deleted — every one existed only to service the fallback/degraded arm.
- **`surge-watchdog.sh` `_reconcile_engine`:** simplified to on-graph → `ok`; ready but Surge off-graph → promote; not ready past budget → treat jackd as down and restart Surge (it fails loud and retries on its own). No `active=alsa` branch, no `release-alsa-for-jackd` arm.

**Settle-window rationale (bash ↔ Python parity —
`MPE_ENGINE_JACKD_SETTLE_DEFAULT` in `scripts/lib/audio-engine.sh` /
`JACKD_SETTLE_SEC` in `patch_browser/audio_engine.py`, both **15s → 5s**).**
The 15s margin existed for an ALSA-contention hazard — Surge holding the tier
device on the fallback path while jackd tried to reclaim it — that cannot
occur once ALSA is not a reachable engine at all. jackd is measured ready in
~6s on the Sound Blaster Play! 3 (§Gate B soak log, `pkill -x jackd` row,
recorded under the old design but the jackd-restart timing itself is
engine-independent). The watchdog polls every 5s (`surge-watchdog.sh`). A 5s
settle clears jackd's own startup without stacking a second full poll cycle of
needless wait on top of it. `MPE_ENGINE_COOLDOWN_DEFAULT` (90s) and
`MPE_ENGINE_MAX_RESTARTS_DEFAULT` (3) — the outer supervisor-restart budget —
are unchanged.

**`MPE_AUDIO_ENGINE` does not survive in any form.** The `engine=` key in
`/run/mpe/engine.state` stays — `mpe engine status` and the touch HUD both
parse it — but its value is now the static constant `MPE_ENGINE_NAME="jack"`
(bash) / `ENGINE_NAME = "jack"` (Python), not a read of an environment
variable. Nothing in the codebase branches on it anymore.

**Diagnostic capability considered and deferred.** "Play a tone so a silent
boot is audibly diagnosable" was considered as a replacement for the retired
fallback's incidental benefit (a jackd failure used to still produce *some*
sound). It is proposed as an `mpe` CLI subcommand in the PR description for
this branch, not implemented here — `mpe-cli` changes require Mitch's explicit
approval per `MPE-Module/AGENTS.md`, and are out of scope for this repo.

**What does not change.** D1 (device selection lives in jackd's
`ExecStartPre` via `detect-audio-device.sh`), D2 (systemd propagates device
changes to jackd, not Surge — six call sites, one inventory), D4 (no `chrt` on
Surge in JACK mode), D6 (separate buffer keys), the supervisor cooldown rules
(§D3 cooldown table), and Phase 2 (looper as JACK callback client) are all
engine-selection-independent and stand as written.

**Criteria retired or superseded (inline markers below):** 2a and 2d
(degraded-boot and promotion-from-degraded — both described a state that no
longer exists; superseded by new criterion **2\***) · 2c (the single-vs-double
failure distinction it tested collapses, since a single jackd failure is now
also `state=failed` — subsumed into **2\***) · 3 (there is no ALSA regression
path left to reproduce) · the `mpe engine set alsa\|jack` half of 17 (nothing
left to set). **Not retired:** 2b and 2b2 (mid-set jackd death and repeated
jackd death) — their described recovery already went through jackd
`Restart=always` + supervisor promotion, not ALSA fallback, so they remain
accurate and their Gate B soak evidence remains valid.

**Pi soak required before merge.** §Gate B soak log below verified the
*retired* fallback design (2a, 2d, 3, and the `degraded` half of 2c). None of
it verifies the hard-failure behaviour this amendment introduces — a fresh
soak for the new criterion **2\*** (jackd masked at boot → `state=failed`,
silent, no fallback; unmask + start → promotes without manual action) has not
been run. This branch is marked **REQUIRES PI SOAK BEFORE MERGE** for exactly
this reason.

## Problem Statement

The appliance runs five processes and four buffer stages to get a note from key
to speaker: Surge → `snd-aloop` → `arecord` → pipe → Python looper → pipe →
`aplay` → DAC. That costs ~40 ms round trip and creates three independent clocks
(`snd-aloop`, the DAC crystal, Python's monotonic clock) that nothing reconciles.
Mitch's acceptance test is *"the playing experience is subpar right now."*

`docs/AUDIO-ENGINE-FOUNDATION.md` Part 8 selects **option D**: run a graph server
so Surge and the looper are clients processed in the same tick, removing
`snd-aloop`, the pipes, and the multi-clock problem together. **Citation note
(added 2026-08-13, for honesty):** this document does not exist on `dev` or on
this branch — it lives only on the unmerged `yolo/looper-phase0` branch
(added there in commit `62845bb`, open as PR #48). Citations to it in this
spec describe content this spec's author read via `git show
yolo/looper-phase0:docs/AUDIO-ENGINE-FOUNDATION.md`, not a file present in
this repo's history on `dev`. It is not duplicated onto this branch — that
would fork a document PR #48 owns.

**Proven on the appliance 2026-08-12** (see §Evidence):

- Surge has JUCE's JACK backend compiled in — dlopens `libjack.so.0`, no rebuild.
- `jackd2` runs Surge on the graph at 256 frames × 3 periods, 24-bit, **16 ms**,
  zero xruns, auto-connected to the DAC.
- Mitch, playing it: *"it sounds the best so far."*

That was a manual bring-up outside systemd. **It does not survive a reboot.**

## Goals

1. JACK is the appliance's **only audio engine**, surviving reboot. *(Amended
   2026-08-13: was "default," now "only" — see §Amendment.)*
2. ~~The instrument never boots silent — if jackd fails, audio still works.~~
   **RETIRED 2026-08-13.** Reversed by design: if jackd will not start, the
   appliance is silent and reports `state=failed` loudly and legibly. There is
   no alternate audio route. See §Amendment for the rationale.
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
| 2a | ~~**jackd fails at boot → sound anyway.**~~ **RETIRED 2026-08-13** — no ALSA fallback exists; superseded by **2\*** below. *(Historical verification, for the record: `sudo systemctl mask mpe-jackd.service`, reboot → Surge ran on an ALSA tier device; `mpe engine status` printed `engine=jack state=degraded reason=no-server`; journal had `ENGINE-FALLBACK`.)* |
| 2\* | **jackd fails at boot → appliance is silent and says so.** *(Added 2026-08-13, replaces 2a/2c/2d.)* Inject: `sudo systemctl mask mpe-jackd.service`, reboot | No fallback audio; `mpe engine status` shows `engine=jack active=none state=failed reason=no-server`; journal + touch HUD surface the failure. Then `unmask` + `start` jackd → supervisor promotes Surge onto the graph without manual action (replaces 2d); `mpe engine status` returns to `state=ok` |
| 2b | **jackd dies mid-set → sound returns.** Inject: `sudo pkill -KILL jackd` while playing | Audio returns within the settle + cooldown budget with no manual action; `mpe engine status` reports the recovery; gap is logged. *(Not retired — recovery here is jackd `Restart=always` + supervisor promotion, not ALSA fallback; Gate B soak evidence remains valid.)* |
| 2c | ~~**Double failure → loud, not silent.** Inject: mask jackd *and* unplug the DAC~~ **RETIRED 2026-08-13** — the single-vs-double distinction it tested no longer exists: a single jackd failure is now *also* `state=failed` (see 2\*). *(Historical verification: journal named both causes; sound was impossible; the criterion was diagnosability — 2\* now covers that for every jackd failure, not only the double-failure case.)* |
| 2b2 | **Repeated jackd death does not wedge Surge.** Inject: `pkill -KILL jackd` five times inside one 300 s window | Surge never hits `StartLimitBurst`; `systemctl status surge-xt-cli` is not `failed`; supervisor honours the 90 s cooldown and escalates to `state=failed` rather than looping. Guards the case where the supervisor is worse than the fault. *(Not retired — engine-selection-independent; Gate B soak evidence remains valid.)* |
| 2d | ~~**Promotion after a degraded boot.**~~ **RETIRED 2026-08-13** — "degraded boot" no longer exists; promotion itself survives, folded into **2\*** above. |
| 3 | ~~`MPE_AUDIO_ENGINE=alsa` reproduces today's behaviour on the defined regression set: device tier chosen, buffer size, `MPE_SURGE_RT_PRIORITY` honoured, looper route available~~ **RETIRED 2026-08-13** — `MPE_AUDIO_ENGINE=alsa` does not exist; there is no regression path to reproduce. |
| 4 | Engine choice survives reboot, readable via `mpe engine status`. *(Amended 2026-08-13: there is no longer a choice to make — this now verifies that `engine=jack` reads back the same after every reboot, unconditionally.)* | Reboot, read |
| 5a | Profile switch (`standalone` ↔ `usb-host` ↔ `usb-host-session`) restarts **jackd**, and the supervisor reconciles Surge onto the new server within the 15 s budget | `mpe engine status` shows the expected `hw:N` per the D1 tier table; zero xruns after settle |
| 5b | **UAC2 host capture open/close mid-session** re-points the graph | With `MPE_AUDIO_PROFILE=usb-host`, start/stop capture on the host; jackd restarts on the new device, supervisor reconnects Surge, audio resumes |
| 6 | Surge's audio **thread** is `SCHED_FIFO` below jackd's; no `chrt` wrapper on the Surge process in JACK mode | `mpe jack status` (reads `/proc/<pid>/task/*`, not process policy) |
| 10 | **Deferred to `yolo/looper-phase0` merge.** With `MPE_LOOPER_ENABLED=1`, every looper entry point refuses with one shared message; `mpe-looper.service` must not restart-loop. *(Amended 2026-08-13: the guard no longer also checks the engine — JACK is the only engine, so the condition is `MPE_LOOPER_ENABLED=1` alone.)* Phase 1 strips looper scripts from this branch — guard policy lives in `engine-guard.sh` + `patch_browser/audio_engine.py` (unit-tested); full criterion verifies when phase0 lands | Unit test on guard helpers now; boot test + `journalctl -u mpe-looper` when phase0 merges |
| 12 | **Engine is `jack`** on a fresh install and after upgrade of an appliance that still has `MPE_AUDIO_ENGINE` set in its `/etc/mpe/mpe.env` from before this amendment. *(Amended 2026-08-13: was "default … with no `MPE_AUDIO_ENGINE` set"; now unconditional — the variable is read nowhere, so a stale `MPE_AUDIO_ENGINE=alsa` left over from a pre-amendment appliance must not do anything.)* | Fresh `/etc/mpe/mpe.env` + upgrade path (including one with a stale `MPE_AUDIO_ENGINE=alsa` line); `mpe engine status` reports `jack` in both |
| 13 | The looper regression is **surfaced, not discovered** — the touch HUD reads `/run/mpe/engine.state` and shows engine/state/`looper=guarded` via `patch_browser/audio_engine.py` + `touch_browser_draw.py` | Boot with both flags; HUD displays `looper=guarded`. *(Amended 2026-08-13: "guarded/degraded state" → "`looper=guarded`" — `degraded` is retired from `state=`; only the looper field's own `guarded` label is relevant here.)* |
| 14 | Direct-ALSA consumers still work with jackd holding the device | Run `calibrate-patch-normalization.py` and `session_capture.py` with jackd up; either they succeed, or the failure is documented and the workflow states "stop jackd first" |
| 15 | USB DAC **unplug/replug** (`99-usb-audio.rules`) restarts jackd, not just Surge, and the graph returns. *(Amended 2026-08-13: the `state=ok`-only qualifier is removed — there is no `degraded` state to special-case anymore, so a replug restarts jackd unconditionally, including from a prior `state=failed`, which is exactly the recovery path a DAC reappearing after failure needs.)* | Pull and reseat the DAC mid-session; audio resumes; zero xruns after settle — from any prior state |
| 16 | `MPE_JACK_BUFFER` / `MPE_JACK_PERIODS` drive the server independently of `MPE_SURGE_BUFFER_SIZE` (D6) | Set JACK keys only; `mpe jack status` reports the requested period; Surge key has no effect in JACK mode |
| 17 | `mpe engine status` exists and is the documented interface. ~~`mpe engine set alsa\|jack`~~ **RETIRED 2026-08-13** — nothing left to set; single-engine `mpe engine status` is unaffected. Whether the `mpe engine set` subcommand itself should be removed from `mpe-cli` is a separate decision for that repo's owner (see PR) — this spec no longer requires it | CLI subcommand test for `mpe engine status` only |

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
restarts jackd — the only engine, so no branch is needed. *(Amended 2026-08-13:
originally "restarts jackd when the engine is JACK, Surge when it is ALSA";
that branch is gone along with the ALSA path.)* This resolves the Part 9
question *"what happens to the `usb-host` UAC2 gadget?"*. `usb-host-session`
is unaffected: Surge stays on the Sound Blaster and only the mic→gadget bridge
starts and stops.

**D3 — Single-engine state machine, not a single startup check.** Load-bearing
because JACK is the only engine: a startup-only check does not cover a jackd
crash mid-set, and there is no fallback to lean on if it fails. States:
*(Amended 2026-08-13 — `degraded` retired; state table collapses from four
states to three. Original four-state table, kept for the historical record of
what the Gate B soak below actually tested:)*

<details>
<summary>RETIRED 2026-08-13 — original four-state table (degraded fallback)</summary>

| State | Entry | Behaviour |
|---|---|---|
| `jack` | jackd up, Surge connected | Normal. |
| `degraded` | `MPE_AUDIO_ENGINE=jack`, no server after `ExecStartPre` wait **or** Surge fell back to ALSA while jackd was running | `start-surge-cli.sh` stops `mpe-jackd.service` (non-blocking) before opening the tier ALSA device, logs `ENGINE-FALLBACK` with `action=stopped-jackd`, and starts Surge on ALSA. jackd stays stopped until reboot or manual start — sound with worse latency, appliance rests degraded. |
| `recovering` | jackd died while running, or came up after Surge fell back | Supervisor restarts Surge onto the correct engine. Audible gap budget: **≤ 15 s**. |
| `failed` | No server *and* no usable ALSA device | `start-surge-cli.sh` must **not** `exit 1` silently as it does today — it logs both causes and surfaces `state=failed` to `mpe engine status`. |

</details>

**Current (2026-08-13) state table:**

| State | Entry | Behaviour |
|---|---|---|
| `ok` | jackd up, Surge connected to the graph | Normal. |
| `recovering` | jackd died while running, or jackd came back up and Surge has not yet been promoted onto it | Supervisor restarts Surge once jackd is ready. Audible gap budget: **≤ 15 s** (see backlog note below — missed in soak practice under the old 15s settle; the 2026-08-13 settle reduction to 5s is expected to help but is unverified without a new soak). |
| `failed` | No JACK server after the bounded readiness wait, and the supervisor's restart budget (3 attempts, §cooldown table) is exhausted | `start-surge-cli.sh` does not `exit 1` silently — it logs the cause and surfaces `state=failed` to `mpe engine status`, the journal, and the touch HUD. **There is no lesser state to rest on**: the appliance stays silent until jackd recovers (manually or via its own `Restart=always`), at which point the supervisor promotes Surge without further action. |

**`BindsTo` is rejected — the reason changes but the conclusion doesn't.**
*(Amended 2026-08-13: the original reason was "makes `degraded` unreachable,"
which is moot now that `degraded` doesn't exist. The reason that survives:)*
`BindsTo=mpe-jackd.service` on Surge (which implies `Requires`) would prevent
`surge-xt-cli.service` from starting at all when jackd is masked or fails to
start. That is exactly wrong under the hard-failure design, because
`start-surge-cli.sh` **must run** in order to detect the absent server and
publish `state=failed` — if `BindsTo` blocked Surge from starting, the
hard-failure state would never become visible in `/run/mpe/engine.state`, the
touch HUD, or the journal, defeating the entire point of failing loud rather
than silent. `Wants=` + `After=` gives ordering (Surge starts after jackd is
given a chance) without coupling liveness (Surge still attempts to start, and
therefore still reports, even when jackd does not).

**Liveness is reconciled by the existing supervisor instead.**
`surge-watchdog.sh` already supervises Surge on its existing cadence. **It is
deliberately not `BindsTo=surge-xt-cli.service`** (amended 2026-08-13): under
the hard-failure design Surge exits non-zero when jackd is absent, and `BindsTo`
would kill the supervisor on exactly the event that requires it to stay alive
and promote Surge later. Ordering is `After=surge-xt-cli.service` only. It gains
an engine reconciliation check on its existing cadence — collapsed to the
single-engine case (amended 2026-08-13; the `engine=alsa` ignore row and
the `active=alsa` degraded-promotion row are removed, not just unreachable):

| Observed | Action |
|---|---|
| Surge already on the JACK graph | none — publish `state=ok` |
| Server not ready | wait out the reconcile budget; if it does not return, restart Surge (it will fail loud via `start-surge-cli.sh` and retry independently) |
| Server ready but Surge not on the graph | restart Surge to **promote** it onto the graph |

This covers criterion 2\* and 2b with one mechanism, and keeps the promotion
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

**State lives in `/run/mpe/`, not memory.** The watchdog restarts Surge and is
itself long-running but restartable (`Restart=always`, plus any operator or
systemd restart) — an in-memory timestamp would be wiped on exactly the events
it exists to rate-limit. `/run` is tmpfs, so it also clears on reboot, which is
the correct lifetime.

Boot ordering is explicit: `mpe-jackd.service` is `After=sound.target` and its
`ExecStartPre` waits for the selected device to exist; Surge is `After=` jackd with
a bounded readiness wait for the socket, not a `sleep`.

**D4 — No `chrt` on Surge.** JACK assigns the client audio thread its
own priority (measured: jackd 70, Surge 65). Wrapping the process in `chrt` fights
the server and elevates non-audio threads. *(Amended 2026-08-13: the `chrt`
wrapper lived in the now-deleted ALSA branch of `start-surge-cli.sh`; there is
no mode left where it would run.)* `MPE_SURGE_RT_PRIORITY` has no consumer left
in the audio-engine path — it is kept only as an `mpe.env.example` /
documentation constant for a separate CPU/RT-tuning concern, unrelated to
engine selection.

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

Message names the reason and the exit: *"looper is unavailable until the JACK
callback client ships (spec Phase 2) — there is no ALSA route to run it
through."* *(Amended 2026-08-13: the guard's decision function dropped the
`engine` parameter entirely — with one engine, `MPE_LOOPER_ENABLED=1` alone
determines the block. The original message named `MPE_AUDIO_ENGINE=alsa` as an
escape hatch; there is no longer an escape hatch to name.)*

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
| *(2026-08-13)* The ALSA fallback (D3 `degraded`) is worth maintaining as a permanent second audio path | It is dead weight instead: doubled state vocabulary, a looper guard that has to special-case it, and a path no test suite exercised as primary — the definition of sunk-cost architecture | Sunk-cost test: would we add a second, untested, special-cased audio engine from a blank slate today? Answer, applied 2026-08-13: no — see §Amendment |
| *(2026-08-13)* The USB DAC is long-term hardware, so a DAC-specific escape hatch (ALSA fallback) has ongoing value | An audio HAT has already been ordered — the USB DAC this fallback served is transitional, removing the fallback's last practical justification | Hardware roadmap (HAT ordered, per Mitch, 2026-08-13) |

**Pre-gig manual checklist** (this is an instrument; run before relying on it):
cold boot with no network; unplug/replug the DAC; profile switch mid-set;
`pkill jackd` while playing; boot once with the looper flag set.

### Gate B soak log (Pi, 2026-08-12, branch `f4d18fb`)

**This log verifies the pre-2026-08-13 fallback design (§Amendment).** Rows
2a, 2d, 3, and the `active=alsa` half of 2c tested behaviour that no longer
exists in the codebase (kept here as a historical record, not as evidence for
the current design). Rows for cold boot, `pkill -x jackd`, DAC replug, 2b2, 5a,
6, the diagnosability half of 2c, 13, and 14 tested mechanisms that are
engine-selection-independent (jackd restart, supervisor promotion, profile
switch, RT topology, HUD reading, calibration) and remain valid evidence for
the current design.

**Gate C soak — complete 2026-08-13 (Pi @ `1f35ade`+, `yolo/jack-drop-alsa-fallback`):**

All five required scenarios below were run on hardware after PR #52 (watchdog
`BindsTo` fix). Merge to `dev` is unblocked on soak evidence.

1. **2\*** — mask jackd at boot → confirm `state=failed`, silent, no fallback audio, visible in `mpe engine status` + journal + touch HUD; unmask + start → confirm promotion without manual action. **PASS**
2. **2b / 2b2 retest at the new 5s settle** — `pkill -x jackd` once, and five times inside one 300s window — confirm recovery timing with the shortened settle and that the supervisor budget/escalation behaviour (§cooldown table) is unchanged. **PASS** (~21s single; 2b2 earlier)
3. **15** — DAC unplug/replug from a prior `state=failed` (not just `state=ok`) — confirm jackd restarts unconditionally and the graph recovers. **PASS**
4. **17** — confirm `mpe engine status` reads correctly with `MPE_AUDIO_ENGINE` entirely absent from `/etc/mpe/mpe.env`, and with a stale `MPE_AUDIO_ENGINE=alsa` line left over from a pre-amendment appliance (criterion 12). **PASS**
5. **D5 looper guard** — boot with `MPE_LOOPER_ENABLED=1`, confirm the guard fires identically to before (message text changed; behaviour should not have). **PASS** (`looper=guarded`, `state=ok`, Surge on graph; `mpe looper enable|disable`)

| Test | Result | Notes |
|------|--------|-------|
| Cold boot | **PASS** | `engine=jack`, `state=ok`, 0 xruns; jackd + Surge on graph without manual fixes |
| `pkill -x jackd` | **PASS** | Full graph recovery ~**39 s** (jackd @ ~6 s; 15 s settle ×3 watchdog polls; then Surge promote) |
| DAC replug from `state=ok` | **PASS** (Mitch confirmed) | Audio restored; subjectively **very slow** — same reconcile path as jackd death |
| 2a jackd masked @ boot | **PASS** | `mpe engine mask-jackd` 16:07 EDT; cold boot ~17:06 EDT → `active=alsa state=degraded reason=no-server`; `ENGINE-FALLBACK` in surge journal. Also `mpe restart all` while masked 16:34 EDT. mpe-cli must stash unit file before `systemctl mask` (configure-pi-paths tee conflict). |
| 2d unmask + start jackd | **PASS** (retest @ `34085fc`) | Auto promotion ~**31 s** after unmask+start; no manual Surge restart. Fix: `release-alsa-for-jackd` when jackd unit seeking start. First attempt 16:37 EDT failed (pre-fix). |
| 2b2 five × kill-jackd --kill | **PASS** | 2026-08-12 ~20:00 EDT; all 5 cycles recovered `state=ok` ~30 s each; 447 s total window; no `surge-xt-cli` failed |
| 5a profile switch | **PASS** | `standalone` → `usb-host` ~30 s → `standalone` ~32 s; jackd restarted each time; `state=ok` + Surge on graph |
| 3 `MPE_AUDIO_ENGINE=alsa` | **PASS** (functional) | `mpe engine set alsa` + restart → `active=alsa`, no jackd; restored to jack. `mpe test pi audio`: 12 failures (touch buffer label / env drift — pre-existing on branch, not jack-specific) |
| 17 `mpe engine status/set` | **PASS** | Shipped in mpe-cli `85dad3c` |
| 6 SCHED_FIFO | **PASS** (spot check) | jackd audio thread `SCHED_FIFO` (pid probe); Surge audio thread via JACK client (not process-level `chrt`) |
| 2c mask + unplug DAC | **PASS** (Mitch) | Diagnosability confirmed on Pi; recovery still **~55–60 s** (same promote path: 15 s jackd settle ×3 + Surge restart). Watchdog 01:20–01:22 UTC log matches. |
| 5b UAC2 host capture | **BLOCKED** | Needs physical rewire before host capture open/close test on `usb-host` profile |
| 13 touch HUD | **PASS** | Steady **JACK** badge; with `MPE_LOOPER_ENABLED=1` boot (D5) → `looper=guarded` in engine.state |
| 14 calibration/session | **PASS** (cal) / **BLOCKED** (session) | `mpe engine calibrate-smoke` @ `137463b`: `--force` 1 favorite (`70s Fizzy String`) with jackd up; loopback `plughw:7,1,0`; entry written; post-cal `engine=jack state=ok`, jackd+Surge+touch active. Fixed `list_missing` stem→dict bug in cal script. `session_capture.py` still blocked (same rewire as 5b). Cal temporarily stops Surge (not jackd) — services restore via teardown |

**Gate C soak — 2026-08-13 (Pi @ `fdeb1fa`, `yolo/jack-drop-alsa-fallback`):**

| Test | Result | Notes |
|------|--------|-------|
| 2\* failure half (mask + surge restart) | **PASS** | `state=failed`, `active=none`, `reason=no-server`, silent, watchdog active (no BindsTo) |
| 2\* promotion (unmask + start) | **PASS** | Fixed by PR #52 (`BindsTo` → `After=`). jackd up ~1s, watchdog promotes Surge; `state=ok` + Surge on graph within ~5s, stable through 60s |
| 2\* boot (mask → reboot → unmask) | **PASS** | Boot with jackd masked → `state=failed`, `reason=no-server`; unmask + start → auto-promote ~5s, stable 60s |
| 2b single kill @ 5s settle | **PASS** | Recovery **~21s** (vs ~39s at 15s settle) |
| 2b2 five × kill @ 5s settle | **PASS** (earlier session) | 5/5 in ~121s, ~4s each |
| 15 DAC replug from `state=failed` | **PASS** | jackd restarted on replug; watchdog promoted Surge from `recovering` → `state=ok` in ~5s, stable 40s |
| 17 stale `MPE_AUDIO_ENGINE=alsa` line | **PASS** | Stale line appended to `/etc/mpe/mpe.env`; `mpe engine status` still `engine=jack state=ok`; Surge restart unchanged |
| D5 looper guard boot | **PASS** | `mpe looper enable` + reboot → `looper=guarded`, `state=ok`, Surge on graph |

**Backlog — faster crash/replug recovery (post–Phase 1 merge):** The 15 s
`MPE_ENGINE_JACKD_SETTLE_S` window plus a full Surge restart makes mid-set recovery
feel unacceptable (~20–45 s). Criterion 2b budgeted 15 s total — **missed in practice**.
Candidates to evaluate later (not Phase 1 scope): shorten settle when Surge was
already on JACK and only the server PID changed; in-process JACK reconnect instead
of `systemctl restart surge-xt-cli`; parallel jackd bring-up + Surge watchdog poll.
*(2026-08-13: the settle window itself was shortened 15s → 5s as part of the ALSA
removal — see §Amendment — because the ALSA-contention hazard the 15s margin
guarded against no longer exists. That is expected to help this backlog item but
is not the same fix as the candidates above, and is unverified without the Gate C
soak.)*

**Backlog — post–graph-change patch reload gap (2026-08-13 Pi soak, Mitch):**
Planned promote now completes in ~5s (PRs #56–#59). The touch UI then re-sends the
last patch over OSC (`patch_browser/touch_browser_patches.py` `_retry_pending_load`).
Patch and on-screen state end correct and the **"Patch loaded"** toast fires, but
operators report a **second audible dropout** during that reload window — distinct
from the graph-promote silence and not caused by the toast itself. Acceptable for
current soak; candidates later: defer OSC reload until Surge output is stable,
avoid redundant reload when the patch path is unchanged, or hold/sustain through
Surge restart if the engine supports it.

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

- ~~Phase 1: set `MPE_AUDIO_ENGINE=alsa` and restart~~ **RETIRED 2026-08-13** —
  `MPE_AUDIO_ENGINE` no longer exists; there is no engine to switch to. `mpe jack
  disable` is unaffected by this amendment.
- Manual experiment: `mpe jack stop` restores systemd Surge.
- **This amendment (2026-08-13):** `git revert` the ALSA-removal commit(s) on
  `yolo/jack-drop-alsa-fallback` before merge, or revert on `dev` after merge —
  restores `MPE_AUDIO_ENGINE=alsa` as a rollback path until the revert itself
  is reverted.
- Full: `git revert` on `dev`; the Pi runs `main` until promotion.

## Technical Notes

- `surge-watchdog.service` is `After=surge-xt-cli.service` (not `BindsTo`), so
  it starts after Surge but survives a Surge hard-failure and can promote once
  jackd recovers.
- jackd `-s` (softmode) prevents an xrun from tearing down the graph. Appropriate
  for a live instrument; keep it in the unit.
- jackd's main thread is `SCHED_OTHER` by design — only the audio thread is RT.
  Any check that reads process-level scheduling policy will report a false
  negative; verification must read `/proc/<pid>/task/*`.
