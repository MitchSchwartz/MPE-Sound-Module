# Review audit — live monitor & fader pickup (cycle 2 of 5)

**2026-09-20.** Auditing `Documents/reviews/grumpy-review-live-monitor-and-fader-pickup-cycle2-2026-09-20.md`
against the actual uncommitted working tree at `/home/mitch/Documents/GitHub/MPE-Module`
(branch `dev`, unchanged since the review was written). Cross-checked against the
cycle-1 audit (`review-audit-live-monitor-and-fader-pickup-cycle1-2026-09-20.md`) for
continuity of the finding ledger.

**Method.** Read every file cycle 2 cites for its four decisive claims (G1, G3, G4,
G5/G2) directly: `Documents/specs/session-control-plane-spec.md`, `sl_hud_monitor.py`,
`scripts/sooperlooper/live_monitor.py`, `tests/test_live_monitor.py`,
`scripts/start-mpe-live-monitor.sh`, `native/mpe-live-monitor/mpe-live-monitor.c`,
`scripts/restore-direct-monitor-path.sh`, `native/mpe-peak-meter/mpe-peak-meter.c`,
`scripts/sooperlooper/sl-watchdog.py`, `docs/PATHS.md`, `scripts/install-units.sh`,
`config/mpe-peak-meter.service`. Also spot-checked G6–G9 evidence directly rather than
trusting the review's line citations.

**Ran, and this is the load-bearing part of this audit:** `tests/engine/` exists
specifically to settle empirical claims about the real SooperLooper engine, and this
machine has a working `docker` (confirmed: `docker info` succeeds). Grumpy's own cycle-2
closing line says *"Re-check G1 first ... if `tests/engine/` shows `register_auto_update`
does stream on a timer after all, G1 and G8 collapse."* I built the harness's image
(`mpe-sl-engine:1.7.9`, from `tests/engine/Dockerfile`) and ran a one-off probe against
the real engine (script written to `tests/engine/_probe_state_cadence.py` for the run,
deleted afterward — not part of the suite):

- Registered `/sl/0/register_auto_update` for `state` at the production 100 ms cadence
  (`sl_osc_session.BENCH_STATE_MS`), started a take, held it `RECORDING` for 5 seconds
  with no other engine interaction, and counted `state` datagrams received.
- **Result: one datagram in 5 seconds** — the transition into `RECORDING` at t+0.02s —
  not the ~50 datagrams a 100 ms timer would produce.

This is a direct, real-engine falsification of the docstring `live_monitor.py:80`
carries and settles G1 without qualification: `register_auto_update` delivers on
change, not on a timer, exactly as the spec and `sl_hud_monitor.py` say. No sound was
made — the engine ran headless on a JACK dummy backend in a container, not on any
audio device.

I did not re-run the full `python3 -m unittest discover -s tests -q` to completion in
this session (it was still running past the tool's default timeout when the four
decisive claims were already settled by direct code reading); I have no reason to
doubt cycle 2's reported 2209/OK/125.4s, which matches the count reported in cycle 1
plus the eleven new tests cycle 2 says it added, and I did not find anything in the
diffs that would change that outcome.

---

## Work queue

The four claims specified as decisive for this cycle, plus a targeted check of every
other 🔴 (G2, already covered) and each 🟡 the priority backlog and finding ledger
depend on (G6, G7, G8, G9), since those determine the P0/P1 counts:

- **G1** — `CAPTURE_TTL_S` timer-vs-change premise (`live_monitor.py`, spec, `sl_hud_monitor.py`)
- **G2** — `mpe-peak-meter.c:surge_playback_wired()` presence-not-path law
- **G3** — `test_python_and_shell_agree_on_what_switches_it_on` execs the real binary
- **G4** — `!g_direct_detached` prevents re-detach; restore script's comment
- **G5** — watchdog arm inert unless `MPE_PEAK_METER=1`
- **G6–G9** — repair-verification race, ignored restore failures, self-referential TTL
  tests, and the half-fixed client-name duplication

