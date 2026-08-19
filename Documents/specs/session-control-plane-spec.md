# Session control plane — one owner per fact, reconciliation over sequences

**Status (2026-08-19).** Phases 0, 1, 2, 3M and 5 have all landed on `dev`. Phase 3M is
**code complete but unverified** — criteria 44, 46 and 47 have never been run, and 42 has a
working tool and no number. Phase 5 is complete except criterion 35 (`128 x 3`), which is the
furthest-away target in the spec: the appliance currently runs `1024 x 3`. Phases 3b and 4 are
not started; Phase 4 remains blocked by D15, whose numbers are void pending the task 6
re-measure. Criterion 7 **passes as of 2026-08-19** (0.53% of a core at 2 Hz) — see
[`docs/measurements/systemd-liveness-cost-2026-08-19.md`](../../docs/measurements/systemd-liveness-cost-2026-08-19.md).
Outstanding work is tracked in [`next-work-order-2026-08-19.md`](next-work-order-2026-08-19.md).

**Every phase table below carries a Status column. Code landing is not a phase closing** —
Phase 3M is the standing example of the difference.
**Author:** written after the 2026-08-17 stability session; every claim in Evidence is measured on `raspberrypi2`, not reasoned.

**Implementation log (2026-08-18).** What shipped, and what it cost:

