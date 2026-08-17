# Session control plane — one owner per fact, reconciliation over sequences

**Status:** Draft — 2026-08-17
**Author:** written after the 2026-08-17 stability session; every claim in Evidence is measured on `raspberrypi2`, not reasoned.

---

## Problem Statement

The appliance is a **distributed system** — eleven long-lived processes, shared
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

### D5 — systemd is the supervisor; lifecycle is declared once

Dependency and restart semantics live in units, not re-decided at each call site. Every
other entry point *defers* to the unit when installed. The three defer-guards added on
2026-08-17 are the compatibility shim for installs without units, not the design.

### D6 — Snapshot in `/run/mpe`, atomically written, versioned

Reuse `mpe_state_write_atomic`. Add a monotonic `version` and a `written_at`. A reader
that sees an unchanged `version` for longer than the publish interval knows the writer
is dead — the failure mode the HUD had for 47 s with nothing logged anywhere.

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

## Acceptance Criteria

### Phase 1 — Observability (no behaviour change)

| # | Criterion | Verification |
|---|---|---|
| 1 | `mpe state` emits one JSON snapshot: engine, graph, grid, loops, buffer/rate, health, per-service liveness | Run it; compare each field against the source of truth it aggregates |
| 2 | It aggregates *existing* truth only — writes nothing, owns nothing | `strace`/review: no writes outside stdout |
| 3 | Every field carries provenance (which file/process/probe it came from) | Field-level `source` key present for all |
| 4 | A stale sub-source is reported as stale, never as a value | Kill `sl-hud-monitor`; snapshot marks grid/transport stale within one publish interval, does not show last-known |
| 5 | The 2026-08-17 faults are visible in one command | Reproduce a leaked client and a dead engine; both appear without correlating other sources |

### Phase 2 — Event stream

| # | Criterion | Verification |
|---|---|---|
| 6 | Discrete events with stable names, one line each, structured | `engine.started`, `engine.exited`, `grid.established`, `grid.dropped`, `buffer.changed`, `client.registered`, `client.leaked` |
| 7 | The client leak is detectable from events alone | Replay 2026-08-17: `client.registered` without matching teardown, rising count |
| 8 | Events are cheap enough to leave on permanently | Measured CPU cost < 1% of a core at steady state, on the Pi |
| 9 | No polling probe may register a JACK client (the `jack_cpu_load` lesson) | Test asserts no per-sample process spawn on any repeating path |

### Phase 3 — Session owner (gated; see Falsification)

| # | Criterion | Verification |
|---|---|---|
| 10 | Grid state has exactly one writer; bench and HUD are readers | Grep: no `GridState` mutation outside the owner |
| 11 | `sync_source` sentinel is deleted | Absent from the tree |
| 12 | **Owner death does not stop audio** (D4) | `kill -9` the owner mid-playback; audio continues; HUD degrades visibly; owner restarts and re-derives state from the engine |
| 13 | Owner restart is stateless — it re-derives from engines, never from its own last snapshot | Delete the snapshot, restart owner, state matches engine truth |
| 14 | Intents are the only edge write path | No edge process sends OSC directly to SL/Surge |
| 15 | Musical behaviour unchanged | Pad-driven record/clear/grid-establish sequence identical before and after, verified by hand on the APC |

### Phase 4 — Reconciler

| # | Criterion | Verification |
|---|---|---|
| 16 | One reconcile loop replaces `restart-sooperlooper.sh`, `surge-watchdog`, and the graph-restart sequences | Those scripts deleted or reduced to thin CLI wrappers |
| 17 | Converges from arbitrary damage | Inject: kill engine, unwire graph, saturate registry, change buffer — each converges without operator action |
| 18 | Restart budgets survive the reconciler's own restart | Existing `/run/mpe` cooldown semantics preserved (spec D3, jack-audio-engine-spec) |

### Phase 5 — Realtime boundary enforced

| # | Criterion | Verification |
|---|---|---|
| 19 | Test fails on `set_process_callback` outside the allowlist | Add a violation; suite goes red |
| 20 | OUT meter is out-of-process or compiled; allowlist empty | `mpe-peak-meter` no longer a Python client |
| 21 | 128 × 3 under playing load, strict mode, zero xruns | `bench-xruns.sh --sweep --strict` while playing |

---

## Phasing

Phases 1 and 2 are **unconditional** — pure profit, no behaviour change, immediately
useful, and prerequisites for diagnosing anything after.

Phase 3 is **gated**: it requires an explicit decision between the daemon and the
cheaper alternative below. Phases 4–5 follow whichever is chosen.

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
fact at a time (grid first) and by criterion 15.

---

## Assumptions & Constraints

- Single appliance, single operator, LAN-only, no untrusted input.
- The Pi 4 has four cores; one is effectively reserved by the realtime plane under
  load. The control plane must fit in what remains alongside the touch UI (25 jiffies/s
  measured post-fix).
- SooperLooper's OSC contract is fixed and cannot be extended; `register_auto_update`
  delivering only on change is a permanent property to design around (D6, criterion 4).
- systemd is available and is the supervisor (D5).
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
   --force` rewriting it is a sequence, not a reconciliation.

## Rollback

Each phase is independently revertable.

- **Phases 1–2:** additive only. Delete the command and the event calls; nothing else
  referenced them.
- **Phase 3:** the merge variant reverts by restoring two units. The daemon variant
  reverts by re-enabling the edges' local state — which must therefore be *removed in a
  separate commit from* the owner landing, so the revert is one commit.
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
  Phase 5's criterion 21 depends on it; do not accept a softmode number.