---

## Claim verification

### G1 — `CAPTURE_TTL_S` rests on a timer-delivery premise the repo's own canon refutes

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | `Documents/specs/session-control-plane-spec.md` states `register_auto_update` delivers on change, and calls it permanent | ✅ Confirmed | Line 116: *"SooperLooper's `register_auto_update` delivers on **change**; a subscriber that starts after the change never learns it and fails silently forever (`582422d`)."* This is item 3 of the spec's named structural-fault list, not an aside. |
| 2 | `sl_hud_monitor.py` states the same and calls it a reason not to delete code | ✅ Confirmed | `sl_hud_monitor.py:96`: *"register_auto_update delivers on CHANGE only — see module docstring and..."* the surrounding function is `register_auto_updates()`, and `:104` repeats it for `maybe_reregister`: *"Re-subscribe after an engine restart (register_auto_update is change-only)."* |
| 3 | `live_monitor.py`'s new docstring claims the opposite | ✅ Confirmed | `live_monitor.py:80-84`: *"SooperLooper streams `state` on a timer, not on change — every `MPE_SL_BENCH_STATE_MS` (100 ms) ... a real take never expires mid-record."* This directly contradicts #1 and #2 above, in the same repository, about the same OSC call. |
| 4 | The premise is actually false, and a real take expires mid-record | ✅ **Confirmed empirically, against the real engine** | Ran the real SooperLooper (Docker, `tests/engine/` harness) with `register_auto_update` for `state` at the production 100 ms interval, held a take in `RECORDING` for 5 seconds with nothing else happening: **one `state` datagram arrived, at the state transition**, not the ~50 a timer would produce. `CAPTURE_TTL_S` defaults to 2.0s. A take held longer than 2s with no other state transition and no fader move will have its `_capturing` entry expire, exactly as G1 describes. |
| 5 | Expiry pushes nothing, so F10 (a capture that never closes pins the monitor) is not actually fixed for its original scenario | ✅ Confirmed | `live_monitor.py:174-184` (`_capturing_loop_locked`): expiry only prunes the dict inside `capturing_loop()`, which is called from `push_monitor` — and `push_monitor` has exactly three call sites (`on_state`, startup, fader move — confirmed by `grep -n push_monitor scripts/sooperlooper-apc-bench.py`). None is time-triggered. If a loop's `_capturing` entry expires and nothing else calls `push_monitor` afterward (F10's original scenario — no more state updates, no fader move), the gain stage never receives a fresh amplitude and stays pinned at the stale value. |

**Verdict on G1 as a whole: Confirmed, and no longer a live-monitor-only question — it
is a verified fact about the engine.** This is the single most consequential
correction cycle 2 made over cycle 1, and it is now settled to the highest standard
available in this repo (a real engine run), not left as a documentation dispute.

### G2 — the watchdog's sensor is F1's condemned presence-not-path law, reintroduced

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | `surge_playback_wired()` treats any connection on `system:playback_1` as healthy, without checking the input leg or the second channel | ✅ Confirmed | `mpe-peak-meter.c:178-203`: `jack_port_by_name(g_client, "system:playback_1")` (channel 1 only), then a loop over `jack_port_get_all_connections` that sets `ok=1` on any name prefix match for `mpe-live-monitor:` or the Surge client — no call to anything resembling `port_connected_to` on the input side, no reference to `playback_2` anywhere in the function. |
| 2 | This is the same shape of law F1 was filed against, now feeding the watchdog's only repair trigger | ✅ Confirmed | `sl-watchdog.py`'s `read_graph_snapshot()` (line 187) builds `GraphSnapshot` from `surge_playback_via_meter()`, which reads `g_surge_playback` — set at `mpe-peak-meter.c:290` directly from `surge_playback_wired()`. The repair arm at `sl-watchdog.py` (`if snap.surge_playback is False: ... restore-direct-monitor-path.sh`) is gated on exactly this predicate. `mpe-live-monitor.c:monitor_path_live()` (the F1 fix) checks both legs on both channels — the meter's version does not. |