| Landed | Notes |
|---|---|
| Phase 0 — calibration stops/restores the looper stack | Guarded by a real D11 maintenance flag (`pid` + 30 min deadline, self-clearing) — the first cut had `surge-watchdog` restarting the units mid-measurement |
| Phase 1 — `session.snapshot.json` schema v1 | Verified on hardware: all four sources fresh, `mode: ok`, HUD live. **No publisher** — built on demand only |
| Phase 2 — `events.jsonl` + emits at engine/graph chokepoints | Bash emits through `scripts/mpe-session-event-emit.py` for safe JSON; escaping verified live |
| Phase 3M — merged looper session (PR #72, #76) | One unit, one OSC session, one cache. **Unverified**: no hand check (44), no crash number (46), no CPU before/after (47), no latency figure (42) |
| Phase 5 — compiled peak meter, RT boundary guard (PR #74) | Python JACK callbacks now fail the suite; meter is a compiled leaf client costing 0% DSP. Criterion 35 still open |
| Phase 1 criterion 6 — CLI re-pointed at the snapshot (mpe-cli #6, #7) | `mpe status`, `engine status`, `jack status`, `diagnose` all read one snapshot. No longer a fifth view |
| Criterion 7 — snapshot cost | 11.5% → **0.53%** of a core at 2 Hz via batched D-Bus liveness. Cached fields carry their own age |

**Staleness model changed during implementation (amends criterion 4).** The spec's
blanket 1.5 s age threshold was wrong for every source in this system: `jack.state`
and `surge.state` carry a process *start* epoch, and `engine.state` is written only on
transition. Measured against the appliance's real file ages, the first implementation
reported `null` for every field, permanently. Staleness is now per-source **liveness**:
each field is fresh while its *writer* unit is confirmed active (`engine.state` follows
`surge-watchdog`, not `surge-xt-cli` — during recovery Surge is down and the engine
field carries the reason). Unknown or transitional liveness reads as stale. `reconcile`
treats "never restarted" as valid, not stale.

**CPU cost is a first-class constraint here** — see [`DECISIONS.md`](../DECISIONS.md)
§ *2026-08-18 — CPU is the scarcest resource*. Adding the Phase 0 reconcile to
`surge-watchdog` cost 9% of a core before it was measured and brought back to ~2%.

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
| `/run/mpe/engine.state` | `start-jackd.sh`, `start-surge-cli.sh`, `surge-watchdog.sh`, `audio-engine.sh` | `patch_browser/audio_engine.py`, `mpe-cli`, Phase 1 snapshot | **Transition-written, not a heartbeat.** Snapshot marks stale unless the *writer* (`surge-watchdog`) is active (D6, amended). No-op republishes are skipped, so `updated` does not advance while nothing changes |
| `/run/mpe/jack.state` | `start-jackd.sh` | snapshot, promote/reconcile probes | `started` is a process **start epoch**, never refreshed — stale unless `mpe-jackd` is active (D6, amended) |
| `/run/mpe/surge.state` | `start-surge-cli.sh` | snapshot, watchdog promote probe | Same, keyed on `surge-xt-cli` (D6, amended) |
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
  Phase 3+, not a requirement now (see Falsification). Phase 3M does not introduce one.
- A new IPC protocol. Phase 3M removes the need for one. `/run/mpe` files already work and are already
  atomic.
- Changing musical behaviour. No phase may alter what the instrument sounds like or
  how a pad responds.

---

## Design Decisions

### D1 — Three planes, with a hard rule at the realtime boundary

| Plane | Members | Rule |
|---|---|---|
| **Realtime** | jackd, `surge-xt-cli`, `sooperlooper` | Compiled only. **Nothing else may hold a JACK process callback.** |
| **Control** | looper session process (Phase 3M), `sl-watchdog`, `surge-watchdog` | Owns state, reconciles, never in the audio cycle. |
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
*(Gate closed 2026-08-18: merge — D17. D4 applies unchanged to the merged process.)*

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

**Publish interval is fixed at 500 ms** (matching `sl_hud_monitor.WRITE_INTERVAL_S`).
It is stated here rather than deferred to Q2 because the first draft made staleness
depend on an open question and the open question depend on staleness. Q2 is now about
whether a *faster lane* is needed for transport, not about this value.

**The 1.5 s staleness threshold is retired (amended 2026-08-18).** It survives only for
`looper.hud`, whose writer genuinely heartbeats at 2 Hz. It was wrong for every other
source: `jack.state` and `surge.state` hold a process *start* epoch and `engine.state`
is transition-written, so against real appliance file ages an age gate reported `null`
for every field, permanently. Those sources now derive staleness from their **writer
unit's liveness** — see criterion 4 and the Implementation log. A source whose writer
heartbeats may use an age gate; a source written on transition must not.

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
*(Criterion 48 carries this forward for Phase 3M.)*

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

### D15 — Looper policy gate (Phase 4 blocked until adopt/kill; **amended by D17**)

Phase 4 reconciliation for the **looper stack** is **blocked** until the SooperLooper eval reaches an adopt/kill verdict
([`Documents/DIRECTION.md`](../DIRECTION.md) B7 full soak, B8 persistence). Phases 1–2
(observability, events), Phase 3M (the merge — see D17 for why) and Phase 5 (realtime
boundary) are **not** gated on this.

The unified snapshot carries a **`looper.policy`** field:

| Value | Meaning |
|---|---|
| `eval` | SooperLooper eval stack supervised; session-owner work for looper deferred |
| `adopt` | Verdict: keep SooperLooper; Phase 4 looper reconciliation may proceed |
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
systemd. **Product decision required before Phase 4:** rename/invert the env var,
delete the guard, or remap `=1` to mean enabled. D15 blocks looper *reconciliation*
until both the adopt/kill verdict **and** this semantics decision land; Phase 3M only
preserves existing guard behaviour and is not blocked (D17). Q8 tracks guard
retirement schedule.

### D17 — Q1 answered: merge, not daemon (2026-08-18)

**Decision (Mitch, 2026-08-18):** take the merge. Phase 3's session-owner daemon is
deferred, not rejected — it earns its keep against a second instrument, a remote agent,
or a non-pygame UI, none of which exist.

**Rationale, and why this is a first step rather than a cheaper substitute.** Every
fault this spec was written for occurred at a boundary between
`sooperlooper-apc-bench.py`, `sl_hud_monitor.py` and `GridState` — three processes
holding one domain, ~1536 lines. You cannot design a good protocol around state smeared
across three processes: doing so encodes the current confusion into an IPC contract,
which is far harder to undo than a Python refactor. Consolidate the domain model first;
then decide what interface it needs. If that answer later turns out to be "a daemon",
nothing is lost — the merged process *is* the session owner, and only its reach changes.

Half the spec's machinery exists solely to make cross-process coordination safe: D6
snapshot format, D7 intents, D11 maintenance mode at Phase 4 scope, D12's CLI contract,
and the entire Security section. The merge retires that scope.

**Explicitly not merged:**

| Component | Stays separate because |
|---|---|
| `sl-watchdog.py` (535 lines) | D4 — a supervisor that dies with the thing it supervises is not a supervisor |
| `touch_browser_app.py` | Different concern (patch/normalization/pressure), implicated in zero faults, and it is the process that must never host a JACK client (D1) |

**Amends D15.** D15 blocks *Phase 3+ looper ownership* on the SooperLooper adopt/kill
verdict. Phase 3M is permitted ahead of that verdict, because it is the one shape of
this work that is defensible under either outcome: it **deletes processes rather than
adding an owner**, it is reversible by restoring two unit files (Rollback), and it
removes the fault modes that made B7/B8/B10 unreliable to run — so it improves the very
evaluation D15 is waiting on. The daemon, the reconciler (Phase 4) and any new
authoritative state remain blocked by D15 as written. If the verdict lands `disabled`,
the merged process retires as one unit instead of three.

**D16 is not a blocker for Phase 3M.** The guard-triple semantics decision is still
required before anything *changes* looper guard behaviour; a refactor that preserves it
does not need the answer. Q8 still tracks retirement.

## Acceptance Criteria

### Phase 1 — Observability by consolidation (no behaviour change)

| # | Criterion | Status | Verification |
|---|---|---|---|
| 1 | One snapshot: engine, graph, grid, loops, buffer/rate, health, per-service liveness, `mode`, `seq`, `schema` | 🟡 partial *(2026-08-19)* | **Added by PR #76:** `services` (per-service liveness, with age and transport), `config`, and `graph.surge_on_graph` behind `include_runtime_probes`. **Still missing:** per-loop state, health |
| 2 | It aggregates *existing* truth only — writes nothing, owns nothing | ✅ | Writes only `session.snapshot.json` + `.seq` |
| 3 | Every field carries provenance (which file/process/probe produced it) | ✅ | Field-level `source` key present for all |
| 4 | A stale sub-source reports **stale**, never a last-known value | ✅ *(amended)* | Per-source **writer liveness**, not a 1.5 s age gate — see Implementation log. Unknown liveness reads as stale |
| 5 | The 2026-08-17 faults are visible from the snapshot alone | 🟡 partial *(2026-08-19)* | Graph wiring now present (`graph.surge_on_graph`, read from `meter.state`). **Leaked-client detection still absent** — that is the remaining half, and it is shared with criterion 10 |
| 6 | **No fifth view.** `mpe status`, `mpe engine status`, `mpe jack status`, `mpe diagnose` are re-pointed at the snapshot; their output shape is unchanged | ✅ *(2026-08-19)* | All four re-pointed (mpe-cli #6). One snapshot per command, no per-field forking. mpe-cli #7 fixed a jq `//` polarity bug that made two of them render `unknown` for healthy units |
| 7 | Snapshot generation costs < 1% of a core at 2 Hz | ✅ *(2026-08-19)* | **0.53% at 2 Hz**, 0.39% at 1 Hz. Batched D-Bus liveness: `active` every 5 s (7.6 ms), `enabled` every 30 s (32.4 ms) because it is configuration, not runtime. Cold build 424 → 42 ms, warm 57 → 1.4 ms. Cached fields carry `active_age_s`/`enabled_age_s` and the transport that answered. See [`systemd-liveness-cost-2026-08-19.md`](../../docs/measurements/systemd-liveness-cost-2026-08-19.md) |
| 8 | A reader on an unknown `schema` major refuses loudly | ✅ | `read_snapshot` raises on `schema > max_schema` |

### Phase 2 — Event stream

**Event emitter cost constraint (Phase 2 expansion).** Bash callers emit via
`scripts/mpe-session-event-emit.py`, which forks a Python interpreter per event
(~360 ms on Pi). That is acceptable for transition-only events (engine start/stop,
buffer change, calibration stop/restore). It is **not** acceptable for periodic or
high-rate events — deferred names like `client.registered` during a graph rebuild
can fire dozens of times per second on the recovery path where jackd is already
missing deadlines. Before adding chatty events, the emitter must become long-lived
or move in-process. Criterion 11 assumes events stay rare.

**Snapshot publisher cost — profiled 2026-08-18, and the number is all one thing.**
The publisher is **not blocked by any dependency**; it fails criterion 7 as written,
for a fully diagnosed reason. Measured in-process on `raspberrypi2`:

| | ms |
|---|---|
| `build_snapshot()` full | **57.3** |
| ↳ one `systemd_unit_active` fork | 18.1 |
| `build_snapshot(unit_active=<stub>)` — everything else | **1.4** |
| ↳ four `read_engine_state` | 0.4 |
| ↳ `read_sl_hud_state` | 0.1 |
| ↳ `next_seq` (flock + fsync) | 0.5 |

**97% of the cost is three `systemctl` forks.** File reads, JSON, locking and fsync
together are 1.4 ms. This is the [`DECISIONS.md`](../DECISIONS.md) 2026-08-18 failure
mode inside the snapshot itself, and it is why a publisher must not ship as-is: at
`PUBLISH_INTERVAL_S = 0.5` it would put ~11.5% of a core on the appliance permanently
— five times what all of `surge-watchdog` costs.

Cost of the options, at 2 Hz:

| Approach | Cost |
|---|---|
| Today — three separate forks per build | 11.5% |
| Batched into one `systemctl is-active` per build | 3.6% |
| Batched + 2 s liveness TTL | ~1.1% |
| Batched + 5 s liveness TTL | ~0.44% ✅ |
| systemd liveness over **D-Bus** (no fork) | *unmeasured; expected ≪ 1 ms* |

**Try D-Bus first (~20 min to find out).** Querying systemd's `ActiveState` over D-Bus
spawns no process and would clear criterion 7 at 2 Hz with **no cache and no honesty
trade**. Only if that fails should a TTL be accepted — and a cached liveness reading is
a soft form of the last-known-good problem this spec's staleness model exists to
prevent, so if one is used the snapshot **must carry the age of the cached liveness**
so a reader can see how stale the judgement is. Batching is worth doing either way.

**Sequencing note (soft, not a block).** The publisher has no consumer until criterion 6
re-points `mpe-cli` at the snapshot. That is a reason to do criterion 6 first, not a
reason the publisher cannot be built.


| # | Criterion | Status | Verification |
|---|---|---|---|
| 9 | Discrete events with stable names, one line each, structured | 🟡 partial | Emitting: `engine.started`, `engine.exited`, `buffer.changed`, `mode.changed`, `looper.units.*`. **Named but never emitted:** `grid.established`, `grid.dropped`, `client.registered`, `client.leaked` |
| 10 | The client leak is detectable from events alone | ❌ | Needs a `client.registered`/`client.leaked` emitter — blocked on the cost constraint above |
| 11 | Events are cheap enough to leave on permanently | ✅ *(conditionally)* | Free at steady state because events are transition-only. Holds **only** while that stays true |
| 12 | No polling probe may register a JACK client (the `jack_cpu_load` lesson) | ✅ *(2026-08-18)* | Healthy path reads `wired=` from `meter.state` (published by the compiled meter, already on the graph). `jack_lsp` is fallback only. Measured 35 → 0 xruns/min at matched load — see below |

**Criterion 12 — RESOLVED 2026-08-18. It is now met, not traded.** The reasoning below
is kept because the trade it describes was wrong in a way worth remembering.

`mpe_surge_on_jack_graph()` no longer runs `jack_lsp` on the healthy path. It reads
`wired=` from `/run/mpe/meter.state`, published at 5 Hz by `mpe-peak-meter` — a compiled
client already permanently on the graph (Phase 5, criterion 34). A file read: no fork,
no registration, no graph reorder. `jack_lsp` survives only as a fallback when the meter
is missing, stale (>5 s) or malformed, so a dead meter cannot produce a false green.

**The trade below was priced in the wrong currency and it caused audible crackle.** The
bounded 10 s probe was called affordable at 1.16% of a core. Measured against the audio
graph instead of the processor: **35 xruns/min, against 0 after the fix, at identical
load** — the largest single xrun source on the appliance, larger than the whole looper
stack. Each `jack_lsp` registration forces jackd to rebuild its processing order, and it
was doing so six times a minute under a player's hands. Full protocol and the two wrong
conclusions along the way:
[`docs/measurements/crackle-root-cause-2026-08-18.md`](../../docs/measurements/crackle-root-cause-2026-08-18.md).

**The general lesson, now `DECISIONS.md` 2026-08-18 rule 9:** a probe has two costs and
CPU is the cheaper one. And the best probe is an observer already on the graph, not a
cheaper one added to it.

<details>
<summary>Superseded reasoning (2026-08-18, earlier the same day)</summary>

**Criterion 12 was knowingly violated, and the tension was real.** The supervisor's
only way to answer *"is Surge on the graph?"* is `jack_lsp`, which registers a client.
Removing the probe is not an option — a short-circuit that never re-probes is blind to
the orphaned-client wedge ([`DECISIONS.md`](../DECISIONS.md) 2026-08-15), which is the
exact fault the supervisor exists to catch. So the criterion was traded down rather
than met, deliberately:

- **Cadence bounded** at 10 s (was every 5 s, twice per tick), always ≤ the 30 s
  supervisor cooldown so detection never lags the ability to act.
- **`timeout -k`** guarantees the client exits, so registrations cannot accumulate —
  this is what makes the 705-leak of 2026-08-17 non-recurring. Live count: 5 clients.
- **Cost measured:** ~116 ms/probe ⇒ ~1.16% of a core.

A `/dev/shm/jack_sem.<uid>_default_<client>` existence check was evaluated as a
fork-free replacement and **rejected**: stale semaphores outlive dead clients
(`jack_cpu_load` sems were present for clients absent from `jack_lsp`), so it is a
reliable negative but an unreliable positive — a reading identical whether Surge is
registered or orphaned. Criterion 12's real intent is *no unbounded, unreaped client
registration on a repeating path*; that intent is met. Reword it in Phase 4 rather
than pretending the letter is satisfied.

</details>

### Phase 3M — Looper session process (**chosen path**, D17)

**One process owns looper session state.** Merge `scripts/sooperlooper-apc-bench.py`
(392), `scripts/sooperlooper/sl_hud_monitor.py` (258) and
`scripts/sooperlooper/sl_grid_state.py` (145) — plus `sl_grid_sync.py` (205) and
`apc_footswitch.py` (536) as they are already bench-owned — into a single unit,
`mpe-looper-session.service`. `sl-watchdog` and the touch UI stay separate (D17).

| # | Criterion | Status | Evidence / what remains |
|---|---|---|---|
| 38 | One unit hosts bench + HUD + grid state | ✅ | `mpe-looper-session.service` present; the two old units deleted (PR #72) |
| 39 | Grid state has exactly one writer; nothing else mutates it | ✅ | Single writer verified by grep guard in the suite |
| 40 | The `sync_source` restart sentinel is deleted | ✅ | Sentinel absent from the tree |
| 41 | One OSC connection with one lifecycle | ✅ 2026-08-19 | One `SlOscSession`, one listen port (9953), one cache (PR #76). 9952 retired |
| 42 | **HUD work never runs on the MIDI path** | 🟡 tool only | `--measure-latency N` measures the real MIDI→OSC path on the Pi. **No number yet** — needs the APC. Results table in `docs/measurements/looper-midi-osc-latency-2026-08-19.md` is empty |
| 43 | **Loud failure on a held OSC port survives** | ✅ | Bind failure still fatal; message preserved through the merge; test covers it |
| 44 | Musical behaviour unchanged | ❌ **needs Mitch** | Pad record → clear → grid-establish by hand on the APC. Never run — the merge rests on this |
| 45 | `sl-watchdog` remains a separate process (D4) | ✅ | `sl-watchdog` still a separate unit |
| 46 | Crash blast radius is measured, not assumed | ❌ | `kill -9` blast radius never measured. The spec accepts the regression *only with a number attached* |
| 47 | CPU no worse than the two processes it replaces | ❌ | CPU before/after via `/proc/<pid>/stat` never run |
| 48 | Grid transitions are unit-testable off-hardware (D13) | ✅ | Grid transitions covered off-hardware in the laptop suite |
| 49 | State is re-derived from the engine on start, never from a local cache | ✅ | State re-derived from the engine on start; no local cache |

**Sequencing for the implementer.** Land in this order, each its own commit, each
green on the Pi before the next: (1) new unit + process skeleton that runs the bench
only, two old units retired; (2) HUD folded in behind criterion 42's threading rule;
(3) `GridState` folded in, single writer (39); (4) sentinel deleted (40). Criterion 44
is checked by hand after each step, not once at the end.

**Not in scope:** the touch UI, patch/normalization state, the config-key duplication,
and the realtime boundary (Phase 5) — which proceeds independently.

### Phase 3 — Session owner daemon (**superseded** by Phase 3M; retained for the record)

> **Not the chosen path (D17, 2026-08-18).** Criteria 13–22 are kept because the daemon
> becomes live again if a second instrument, a remote agent, or a non-pygame UI appears.
> Do not implement these without re-opening Q1. Criteria 15–16 and 19–22 have no
> analogue under Phase 3M (there is no separate owner to kill); 13–14 and 18 carry over
> as 39, 40 and 44.

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

### Phase 5 — Realtime boundary enforced (**promoted 2026-08-18: no longer optional**)

**Measured on the appliance 2026-08-18 — the Python meter costs ~30 points of peak DSP.**
Three 90 s runs at `512 x 3` while playing, `surge-xt-cli` CPU used as the matched-load
control. Runs A and C differ in one variable: whether `mpe-peak-meter` is on the graph.

| Run | Meter | surge CPU (control) | DSP median | DSP p90 | DSP max | samples >70% |
|---|---|---|---|---|---|---|
| A | **on** | 39% | 59.0 | 81.2 | **91.9** | **26** |
| B | off | 11% *(light play — confounded, discarded)* | 18.0 | 18.4 | 18.7 | 0 |
| C | **off** | 39% | 43.5 | 51.1 | **61.2** | **0** |

**The distribution proves the mechanism, not just the cost.** Between A and C the median
moves 15 points but p90 and max move **30**. A process that merely consumes CPU shifts a
distribution uniformly; this one barely lifts the floor and collapses the tail. That is
the signature of *intermittent blocking* — periods where the realtime callback waited on
the GIL held by the UI's `SCHED_OTHER` draw loop — and it explains why the crackle was
sporadic rather than constant.

**Consequences:**

- Phase 5 was filed as orthogonal, do-it-whenever. It is now the work standing between
  this appliance and low-latency operation, with a number attached.
- `512 x 3` is comfortable **without** the meter (max 61%) and marginal **with** it
  (max 92%). The buffer was never the fault; it was the margin hiding one.
- `MPE_PEAK_METER=0` is the live mitigation and is set on the appliance.
- This is a re-violation of [`DECISIONS.md`](../DECISIONS.md) 2026-08-13, *No Python on
  the JACK audio thread* — a rule this project wrote, then broke in PR #64. Criterion 33
  exists so the next violation fails a test instead of a gig.

**Why a ring buffer does not fix it.** The obvious remedy — RT side writes to a lock-free
ring, reader takes what is there or nothing — does not apply, because the blocking happens
*before* the callback's first instruction: `port.get_array()` requires acquiring the GIL.
A Python JACK callback can never be RT-safe, since GIL acquisition is an unbounded wait on
a lock owned by a non-realtime thread. Criterion 34 must be satisfied by a **compiled**
client (levels into shared memory, UI reads at its leisure) or by not being a JACK client
at all — never by making the Python callback cheaper.

| # | Criterion | Status | Evidence / what remains |
|---|---|---|---|
| 33 | Test fails on `set_process_callback` outside the allowlist | ✅ | `tests/test_jack_rt_boundary.py` fails on a planted violation; verified |
| 34 | OUT meter is out-of-process or compiled; allowlist empty | ✅ | `native/mpe-peak-meter` is a compiled JACK leaf client; allowlist empty |
| 35 | 128 × 3 under playing load, strict mode, zero xruns | ❌ **needs Mitch** | Appliance runs `1024 x 3`. `512 x 3` is clean at DSP median 42%, but some patches still need 1024 — see `docs/measurements/per-patch-headroom-open-2026-08-19.md`. `128 x 3` is untested |
| 36 | Any unit hosting a JACK client declares `LimitRTPRIO` | ✅ | Enforced over `config/*.service` by test |
| 36a | The meter's replacement is proven by the same A/B, not by inspection | ✅ | Matched-load A/B re-run with the compiled meter: 0% DSP cost, 0 xruns |

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

**Sequencing:** Phases 1–2 are **done** and soaking. **Phase 3M is next and unblocked**
(D17). Phase 4 for the looper stack remains gated on the SooperLooper adopt/kill verdict
(D15, [`DIRECTION`](../DIRECTION.md) B7/B8) and on resolving `MPE_LOOPER_ENABLED` guard
semantics (D16). Phase 5 (realtime boundary) proceeds in parallel — it is orthogonal to
looper ownership.

### Phase 0 (immediate — no reconciler)

Before Phase 1 ships, fix the live calibration regression (Evidence gap review):
`calibration_teardown.stop_mpe_audio_services()` must **stop and later restart** the
looper units (`mpe-sooperlooper`, `mpe-apc-bench`, `sl-hud-monitor`, `sl-watchdog`) —
not only Surge/touch/pressure/governor. This is an **interim D11 shim**: explicit
`systemctl stop`/`start` in calibration, not maintenance mode and not a reconciler.
Without it, `Restart=always` on looper units undoes calibration within 5 s.

**The Phase 3 gate is closed (D17, 2026-08-18): merge.** Phase 3M (criteria 38–49) is
the implementable path and is **ready to start**. Phase 3 (daemon, criteria 13–22) is
superseded and retained for the record.

Phase 3b (buffer change) remains one declared operation — implemented via the existing
promote path until Phase 4, not as a new intent bus. Phase 3M does **not** need D11
maintenance mode at Phase 4 scope: fewer supervised units to suppress during
calibration, and the flag written in Phase 0 already covers the calibration case.

**Phase 4 (reconciler) stays blocked** by D15 until the SooperLooper adopt/kill verdict
and by D16 until the guard-semantics decision. Phase 3M is permitted ahead of both —
see D17 for why.

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

> ✅ **Gate closed 2026-08-18: one and no — merge.** See D17 and Phase 3M. The
> falsification below still stands as the argument that was tested, not as an open
> question.
>
> **What would falsify the merge, once built:** if cross-process state bugs keep
> appearing *after* it, they will be between the merged looper process and the touch UI
> — patch state, normalization, volume. That is when the daemon earns its keep, with
> evidence instead of a prediction. Re-open Q1 at that point, not before.

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

1. **Q1 — Daemon or merge?** ✅ **ANSWERED 2026-08-18 (Mitch): merge.** See D17 and
   Phase 3M. The daemon is not rejected forever — it is deferred until a second
   instrument, a remote agent, or a non-pygame UI exists. Phase 3 (daemon, criteria
   13–22) is **superseded** by Phase 3M (criteria 38–49).
2. **Q2 — Snapshot publish interval.** The HUD writes at 2 Hz today. Is that the right
   rate for a general snapshot, or does the meter/transport need a faster lane?
   Measure before choosing. *(2026-08-18: partly answered by profiling — the interval is
   no longer the lever. Snapshot cost is 1.4 ms of real work plus ~56 ms of `systemctl`
   forks, so fixing liveness matters far more than choosing a rate. Once liveness is
   fork-free, 2 Hz costs ~0.3% and the question becomes a product one about how fresh
   the transport needs to look, not a budget one.)*
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
10. **Q10 — We cannot count xruns without arming the client-killer.** `jackd -s`
    (softmode) bundles two behaviours JACK does not let us separate: *do not zombify a
    client that misses a deadline* — which is correct for shipping, nobody loses a gig to
    one late period — and *do not report xruns at all*. We chose softmode for the first
    and inherited the second, so on the shipping configuration **the xrun channel is dead
    and `0 xruns` is indistinguishable from `not measured`**. This is the
    [`DECISIONS.md`](../DECISIONS.md) 2026-08-15 failure mode baked into the product
    default. Consequences and the open question:
    - Our only in-band evidence today is **DSP headroom sampled at 1 Hz** by
      `jack_cpu_load`. Crackle happens inside a 10.67 ms period, so a 1 Hz smoothed
      reading can show comfortable margin while individual periods still overrun.
      **This is why residual crackle can persist at DSP max 61% and we cannot see it.**
    - `bench-xruns.sh --strict` remains the only honest counter, and it is unusable while
      playing for real — strict mode is exactly the client-killer softmode exists to
      avoid.
    - **To investigate:** does JACK2 still invoke `jack_set_xrun_callback` under softmode?
      If it does, a tiny compiled client can count xruns continuously on the shipping
      configuration without arming strict mode — closing the blindness permanently. That
      client is the same shape as the Phase 5 compiled meter (criterion 34) and should be
      built alongside it. If it does not, we need a driver-level or ALSA-level counter, or
      we accept that the shipping default cannot self-report and say so in the docs rather
      than letting `0` read as `fine`.

## Rollback

Each phase is independently revertable.

- **Phases 1–2:** additive only. Delete the command and the event calls; nothing else
  referenced them.
- **Phase 3M (chosen):** reverts by restoring `mpe-apc-bench.service` and
  `sl-hud-monitor.service` and reverting the merge commits. Because the sequencing above
  lands one fold per commit, any single step reverts on its own. Keep the two retired
  unit files in git history reachable — do not squash the retirement into the merge.
  *(Superseded daemon variant: would have reverted by re-enabling the edges' local
  state, which is why that had to land in a separate commit from the owner.)*
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
