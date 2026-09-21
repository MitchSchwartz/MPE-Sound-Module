# Grumpy review — live monitor & fader pickup, cycle 2

**2026-09-20.** Branch `dev`, working tree still uncommitted: 14 modified files
and 8 new ones (the cycle-1 fixes added `scripts/restore-direct-monitor-path.sh`
and a `native` CI job, and pulled `mpe-peak-meter.c`, `audio_engine.py`,
`sl-watchdog.py` and `.github/workflows/test.yml` into scope).
`docs/CLASSIC-MIDI-PLAN.md` remains out of scope and was not read.
**Still nothing on the hardware; the Pi is unreachable.**

**Read in full this cycle:** `native/mpe-live-monitor/mpe-live-monitor.c` (whole
file, re-read after the fixes), `scripts/sooperlooper/live_monitor.py`,
`tests/test_live_monitor.py`, the `loop_mix.py` / `test_loop_mix.py` diffs,
`config/mpe-live-monitor.service`, `scripts/restore-direct-monitor-path.sh`,
`scripts/start-mpe-live-monitor.sh`, the diffs to `mpe-peak-meter.c`,
`patch_browser/audio_engine.py`, `sl-watchdog.py`, `wire-jack-graph.sh`,
`install-units.sh`, `sooperlooper-apc-bench.py`, `sl_bench_listener.py`,
`.github/workflows/test.yml`, `AGENTS.md`, `docs/PATHS.md`. **In part:**
`sl_osc_session.py` (registration and cadence), `sl_hud_monitor.py`,
`Documents/specs/session-control-plane-spec.md`, three prior reviews.
**Not read:** LED modules, `slot_*`, `looper_songs`, `track_gesture`.

**Ran:**

- `python3 -m unittest discover -s tests -q` → **2209 tests, OK (skipped=23),
  125.4 s.** Matches the claim handed to me.
- The CI `native` job's exact command over all three `native/*/` dirs with
  `CFLAGS="-O2 -Wall -Wextra -Werror -std=c11"` → **all three compile clean,
  including `mpe-xrun-probe`**, so the new gate is green and not about to fail
  on an unrelated file.
- `MPE_LIVE_MONITOR=1 bash scripts/start-mpe-live-monitor.sh` → it `exec`s the
  real JACK client (see G3).
- `LiveMonitor` + `LoopMix` driven directly to measure the TTL's behaviour
  during a take (see G1).

Nothing touched the appliance and nothing made sound.

---

## Step 0 — the fate of the cycle-1 findings

Eighteen findings, all four 🔴 and thirteen of fourteen 🟡 were worked. That is a
real result and the ledger is why it was cheap to check. But **three of the
closures moved the failure rather than removing it**, and one of those is the
watchdog that F2 existed to get.

| # | Cycle-1 finding | Mark | Evidence now |
|---|---|---|---|
| F1 | Graph guards key on presence / output leg | **CLOSED — and the class reopened elsewhere** | `mpe-live-monitor.c` now has `input_leg_live()` + `output_leg_live()`, `monitor_path_live()` is their conjunction; `wire-jack-graph.sh:live_monitor_carrying()` tests both legs on both channels with `jack_lsp -c`. Both correct. **But `mpe-peak-meter.c:surge_playback_wired()`, added in the same diff, answers the same question by presence of any connection on `system:playback_1`** — no input leg, channel 1 only — and it is the predicate the new watchdog repairs from → **G2** |
| F2 | A crash strands the graph; no `ExecStopPost`, no watchdog arm | **HALF-CLOSED** | `ExecStopPost=-…/restore-direct-monitor-path.sh` is real and covers SIGKILL/segfault — the cheap half is done properly. The watchdog arm exists (`sl-watchdog.py:557-585`) but **is inert unless `MPE_PEAK_METER=1`**, which is off by default and coupled to nothing → **G5**; its success check races the meter's 2 s refresh → **G6**; and it keys on the weak predicate → **G2** |
| F3 | `jack_client_open` without `JackUseExactName` | **CLOSED** | `main()`: `JackNoStartServer \| JackUseExactName`, `jack_status_t` read, explicit `JackNameNotUnique` refusal with a message naming the doubled-signal reason |
| F4 | Nothing executable checks the C | **MOSTLY CLOSED** | New `native` CI job compiles all `native/*/` with `-Werror`; I ran it, clean. `test_the_control_port_matches_the_client` now regexes `CONTROL_PORT_DEFAULT` out of the `.c`; `test_the_surge_client_name_matches_the_client` regexes `SURGE_CLIENT_DEFAULT` and checks two shell files. **Still no test exercises the binary's behaviour** — `parse_gain`, `clamp_gain`, the ramp, the detach ordering and both safety properties remain unenforced. The enforced surface is "it compiles" and "two constants agree" |
| F5 | Four laws for "is the monitor on" | **CLOSED, well** | `live_monitor.enabled()` is the law; the shell lowercases and matches the same four words; `test_python_and_shell_agree_on_what_switches_it_on` runs the **real script** over 17 values. This is the right shape of fix. It also introduced **G3** |
| F6 | Port and Surge client name duplicated | **PORT CLOSED · NAME HALF-CLOSED** | The port test reads the `.c`. The name test covers 2 of 4 sites; `wire-sooperlooper-graph.sh:18` is uncovered and **`mpe-peak-meter.c:351` still reads a different variable (`MPE_PEAK_METER_SURGE_CLIENT`)** — which the cycle-1 review named and which is now load-bearing for the watchdog → **G9** |
| F7 | Fader law is path-dependent; docstring claims convergence | **OPEN — 0 days, handed to user, no decision recorded** | `loop_mix.py:33-35` still reads "the two positions converge as you play" with no mention of route-dependence. `_scaled_level`'s docstring is now honest about *step size* but silent about *path*. No option chosen, nothing written down |
| F8 | "It never jumps" is false by 15 dB | **CLOSED** | Replaced with "It does not jump on the grab … a single CC can still move the level a long way … MEASURED against this module, 2026-09-20: -32.1 dB to -17.0 dB … sparse CCs on a fast drag are ordinary, not an edge case." The claim is now true and the number is in the source. The step is still unbounded — stated, not hidden |
| F9 | Unlocked cross-thread mutation | **CLOSED for the two objects named** | `LiveMonitor._lock` guards `note_state`/`capturing_loop`; `LiveMonitorSender._lock` makes dedup-and-send one critical section; two tests hammer both from four threads. Residual: `push_monitor` reads `mix.wet_for` (a `LoopMix` the main loop mutates) from OSC threads, unlocked — pre-existing class, new reader |
| F10 | A capture that never closes pins the monitor | **MOVED — and it is now worse** | The TTL is founded on a claim this repo has designated a permanent property *in the opposite direction*, and expiry pushes nothing → **G1** |
| F11 | `ECONNREFUSED` swallowed | **CLOSED, well** | Connected socket, `refused`/`refused_total`/`delivered`, time-rate-limited complaint, recovery streak, and tests that use a genuinely closed port rather than a mock. The docstrings explain why counting consecutive refusals failed. Residual → **G10** |
| F12 | Client silent in the journal | **MOSTLY CLOSED** | Lines on detach, on restore, on "Surge is not connected to our input" (edge-triggered) and on recovery. `MPE_RUN_DIR` export deleted with the reason. Gain values still unlogged in C; the bench logs them behind `MPE_APC_FADER_LOG` (default on) |
| F13 | Futile `jack_disconnect` every 2 s | **CLOSED, and it moved a bug** | `detach_direct_path()` now tests each connection before removing it. But `ensure_wiring` *also* gained `&& !g_direct_detached`, which stops the client re-detaching a direct path restored underneath it → **G4** |
| F14 | Both paths live during handover; return values ignored | **HALF-CLOSED** | Return values now counted and logged per channel with an L/R-imbalance explanation — good. **Ordering unchanged** (connect `out→playback`, then check, then detach) and the AGENTS.md invariant was **not** amended, so the doc still states as absolute a property the implementation violates by construction |
| F15 | RT NULL check in the hot loop | **CLOSED** | Hoisted above the sample loop with a `memset` on NULL input and a comment saying why stale audio was the wrong failure direction. The per-sample `continue` is still there as a second guard — dead, and now dead twice |
| F16 | Three tests that cannot fail | **CLOSED for those three** | `test_off_unless_asked_for` is gone, replaced by a real shell-vs-Python table; `test_two_faders_move_independently` now starts both columns below unity and asserts divergence in both directions plus an untouched third column; `test_a_drag_does_not_compound` asserts thirteen steps land where one step lands, with bounds instead of the formula. The pattern recurs in the *new* tests → **G8** |
| F17 | `_picked_up` dead state | **CLOSED** | Deleted; `grep -n _picked_up` returns nothing |
| F18 | `_accept` docstring says "then delta applies" | **CLOSED** | Rewritten and accurate |
| Minors | pronoun, PATHS pipe, `CHANNELS 2`, `round()` | **CLOSED (4/4)** | All four fixed, and the `CHANNELS` and `round()` comments explain themselves |