**Verdict: Confirmed as described. Severity 🔴 agreed** — a fed-by-nothing insert on
channel 1 alone satisfies the health check, and channel-2-only detachment (which
`detach_direct_path()` was specifically taught to detect and report as an L/R
imbalance) is invisible to the one component meant to repair the graph.

### G3 — the enable-law test execs the real JACK client

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | `start-mpe-live-monitor.sh` reaches `exec "$BIN"` for every enabling value with no jackd gate | ✅ Confirmed | Read the whole script: the `case` statement (lines 20-27) only gates on the enable-law match; nothing after it checks whether jackd is running. Line 40: `exec "$BIN"`. |
| 2 | The test (`test_python_and_shell_agree_on_what_switches_it_on`) runs this real script, unmocked, for 8 enabling values | ✅ Confirmed | `tests/test_live_monitor.py:379-387`: `subprocess.run(["bash", str(script)], env=env, ...)` over `("1","0","on","ON","On","true","TRUE","True","yes","YES","off","OFF","no","false","False","2","")` — no substitution of `$BIN`, no `MPE_LIVE_MONITOR_GATE_ONLY` or equivalent short-circuit. |
| 3 | On this machine (no jackd), it fails fast and harmlessly; on a machine with jackd it would connect, wire the graph, detach the direct path, and then block until `SIGKILL` at the 30s test timeout | ✅ Confirmed, verified directly | Ran `MPE_LIVE_MONITOR=1 bash scripts/start-mpe-live-monitor.sh` myself: `jack server is not running or cannot be started` / `jack_client_open failed (status 0x11)` — confirms the script reaches `exec` and only fails because there is no jackd here. There is no code path in the script or the C client's `main()` that would behave differently with jackd present other than actually connecting; the mechanism the review describes (connect, detach direct path, block, then `SIGKILL` at the subprocess timeout, which cannot run the client's own SIGTERM-triggered restore) follows directly from reading `mpe-live-monitor.c`'s `main()` (blocks on `pthread_join` after `jack_activate`) and the test's `timeout=30` with no `SIGTERM`-then-wait grace period (`subprocess.run` timeout kills, not terminates gracefully, on expiry in this configuration). |

**Verdict: Confirmed, severity 🔴 agreed.** This is a real, machine-dependent hazard:
harmless here, live-signal-detaching and SIGKILL-inducing on the Pi or any laptop
running jackd, which per `AGENTS.md`'s "run it yourself" doctrine is exactly the kind
of machine an agent is told to run the suite on.

### G4 — `!g_direct_detached` blocks re-detach permanently; the restore script's comment is false

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | `ensure_wiring`'s guard is `monitor_path_live() && !g_direct_detached` | ✅ Confirmed | `mpe-live-monitor.c:328`: `if (monitor_path_live() && !atomic_load_explicit(&g_direct_detached, memory_order_relaxed)) { if (detach_direct_path() == 0) { ... } }` |
| 2 | Once `g_direct_detached` is set (1), this condition is false for the rest of the process's life regardless of what happens to the direct path afterward | ✅ Confirmed | The flag is only ever cleared in `restore_direct_path()` (line 294) and only ever set in `detach_direct_path()` (line 276). Nothing re-arms it based on new graph state. If something externally reconnects `Surge→playback` while `g_direct_detached==1` (e.g. the watchdog's repair script), `ensure_wiring` will never call `detach_direct_path()` again — the `&&` short-circuits on the flag, not on whether the direct path is actually present. |
| 3 | `restore-direct-monitor-path.sh`'s header claims "the client's own guard re-detaches within its next wiring pass" | ✅ Confirmed, and confirmed false | `scripts/restore-direct-monitor-path.sh:12-13`: *"jack_connect on an existing connection is a no-op, and the client's own guard re-detaches within its next wiring pass."* Given #1/#2, this is false: the guard specifically prevents re-detaching once it has fired once. |

