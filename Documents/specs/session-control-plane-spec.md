# Session control plane — one owner per fact, reconciliation over sequences

**Status:** Draft — Phases 0–2 (Pi verification pending)
**Author:** written after the 2026-08-17 stability session; every claim in Evidence is measured on `raspberrypi2`, not reasoned.

---

## Document map

| Doc | Role |
|---|---|
| [`docs/CODE-MAP.md`](../../docs/CODE-MAP.md) | Boot order, systemd units, runtime state inventory, call graph — **source of truth for what exists today** |
| [`Documents/DIRECTION.md`](../DIRECTION.md) | Phase 2 looper eval verdict (B7/B8 adopt/kill gate) |
| [`Documents/specs/jack-audio-engine-spec.md`](jack-audio-engine-spec.md) | JACK/Surge engine lifecycle, reconcile cooldown, promote semantics |

---

## Current runtime inventory

Every row is a **fact holder** the control plane must either own, aggregate, or
retire. Staleness rules describe when a reader must treat a value as unknown — not
when the writer last touched disk.

| Path | Writers | Readers | Staleness rule |
|---|---|---|---|
| `/run/mpe/engine.state` | `start-jackd.sh`, `start-surge-cli.sh`, `surge-watchdog.sh`, `audio-engine.sh` | `patch_browser/audio_engine.py`, `mpe-cli`, Phase 1 snapshot | No reader-side TTL today; snapshot marks **stale** if `updated` age > 1.5 s (D6) |
| `/run/mpe/jack.state` | `start-jackd.sh` | snapshot, promote/reconcile probes | Same as engine aggregate (D6) |
| `/run/mpe/surge.state` | `start-surge-cli.sh` | snapshot, watchdog promote probe | Same as engine aggregate (D6) |
| `/run/mpe/engine-reconcile.state` | `surge-watchdog.sh` | `surge-watchdog.sh`, snapshot | Informational; no independent TTL |
| `/run/mpe/jack-device` | `jackd-prestart.sh` | `start-jackd.sh`, detection scripts, snapshot | Valid for current boot; cleared on jackd restart |
| `/run/mpe/planned-promote` | `mpe_promote_surge_planned()` | `surge-watchdog.sh`, promote callers | Ephemeral intent flag; absent = no promote in flight |
| `~/.mpe_sl_hud_state.json` | `sl-hud-monitor.py` (`HudWriter`, ~2 Hz) | `patch_browser/sl_hud_state.py` → touch looper bar | **2 s** default; **5 s** when `source` is `jack_transport` or `sl_internal` (`MPE_SL_HUD_*_STALE_S`) |
| `~/.mpe_sl_watchdog.json` | `sl-watchdog.py` (alarm on wedge / xrun rate) | none in production yet (artifact for operators / future snapshot) | Alarm file; treat absent as healthy, present as **alarm active** until cleared |
| In-process `GridState` (`scripts/sooperlooper/sl_grid_state.py`) | `sooperlooper-apc-bench.py` (main + OSC handler threads) | `apc_footswitch.py`, bench transport/grid paths | **No TTL** — in-process only; desync is the failure mode (D2) |

Source: [`docs/CODE-MAP.md`](../../docs/CODE-MAP.md) §2.3, §4.3.

---

## Problem Statement

The appliance is a **distributed system** — **14 enabled systemd units** (see
Appendix A), shared
mutable state, partial failures, no consensus — implemented as **scripted
sequences**, each written as though it were the only actor.

A sequence encodes *"do A, then B, then C."* It assumes a known starting state and
that nothing else moves while it runs. On this appliance both assumptions are false
continuously: jackd restarts under its clients, USB devices come and go, the engine
dies, and three different entry points may each decide to start the same process.

Every fault in the 2026-08-17 session was a distributed-systems fault presenting as a
local-script fault. None were bugs in the ordinary sense; they were the absence of
three concepts.

**1. No owner of lifecycle.** "Who starts SooperLooper?" had three answers:
`restart-sooperlooper.sh`, `mpe-sooperlooper.service`, and `mpe looper sl-restart`.
Three defer-guards were added in one evening (`98e87ff`, mpe-cli `150c969`, and the
unit itself) so the answer would be "systemd". Three patches for one missing concept.

**2. State is copied, not derived.** "Is there a grid?" exists in four places:
`GridState` in the APC bench process, per-loop `quantize`/`sync`/`mute_quantized` in
the engine, `sync_source` used as a restart *sentinel*, and tempo cached in the HUD
writer. `MPE_JACK_BUFFER` vs `MPE_SURGE_BUFFER_SIZE` was the same disease in config
(`0dc9e5b`). The sentinel is the tell: a process polling to detect that reality moved
underneath it is doing archaeology, not coordination.

**3. Change propagates by polling and inference, not by events.** SooperLooper's
`register_auto_update` delivers on *change*; a subscriber that starts after the change
never learns it and fails silently forever (`582422d`). The APC bench re-registered
OSC into a dead engine for six hours. `sl-watchdog` polls at 5 s. Nothing publishes
"this happened"; everything infers "something must have happened."