**Counts: 11 CLOSED · 4 HALF-CLOSED or MOVED · 1 OPEN (F7, handed to user).**

Carried from 2026-09-07, still untouched and now **16 days** old:
`tests/test_looper_timing.py` has no `binding_table` cross-check (`grep -c` → 0),
and `tests/fake_sl_engine.py:67` still discards every non-`hit` message
(**17 days**). Both are test-infrastructure findings, which is the same pattern
cycle 1 named: findings closed by editing the file under discussion get closed;
findings that require touching the harness do not.

**The headline:** cycle 1 closed fourteen of eighteen cleanly and the quality of
the closures is high — F11 and F5 in particular were fixed by making the law
executable rather than by restating it. Against that, **the single most
consequential finding, F2, was closed by adding a watchdog that does not run in
the default configuration**, and **F10 was closed by adding a timeout whose
stated justification contradicts a property this repo has written down as
permanent.** Both now have a document asserting the guarantee. That is a worse
state than cycle 1, where at least the C header said plainly what was missing.

---

## 1. First impressions

Better than cycle 1, and cycle 1 was already good. The C client now says what it
is doing: "inserted on the monitor branch", "Surge is not connected to our input
— leaving the direct path in place (check MPE_SL_SURGE_CLIENT)", "could not
detach … both paths are live on this channel". An operator reading the journal
can now tell those three states apart, which is exactly what F12 was for.
`detach_direct_path`'s failure counting and the L/R-imbalance sentence are the
kind of thing people write after they have debugged one.

`LiveMonitorSender`'s docstrings are the best prose in the diff. They record two
*failed* attempts at the fix — the unconnected socket that reported zero errors
either way, and the consecutive-refusal counter that reset every other datagram
because ICMP arrives one send late — and explain why the current shape is what
it is. That is how you stop the next person re-making the same mistake.

And the enable-law fix is the correct shape: one predicate, and a test that runs
the **actual shell script** over seventeen values rather than a model of it. If
the rest of this repo's cross-language constants were pinned that way, section 3
would be a paragraph.

What I cannot credit is the direction of the remaining risk. Cycle 1's verdict
was "a component that writes down its own unfixed hole and ships anyway". Cycle
2 has filled the holes with mechanisms that are off by default, race their own
instrument, or rest on a premise the repo has already refuted in writing — and
deleted the sentences that said the hole was there. The failure and the success
still read alike; there is just more confident prose in between now.

## 2. Architecture & structure

The split is unchanged and still right. What changed is that the **live monitor
now has a supervisor**, and the supervisor's sensor lives in a third process
(`mpe-peak-meter`) that the monitor knows nothing about and that is disabled by
default. The health path is:

```
mpe-live-monitor (detaches)  →  JACK graph
                                      ↓
                    mpe-peak-meter:surge_playback_wired()   [off by default]
                                      ↓  /run/mpe/meter.state, 200 ms writes, 2 s flag refresh
                    audio_engine.surge_playback_via_meter()
                                      ↓
                    sl-watchdog.py (10 s tick)  →  restore-direct-monitor-path.sh  →  JACK graph
```