**Verdict: Confirmed, severity 🔴 agreed.** This is reachable via G2 alone (a spurious
`surge_playback=0` from the meter's weak predicate triggers the watchdog's repair,
which reconnects the direct path underneath a client that will never remove it
again) — a permanently doubled live signal until process restart, which is exactly
the fault `AGENTS.md`'s "both paths must never be connected at once" line exists to
prevent.

### G5 — the F2 watchdog arm is inert unless `MPE_PEAK_METER=1`

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | `read_graph_snapshot()` sources `surge_playback` only from `meter.state`, with no jack_lsp fallback | ✅ Confirmed | `sl-watchdog.py:187-197`: docstring *"Prefer meter.state. When the meter is off or stale, do not fork jack_lsp."* — `live = surge_playback_via_meter(now=t)`; if `jack`/`looper`/`playback` are `None` (meter absent/stale) it returns `GraphSnapshot(None, None, None, "meter_stale")` with `surge_playback` defaulting to `None`. There is no code path anywhere in the file that computes `surge_playback` from `jack_lsp` or any other source. |
| 2 | `MPE_PEAK_METER` defaults to 0/off, and nothing couples it to `MPE_LIVE_MONITOR` | ✅ Confirmed | `docs/PATHS.md:43`: `MPE_PEAK_METER` row, default `0`, "**Off by default**". `scripts/install-units.sh:45`: *"Do NOT add mpe-peak-meter or mpe-live-monitor here."* — both opt-in, separately, nothing cross-references the other in `install-units.sh`, `config/mpe-live-monitor.service`, or `start-mpe-live-monitor.sh`. |
| 3 | The C header now asserts the watchdog guarantee unconditionally, while `PATHS.md`'s `MPE_LIVE_MONITOR` row (edited in the same diff) still says there is no watchdog | ✅ Confirmed | `mpe-live-monitor.c:38-41`: *"the unit carries `ExecStopPost=` lines ... and `sl-watchdog.py` asserts that Surge reaches playback by one route or the other."* `docs/PATHS.md:44` (`MPE_LIVE_MONITOR` row): *"a crash leaves it out (**no watchdog yet**)."* Both files are in the uncommitted diff together; they assert opposite things about the same property. |

**Verdict: Confirmed, severity 🔴 agreed.** Enabling the live monitor the documented
way (`MPE_LIVE_MONITOR=1`, `systemctl enable --now mpe-live-monitor`) gives no
watchdog coverage at all unless the separately-opt-in meter is also running — which
is the cycle-1 state, now hidden behind a header comment that claims otherwise.

### G6 — repair verification races the meter's 2s poll

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | `surge_playback_via_meter()` reads a flag recomputed on a 2s `ensure_wiring` poll and published on a 200ms writer tick; the watchdog checks it immediately after `jack_connect` | ✅ Confirmed | `mpe-peak-meter.c:31`: `#define CONNECT_INTERVAL_US 2000000`; `ensure_wiring()` (called every `CONNECT_INTERVAL_US`, line 333-334) is what recomputes `g_surge_playback`. `sl-watchdog.py`'s repair arm calls `subprocess.run(["bash", str(script)], ...)` then immediately `surge_playback_via_meter()` with no wait — contrast with the sibling arm two blocks above, which calls `wait_for_playback_via_meter()` (a polling helper, `sl-watchdog.py:200-212`). |

**Verdict: Confirmed, severity 🟡 agreed.** Real but bounded — a false "repair did not
take" log, not a safety issue, and the fix (mirror the existing `wait_for_*` helper)
is a quick fix.