And one fault that is not structural but is the most dangerous:

**4. The realtime boundary is not enforced by construction.** A pygame process
registered a JACK process callback (`5143e8b`), in a process with
`Max realtime priority: 0`, whose main thread held a full core. jackd (`SCHED_FIFO`
70) then blocked every period on a `SCHED_OTHER` Python thread waiting for the GIL.
Nothing prevented this, and observability would not have caught it — the system was
working exactly as written.

---

## Evidence (measured 2026-08-17, this appliance)

| Observation | Value | Source |
|---|---|---|
| Orphaned JACK clients from a leaked `timeout N jack_cpu_load` | **705**, oldest 45.7 h, each holding 4 jackd FDs + 13 shm mappings | `pgrep`, `/proc/<pid>/fd`, `ss -ulnp` |
| Effect on the server | `jack_lsp` → `BDB2034 unable to allocate memory for mutex` | client registry saturated |
| Looper engine downtime, unsupervised, unnoticed | **6 h** (died 16:15, found 22:0x) | `journalctl`, `midi-clock-in` last port sighting |
| Touch UI CPU, idle, before | **101 jiffies/s** (one full core) | `/proc/<pid>/stat` |
| `newfstatat` calls, 5 s sample | **95,235** (~3,800/frame) | `strace -c` |
| Same, after fix | **198** | `strace -c` |
| xruns under overload at 128 frames, strict mode | **1060**, culprit named | `JackEngine::XRun: client = mpe-peak-meter was not finished` |
| xruns reported by the bench in softmode | **0**, regardless of truth | `-s` suppresses the message (`d5ac0cc`) |

The last row is the important one: the tool written that day to prevent unverifiable
numbers was itself producing one. Any design that does not make state observable will
reproduce this.

**Two findings from the gap review of this document (2026-08-17), recorded because
they change the design rather than merely annotating it:**

| Finding | Detail |
|---|---|
| **Calibration now conflicts with the looper units** | `calibration_teardown.stop_mpe_audio_services()` stops `touch-patch-browser`, `mpe-pressure-remap`, `surge-poly-governor`, `surge-xt-cli` — it does **not** stop `mpe-sooperlooper` or `mpe-apc-bench`, which gained `Restart=always` on 2026-08-17 (`4ecd84a`). Calibration therefore now runs with the looper still on the graph, and any attempt to stop it by hand is undone by systemd within 5 s. This is a live regression introduced by supervision, and it is why D11 exists. |
| **Phase 1 as first drafted would have made the problem worse** | `mpe status`, `mpe engine status`, `mpe jack status` and `mpe diagnose` already aggregate fragments of the proposed snapshot. Adding `mpe state` alongside them yields a **fifth** partial view. Phase 1 is consolidation, not addition — see D12. |

---

## Goals

1. **One owner per fact.** Every piece of session state has exactly one writer; all
   other holders are derived views.
2. **Reconciliation over sequencing.** Operations declare desired state and converge
   from *any* current state, idempotently.
3. **A single observable truth.** One command answers "what is this instrument doing
   right now" without correlating five sources.
4. **A structured event stream.** Discrete facts (`engine.started`, `grid.dropped`,
   `client.registered`) rather than free-text logs across N units.
5. **The realtime boundary enforced mechanically**, not by convention.
6. **Audio survives the control plane.** Losing coordination must never lose sound.

## Non-Goals

- A rewrite. The instrument works; this is incremental and reversible at each phase.
- Replacing systemd, JACK, OSC, or SooperLooper.
- Multi-instrument or networked operation. Named as a *future* justification for
  Phase 3+, not a requirement now (see Falsification).
- A new IPC protocol before Phase 3. `/run/mpe` files already work and are already
  atomic.
- Changing musical behaviour. No phase may alter what the instrument sounds like or
  how a pad responds.

---

## Design Decisions

### D1 — Three planes, with a hard rule at the realtime boundary

| Plane | Members | Rule |
|---|---|---|
| **Realtime** | jackd, `surge-xt-cli`, `sooperlooper` | Compiled only. **Nothing else may hold a JACK process callback.** |
| **Control** | session owner (Phase 3+), `sl-watchdog`, `surge-watchdog` | Owns state, reconciles, never in the audio cycle. |
| **Edge** | touch UI, APC bench, HUD writer, `mpe-cli` | Stateless. Emits intents, renders snapshots. |

This is kernel space vs user space, and the 2026-08-17 crackle is what putting an
interpreter in kernel space costs. Enforcement is a test that fails on
`set_process_callback` outside an allowlist — a convention nobody can accidentally
violate — plus `LimitRTPRIO` on units that legitimately need RT.

**The OUT meter is the live exception** and must be resolved: it is a Python JACK
client, currently allowlisted with `MPE_PEAK_METER` default off. Under overload at 128
frames it is the *first* client in the graph to miss its deadline (Evidence). It moves
out-of-process or becomes compiled before the allowlist is removed.

### D2 — One owner per fact; edges hold no authoritative state

The APC bench stops being a state machine that drives hardware and becomes a *view* of
session state plus a source of button events. `GridState` moves to the owner. The HUD
writer stops caching tempo and reads it.