Five processes and a file between the fault and the repair, three of them
optional. The alternative was one `jack_lsp -c` in `sl-watchdog.py` (one fork per
10 s, which is the cadence it already pays for everything else), or having the
live monitor publish its own state file the way the meter does. Routing the
monitor's health through the *meter's* client-name configuration is how G9 turned
a latent duplication into an operational one.

`run_bench` grows three more closures (`push_monitor`, `on_state`, the sender's
log lambda) and is now the place where the monitor, the mix, the sender, the OSC
threads and the MIDI loop meet — still with zero tests that construct it
(2026-09-07 F5, still open).

## 3. Single authority

### Q1 — "Is the live monitor on?" — **now has one owner. Credit.**

`live_monitor.enabled()` is the law; `start-mpe-live-monitor.sh` implements the
same four words; `tests/test_live_monitor.py` proves they agree by running the
script. Closed properly.

### Q2 — "What is the control port?" — **one owner.**

`mpe-live-monitor.c:CONTROL_PORT_DEFAULT` is the authority and the test reads it.
Closed.

### Q3 — "What is Surge's JACK client name?" — **four sites, two variables, two of them tested.**

| Site | Reads | Tested? |
|---|---|---|
| `mpe-live-monitor.c:62,342` | `MPE_SL_SURGE_CLIENT` | authority |
| `wire-jack-graph.sh:23` | `MPE_SL_SURGE_CLIENT` | ✅ |
| `restore-direct-monitor-path.sh:17` | `MPE_SL_SURGE_CLIENT` | ✅ |
| `wire-sooperlooper-graph.sh:18` | `MPE_SL_SURGE_CLIENT` | ❌ |
| `mpe-peak-meter.c:351` | **`MPE_PEAK_METER_SURGE_CLIENT`** | ❌ |

Neither variable appears in `docs/PATHS.md`. Both default to `Surge XT`, so this
is latent — until someone sets `MPE_SL_SURGE_CLIENT`, at which point the meter
keeps looking for "Surge XT", reports `surge_playback=0` forever, and the
watchdog runs a repair script that connects the *right* name every ten seconds
and then reports that the repair did not take. **Owner:** the `.c` constant, with
the test extended to all four sites and the meter switched to
`MPE_SL_SURGE_CLIENT`. → **G9**

### Q4 — "Does Surge reach playback?" — **three laws, and the weakest one is the safety net.**

| Site | Law | Strength |
|---|---|---|
| `mpe-live-monitor.c:monitor_path_live()` | `Surge:out_N → in_N` **and** `out_N → playback_N`, both channels | correct |
| `wire-jack-graph.sh:live_monitor_carrying()` | same, via `jack_lsp -c`, both channels | correct, duplicated, no cross-check |
| `mpe-peak-meter.c:surge_playback_wired()` | *any* connection on `system:playback_1` whose name starts `mpe-live-monitor:` or `<surge>:` | **wrong — this is F1's condemned law** |

The first two are the F1 fix. The third was written in the same diff, answers the
same question, and is the one `sl-watchdog.py` repairs from. It does not ask
whether the insert is fed, and it does not look at channel 2 at all. → **G2**

**Owner:** the predicate belongs in one place. The live monitor already knows the
answer for its own path; the honest fix is for it to publish
`/run/mpe/live-monitor.state` the way the meter does, and for the watchdog to
read that plus a direct-path check — or for the watchdog to fork one `jack_lsp`
and own the question outright.

### Q5 — "Is this track capturing?" — **one owner, wrong clock.**

`live_monitor.LiveMonitor._capturing` is the only site, which is right. Its
*expiry* is a second opinion about engine liveness that disagrees with
`sl_osc_session`'s documented delivery semantics. → **G1**

## 4. Code quality

Naming, error handling and comment quality all improved. Three things:

- **`push_monitor` computes `target_amp` three times** when `fader_log` is on
  (`sooperlooper-apc-bench.py:455`, `:456` via `capturing_loop`, `:459`) — it was
  twice in cycle 1. It runs per fader CC and per state datagram.
- **The per-sample NULL guard in `process()` is now dead twice.** The hoisted
  check above the loop is the fix; the `if (in[ch] == NULL || out[ch] == NULL)
  continue;` inside the sample loop was left behind and still costs two compares
  per sample per channel. Delete it, or keep it and delete the hoist — not both.
- **`config/mpe-peak-meter.service:6`** says "install-units.sh lists this in
  DISABLED". `install-units.sh:45` now says explicitly *do not* add it. The new
  live-monitor unit copied the correct version; the meter's is stale and now
  contradicted by a file in the same diff.

## 5. Code smells — the hall of shame

### 🔴 G1 — The capture TTL rests on a claim this repo has designated a permanent property *in the opposite direction*

```python
#: How long a `state` update keeps a loop in the capturing set.
#:
#: SooperLooper streams `state` on a timer, not on change — every
#: `MPE_SL_BENCH_STATE_MS` (100 ms, `sl_osc_session.BENCH_STATE_MS`) — so a live
#: subscription refreshes this ten times a second and a real take never expires
#: mid-record.
CAPTURE_TTL_S = float(os.environ.get("MPE_LIVE_MONITOR_CAPTURE_TTL_S", "2.0"))
```

It does not stream on a timer. It streams **on change**, and this repo says so in
four places, one of which calls it permanent:

- `Documents/specs/session-control-plane-spec.md:116` — *"SooperLooper's
  `register_auto_update` delivers on change; a subscriber that starts after the
  change never learns it and fails silently forever (`582422d`)."*
- the same spec at `:811-812`, quoted in
  `grumpy-review-ownership-track-state-2026-08-30.md:523` — *"`register_auto_update`
  delivering only on change is a permanent property to design around (D6,
  criterion 4)."*
- `sl_hud_monitor.py:96` and `:104` — *"register_auto_update delivers on CHANGE
  only … Do not delete it."*