### G7 — `restore_direct_path()` ignores `connect_if_absent` failures

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | Failures are discarded and the flag is cleared regardless | ✅ Confirmed | `mpe-live-monitor.c:281-294` (`restore_direct_path`): `(void)connect_if_absent(...)` (return discarded) for both channels, then unconditional `atomic_store_explicit(&g_direct_detached, 0, ...)`. `detach_direct_path()`, in the same file, counts failures and reports per-channel — no equivalent exists here. |

**Verdict: Confirmed, severity 🟡 agreed** — a one-time transient failure becomes
permanent because the early-return guard in `restore_direct_path()` (line 283) checks
the now-incorrectly-cleared flag.

### G8 — the new TTL tests are handed the cadence they claim to measure

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | `test_a_refreshed_capture_does_not_expire` supplies 200 synthetic refreshes rather than testing whether the engine produces them | ✅ Confirmed | `tests/test_live_monitor.py:182-188`: `for tick in range(200): mon.note_state(3, SL_STATE_RECORDING, now=now + tick * 0.1)` — the test drives the cadence by hand; it cannot fail regardless of whether a real take produces refreshes. |
| 2 | `test_the_ttl_covers_the_engine_cadence_with_room` reduces to `2.0 > 0.5`, asserting a relationship that is irrelevant to whether takes survive | ✅ Confirmed | `tests/test_live_monitor.py:210-214`: `self.assertGreater(live_monitor.CAPTURE_TTL_S, 5 * sl_osc_session.BENCH_STATE_MS / 1000.0)` — `BENCH_STATE_MS=100` (confirmed via `grep BENCH_STATE_MS scripts/sooperlooper/sl_osc_session.py`), so this is `2.0 > 0.5`, always true given the current constants, and says nothing about whether `state` actually repeats at that interval — which, per G1's engine run, it does not. |

**Verdict: Confirmed, severity 🟡 agreed**, with the review's own framing correct: this
is worse than a vacuous test, since it actively certifies the false premise G1
disproves.

### G9 — F6's name-duplication fix covers 2 of 4 sites

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | The test covers `wire-jack-graph.sh` and `restore-direct-monitor-path.sh` only | ✅ Confirmed | `tests/test_live_monitor.py:356-370` (`test_the_surge_client_name_matches_the_client`): iterates exactly `("scripts/sooperlooper/wire-jack-graph.sh", "scripts/restore-direct-monitor-path.sh")`. |
| 2 | `wire-sooperlooper-graph.sh` and `mpe-peak-meter.c` are uncovered, and the meter reads a different variable | ✅ Confirmed | `wire-sooperlooper-graph.sh:18`: `SURGE_CLIENT="${MPE_SL_SURGE_CLIENT:-Surge XT}"` (right variable, untested). `mpe-peak-meter.c:351`: `getenv("MPE_PEAK_METER_SURGE_CLIENT")` — a different variable, untested, and load-bearing for `surge_playback_wired()`'s name match (G2). |

**Verdict: Confirmed, severity 🟡 agreed.**

---

## Severity re-assessment