This is what retires the `sync_source` sentinel: a view cannot desynchronise from the
truth, because it has no copy to desynchronise.

### D3 — Reconciliation, not sequencing

Not *"restart jackd, wait, promote Surge, rewire the looper."* Instead: desired state
is `{period: 256, surge: on-graph, looper: wired}`, and a loop makes it true from
whatever exists now.

The instinct is already in the codebase, applied to one narrow case:
`mpe_engine_reconcile_decision` is a pure function mapping observed state to
`restart | jackd-settling | cooldown | failed`. Generalise that shape; most of the
shell sequences dissolve into it.

### D4 — The realtime plane must not depend on the control plane

If the session owner dies mid-set, jackd/Surge/SooperLooper keep playing. Lost: HUD,
pad LEDs, coordination. Not lost: audio.

This is the existing principle — *"a supervisor that dies with the thing it supervises
is not a supervisor"* (`sl-watchdog.service`) — extended one level up. **A design that
cannot guarantee this is the wrong design and must be rejected at the Phase 3 gate.**

### D5a — systemd owns processes

Dependency and restart semantics live in units, not re-decided at each call site. Every
other entry point *defers* to the unit when installed. The three defer-guards added on
2026-08-17 are the compatibility shim for installs without units, not the design.

### D5b — reconciler declares desired state

Phase 4 adds a reconcile loop that **invokes `systemctl` to match declared desired
state** — it does not fight `Restart=` by spawning parallel start paths. systemd
remains the process supervisor (D5a); the reconciler is the **intent layer** that
decides *what* should be running and lets units converge. While maintenance mode is
set (D11), the reconciler publishes but does not invoke corrective `systemctl` actions.

### D6 — Snapshot in `/run/mpe`, atomically written, versioned two ways

Reuse `mpe_state_write_atomic`. The snapshot carries **two** version fields, which are
not the same thing and were conflated in the first draft:

| Field | Purpose |
|---|---|
| `seq` | Monotonic counter, bumped every publish. An unchanged `seq` for longer than `2 × publish_interval` means the writer is dead — the failure the HUD had for 47 s with nothing logged anywhere. |
| `schema` | Integer, bumped on any field rename or removal. Readers refuse to parse an unknown major and say so loudly rather than silently reading `None`. |

`schema` is load-bearing because readers live in **another repository** (`mpe-cli`) and
another process (touch UI). Without it, a field rename in this repo breaks the CLI at a
distance, with no error at the point of change.

**Publish interval is fixed at 500 ms** (matching `sl_hud_monitor.WRITE_INTERVAL_S`),
and **staleness threshold at 1.5 s**. These are stated here rather than deferred to Q2
because criterion 4 cannot be satisfied against an undecided interval — the first draft
made staleness depend on an open question and the open question depend on staleness.
Q2 is now about whether a *faster lane* is needed for transport, not about this value.

### D7 — Intents are the only write path from edges

Edges never mutate engine state directly. They submit intents; the owner validates,
applies, reconciles, and publishes. This is what makes "who did that?" answerable.

### D8 — Observability before ownership

Phases 1–2 change no behaviour and move no state. They exist so Phases 3–5 are
diagnosable while in flight. Reversing this order means debugging a new control plane
with the same instruments that failed on 2026-08-17.

### D9 — Every operation idempotent

`wire-jack-graph.sh connect` already is. Generalise: re-running any operation from any
state must be safe, so the reconciler can simply re-run everything rather than track
what it has done.

### D10 — No secrets, no network surface

The snapshot is local, world-readable, and contains no credentials. Intents arrive over
a Unix socket with filesystem permissions, not a TCP port. OSC ports stay bound to
`127.0.0.1` as today.

---

### D11 — Maintenance mode: the reconciler must be suppressible

A reconciler whose job is "make desired state true" will fight any operator who
deliberately stops something. This is not hypothetical: calibration
(`calibration_teardown.py`) stops the audio stack on purpose, and as of 2026-08-17 the
looper units restart themselves within 5 s regardless (see Evidence).

**A maintenance mode must exist before Phase 4 ships**, or the reconciler breaks
calibration and `session_capture.py` on the day it lands.

- A flag file in `/run/mpe` (`maintenance`), consistent with existing runtime state.
- While set: the reconciler observes and publishes but takes **no corrective action**,
  and `mpe state` reports `mode: maintenance` prominently — a suppressed reconciler
  that looks identical to a healthy one is the measurement-integrity failure again.
- It must be **self-clearing on a deadline** (default 30 min). A maintenance flag left
  set by a crashed calibration run is an unsupervised appliance that looks supervised.
- `stop_mpe_audio_services()` sets it; `restore_mpe_audio_services()` clears it.

### D12 — Phase 1 consolidates the existing CLI surface; it does not add to it

`mpe status`, `mpe engine status`, `mpe jack status` and `mpe diagnose` already exist
and each aggregate a fragment. The snapshot is the **one** source they all render from.

- The snapshot generator lives in **this** repo (it reads `/run/mpe`, units, and the
  graph); `mpe-cli` becomes a renderer of it.