- `grumpy-review-ownership-lifecycle-2026-08-30.md` F3 🔴 — an entire prior P0
  caused by assuming otherwise.

During a take the state is `RECORDING` and does not change, so **no further
`state` datagram arrives for that loop.** `loop_pos` streams at 20 ms, but
`sl_bench_listener` routes only `control == "state"` to `on_state`.
`maybe_reregister()` fires every 15 s and does not seed — `sl_hud_monitor` calls
`seed_tempo()` separately precisely because registration delivers nothing.

Measured, driving the real `LiveMonitor` and `LoopMix` with one state change and
a column fader at 60:

```
CAPTURE_TTL_S = 2.0
  t+ 0.0s  capturing=0     target=0.1573  live=1.0000
  t+ 1.9s  capturing=0     target=0.1573  live=1.0000
  t+ 2.1s  capturing=None  target=1.0000  live=1.0000   ← +16.1 dB, mid-take
  t+ 5.0s  capturing=None  target=1.0000  live=1.0000
```

Two consequences, and they point in opposite directions:

1. **The louder one.** `push_monitor` is called on every fader move. Move the
   column fader more than two seconds into a take — which is the exact gesture
   `test_capturing_matches_what_the_track_will_play_back_at` exists to protect —
   and the target is no longer the track's level, it is the live level. On an
   appliance that may have headphones on someone's head, an unbounded upward jump
   on a routine gesture is the failure direction AGENTS.md's audio-safety section
   is entirely about.
2. **F10 is not actually fixed.** `push_monitor` has exactly three call sites
   (`:467` on_state, `:535` startup, `:738` fader move) and **none of them is on a
   timer**. In F10's own scenario — engine restart, lapsed subscription, deleted
   loop — no state updates arrive and no faders move, so the entry expires
   internally and *nothing sends the new amplitude*. The gain stage stays pinned
   at the dead take's level exactly as it did before the fix. The only recovery is
   still moving that column's pickup-gated fader, which is what cycle 1 called out
   as "not obviously available."

**Fix:** delete the TTL and get the liveness signal from something that actually
ticks — the engine-restart epoch `sl_osc_session` already tracks, or an explicit
clear when `register_bench` re-subscribes. If a TTL stays, it must be longer than
the longest plausible take (it is bounding *subscription* liveness, not take
length), and expiry must call `push_monitor`. Either way `tests/engine/` can
settle the premise empirically in an afternoon, and should, because the whole
design rests on it.

### 🔴 G2 — The watchdog's health check is the presence-not-path law F1 was filed against

```c
static int surge_playback_wired(void)
{
    jack_port_t *pb = jack_port_by_name(g_client, "system:playback_1");
    ...
    for (int i = 0; connections[i] != NULL; i++) {
        if (strncmp(connections[i], "mpe-live-monitor:", 17) == 0) { ok = 1; break; }
        if (strncmp(connections[i], g_surge_client, surge_len) == 0 &&
            connections[i][surge_len] == ':') { ok = 1; break; }
    }
```

An `mpe-live-monitor:out_1` hanging off `playback_1` satisfies this **whether or
not anything feeds the insert.** That is the F1 state verbatim, moved from the
wiring script into the health check. Reachable with one fault: the monitor
detaches the direct path, Surge restarts and comes up as `Surge XT-01` (JACK
renames on collision — the same behaviour F3 was filed about), the monitor's
`input_leg_live()` goes false and it calls `restore_direct_path()`, which
`jack_connect`s the **old** name, fails, ignores the failure (**G7**) and clears
its flag. Now `playback_1` is fed only by an insert fed by nothing. Total
silence — and `surge_playback=1`, so the watchdog says healthy.

It is also **channel 1 only**, so the L/R half-detach that `detach_direct_path`
was just taught to report is invisible to the thing meant to repair it.

**Fix:** the watchdog should ask the same question the C client and the wiring
script ask — both legs, both channels — from one implementation. Cheapest correct
version: the live monitor publishes its own `carrying=0/1` state file and the
watchdog requires `direct OR (monitor present AND carrying)`.

### 🔴 G3 — The enable-law test starts the real JACK client, and can silence the instrument

```python
for value in ("1", "0", "on", "ON", "On", "true", "TRUE", "True",
              "yes", "YES", "off", "OFF", "no", "false", "False", "2", ""):
    proc = subprocess.run(["bash", str(script)], env=env, capture_output=True,
                          text=True, timeout=30)
```

`start-mpe-live-monitor.sh` ends in `exec "$BIN"`. For the eight enabling values
this test **runs the live monitor itself**. Verified:

```
$ MPE_LIVE_MONITOR=1 bash scripts/start-mpe-live-monitor.sh
jack server is not running or cannot be started
mpe-live-monitor: jack_client_open failed (status 0x11)
```

No jackd here, so it exits in milliseconds and the test is fast and green. On a
machine **with** jackd — the Pi, or the laptop during a session — the same call
connects, registers ports, wires `Surge→in` and `out→playback`, **detaches
`Surge XT:out_N → system:playback_N`**, and then blocks. `subprocess.run` kills
it at 30 s, which for a blocking child is `SIGKILL` — the one signal that cannot
run `restore_direct_path()`. There is no systemd here, so no `ExecStopPost`. If
`MPE_PEAK_METER=0` (default) there is no watchdog either.

So: running the unit test suite on the appliance leaves it silent, eight times
over, adding four minutes and a `TimeoutExpired` error while it does. AGENTS.md
tells every agent to run this suite before opening a PR, and §"Never ask Mitch to
run a test you could have run yourself" pushes hard toward running it wherever
you are.

If the service is already running, `JackUseExactName` makes the test's instance
refuse — so F3 accidentally mitigates the worst case, but only in the
configuration where the monitor is already enabled.

**Fix:** the test must assert the gate without crossing it. Either honour an
`MPE_LIVE_MONITOR_GATE_ONLY=1` that makes the script print its verdict and exit
before `exec`, or point `MPE_MODULE_REPO` at a fixture directory whose `$BIN` is
`/bin/true`. Keep running the real script — that part is right.