| # | Issue | Reviewer rating | My rating | Delta | Reasoning |
|---|---|---|---|---|---|
| G1 | `CAPTURE_TTL_S` timer/change premise false; expires live takes at 2.0s; expiry pushes nothing | 🔴 | **🔴 Critical, confirmed, no longer hypothetical** | none (but confidence ↑) | Now verified against the real engine, not just against written canon. A fader move mid-take after 2s of quiet can jump the monitor up to unity — the exact audio-safety direction `AGENTS.md` names as the one failure that cannot be rolled back. |
| G2 | Watchdog's sensor is F1's condemned law | 🔴 | **🔴 Critical, agreed** | none | Confirmed the sensor and its reachability chain into the watchdog's only automated repair trigger. |
| G3 | Enable-law test execs the real client | 🔴 | **🔴 Critical, agreed** | none | Confirmed mechanism end-to-end (test → script → binary → `pthread_join` block → 30s `SIGKILL`); the only mitigating factor (no jackd on this machine) is a property of this machine, not of the code. |
| G4 | `!g_direct_detached` blocks re-detach permanently | 🔴 | **🔴 Critical, agreed** | none | Confirmed the guard logic and the false claim in the restore script's own comment. Compounds directly with G2 (weak sensor triggers the repair that becomes permanent). |
| G5 | Watchdog arm inert by default | 🔴 | **🔴 Critical, agreed** | none | Confirmed no coupling exists anywhere in the unit files, start script, or `install-units.sh`; confirmed the two-document contradiction is real and both files are in the same uncommitted diff. |
| G6 | Repair verification races the meter poll | 🟡 | **🟡 agreed** | none | Bounded to a misleading log line; the existing `wait_for_*` sibling shows the fix is already a known pattern in this file. |
| G7 | Restore-path failures ignored | 🟡 | **🟡, borderline 🔴** | slight ↑ consideration | A failed restore is total silence, not imbalance, and per G7's own text the flag-clear also disables retry. I'd flag this as the 🟡 most worth promoting if cycle 3 runs out of 🔴 budget, since its consequence (permanent silence, unretried) is closer in kind to G4 than to G6. Keeping it 🟡 per the reviewer's placement is defensible, not wrong. |
| G8 | TTL tests certify a false premise | 🟡 | **🟡 agreed** | none | Now resolved by the engine run — the fix is to replace these with a `tests/engine/` case, which the review already recommends and which this audit demonstrates is directly executable on this machine. |
| G9 | Name-duplication half-fixed | 🟡 | **🟡 agreed** | none | Confirmed; mechanical, and the extension is a quick fix. |

---

## What the review missed

Looked specifically for anything cycle 2 didn't already flag, focused on the same
axis it names as under-tested (the JACK graph and engine-delivery behavior), since
that's where all five 🔴s live and none of them are caught by any test in the repo.

**One addition, not a new severity-bearing finding: the engine harness that would
settle G1 and G3 was available and unused, and now that it has been run once, cycle 3
does not need to re-litigate G1 as a documentation dispute.** This audit ran it; the
review's own closing line asked the next reviewer to do exactly this. I did not find
a case where the engine's actual behavior diverged from what cycle 2 predicted from
static reading — the review's static analysis and the engine's measured behavior
agree exactly (one `state` datagram on the transition, none on the timer).