- Existing subcommands keep their output shape initially and are re-pointed at the
  snapshot underneath, so no muscle memory breaks.
- Cross-repo work: `mpe-cli` is a separate repository with its own branch and release.
  A snapshot `schema` bump (D6) is the contract between them.

### D13 — Testable without the appliance

The repo's methodology is 847 tests that run on a laptop. Every decision function in the
control plane is a **pure function over an observed-state struct**, following
`mpe_engine_reconcile_decision`. Hardware appears only in thin adapters.

A control plane testable only on the Pi would be a regression in this project's own
practice, and would mean the Phase 3 gate is judged by hand on the instrument.

### D14 — Ports and JACK client names are assigned in one registry

Current allocation, discovered rather than documented, and already a source of hard
failures (the APC bench refuses to start if 9953 is held — deliberately, and correctly):

| Port | Owner |
|---|---|
| 9951 | SooperLooper OSC server |
| 9952 | `sl-hud-monitor` listener |
| 9953 | `sooperlooper-apc-bench` listener |
| 9954 | `sl-health` listener |
| 9961 | `sl-watchdog` listener |
| 53280 / 53270 | Surge OSC in / out |

The control plane adds at least one listener. **New ports are allocated in this table
or not at all**, and a test asserts no two entries collide. Ad-hoc probe ports (a
diagnostic binding 9977 to ask a question) must use the ephemeral range, never a fixed
number that could clash with a service.

### D15 — Looper policy gate (Phase 3+ blocked until adopt/kill)

Phase 3 session ownership and Phase 4 reconciliation for the **looper stack** are
**blocked** until the SooperLooper eval reaches an adopt/kill verdict
([`Documents/DIRECTION.md`](../DIRECTION.md) B7 full soak, B8 persistence). Phases 1–2
(observability, events) and Phase 5 (realtime boundary) are **not** gated on this.

The unified snapshot carries a **`looper.policy`** field:

| Value | Meaning |
|---|---|
| `eval` | SooperLooper eval stack supervised; session-owner work for looper deferred |
| `adopt` | Verdict: keep SooperLooper; Phase 3+ looper ownership may proceed |
| `disabled` | Verdict: kill eval stack; looper session control retires or migrates |