### 🔴 G4 — The client never re-detaches, so a restored direct path is permanent, and the restore script documents the opposite

```c
if (monitor_path_live() && !atomic_load_explicit(&g_direct_detached, memory_order_relaxed)) {
    if (detach_direct_path() == 0) { ... }
}
```

The `!g_direct_detached` half is new — part of the F13 idempotence fix — and it is
not needed for that: `detach_direct_path()` already tests each connection with
`port_connected_to()` before disconnecting, which is the whole F13 fix. What the
flag adds is that **once the client has detached, it will not detach again for the
life of the process**, no matter what reappears in the graph.

And something does reconnect it. `scripts/restore-direct-monitor-path.sh` runs
from the watchdog's repair arm while the client is alive. Its own header says:

> It is also safe to run when the monitor is healthy — jack_connect on an
> existing connection is a no-op, and **the client's own guard re-detaches within
> its next wiring pass.**

It does not. After that repair, `Surge→playback` and `Surge→monitor→playback` are
both connected, permanently, until the unit restarts. That is two copies of the
live signal one insert-latency apart — the doubling AGENTS.md says must never
happen and `dump-loop-levels.py:45` describes as "heard as random volume swells".

It takes one spurious `surge_playback=0` to get there: a transient
`jack_port_by_name("system:playback_1")` miss, a meter that restarted mid-cycle,
or the client-name divergence in G9 (which makes it fire *every ten seconds,
forever*).

**Fix:** drop `&& !g_direct_detached` from the guard. The per-connection check in
`detach_direct_path()` already gives F13's idempotence, and the flag can stay as
the "should I restore on exit" memory it was. Then correct the restore script's
comment, or delete the sentence.

### 🔴 G5 — The watchdog arm F2 asked for does not run in the default configuration

`read_graph_snapshot()` sources everything from `/run/mpe/meter.state`, which is
written by `mpe-peak-meter`. `MPE_PEAK_METER` defaults to **`0`**
(`docs/PATHS.md:43`, "**Off by default**"), the unit is opt-in, and nothing
couples it to `MPE_LIVE_MONITOR`: the live monitor's unit, its start script,
`PATHS.md`'s new rows and `install-units.sh` all say nothing about needing the
meter. Enable the live monitor the documented way and you get the detach with no
supervisor at all — the exact cycle-1 state, minus the header comment that
admitted it.

Worse, the comment now asserts the guarantee:

```c
 *      ... and `sl-watchdog.py` asserts that Surge reaches playback by one
 *      route or the other. Nothing covers power loss, which nothing can.
```

while `docs/PATHS.md`'s `MPE_LIVE_MONITOR` row, edited in the same diff, still
says *"a crash leaves it out (**no watchdog yet**)"*. Two documents, written
together, stating opposite things about the same safety property. Whichever a
reader lands on, one of them is lying.

**Fix:** either make the live monitor's own start script refuse to run without a
fresh `meter.state` (a hard dependency, stated), or give `sl-watchdog.py` a
meter-independent path for this one question — one `jack_lsp -c system:playback_1`
per 10 s tick, which is ~1 fork/10 s against a budget that already spends more
than that. And pick one story for the two docs.

### 🟡 G6 — A successful repair reports as a failed one

```python
proc = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=30)
if surge_playback_via_meter() is True:
    repaired.append("reconnected Surge -> playback")
    problems.pop()
else:
    log(f"repair did not take: restore-direct-monitor-path.sh exited {proc.returncode}")
```

`surge_playback_via_meter()` reads the meter's published flag. That flag is
recomputed in `mpe-peak-meter.c:ensure_wiring()` on a **2 s** poll
(`CONNECT_INTERVAL_US`) and published on a 200 ms writer tick. The check runs
immediately after `jack_connect` returns — tens of milliseconds — so it reads the
pre-repair value essentially every time. A repair that worked perfectly logs
"repair did not take", dumps four lines of the restore script's *successful*
output, and leaves the problem in the list.

The arm two blocks above does this correctly: it calls
`wait_for_playback_via_meter()`, which polls. The new arm copied the shape and
dropped the wait.

**Fix:** `wait_for_surge_playback_via_meter(timeout_s=4.0)`, mirroring the
existing helper.

### 🟡 G7 — `restore_direct_path()` ignores its failures and clears the flag anyway

```c
(void)connect_if_absent(src, playback);
...
atomic_store_explicit(&g_direct_detached, 0, memory_order_relaxed);
```

In the same file, in the same diff, `detach_direct_path()` was taught to count
failures and print which channel it could not remove — because a half-failed
detach is an audible L/R imbalance. The restore path got none of that, and it is
the more consequential of the two: a failed restore is **silence**, not
imbalance. Clearing the flag unconditionally means `ensure_wiring` will not try
again either, since `restore_direct_path()` early-returns when the flag is clear.
One transient failure is permanent.

**Fix:** mirror `detach_direct_path()` — count, log the channel, and only clear
the flag when every channel came back.

### 🟡 G8 — The tests for the new TTL are handed the thing they measure

```python
def test_a_refreshed_capture_does_not_expire(self):
    mon = LiveMonitor()
    now = 1000.0
    for tick in range(200):  # 20 seconds of takes at the real 100 ms cadence
        mon.note_state(3, SL_STATE_RECORDING, now=now + tick * 0.1)
```

The cadence is an argument. The test supplies two hundred refreshes and then
asserts the entry did not expire — which is arithmetic, not behaviour. Whether
the engine sends those refreshes is the only question that matters and is the one
thing the test cannot express. This is precisely cycle 1's §7 reading of the
doubles ("what it *computes* versus what it is *handed*") recurring inside the fix
for F10.

```python
def test_the_ttl_covers_the_engine_cadence_with_room(self):
    """A TTL under the state interval would expire live takes."""
    self.assertGreater(live_monitor.CAPTURE_TTL_S, 5 * sl_osc_session.BENCH_STATE_MS / 1000.0)
```