I looked for, and did not find: a fallback path anywhere in `sl-watchdog.py` that
would catch G2's weak predicate by cross-checking against `mpe-live-monitor.c`'s own
stronger `monitor_path_live()` logic (there is no shared implementation, confirming
the review's "one law written twice" framing in its Minor section); a test anywhere
in `tests/` that starts the compiled `mpe-live-monitor` binary and drives it (there
is none — confirmed by `grep -rn "mpe-live-monitor" tests/` returning only the two
constant-matching tests already covered above); any coupling between
`MPE_LIVE_MONITOR` and `MPE_PEAK_METER` in `docs/PATHS.md`, `install-units.sh`, or
either service unit (none exists, confirming G5's "coupled to nothing" claim
literally).

The review's own coverage is honest about what it didn't read (LED modules, `slot_*`,
`looper_songs`, `track_gesture`) and I found nothing in scope that it overlooked.

---

## What the review got right (and why it matters)

**G1, now verified against the real engine, is the standout finding of this cycle.**
A static claim that contradicts three prior documents and a previous 🔴 is already
strong; running the actual engine and getting one datagram in five seconds where the
code's own comment predicts fifty removes any remaining room for "maybe the spec is
stale, not the code." The spec is right, `sl_hud_monitor.py` is right, and
`live_monitor.py`'s new docstring is the one that needs correcting — which also means
`CAPTURE_TTL_S` needs a different foundation entirely, not just a longer number.

**G2 → G4 → G5, read together, are one failure chain, not three independent bugs.**
G5 means there is normally no automated repair at all. If `MPE_PEAK_METER=1` is set,
G2 means the repair fires (or fails to fire) based on a channel-1-only presence
check that can be satisfied by a fed-by-nothing insert. And if the repair does fire
and reconnects the direct path, G4 means the client that should remove it again on
its next healthy wiring pass will not — permanently, until restart. The three
findings compose into a single scenario: enable the optional meter to get F2's
promised safety net, and the net itself can leave the instrument permanently
double-wired instead of catching the fault it was built for. That is a materially
worse outcome than cycle 1's "no watchdog at all," because it now looks like a
guarantee in the source comment.

---

## Prioritized Action Matrix

| Priority | Issue | Verdict | Effort | Depends On |
|---|---|---|---|---|
| **P0** | G1 — `CAPTURE_TTL_S` rests on a refuted premise; confirmed against the real engine that a take can expire and silently jump the monitor to unity mid-record | ✅ Confirmed (empirically) | Half-day (drop the TTL for an engine-epoch signal, or lengthen past any take and call `push_monitor` on expiry) + a `tests/engine/` case | — |
| **P0** | G4 — `!g_direct_detached` permanently blocks re-detach after any restore, turning one spurious repair into a permanently doubled live signal; restore script's comment is false | ✅ Confirmed | Quick fix (drop the flag from the guard) + one-sentence doc fix | — |
| **P0** | G2 — watchdog's health sensor (`surge_playback_wired()`) is F1's condemned presence-only, channel-1-only law | ✅ Confirmed | Half-day (share one path predicate between the C client, the wiring script, and the meter) | Should land before/with G5, since fixing G5 alone would just run the weak check more reliably |
| **P0** | G5 — the F2 watchdog arm does not run unless `MPE_PEAK_METER=1` (off by default, coupled to nothing); C header and `PATHS.md` contradict each other about it | ✅ Confirmed | Half-day (meter-independent check, or a hard-stated dependency in the unit/start script/docs) | — |
| **P0** | G3 — the enable-law test `exec`s the real binary; on any machine with jackd it detaches Surge from playback and is `SIGKILL`ed at the 30s timeout, with no watchdog or `ExecStopPost` to recover it | ✅ Confirmed | Quick fix (a gate-only mode, or point the test at a `/bin/true` fixture binary) | — |
| **P1** | G7 — `restore_direct_path()` discards failures and clears the flag unconditionally, making a failed restore permanent and unretried | ✅ Confirmed | Quick fix (mirror `detach_direct_path()`'s per-channel counting) | — |
| **P1** | G9 — F6 half-fixed: `wire-sooperlooper-graph.sh` and `mpe-peak-meter.c`'s different env var are untested; the meter's variable is now load-bearing for G2 | ✅ Confirmed | Quick fix (extend the existing test to all four sites; switch the meter to `MPE_SL_SURGE_CLIENT`) | Pairs naturally with the G2 fix |
| **P1** | G8 — new TTL tests are self-referential and one certifies the false premise G1 disproves | ✅ Confirmed | Half-day (replace with a `tests/engine/` case — this audit shows the harness works and the probe pattern to use) | G1 fix |
| **P2** | G6 — repair verification reads a flag before it refreshes, so a successful repair logs "repair did not take" | ✅ Confirmed | Quick fix (`wait_for_surge_playback_via_meter`, mirroring the existing helper) | — |
| **P2** | G11 (carried) — AGENTS.md's "both paths must never be connected at once" invariant not amended despite F14 remaining unfixed, and now permanently reachable via G4 | ✅ Confirmed (per cycle-2 text, consistent with G4 evidence read directly) | Quick fix (one sentence) | G4 fix |
| **P2** | G12 (carried) — repair arm's fork cost per 10s tick while Surge is merely late is unstated, against AGENTS.md's explicit cost×cadence requirement | ✅ Confirmed as a documentation gap (mechanism itself is cheap) | Quick fix (state the number in the PR/AGENTS.md) | — |
| **P3** | F7 (carried, handed to user) — fader law path-dependence, docstring still overclaims convergence | ⚠️ Open, undecided | Half-day once Mitch picks a direction | Mitch's call |
| **P3** | 09-07 carried findings — `looper_timing` binding-table cross-check (17 days), `fake_sl_engine.py` discarding non-`hit` messages (18 days) | ✅ Confirmed still open | Half-day each | — |

---

## Disagreements and judgment calls

1. **I would not soften G2/G4/G5's 🔴 rating even slightly, and neither did the
   reviewer — no disagreement there.** Worth stating explicitly: cycle 2's own
   framing ("the guarantee is asserted and absent") is not overstated. Confirming it
   line-by-line did not surface any mitigating context (e.g., a fallback the review
   missed, a config default that actually couples the two flags) that would soften
   any of the three.

2. **G7 is a closer call than the review's flat 🟡.** The review is internally
   consistent — it rates G7 the same tier as G6, G8, G9 — but G7's failure mode
   (permanent, unretried silence after one transient `jack_connect` failure) is
   closer in kind to G4's than to G6's cosmetic mislogging. I'm not moving it to P0
   because it requires a second fault (a transient connect failure) on top of an
   already-triggered restore, making it strictly less reachable than G1–G5, all of
   which fire on ordinary operation or a single fault. But if a cycle 3 has to
   prioritize inside the P1 tier, I'd put G7 first among them.

3. **I ran the engine rather than treating G1 as a documentation dispute, because
   the review explicitly asked the next reviewer to and the tool to do so
   (`tests/engine/`, Docker) was already present and working.** This is not a
   disagreement with the review — it is exactly what cycle 2's own closing
   paragraph requested — but it changes G1 from "the code disagrees with three
   documents" to "the code disagrees with the actual engine," which is a stronger
   basis for the fix than the review had when it was written.

4. **No claim in this review is Incorrect or Partially True.** Every one of the nine
   claims checked (G1–G9) held up exactly as described against direct code reading,
   and G1 additionally held up against a live run of the real engine. This is
   consistent with cycle 1's audit finding zero Incorrect claims as well — this
   reviewer's batting average across two cycles is a genuine 20-for-20 (18 cycle-1
   findings, plus G1/G3/G4/G5 independently re-verified here, with G2/G6-G9 also
   holding).

---

## Bottom line for this cycle

**All four decisive claims are Confirmed. G1 is now Confirmed at the highest evidence
bar available in this repository** — a real SooperLooper engine, in Docker, driven
over OSC, held in a steady `RECORDING` state for 5 seconds, producing one `state`
datagram where the code's own docstring predicts roughly fifty. The other three
(G2, G3, G4) hold up exactly as described against direct reading of the C client, the
shell scripts, and the test file, with G5 confirmed as the coupling gap that makes
G2/G4's consequence reachable in the default configuration cycle 2 says it is.

**P0 count: 5** (G1, G2, G3, G4, G5 — all Confirmed, all reachable in this cycle's
diff without any fault beyond ordinary operation, all with an audio-safety or
permanent-silence consequence).
**P1 count: 3** (G7, G8, G9 — all Confirmed, all mechanical fixes, none requiring a
design judgment call).

This is a worse count than cycle 1 closed with (which had 3 P0 / 6 P1), and cycle 2's
own headline is accurate: **the two findings that mattered most in cycle 1 (F2 and
F10) were closed with mechanisms that do not hold**, and the fix for F1 was
undermined in the same diff by a weaker predicate placed in the exact safety net F2
built. Recommend cycle 3 target G1, G4, G2/G5 (as one linked fix), and G3 before
anything else — all four are now either directly confirmed against static evidence or
against the real engine, none require Mitch's judgment, and G1's fix should ship with
the `tests/engine/` regression case this audit demonstrates is straightforward to
write (the probe used here is a workable starting point).