Until the verdict lands, treat looper session state as **eval-only inventory** (this
section's table), not as input to a new owner.

### D16 — `MPE_LOOPER_ENABLED` semantics (inverted; guard triple)

**Today `MPE_LOOPER_ENABLED=1` means guarded/blocked**, not enabled — the name is
inverted relative to behaviour. The guard fires when the value is `"1"`:

| Location | Function | Effect |
|---|---|---|
| `scripts/lib/engine-guard.sh` | `mpe_looper_engine_blocked()` | Bash callers refuse looper entry |
| `scripts/lib/audio-engine.sh` | `mpe_looper_state_label()` | Writes `looper=guarded` into `engine.state` |
| `patch_browser/audio_engine.py` | `looper_guard_blocked()` | Touch HUD shows guarded badge |

The guard triple predates SooperLooper and encodes a **stale premise** (v0 snd-aloop
looper impossible on JACK) while the appliance now runs 16 SooperLooper loops under
systemd. **Product decision required before Phase 3:** rename/invert the env var,
delete the guard, or remap `=1` to mean enabled. D15 blocks looper ownership work
until both the adopt/kill verdict **and** this semantics decision land. Q8 tracks guard
retirement schedule.

## Acceptance Criteria

### Phase 1 — Observability by consolidation (no behaviour change)

| # | Criterion | Verification |
|---|---|---|
| 1 | One snapshot: engine, graph, grid, loops, buffer/rate, health, per-service liveness, `mode`, `seq`, `schema` | Compare each field against the source of truth it aggregates |
| 2 | It aggregates *existing* truth only — writes nothing, owns nothing | Review: no writes outside its own output and `/run/mpe` snapshot |
| 3 | Every field carries provenance (which file/process/probe produced it) | Field-level `source` key present for all |
| 4 | A stale sub-source reports **stale**, never a last-known value | Kill `sl-hud-monitor`; grid/transport marked stale within 1.5 s (D6), value suppressed |
| 5 | The 2026-08-17 faults are visible from the snapshot alone | Reproduce a leaked client and a dead engine; both appear without correlating other sources |
| 6 | **No fifth view.** `mpe status`, `mpe engine status`, `mpe jack status`, `mpe diagnose` are re-pointed at the snapshot; their output shape is unchanged | Diff each command's output before/after; byte-identical or documented delta |
| 7 | Snapshot generation costs < 1% of a core at 2 Hz | Measure on the Pi with `/proc/<pid>/stat`, as in Evidence |
| 8 | A reader on an unknown `schema` major refuses loudly | Bump `schema`, run an old `mpe-cli`; it errors rather than printing nulls |

### Phase 2 — Event stream

**Event emitter cost constraint (Phase 2 expansion).** Bash callers emit via
`scripts/mpe-session-event-emit.py`, which forks a Python interpreter per event
(~360 ms on Pi). That is acceptable for transition-only events (engine start/stop,
buffer change, calibration stop/restore). It is **not** acceptable for periodic or
high-rate events — deferred names like `client.registered` during a graph rebuild
can fire dozens of times per second on the recovery path where jackd is already
missing deadlines. Before adding chatty events, the emitter must become long-lived
or move in-process. Criterion 11 assumes events stay rare.

**Snapshot publisher cost (Phase 3).** `build_snapshot()` is ~58 ms in-process on
the Pi (~36 ms of that is systemctl). At `PUBLISH_INTERVAL_S = 0.5` that is ~12%
of a core unless unit liveness is batched and cached (~1 s TTL). Batch the three
distinct units into one `systemctl is-active` call when building the Phase 3
publisher — not speculative work until that publisher lands.


| # | Criterion | Verification |
|---|---|---|
| 9 | Discrete events with stable names, one line each, structured | `engine.started`, `engine.exited`, `grid.established`, `grid.dropped`, `buffer.changed`, `client.registered`, `client.leaked`, `mode.changed` |
| 10 | The client leak is detectable from events alone | Replay 2026-08-17: `client.registered` without matching teardown, rising count |
| 11 | Events are cheap enough to leave on permanently | Measured CPU cost < 1% of a core at steady state, on the Pi |
| 12 | No polling probe may register a JACK client (the `jack_cpu_load` lesson) | Test asserts no per-sample process spawn on any repeating path |

### Phase 3 — Session owner (gated; see Falsification)

| # | Criterion | Verification |
|---|---|---|
| 13 | Grid state has exactly one writer; bench and HUD are readers | Grep: no `GridState` mutation outside the owner |
| 14 | `sync_source` sentinel is deleted | Absent from the tree |
| 15 | **Owner death does not stop audio** (D4) | `kill -9` the owner mid-playback; audio continues; HUD degrades visibly; owner restarts and re-derives state from the engine |
| 16 | Owner restart is stateless — re-derives from engines, never from its own last snapshot | Delete the snapshot, restart owner, state matches engine truth |
| 17 | Intents are the only edge write path for **looper session control** | No edge process sends session-control OSC to SooperLooper (transport, grid, loop record/clear, quantize). **Carve-out:** Surge patch-load OSC from the touch UI (`PatchLoader` → `/load`, volume) remains allowed — that is synth voice state, not looper session control |
| 18 | Musical behaviour unchanged | Pad-driven record → clear → grid-establish sequence identical before and after, verified by hand on the APC |
| 19 | **Cold boot converges** with no operator action, from power-on | Reboot 5×; each time the graph is wired, the grid is freeform, and the snapshot reaches `mode: ok` within 60 s |
| 20 | The snapshot is honest *during* boot — partial, not wrong | Snapshot during startup reports each not-yet-present source as stale/absent, never as a default value |
| 21 | Owner CPU budget: < 3% of a core at steady state, < 10% during a graph change | `/proc/<pid>/stat` over 60 s idle and across a buffer change |
| 22 | Decision logic is unit-testable off-hardware (D13) | Reconcile/ownership tests run in the laptop suite with no Pi, no JACK, no OSC |

### Phase 3b — The buffer change, end to end

The single hardest operation: it rewrites config, restarts jackd, restarts Surge,
restarts the looper, rewires the graph, and clears loops — touching all three planes.
It is also where a Surge segfault was observed on 2026-08-17. It is the best available
test of "reconciliation over sequencing" and gets its own criteria.

| # | Criterion | Verification |
|---|---|---|
| 23 | A buffer change is **one declared intent**, not a sequence of restarts at the call site | `set-surge-audio.sh` reduces to submitting an intent |
| 24 | It converges from a partially-applied state | Interrupt mid-change (kill the owner between jackd restart and Surge promote); on restart the appliance reaches the requested period with no operator action |
| 25 | Exactly one engine restart occurs — no races between systemd, the reconciler and any script | Event stream shows a single `engine.exited`/`engine.started` pair per change |
| 26 | Surge failing to start is surfaced, not swallowed | Induce the 2026-08-17 segfault path; snapshot reports `surge: failed` with the reason; the operation reports failure rather than "Applied" |

### Phase 4 — Reconciler

| # | Criterion | Verification |
|---|---|---|
| 27 | One reconcile loop replaces `restart-sooperlooper.sh`, `surge-watchdog`, and the graph-restart sequences | Those scripts deleted or reduced to thin CLI wrappers |
| 28 | Converges from arbitrary damage | Inject: kill engine, unwire graph, saturate registry, change buffer — each converges without operator action |
| 29 | Restart budgets survive the reconciler's own restart | Existing `/run/mpe` cooldown semantics preserved (jack-audio-engine-spec D3) |
| 30 | **Maintenance mode suppresses all corrective action** (D11) | Set the flag; kill the engine; nothing restarts it; snapshot shows `mode: maintenance` |
| 31 | **Calibration still works end to end** | Run `calibrate-patch-normalization.py` with the reconciler live; it completes, and the stack is restored afterwards |
| 32 | A stale maintenance flag self-clears | Set the flag, kill the setter, wait past the deadline; reconciler resumes and the event stream records `mode.changed` |

### Phase 5 — Realtime boundary enforced

| # | Criterion | Verification |
|---|---|---|
| 33 | Test fails on `set_process_callback` outside the allowlist | Add a violation; suite goes red |
| 34 | OUT meter is out-of-process or compiled; allowlist empty | `mpe-peak-meter` no longer a Python client |
| 35 | 128 × 3 under playing load, strict mode, zero xruns | `bench-xruns.sh --sweep --strict` while playing — softmode numbers are not accepted (`d5ac0cc`) |
| 36 | Any unit hosting a JACK client declares `LimitRTPRIO` | Test over `config/*.service`, cross-referenced with the allowlist |

### Success metric (how we know this worked)

| # | Criterion | Verification |
|---|---|---|
| 37 | The next unexplained incident is **characterised from the snapshot and event stream alone**, in under 5 minutes, with no ssh-and-correlate across units, `/run` files, `ps` and `jack_lsp` | Recorded at the time of the next incident, in `docs/measurements/` |

Criterion 37 is the point of the whole document. If Phases 1–2 ship and the next
incident still takes the 2026-08-17 shape — hours of correlation across five sources —
the observability model is wrong and Phase 3 must not be built on top of it.

---

## Phasing

Phases 1 and 2 are **unconditional** — pure profit, no behaviour change, immediately
useful, and prerequisites for diagnosing anything after.

**Sequencing:** Phases 1–2 can proceed now. Phase 3+ for the looper stack is gated on
the SooperLooper adopt/kill verdict (D15, [`DIRECTION`](../DIRECTION.md) B7/B8) and
on resolving `MPE_LOOPER_ENABLED` guard semantics (D16). Phase 5 (realtime boundary)
proceeds in parallel with 1–2 — it is orthogonal to looper ownership.

### Phase 0 (immediate — no reconciler)

Before Phase 1 ships, fix the live calibration regression (Evidence gap review):
`calibration_teardown.stop_mpe_audio_services()` must **stop and later restart** the
looper units (`mpe-sooperlooper`, `mpe-apc-bench`, `sl-hud-monitor`, `sl-watchdog`) —
not only Surge/touch/pressure/governor. This is an **interim D11 shim**: explicit
`systemctl stop`/`start` in calibration, not maintenance mode and not a reconciler.
Without it, `Restart=always` on looper units undoes calibration within 5 s.

Phase 3 is **gated**: it requires an explicit decision between the daemon and the
cheaper **Phase 3-merge** alternative below. Phases 4–5 follow whichever is chosen.

### Phase 3-merge (alternative to daemon)

If the Phase 3 gate answers *one instrument, pygame-only* (see Falsification Analysis),
take the merge path instead of a session-owner daemon:

- Merge `sooperlooper-apc-bench.py`, `sl-hud-monitor.py`, and in-process `GridState`
  into **one looper session process** with a single grid writer.
- Acceptance criteria **13–14, 18** still apply; **15–16, 19–22** shrink or defer
  (no separate owner to kill; cold-boot convergence stays a systemd problem).
- Phase 3b buffer change remains one declared operation — implemented as a reconciled
  intent inside the merged process or via existing promote path until Phase 4.
- Does **not** need D11 maintenance mode at the same scope — fewer supervised units to
  suppress during calibration.

Outline only; full criteria live in Falsification Analysis until the gate decision.

---

## Falsification Analysis

**The strongest case that this is the wrong move:** it adds a control plane, a
protocol, and a new single point of failure to a one-instrument appliance that already
makes music — in exchange for properties (multi-instrument, remote agents, alternate
UIs) that may never be needed. Kubernetes-shaped thinking applied to a synth.

**The cheaper alternative, taken seriously.** Most of the benefit is available by
*deleting processes rather than adding one*: merge `sooperlooper-apc-bench.py`,
`sl-hud-monitor.py`, and `GridState` into a single process that owns looper session
state. One process, one copy of the grid, no protocol, no daemon, no snapshot format.
That kills faults 1–3 for the looper — which is where every one of them actually
occurred — at a fraction of the cost and risk.

It does **not** solve: the touch UI holding its own patch/normalization state, the
config-key duplication, or the realtime boundary (D1/Phase 5, which is orthogonal and
should proceed regardless).

**Decision rule for the Phase 3 gate.** If the answer to *"how many instruments will
ever run this, and will anything other than pygame ever drive it?"* is **one and no**,
take the merge. The daemon earns its keep only against a second instrument, a remote
agent, or a non-pygame UI. That is a product question, and it is the owner's, not this
spec's.

**What would falsify Phases 1–2?** If `mpe state` and the event stream are built and
the next incident still requires ssh-and-correlate, the observability model is wrong
and Phase 3 should not proceed on top of it.

**Known cost of Phase 3 even if correct:** a period where state has two owners
(migration), which is exactly the class of bug being removed. Mitigated by moving one
fact at a time (grid first) and by criterion 18.

**The gap review surfaced a cost the first draft hid.** D11 (maintenance mode) is not
an optional refinement — without it, Phase 4 breaks calibration on the day it lands,
because the reconciler will restart what calibration deliberately stops. That is a
whole subsystem (`maintenance` flag, deadline, self-clear, honest reporting, event)
that exists only to make automation suppressible. It is real complexity, added to
manage complexity, and it is a fair point against the reconciler.

The merge alternative does not need it: fewer processes means fewer things to
suppress, and calibration's existing `systemctl stop` list keeps working.

---

## Security Considerations

Omitted from the first draft, which folded it into a one-line decision. It needs a
threat model, because **intents are a privilege boundary**.

**The existing posture.** `scripts/pi/provision-mpe-agent.sh` grants the remote agent a
deliberately narrow sudo: named units, named verbs, and explicitly *not* `sudo kill`,
on the reasoning that sudo-kill can signal any process including root ones and is
therefore a far wider grant than one more named unit. That reasoning is sound and this
design must not undo it.

**The risk.** If edges submit intents that a privileged owner executes, then *the intent
vocabulary becomes the real privilege grant*, and it routes around the sudoers file. An
agent that may not `systemctl stop mpe-jackd` but may submit `{intent: "set_buffer"}` —
which restarts jackd — has been granted the thing the sudoers file withheld, silently.

| Rule | Rationale |
|---|---|
| The owner runs as `MPE_PI_USER`, **not root** | It should not be able to do more than the operator's own session can |
| Intents arrive over a **Unix socket** with filesystem permissions — never a TCP port | Matches the LAN-only, no-network-surface posture; `chmod`/`chown` is the ACL |
| The intent vocabulary is **closed and enumerated** in this spec; unknown intents are rejected and logged | An open vocabulary is an open shell |
| Any intent whose effect exceeds the submitter's own sudo rights is **refused**, not proxied | The owner must not be a privilege-laundering service |
| Intents are **logged with their submitter** to the event stream | "Who did that?" must be answerable — currently it is not |
| No intent may name an arbitrary path, unit, or command | Keeps the surface a fixed enum, not a script runner |

**Not in scope:** authentication between processes on the appliance. Single operator,
single machine, no untrusted local users. Filesystem permissions are the boundary. If a
second user account ever runs edge processes, this section needs revisiting.

**Unchanged from today:** OSC ports stay bound to `127.0.0.1`; the snapshot is
world-readable and contains no credentials; secrets remain absent from `/run/mpe`.

## Assumptions & Constraints

- Single appliance, single operator, LAN-only, no untrusted input.
- The Pi 4 has four cores; one is effectively reserved by the realtime plane under
  load. The control plane must fit in what remains alongside the touch UI (25 jiffies/s
  measured post-fix).
- SooperLooper's OSC contract is fixed and cannot be extended; `register_auto_update`
  delivering only on change is a permanent property to design around (D6, criterion 4).
- systemd is available and is the process supervisor (D5a).
- Changes ship on `dev` and soak on the appliance before `main`, per `AGENTS.md`.

## Open Questions

1. **Q1 — Daemon or merge?** The Phase 3 gate. Needs a product answer, not a technical
   one. *(Blocking for Phase 3 only; Phases 1–2 proceed either way.)*
2. **Q2 — Snapshot publish interval.** The HUD writes at 2 Hz today. Is that the right
   rate for a general snapshot, or does the meter/transport need a faster lane?
   Measure before choosing.
3. **Q3 — Does the touch UI join the edge plane in Phase 3, or later?** It holds
   normalization, pressure, hold and favourites state, all file-backed and all
   single-writer today. Lower risk than the looper; also lower value.
4. **Q4 — Event transport.** Journal (free, structured via `SYSLOG_IDENTIFIER`, already
   collected) vs a dedicated file. Journal is the default unless measurement says
   otherwise.
5. **Q5 — What owns `/etc/mpe/mpe.env`?** Config is state too, and `configure-pi-paths.sh
   --force` rewriting it is a sequence, not a reconciliation. Note that
   `bench-xruns.sh --strict` and D11's maintenance flag both now *write* config or
   runtime state as a side effect of a diagnostic — a second writer to state the
   reconciler reads.
6. **Q6 — The motivating bug is still unexplained.** On 2026-08-17 the engine was
   observed with `quantize=1.0, sync=1.0, mute_quantized=1.0` on loop 0 while **zero
   clips were loaded** — grid-active over an empty session, which is the reported
   "grid doesn't revert to freeform after clean". Observed once, never reproduced.
   Two hypotheses were tested and **both disproven**: the pad-driven clear path
   reverted correctly (`quantize 1.0 → 0.0`), and an engine restart was handled
   correctly (`bench: no grid to restore — next take defines one`).

   **This matters for Phase 3.** Moving grid ownership without understanding this risks
   claiming a fix for something never diagnosed. It is also an argument *for* Phases
   1–2 first: the anomaly is exactly the kind of thing a snapshot plus
   `grid.established`/`grid.dropped` events would have caught in the act.

   Constraint on any future attempt: `GridState.arm()` only fires from a pad press, so
   the fault cannot be reproduced by driving OSC directly. Reproduction requires the
   hardware and the operator.
7. **Q7 — Does the reconciler own `mpe-apc-bench` and `sl-hud-monitor` restarts, or
   does systemd?** D5a says systemd owns processes; D5b says the reconciler declares
   desired state via `systemctl`; D11 says the reconciler must be suppressible. The
   two must not both act on the same unit, or maintenance mode suppresses one path and
   not the other — the same double-ownership this document exists to remove. See
   **Appendix A** for which units carry `Restart=always` today and are therefore in
   scope for this question.
8. **Q8 — Guard triple retirement schedule.** When does `MPE_LOOPER_ENABLED` /
   `engine-guard.sh` / `looper_guard_blocked()` get renamed, inverted, or deleted?
   Blocked on D16 product decision; must land before or with Phase 3 looper ownership.
9. **Q9 — Home-dir JSON in snapshot or excluded?** Should `~/.mpe_sl_hud_state.json`
   and `~/.mpe_sl_watchdog.json` appear in the unified snapshot (with provenance and
   staleness), or stay edge-local artifacts excluded from `/run/mpe` aggregation?

## Rollback

Each phase is independently revertable.

- **Phases 1–2:** additive only. Delete the command and the event calls; nothing else
  referenced them.
- **Phase 3:** the merge variant reverts by restoring two units. The daemon variant
  reverts by re-enabling the edges' local state — which must therefore be *removed in a
  separate commit from* the owner landing, so the revert is one commit.
- **Phase 4:** the riskiest to revert, because it *deletes working scripts*. Therefore:
  `restart-sooperlooper.sh`, `surge-watchdog.sh` and the graph-restart sequences are
  **retained as-is** through Phase 4 and only deleted in a separate, later commit once
  the reconciler has soaked. Until that commit, rollback is "stop the reconciler, set
  maintenance mode off, the old scripts still work." Maintenance mode (D11) must be
  revertable independently — a stuck flag with no reconciler running must not leave the
  appliance unsupervised and looking fine.
- **Phase 5:** the allowlist test can be skipped; the meter's out-of-process form is a
  separate binary that can simply not be started.

## Technical Notes

- Reuse `mpe_state_write_atomic` and `/run/mpe` (tmpfs, `RuntimeDirectoryPreserve=yes`)
  rather than inventing storage.
- `mpe_engine_reconcile_decision` is the model for D3: a pure function over observed
  state, unit-testable without hardware. Every reconciler rule should have that shape.
- The existing `tests/test_systemd_units.py` guards lifecycle declaration; extend it
  rather than adding a parallel mechanism.
- `bench-xruns.sh --strict` is the only trustworthy xrun measurement (`d5ac0cc`).
  Phase 5's criterion **35** depends on it; do not accept a softmode number.

---

## Appendix A — systemd unit matrix

From [`docs/CODE-MAP.md`](../../docs/CODE-MAP.md) §2.2 (`scripts/install-units.sh`).
**14 enabled units** on a typical touch + looper eval install. Maintenance-mode note:
units with `Restart=always` will fight calibration (D11) and Phase 0 until looper units
are included in `stop_mpe_audio_services()`.

| Unit | After / Wants | Restart policy | Maintenance-mode note |
|---|---|---|---|
| `mpe-jackd.service` | After `sound.target` | `always` | Stopped by graph restart / promote; reconciler must respect D11 |
| `surge-xt-cli.service` | After `mpe-jackd`, `usb-audio-gadget`, governors | `on-failure` | Stopped by calibration teardown today |
| `surge-watchdog.service` | After `surge-xt-cli` (not BindsTo) | `always` | Observes/publishes; corrective restarts subject to D11 |
| `mpe-sooperlooper.service` | After `mpe-jackd`, `surge-xt-cli` | `always` | **Not stopped by calibration today** — Phase 0 fix required |
| `mpe-apc-bench.service` | After `mpe-sooperlooper` | `always` | **Not stopped by calibration today** — Phase 0 fix required |
| `sl-hud-monitor.service` | After `mpe-sooperlooper` | `always` | **Not stopped by calibration today** — Phase 0 fix required |
| `sl-watchdog.service` | After `mpe-jackd` | `always` | **Not stopped by calibration today** — Phase 0 fix required |
| `touch-patch-browser.service` | After `surge-xt-cli`, `touch-boot-animation` | `on-failure` | Skipped when `MPE_CALIB_FROM_BROWSER=1` |
| `surge-poly-governor.service` | — | (unit default) | Stopped by calibration teardown today |
| `mpe-cpu-governor.service` | — | (unit default) | — |
| `mpe-audio-profile-sync.service` | — | (unit default) | — |
| `mpe-pressure-remap.service` | — | (unit default) | Stopped by calibration teardown today |
| `midi-clock-in.service` | After `sound.target` | `on-failure` | — |
| `mpe-shutdown-splash.service` | — | (unit default) | — |

**Disabled by default (not in the 14):** `midi-clock-out`, `boot-animation`,
`mic-to-uac2-bridge`. **Static:** `foot-pedal.service`. **UI mode:** `MPE_UI_MODE=touch`
→ touch units; `oled` → `patch-browser.service` + OLED animations.