`2.0 > 0.5`. This is worse than a tautology: it is a *green certification of a
false premise*. It links two constants whose relationship is irrelevant, under a
docstring stating the thing G1 disproves, and a reader checking whether takes are
protected will find it and stop looking.

**Fix:** the only honest test of this lives in `tests/engine/` — record into a
loop on the real engine for five seconds and assert the monitor target never
returns to the live level. That harness exists and runs SooperLooper for exactly
this class of question.

### 🟡 G9 — F6's name test covers two of four sites, and misses the one that now matters

`test_the_surge_client_name_matches_the_client` checks `wire-jack-graph.sh` and
`restore-direct-monitor-path.sh`. It does not check `wire-sooperlooper-graph.sh`,
and it does not check `mpe-peak-meter.c` — which cycle 1 named explicitly ("reads
a **different** env var, `MPE_PEAK_METER_SURGE_CLIENT`") and which the same diff
made load-bearing for the watchdog's health check. A test that pins the two sites
that already agreed, while leaving the one that diverges untested, satisfies the
finding and leaves the failure reachable.

Neither variable is in `docs/PATHS.md`.

**Fix:** extend the test to every file that names Surge's client, and switch the
meter to `MPE_SL_SURGE_CLIENT` (keeping the old name as a fallback if anything
sets it).

### 🟡 G10 — Nothing pushes the monitor on a timer, so the new reporting only fires when a level moves

`push_monitor` runs on a state update, a fader move, and once at startup. Every
piece of new observability hangs off `LiveMonitorSender.send`, which only those
three call. So during an idle stretch — no faders, no state changes — a gain
stage that died reports nothing, the refusal counter stays where it was, and the
recovery announcement cannot fire either. It is a smaller version of the same
shape F11 fixed: the reporting is correct and its trigger is the thing that goes
quiet.

The idle branch runs at ~485 Hz and must not do this every pass. A 1 Hz forced
re-send (one 14-byte datagram; the client ramps, so a repeat is inaudible) costs
nothing measurable and makes the refusal path self-testing. Note also that
`monitor_sender.close()` is never called on bench exit.

### 🟡 G11 — F14's doc amendment was not made

`AGENTS.md` still states *"**both paths must never be connected at once**"* as an
absolute, and `ensure_wiring` still connects `out_N → playback_N` before checking
and detaching, so both are live for two to four JACK IPC round-trips at every
insertion. Cycle 1 offered two fixes — say "must not be **left** connected", or
reorder — and neither was taken. With G4 in play the sentence is now false in a
second, non-transient way.

### 🟡 G12 — The repair arm's cadence × cost is not stated, and it fires while Surge is merely late

AGENTS.md: *"Before adding any polling loop, watchdog tick, or timer … Compute
cost × cadence and put it in the PR."* The new arm forks `bash` plus two
`jack_connect` processes on every 10 s tick where `surge_playback` is false. That
includes the ordinary case of Surge not being up yet — boot, or a
`surge-watchdog` restart — where it will fail, log five lines, and repeat. Cheap
in absolute terms; unstated, and the journal noise is the kind that trains people
to ignore the file.

### 🟢 Minor

- `live_monitor_carrying()` in the shell and `monitor_path_live()` in the C are
  the same law written twice, in two languages, with nothing checking they agree
  — the F6 pattern, one abstraction level up. Worth a comment at minimum naming
  the C as authoritative.
- The dead per-sample NULL `continue` in `process()` (see §4).
- `config/mpe-peak-meter.service:6` contradicts `install-units.sh:45` (see §4).
- `push_monitor` evaluates `target_amp` three times when logging.
- The C header says the unit carries `ExecStopPost=` "lines"; it carries one,
  calling a script.

## 6. Logic & business rules

The monitor's rule is still stated in one sentence and the code is still a direct
transcription of it. What the TTL added is a **second rule that is not stated in
that sentence**: "…unless the engine has been quiet about this track for two
seconds, in which case you hear yourself at the live level." That rule is not in
the module docstring, is not in `PATHS.md`'s description of the flag, and
contradicts the one that is. When a rule gets an exception, the exception belongs
in the sentence.

**Settings read once**, re-checked per the skill: `live_monitor.ENABLED`,
`CAPTURE_TTL_S`, `REFUSAL_COMPLAIN_S`, `RECOVERY_STREAK`, `resolve_live_cc()`,
the C's `load_env()`. All correct for this appliance — env changes require a
restart. `GraphSnapshot.surge_playback` correctly defaults to `None` rather than
`False`, and `surge_playback_via_meter`'s docstring gives the right reason: a key
that was never written must not be read as "disconnected", because repairing on
it would reconnect the direct path under a healthy insert. That is careful
thinking — and G4 means the consequence it names is exactly what a *spurious
False* now produces permanently.

**Recording into a silent track still makes you deaf**, and cycle 1 handed that
to the user. No decision is recorded, no log line was added. Combined with G1 it
now has a companion: recording into a silent track makes you deaf for two
seconds, then abruptly loud.

## 7. Test strategy & execution

2209 tests, OK, 125 s — verified, not taken on trust. Eleven new tests, and most
of them are real. `SayingNothingIsWorking` is the strongest class in the diff:
`_unreachable_sender` binds a real socket to get a real port, closes it, and
pushes until the ICMP error lands, with a docstring explaining why a mock would
have proved the wrong thing. `test_recovery_is_announced_once_the_gain_stage_is_back`
stands a listener up on the port it was complaining about. That is testing
behaviour against the operating system, not against a model of it.
`test_python_and_shell_agree_on_what_switches_it_on` is the right idea executed
one step too far (G3).

**The doubles, as instruments.** Still one: `LiveMonitorSender(send=list.append)`,
which computes nothing and is handed the payload. Two new axes appeared and both
are supplied rather than measured:

| What the suite measures | How it gets it |
|---|---|
| the level Python decides | computed by the code — real |
| whether a refusal is reported | **measured against a real closed port** — the one genuine improvement |
| whether a take survives the TTL | **handed in**, as 200 synthetic `note_state` calls (G8) |
| whether the TTL is long enough | **asserted against a constant**, from a false premise (G8) |
| whether the C client behaves | not expressed at all |
| whether the graph ends up right | not expressed at all |

The axis the reported bugs travel on is still "what happens to the graph, and
what the engine actually sends." Of this cycle's five 🔴s, **four (G1, G2, G3,
G4) are invisible to every test in the repository**, and the fifth (G5) is a
configuration fact no test asserts. `tests/engine/` runs a real SooperLooper on a
dummy JACK backend and could settle G1 and G3 directly; nothing in this diff
went near it.

On the fader side the rewrite is a genuine improvement.
`test_a_drag_does_not_compound` now asserts thirteen steps land where one step
lands, with `>90` / `<127` bounds instead of the transcribed formula — though the
equality holds exactly for *any* multiplicative law, so it constrains the class
rather than the value; the bounds do the behavioural work.
`test_two_faders_move_independently` now starts both columns at half travel and
asserts they diverge in opposite directions plus an untouched third column —
genuinely capable of failing if faders leak into each other. Both closures are
real.

What is still missing on that side is F7's axis: nothing asserts what a player
experiences over a *sequence* of moves. `test_slow_moves_near_silence_still_accumulate`
is the closest and it only checks monotonicity.

## 8. Security & performance

Unchanged and still fine. Loopback-only bind, a parser that rejects anything but
`gain <float>`, clamp-to-attenuation. Still unauthenticated by design — any local
process can quiet the monitor — and now, unlike cycle 1, the *bench* would notice
its own datagrams being refused, though not a third party's being accepted.

No secrets, hostnames or addresses in any new file; I checked the restore script
and the CI job against the public-repo banner. The CI job installs
`libjack-jackd2-dev` from Ubuntu and compiles three files; no new supply-chain
surface.

Performance: the RT callback is unchanged apart from the hoisted guard (a small
win, minus the dead inner branch). The watchdog's new fork cost is real but
small and unstated (G12). `push_monitor` on every state datagram is tens of calls
per second — and, per G1, *fewer* than the code assumes, which is the finding.

## 9. Documentation vs. reality

The AGENTS.md rewrite still earns most of its claims. Re-checked, plus the new
prose:

| Claim | Verdict |
|---|---|
| "four gain stages, and they are not all in series" | ✅ True |
| "**both paths must never be connected at once**" | ❌ Violated transiently by construction (F14, unfixed) and now **permanently reachable** via G4 |
| `mpe-live-monitor.c` header: "the unit carries `ExecStopPost=` lines that restore the direct path whatever killed this process" | ✅ True |
| `mpe-live-monitor.c` header: "and `sl-watchdog.py` asserts that Surge reaches playback by one route or the other" | ❌ Only when `MPE_PEAK_METER=1`, which is off by default — G5 |
| `docs/PATHS.md` `MPE_LIVE_MONITOR`: "a crash leaves it out (**no watchdog yet**)" | ❌ Stale in the opposite direction from the header above. One diff, two contradictory claims |
| `restore-direct-monitor-path.sh`: "the client's own guard re-detaches within its next wiring pass" | ❌ False — G4. This sentence is what makes the script look safe to run live |
| `live_monitor.py`: "SooperLooper streams `state` on a timer, not on change" | ❌ False, and contradicted by `session-control-plane-spec.md:811-812`, `sl_hud_monitor.py:96`, and a prior 🔴 — G1 |
| `live_monitor.py`: "a real take never expires mid-record" | ❌ Expires at 2.0 s — measured — G1 |
| `loop_mix.py`: "the two positions converge as you play" | ⚠️ Still misleading about path-dependence (F7, open) |
| `_scaled_level`: "a single CC can still move the level a long way … MEASURED … -32.1 dB to -17.0 dB" | ✅ True, and citing the measurement in the source is the right habit |
| `live_monitor.py` sender docstrings on the two failed fix attempts | ✅ Accurate and unusually valuable |
| `surge_playback_via_meter` docstring on why `None ≠ False` | ✅ Correct reasoning |

Build/deploy: the `native` CI job is a real gate — I ran its exact command and all
three clients compile clean under `-Werror`. Putting `-Werror` in CI and not in
the Makefile, with the reason ("a warning must not stop Mitch getting sound out
of a Pi at a gig"), is the right call written down properly.

---

## Verdict

Cycle 1 closed fourteen of eighteen findings and several of them beautifully —
F11's reporting path, F5's shared law, F3's refusal, F16's rewritten assertions
and the whole of F12/F13/F15/F17/F18 are the work of someone who read the
findings rather than pattern-matched them. The C client is genuinely better and
the journal will now tell an operator which of three states they are in.

But the two findings that mattered most were closed with mechanisms that do not
hold. F2's watchdog exists and does not run unless an unrelated, off-by-default
service is enabled, and the header comment now states the guarantee flatly while
`PATHS.md` in the same diff still says it is missing. F10's timeout rests on a
claim about the engine that this repository has written down as permanently false
in three files and one prior 🔴, and it fails in both directions at once: it jumps
the monitor to unity two seconds into a take if you touch a fader, and it still
does not unstick the case it was written for, because nothing pushes the level on
expiry. Meanwhile F1's fix — two correct path predicates — was undermined in the
same diff by a third, weaker predicate placed in the safety net, and F13's
idempotence guard quietly removed the client's ability to re-detach, which turns
one spurious repair into a permanently doubled live signal.

The fader work is a different story and is close to done. F8, F16, F17 and F18
are properly closed, the new tests can fail, and the measured numbers are in the
source where the next reader will find them. F7 is the only thing left there and
it is a decision, not a bug.

Everything hard in this component is still on the axis nothing tests: what
happens to the JACK graph, and what the engine actually sends. `tests/engine/`
exists, runs a real SooperLooper, and would settle G1 and G3 in an afternoon.
That is the single highest-value thing left to do before this reaches a Pi.

## Priority backlog (🔴 only)

1. **G1 — delete or re-found the capture TTL.** The premise is refuted by
   `session-control-plane-spec.md:811-812`. Confirm with `tests/engine/`, then
   either drop the TTL for an engine-epoch signal or lengthen it past any take
   *and* call `push_monitor` on expiry. Until then a fader move mid-record can
   raise the monitor by 16 dB.
2. **G4 — drop `&& !g_direct_detached` from `ensure_wiring`'s detach guard,**
   and fix the sentence in `restore-direct-monitor-path.sh` that claims the
   client re-detaches. One condition; it is the difference between "the watchdog
   repaired the graph" and "the live signal is doubled until reboot".
3. **G3 — stop the enable-law test from `exec`ing the binary.** Running the test
   suite on a machine with jackd detaches Surge from playback and SIGKILLs the
   process that would have restored it.
4. **G5 — make the watchdog arm run in the default configuration,** or state the
   dependency on `MPE_PEAK_METER=1` in the unit, the start script and `PATHS.md`,
   and reconcile the header comment with the `PATHS.md` row. Right now the
   guarantee is asserted and absent.
5. **G2 — give "does Surge reach playback" one implementation.** The meter's
   version asks about presence on channel 1; the other two ask about the path on
   both channels. The weak one is the one that repairs.

## Finding ledger

| # | Finding | Severity | Fate | Enforced by | Fails today if it regresses? |
|---|---------|----------|------|-------------|------------------------------|
| G1 | `CAPTURE_TTL_S` assumes timer-based `state` delivery; repo canon says change-only. Expires a live take at 2.0 s (measured: target 0.157 → 1.000); expiry pushes nothing, so F10's own scenario is unfixed | 🔴 | Fixed now + Enforced | *to write* — a `tests/engine/` case that records for 5 s and asserts the target never returns to the live level | **No** |
| G2 | `mpe-peak-meter.c:surge_playback_wired()` keys on presence of a connection on `playback_1` only — F1's condemned law, now the watchdog's sensor; channel 2 invisible | 🔴 | Fixed now | *to write* — one shared path predicate; a test that a fed-by-nothing insert reads as not-carrying | **No** |
| G3 | `test_python_and_shell_agree_on_what_switches_it_on` `exec`s the real client 8×; on a jackd host it detaches Surge→playback and is SIGKILLed at 30 s | 🔴 | Fixed now | `tests/test_live_monitor.py` — the test is the defect | **No** |
| G4 | `ensure_wiring`'s new `!g_direct_detached` guard stops the client re-detaching a restored direct path → permanent doubled live signal; the restore script documents the opposite | 🔴 | Fixed now + doc fix | *to write* — a `tests/engine/` case that reconnects the direct path under a live client and asserts it goes away | **No** |
| G5 | The F2 watchdog arm is inert unless `MPE_PEAK_METER=1` (off by default, coupled to nothing); C header asserts it unconditionally while `PATHS.md` says "no watchdog yet" | 🔴 | Fixed now or Handed to user | *to write* — a start-script refusal, or a meter-independent check | **No** |
| G6 | Repair verification reads a flag that refreshes every 2 s, immediately after the repair → a successful repair logs "repair did not take" forever | 🟡 | Fixed now | *to write* — `wait_for_surge_playback_via_meter`, mirroring the existing helper | **No** |
| G7 | `restore_direct_path()` ignores `connect_if_absent` failures and clears `g_direct_detached` regardless → a failed restore is permanent, unreported, and never retried | 🟡 | Fixed now | *to write* — mirror `detach_direct_path()`'s failure count | **No** |
| G8 | New TTL tests are handed the cadence they measure; `test_the_ttl_covers_the_engine_cadence_with_room` certifies a false premise (`2.0 > 0.5`) | 🟡 | Fixed now | `tests/test_live_monitor.py::CaptureExpiry` — rewrite against `tests/engine/` | **No** |
| G9 | F6 half-closed: the name test covers 2 of 4 sites; `mpe-peak-meter.c` still reads `MPE_PEAK_METER_SURGE_CLIENT` and it is now load-bearing; neither var is in `PATHS.md` | 🟡 | Enforced | *to write* — extend `test_the_surge_client_name_matches_the_client` to all four sites | **No** |
| G10 | `push_monitor` has no timer, so refusal reporting, recovery and any time-derived target only reach the gain stage on a fader move or state change; `monitor_sender.close()` never called | 🟡 | Fixed now | *to write* — 1 Hz forced re-send | **No** |
| G11 | F14 unfixed: handover order unchanged and the AGENTS.md "never connected at once" invariant not amended | 🟡 | Fixed now + doc amendment | — | **No** |
| G12 | Watchdog repair arm forks 3 processes per 10 s tick while Surge is merely late; cadence × cost unstated (AGENTS.md requires it) | 🟡 | Fixed now | — | **No** |
| F7 (carried) | Fader law is path-dependent; module docstring still says the positions "converge as you play" | 🟡 | Handed to user — **still undecided** | — | **No** |
| 09-07 F1 (carried) | `looper_timing._assert_total()` has no `binding_table` cross-check | 🟡 | Enforced | *to write* — **OPEN 16 days** | **No** |
| 09-07 Step0 (carried) | `tests/fake_sl_engine.py:67` discards every non-`hit` message | 🟡 | Fixed now | *to write* — **OPEN 17 days** | **No** |

*Fifteen rows — five 🔴, ten 🟡. **"Fails today if it regresses?" is still "No" on
every row**, for the same reason as cycle 1: the executable surface added this
cycle covers compilation and two constants, and every 🔴 lives on the graph axis
or the engine-delivery axis, neither of which any test touches. Four of the five
🔴s were introduced by cycle-1 fixes, which is the argument for a cycle 3 rather
than against the fixes. Next reviewer: this table is your Step 0. Re-check G1
first — if `tests/engine/` shows `register_auto_update` does stream on a timer
after all, G1 and G8 collapse and the repo's spec needs correcting instead.*
