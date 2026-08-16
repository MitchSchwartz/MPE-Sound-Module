# Review audit — grumpy-review-looper-2026-08-15

Adversarial verification of `Documents/reviews/grumpy-review-looper-2026-08-15.md`.
Repo `MPE-Module`, branch `dev`. Read-only. Live appliance checked over ssh
(`mitch@raspberrypi2.local`) — no process restarted, no file written on the Pi.

**Headline:** the review is a strong *static* read of the code — most of its
code-level claims survive. But its two most confident causal claims are wrong,
and it missed the actual live fault, which is running on the appliance right
now and explains the reported symptoms without invoking a single race.

---

## 0. The thing the review missed — and it is the whole story

SooperLooper is running with **no JACK client at all**. Verified live:

```
ps:  2537288  Sat Aug 15 20:16:09  .../sooperlooper -q -D yes -l 16 -c 2 -t 40 -p 9951 -j mpe-looper
     2540781  Sat Aug 15 20:20:42  jackd -R -P70 -s -d alsa -P hw:1 -r 48000 -p 256 -n 3

jack_lsp:
  system:playback_1
  system:playback_2
  Surge XT:out_1
  Surge XT:out_2
```

`mpe-looper` has **zero ports**. jackd was restarted at 20:20:42, four and a
half minutes *after* SooperLooper started. SL lost its JACK client and never
re-registered. It has been an orphan process for ~45 minutes.

The watchdog agrees, and its alarm file is frozen at the moment it happened:

```
~/.mpe_sl_watchdog.json   mtime 2026-08-15 20:20:56   ("wedged")
```

14 seconds after jackd came back. Every consequence follows:

1. **The "unknown wedge" is not unknown.** `sl-watchdog.py:23-27` and
   `sl-health.py:3-15` both record the signature — `/get` answers, `/set` and
   `/hit` are silently ignored — and both say root cause UNKNOWN since
   2026-08-14. The root cause is *jackd restarting under a live SooperLooper*.
   `/set`/`/hit` go through `push_nonrt_event()`, whose queue is drained from
   the JACK process callback. No JACK client → no callback → the queue is never
   drained → commands vanish. `/get` reads state directly and keeps answering.
   That is exactly the observed signature, and the repo already names the
   condition elsewhere: `restart-sooperlooper.sh` has a
   `jack_client_visible()` check and logs `"orphan detected (process without
   JACK client)"`. **The watchdog never performs that check.**

2. **This is why the watchdog logs PROBLEM every cycle and never repairs.**
   `playback_sources()` returns `{Surge XT:out_1, Surge XT:out_2}` — non-empty,
   so the `if srcs and ...` guard at `sl-watchdog.py:149` passes and the problem
   is appended. The repair shells out to `wire-jack-graph.sh connect`, which
   runs `jack_connect mpe-looper:common_out_1 system:playback_1` **against a
   port that does not exist**. It cannot ever succeed. The re-check at `:156`
   is correctly negative, forever.

3. **"Green pad, no audio" needs no race to explain it.** The bench sends
   `record`; SL queues it and never executes it; the bench optimistically
   paints green (`apc_footswitch.py:317`/`:231`). Nothing was recorded because
   the engine is not on the audio bus at all.

4. **"Grid still quantized after a track reset" needs no race either.**
   `reset_all_loops` sends 32 `/hit` messages into the same dead queue. So does
   `set_grid_active`'s 96-datagram burst. Nothing is applied.

5. **The alarm file is a liveness lie.** `write_alarm("wedged", ...)` is only
   called on the *transition* (`wedged_since is None`), so `updated_at` freezes
   at first detection. Anything treating that file as a heartbeat — including a
   human glancing at it — reads a 45-minute-old timestamp as current.

6. **`sl-health.py` would have diagnosed this in one command.** It has an
   explicit audio-path check that prints `FAIL audio path mpe-looper:common_out
   is NOT connected to system:playback`. The tool exists and works. Nobody ran
   it. Meanwhile the review spent its §4-A budget speculating about `PATH`.

7. **Surge has a recovery path for exactly this; SL does not.**
   `scripts/surge-watchdog.sh:62-96` implements `promote-to-jack` /
   `jackd-down` reconciliation and restarts Surge so it re-joins the bus.
   Surge was in fact restarted at 20:44:51 and is on the bus. SL's watchdog
   correctly refuses to restart (a restart destroys takes — the policy is
   right), which leaves the orphan state with **no recovery path and no
   escalation**: it neither repairs nor names the condition nor re-alarms.

Everything below should be read against this. The control-layer defects the
review found are real, but they are not what is broken today.

---

## 1. Claim verification

### `sl-watchdog.py` / `wire-jack-graph.sh` (§4-A)

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| A1 | `subprocess.run(..., capture_output=True)` discards returncode/stdout/stderr; a non-zero exit raises nothing | ✅ Confirmed | `sl-watchdog.py:154-155`. No `check=True`, no `.returncode` read. The `except Exception` at `:160` can only catch a timeout or a missing `bash`. |
| A2 | *"This is the whole answer to 'logs PROBLEM every cycle, never repaired.'"* | ❌ **Wrong** | The watchdog does **not** use the exit code to decide success. `:156-158` re-runs `playback_sources()` and checks the graph directly. Discarding the exit code explains the **silence**, not the **failure**. The repair fails because `mpe-looper` has no ports (§0). The review conflated "no diagnostics" with "the cause". |
| A3 | Likely cause: `need_cmd oscsend` in `wire_connect` exits 1 before `connect_graph` | ❌ **Wrong** | `which oscsend` on the Pi → `/usr/bin/oscsend`. Also `/usr/bin/jack_connect`, `/usr/bin/jack_lsp`. `/usr/bin` is on any plausible PATH including systemd's default. The gate never fires. The recommendation ("drop `need_cmd oscsend` from `wire_connect`") is still *defensible hygiene* — `set_dry_all` genuinely needs it, `connect_graph` does not — but it is not the bug. |
| A4 | Second candidate: `set -euo pipefail` + `try_oscsend` returning 1 aborts after connections are made | ⚠️ Partially true, and moot | `wire-jack-graph.sh:62-68` — `try_oscsend` returns 1 on failure, called bare in a `for` loop under `set -e` (`:93`), so it *would* abort. But `oscsend` is fire-and-forget UDP: it exits 0 even against a wedged engine. Reachable only if `oscsend` itself errors on argument syntax. And even if it aborted, `connect_graph` (`:102`) has already run — so this never blocks the repair. Real latent fragility, wrong diagnosis. |
| A5 | Third candidate: `JACK_CLIENT` mismatch makes the detector permanently true and the repair permanently ineffective | ⚠️ **Right shape, wrong mechanism** — and this is the closest the review got | The name is *not* mismatched (`-j mpe-looper` matches `MPE_SL_JACK_CLIENT` default). But the *effect* the review describes — detector permanently true, repair permanently ineffective — is exactly what is happening, because the ports don't exist rather than because the name is wrong. |
| A6 | `problems.pop()` at `:159` un-reports by popping a list; correct only because exactly one problem was appended | ✅ Confirmed | `sl-watchdog.py:150` appends, `:159` pops. Currently correct, structurally fragile. |
| A7 | The health probe writes `dry` on loop 0 every 10 s and restores only if `before` was readable | ✅ Confirmed, and worse than stated | `sl-watchdog.py:168-187`. If `before is None`, `target` becomes `0.5`, the write happens, and the restore at `:185` is gated on `elif before is not None` — so `dry` is **left at 0.5 permanently**, undoing `wire-jack-graph.sh:93`'s deliberate `dry 0.0`. The identical bug is in `sl-health.py:97-104`. Also note both probes fire against loop 0 specifically — if the player is using loop 0, this is an audible dry-signal leak in the middle of a take. |

**New (not in the review):**

| # | Finding | Evidence |
|---|---|---|
| A8 | **`if srcs and ...` (`:149`) silently disables the entire audio check when nothing is connected to playback.** | If `system:playback` has no connections at all — the total-silence case, the one you most need reported — `srcs` is empty, the guard short-circuits, no problem is appended, and `write_alarm("ok")` runs at `:194`. The watchdog reports *healthy* on the worst audio failure it exists to catch. |
| A9 | **`playback_sources()` swallows every exception into `set()` (`:92-93`)**, which lands in the same trap: `jack_lsp` missing, jackd down, or a timeout all read as "ok". | `sl-watchdog.py:88-100`. The review noted the swallow in §3 but did not connect it to A8. |
| A10 | **The watchdog never checks whether SL is on the JACK bus**, despite `restart-sooperlooper.sh` already having `jack_client_visible()`. | This is the single missing check that would have named today's fault. |
| A11 | **`write_alarm` only fires on the wedge transition**, so the alarm file's `updated_at` is not a heartbeat. | `sl-watchdog.py:176-182` guarded by `if wedged_since is None`. |

### `apc_footswitch.py`

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| B1 | `_clear_loop` calls `grid.cancel()`, which clears only `_pending`; `_occupied` is untouched | ✅ Confirmed (mechanically) | `apc_footswitch.py:269-279`; `sl_grid_state.py:101-103`. |
| B2 | *"leaves `_occupied` non-empty and the grid established **forever**"* | ❌ **Wrong — it self-heals** | `sync_from_sl` calls `note_loop_content(self.loop, sl_state != SL_STATE_OFF)` **unconditionally on every update** (`:156-161`), before the `changed` gate. Auto-updates arrive at 100 ms (`sl_bench_listener.py:14`). The moment the engine reports `Off` for the cleared loop, `_occupied.discard()` runs, `note_loop_content` returns True, and `_on_grid_dropped` fires. Worst case latency ≈ one update. "Forever" requires the engine to *never* report `Off` for a cleared loop — which is today's situation, but for the §0 reason, not this one. |
| B3 | *"the bench itself provokes `Paused` (14) via `pause` in `reset_all_loops:444`"* | ⚠️ Partially true, and the review buried the real bug | `reset_all_loops` calls `grid.reset()` **first** (`:439`), which clears `_occupied` wholesale. A late `Paused` update re-adds to `_occupied` but `established` is already `False`, so `note_loop_content` returns `False` harmlessly (`sl_grid_state.py:133`). Residual `_occupied` only *delays* the next teardown; it does not keep a grid established. |
| B4 | **New, and larger than B1:** `reset_all_loops:444` sends `pause` — the **toggle** | ✅ New finding | `DECISIONS.md` 2026-08-15 states outright: *"`pause`/`trigger` are toggles; `pause_on`/`pause_off` are explicit. Toggles desync the moment bench and engine disagree — same root error as mirroring state instead of reading it."* `stop_all_loops:480` correctly uses `pause_on`. `reset_all_loops:444` uses the toggle, so a track reset performed on already-paused loops (i.e. straight after a Stop All) **un-pauses them**. Same defect in `stop-all-loops.sh:21-24`, which `reset-all-loops.sh:15` depends on. The review read `DECISIONS.md` and did not check the code against it. |
| B5 | `:313-317` asserts `STATE_PLAYING` with no engine confirmation | ✅ Confirmed | `if self.quantized and not defining: self._begin_quantize_wait() else: self.state = STATE_PLAYING`. |
| B6 | `:231` paints the LED from bench state, not `sl_state`, contradicting the comment at `:213-215` | ✅ Confirmed | `elif self.state == STATE_PLAYING: self._set_led(LED_GREEN, force=True)`. The comment two lines above says *"Read from SL, not bench state, so the blink reflects truth."* It then reads bench state. |
| B7 | A stale update pushing the pad to `STATE_STOPPED` makes the next tap send `pause_off`+`trigger` to an empty loop instead of `record`; `grid.arm()` at `:293` never runs | ✅ Confirmed as a code path, ⚠️ overstated as *the* live cause | `:324-345` vs `:289-296`. The path is real and the consequence (no take armed as definer, engine grid config unchanged) is correctly traced. But it requires a specific stale-delivery interleaving, and it self-heals on the next update; today's symptom has a much simpler cause (§0). |
| B8 | `:130-133` clears `_launch_queued` on every `PAUSED`/`MUTE` update, destroying the queued-launch blink | ✅ Confirmed | `elif sl_state in (SL_STATE_PAUSED, SL_STATE_MUTE): ... self._launch_queued = False`. A quantized `trigger` leaves the clip in `Mute` until the boundary; the next 100 ms poll clears the flag. `changed` is `False` (same `sl_state`, same bench state) so `_sync_led` is not called and the blink survives by accident — but any *other* `_sync_led` caller in that window drops the pad to solid yellow with the launch still pending. Exactly the failure the comment at `:43-47` says cost an evening. |
| B9 | "Queued to stop" (state 6) does not exist | ✅ Confirmed | `:322-323`: `self._hit("mute_on"); self.state = STATE_STOPPED`. With `mute_quantized 1.0` the clip sounds for up to a bar while the pad reads solid yellow. Directly violates the design rule at `:47` ("Solid = it happened. Blink = it is coming"). |
| B10 | `quantized` is set once at construction and never updated; the real predicate is `grid.established` | ✅ Confirmed | `:95`, set from `grid_active` in `sooperlooper-apc-bench.py:142`, never reassigned anywhere. After a mid-session grid drop the pad still arms a wait for a boundary that no longer exists — bounded only by `QUANTIZE_WAIT_TIMEOUT_S` (6 s). |
| B11 | Unreachable: the `else` at `:346-348` | ✅ Confirmed | `self.state` only ever takes the four `STATE_*` values. |
| B12 | `build_footswitches` shares one `GridState`, then `reset_all_loops` excavates it with a `break` (`:437-442`) and `stop_all_loops` with `next(...)` (`:482`) | ✅ Confirmed | Cosmetic but a genuine boundary inversion. |
| B13 | `_osc_send` (`:71`), `LED_YELLOW_BLINK` (`:32`), `self.num_loops` (`:96`) are dead | ✅ Confirmed | grep across `scripts/`, `patch_browser/`, `tests/`: definition sites only. |

### `sl_grid_sync.py` / `sl_grid_state.py` / `sl_loop_states.py`

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| C1 | `set_grid_active` sends 96 datagrams; `reset_all_loops` 32 more; nothing verifies | ✅ Confirmed | `sl_grid_sync.py:57-72` — 6 sends × 16 loops. `apc_footswitch.py:443-445` — 2 × 16. |
| C2 | §J is now a lie: the transport-mode probe was deleted, and `MPE_SL_GRID_CLOCK=transport` will send `sync_source = -1` with no probe | ✅ Confirmed | `sl_grid_sync.py:88-89` (review cites `:93-94` — off by five lines, substantively right). The module docstring at `:12-16` says *"Never apply a sync_source whose clock is not running"* and then does exactly that behind an env var. |
| C3 | `set_count_in` is a dead "Deprecated alias" | ✅ Confirmed | `sl_grid_sync.py:106-110`; no call sites. |
| C4 | `anchor_phase` and `display_bpm` imported in the bench (`:26,28`), never called | ✅ Confirmed | `anchor_phase` appears only at its definition and the bench import. `display_bpm` likewise (the `stabilize_display_bpm` hits in `patch_browser/midi_clock.py` are an unrelated symbol). |
| C5 | `ACTIVE_RECORD` dead; the SL state enum exists in four places, three using raw literals | ✅ Confirmed | `sl_loop_states.py:11` (no consumers); `sl_hud_monitor.py:37` `PLAYING_STATES = frozenset({4, 5})`; `sl_hud_state.py:68` `state in (4, 5)`; `sl-health.py:32-35` `STATE_NAMES`. **Worse than the review said:** `STATE_NAMES` omits `10: Mute` entirely (so a muted loop prints `"?"`), and it carries `5: Overdubbing`, which `sl_loop_states.py` doesn't define at all. The copies have already drifted. |
| C6 | `sl_grid_state.py` is well-engineered, keep as-is | ✅ Agreed | Pure, 146 lines, no I/O, no threads, the only module with meaningful tests. |

### Concurrency (§5)

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| D1 | `ThreadingOSCUDPServer` is thread-per-datagram | ✅ Confirmed | `sl_bench_listener.py:78`; python-osc's `ThreadingOSCUDPServer` is `socketserver.ThreadingUDPServer`, which spawns a thread per `process_request`. |
| D2 | **Out-of-order state delivery leaves a green pad over an empty loop *indefinitely*** | ⚠️ **Real mechanism, materially overstated** | Reordering is genuinely possible: the socket is read serially but handler threads race, so two datagrams dispatched microseconds apart can execute in either order. With 320 datagrams/s in bursts of 32 (`state` + `loop_len` × 16 loops), same-burst reordering is likely. **But** the two updates being reordered must be for the *same loop's* `state` control, and those are 100 ms apart — reordering requires >100 ms of thread-start skew. More importantly, auto-updates are **periodic, not edge-triggered**, so any transient inversion is corrected on the next tick. "Indefinitely" is wrong; "up to ~100 ms of LED lag with a possible wrong-branch tap in that window" is right. Downgrade 🔴 → 🟡. |
| D3 | **Stale updates registered before `reset_all_loops` land after it and re-populate state** — *"mechanism behind both 'grid still on after reset' and 'green with no audio'"* | ⚠️ **Real but narrow; and it is not the reported mechanism** | The interleaving exists: `reset_all_loops` iterates 16 footswitches setting `state = STATE_IDLE` (`:446-450`) while OSC handler threads concurrently set `state = STATE_STOPPED`. Window ≈ the duration of the reset loop plus one update interval. A tap inside that window takes the wrong branch. But (a) `established` is not corrupted (B3), and (b) it self-corrects within ~100 ms. Calling it *the* mechanism behind the reported symptoms is unsupported — §0 explains both symptoms completely and permanently. Downgrade 🔴 → 🟡. |
| D4 | `GridState` mutated from both threads — `arm()` on main, `establish()`/`note_loop_content()`/`reset()` on OSC | ✅ Confirmed | `apc_footswitch.py:293` (main, from `_tap`) vs `:157`/`:184` (OSC handler thread). No lock anywhere in either file. The `arm()`-then-`reset()` interleaving the review describes is real: `reset_all_loops` on the main thread calls `grid.reset()` which clears `_pending`, and `_maybe_establish_grid` on an OSC thread reads `is_pending`. The take silently fails to define the grid with no log line explaining it. This is the strongest of the four race claims. |
| D5 | `rtmidi.MidiOut` used from multiple threads | ✅ Confirmed | `_set_led` → `self._midi_out.send_message(...)` reached from `_tap` (main), `sync_from_sl` (OSC thread), and `poll_led` (main). python-rtmidi does not document thread safety. Severity 🟡 is right — 3-byte messages are written under one `send_message` call, so interleaving is unlikely in practice. |

### Tests (§6)

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| E1 | Every test drives `LoopFootswitch` against `MagicMock` and asserts on the OSC call list | ✅ Confirmed | `tests/test_apc_footswitch.py` — `MagicMock()` in every fixture; assertions are on `osc.send_message.call_args_list`. |
| E2 | `reset_all_loops` has zero tests; `sl-watchdog.py` has zero tests | ✅ Confirmed | grep: `stop_all_loops` appears at `test_apc_footswitch.py:315,321`; `reset_all_loops` appears nowhere in `tests/`. No `test_sl_watchdog*` file exists. |
| E3 | **`test_apc_footswitch.py:120-133` asserts `grid.established` after clearing the only clip — "the opposite of §K.3", "passes only because of bug B"** | ❌ **Wrong, and this is the review's worst misread** | The test is `test_hold_clear_drops_grid_when_engine_reports_last_clip_off`. Its docstring: *"No clips, no grid — driven by SL state, not bench hold-clear alone."* The assertion the review quotes carries the message *"hold-clear alone must not drop grid before SL confirms OFF"*, and the very next lines are `fs.sync_from_sl(SL_STATE_OFF)` → `assertFalse(grid.established)`. The test asserts the **documented design**: `sl_grid_state.py:126-127` — *"Driven by engine state (SL_STATE_OFF), not by bench bookkeeping, so it stays true however the clips were cleared"* — and `DECISIONS.md`: *"drops the grid, driven by engine state."* The review read half a test, called it bug-cementing, and then recommended a fix (§4-B: "call `grid.note_loop_content(loop, False)` in `_clear_loop`") that **would contradict a recorded engineering decision**. Do not apply that fix as written. |
| E4 | That test's second `on_pad_up()` has no matching `on_pad_down` and is a silent no-op | ✅ Confirmed | `on_pad_up` guards on `if self._pad_down and not self._hold_fired` (`:363`); `_pad_down` is already `False`. The test never stops the recording it believes it stopped. Sloppy, but it does not change what the test proves. |
| E5 | `beat_and_bar` / `beat_and_bar_from_transport` are production code existing only so a test can exist | ✅ Confirmed | `sl_hud_monitor.py:41-47` and `:65-70`. The only production caller in that file is `beat_and_bar_from_tempo` (`:173`). Sole references to the other two: `tests/test_sl_hud_state.py:10,15-22`. The docstring literally says *"kept for tests."* |
| E6 | Assertions on private state will fight a refactor | ✅ Confirmed | `_stop_queued`, `_led_transition`, `awaiting_quantize`, `fs._wait_since -= ...` all appear as test assertions/manipulations. |
| E7 | 55 tests, 0.26 s | ✅ Close enough | The eight looper-adjacent test files run 58 tests in 0.17 s here. Immaterial. |

### Misc

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| F1 | Two module identities: production `sys.path.insert` + `import apc_footswitch`; tests `import scripts.sooperlooper.apc_footswitch` | ✅ Confirmed | `sooperlooper-apc-bench.py:16-17` vs `tests/test_apc_footswitch.py:6`. Both modules can coexist in one interpreter with independent module-level state. |
| F2 | `import time` inside two functions | ✅ Confirmed | `sl_bench_listener.py:49,59`. |
| F3 | `sl_bench_listener.py` bind failure → fatal `SystemExit` with reason and fix, and that is right | ✅ Agreed | `:77-87`. |
| F4 | 2 ms busy-poll; 320 thread spawns/s | ✅ Confirmed | `sooperlooper-apc-bench.py:201` `time.sleep(0.002)`; 16 loops × 2 controls × 10 Hz. |
| F5 | 20+ undocumented env vars | ✅ Confirmed by inspection | Every module reads its own `MPE_*` set with no central registry. |

---

## 2. Severity re-assessment

| # | Issue | Reviewer | Mine | Δ | Reasoning |
|---|---|---|---|---|---|
| **NEW** | SL orphaned from JACK after a jackd restart; no detection, no recovery, no escalation | — | **Critical** | ↑↑ | Not in the review. It is the live fault, it explains both reported symptoms, and it is the "unknown wedge" that has burned two evenings. |
| A1/A2 | Watchdog discards repair output | 🔴 | **High** (diagnostics) | ↓ as a cause, ↑ as a cost | Confirmed defect; wrong causal claim. The three lines are worth writing — they would have printed "cannot connect: no such port" and ended the mystery — but they fix visibility, not the repair. |
| A3 | `need_cmd oscsend` gates the repair | 🔴 | **Negligible** | ↓↓↓ | Falsified on the appliance. |
| A7 | `dry` probe leaves 0.5 on a read timeout | 🟡 | **High** | ↑ | Silently undoes the deliberate `dry 0.0`, every 10 s, on loop 0, mid-performance. Audible. Present in *two* tools. Under-rated. |
| **A8** | `if srcs and ...` reports "ok" when nothing is connected to playback | — | **High** | ↑ | New. The watchdog is blind to total silence — the failure it exists to catch. |
| B1/B2 | `_clear_loop` leaves `_occupied` | 🔴 | **Low** | ↓↓ | Self-heals in ≤100 ms via `sync_from_sl`. And the recommended fix contradicts a recorded decision (E3). |
| **B4** | `reset_all_loops` uses the `pause` toggle | — | **High** | ↑ | New. Violates `DECISIONS.md` explicitly. Deterministic desync, no race needed. |
| B5/B6 | Optimistic `STATE_PLAYING` + LED from bench state | 🔴 | **High** | ↓ slightly | Real, deterministic, and the direct cause of "green pad lies". Keep near the top — but it is a *display and dispatch* bug, not the reason nothing recorded. |
| B8 | `_launch_queued` cleared by the poll | 🔴 | **Medium** | ↓ | Currently masked by the `changed` gate. Latent, will bite on any `_sync_led` refactor. |
| B9 | Missing "queued to stop" state | 🔴 | **Medium** | ↓ | UX correctness, not a fault. Real, and it breaks the design's own promise. |
| B10 | `quantized` frozen at construction | — | **Medium** | — | Bounded by the 6 s timeout, but produces a 6 s dead pad after a grid drop. |
| D2 | Out-of-order delivery | 🔴 | **Medium** | ↓ | Real mechanism, self-correcting, ~100 ms exposure. |
| D3 | Stale updates survive reset | 🔴 | **Medium** | ↓ | Real, narrow window, self-correcting, `established` uncorrupted. |
| D4 | `GridState` mutated from two threads | 🔴 | **Medium-High** | — | The one race that produces a *silent, non-self-healing* wrong outcome: a take that fails to define the grid with no log line. Strongest of the four. |
| D5 | `MidiOut` cross-thread | 🟡 | **Low** | — | Agreed. |
| E3 | "Test contradicting the spec" | 🟡 | **Wrong** | ✗ | Withdraw. The test encodes the spec. |
| C2 | §J re-armed behind an env var | 🟡 | **Medium** | — | Agreed; a documented "cost an evening" failure is one env var away. |

---

## 3. Blind spots — additional findings

Beyond §0 and the new items already tabled (A8–A11, B4, C5):

**G1 — `spike-internal-sync-phase.py` is a loaded gun (🔴).** 89 lines, runnable,
sends `playback_sync 1` (`:49`) and `sync 1` (`:48`) to all 16 loops and sets
`tap_tempo 0` (`:44`). `DECISIONS.md` 2026-08-15 records all three as *known
wrong*: *"`ports[PlaybackSync] = 0.0f` is SL's default; forcing 1 made a fresh
clip wait for the next boundary after record-stop had already landed on one"*
and *"The `tap_tempo` guess carried since `8d7a426` was never needed."* It also
never disables `smart_eighths`, so it re-arms the documented cycle-doubling bug
below 60 BPM. Anyone running this spike to "check the phase" silently
reconfigures the engine into the exact broken state the session spent two days
escaping — with no way to tell from the pads. `DECISIONS.md` already ordered the
deletion of the other dead spikes (`jack_timebase.py`, `spike-jack-transport.py`,
`jack_transport_util.py`, `start-jack-timebase.sh`); those four still exist too.
**Delete this file.**

**G2 — `stop-all-loops.sh` uses the `pause` toggle, twice (🟡).** `:21` sends
`/sl/-1/hit pause` and then `:22-25` sends `pause` to each loop *individually* —
so every loop gets the toggle **twice** (once via `-1`, once directly) and ends
up back where it started. The script's stated purpose ("pause every loop") is
not achieved on a running set. `reset-all-loops.sh:15` calls it first, so the
CLI track-reset path inherits the bug. Compare `apc_footswitch.py:480`, which
gets it right with `pause_on`.

**G3 — `sl-health.py` carries the same `dry`-restore bug as the watchdog (🟡).**
`:97-104`: `if before is not None` gates the restore. A health check that leaves
the instrument in a different state than it found it.

**G4 — `sl-health.py --record-test` is destructive with no confirmation (🟡).**
`:141` sends `undo_all` to loop 0. The `--help` text does say "clears loop 0",
which is honest, but it is one flag away from destroying a take on a tool whose
whole purpose is to be run when things look wrong.

**G5 — the bench registers auto-updates against an engine that may not exist
(🟢).** `sooperlooper-apc-bench.py:151-153` runs `register` unconditionally at
startup; today the bench (20:14:11) started before SL (20:16:09). Saved only by
`maybe_reregister` every 15 s. Works, but it means the first 15 s of any session
started in the wrong order is silently blind.

**G6 — `apply_grid_sync` is never re-applied after an engine restart (🟡).** It
runs once at bench startup (`:109-112`). SL restarting (the documented remedy
for a wedge) resets every parameter to defaults — `smart_eighths` back on,
`sync_source` back to default — while the bench keeps running with `grid` state
intact and no idea the engine's configuration evaporated. There is no
engine-generation detection anywhere in the bench.

---

## 4. What the review got right, and why it matters

- **`sl_grid_state.py` is the asset.** Confirmed. It is the only module with a
  model rather than a history. Any rewrite should be measured against it.
- **The engine-fact catalogue is the real value.** Confirmed and reinforced by
  this audit: §0 was solvable only because `restart-sooperlooper.sh` and the
  `sl-watchdog.py` docstring had written down the symptom signature precisely
  enough to match against `ps` output.
- **"The comments are load-bearing"** is the sharpest observation in the review
  and is correct. B4/G1/G2 are all cases of the *comments and `DECISIONS.md`
  being right while the code diverged* — which is what happens when prose is
  the only structure.
- **The optimistic-state critique (B5/B6) is the correct top control-layer
  finding.** It is deterministic, needs no race, and it is why the pads lie.
  Fixing it would have turned today's silent failure into a visibly-stuck pad
  within one poll interval.
- **The "no fake engine" observation is the right diagnosis of the test suite.**
  Reinforced: a `FakeEngine` that refuses to drain its command queue would
  reproduce §0 in a unit test.

---

## 5. Prioritized action matrix

| Priority | Issue | Verdict | Effort | Depends on |
|---|---|---|---|---|
| **P0** | **Add a JACK-client-visibility check to `sl-watchdog.py`**; when SL is an orphan, say so by name ("engine is not on the JACK bus — jackd restarted under it; `mpe looper sl-restart` will fix it and WILL destroy loops") instead of endlessly retrying an impossible `jack_connect` | NEW ✅ | Quick fix | — |
| **P0** | **Fix the `if srcs and ...` guard (A8)** so an empty playback graph is reported, not swallowed; distinguish "jack_lsp failed" from "nothing connected" in `playback_sources()` | NEW ✅ | Quick fix | — |
| **P0** | **`reset_all_loops:444` → `pause_on`** (and `stop-all-loops.sh` → `pause_on`, once, not twice) | NEW ✅ | Quick fix | — |
| **P1** | **Log the watchdog's repair output** — `returncode`, `stdout`, `stderr`, and the observed `playback_sources()` on failure | ✅ (A1) | Quick fix | — |
| **P1** | **Restore `dry` unconditionally** in `sl-watchdog.py` *and* `sl-health.py`; better, probe a control with no audible effect | ✅ (A7/G3) | Quick fix | — |
| **P1** | **Render the LED from `sl_state` only** — do what the comment at `:213-215` already says | ✅ (B6) | Half-day | — |
| **P1** | **Dispatch `_tap` from `sl_state`, not `self.state`**; delete `self.state` and `self.quantized` (use `grid.established`) | ✅ (B5/B10) | Half-day | LED fix |
| **P1** | **Delete `spike-internal-sync-phase.py`** and the four dead JACK-timebase files `DECISIONS.md` already condemned | NEW ✅ | Quick fix | — |
| **P1** | **Re-alarm on every wedge cycle** so the alarm file is a heartbeat, not a tombstone | NEW ✅ | Quick fix | — |
| **P2** | **Serialize OSC updates onto the main loop** via `queue.Queue` (`ThreadingOSCUDPServer` → `BlockingOSCUDPServer` on its own thread, or a `queue` drained by the 2 ms poll). Kills D2/D3/D4/D5 by construction | ⚠️ (D2/D3) ✅ (D4) | Multi-day | LED/dispatch fixes |
| **P2** | **Add the "queued to stop" state**; clear `_launch_queued` only on the transition into `PLAYING` | ✅ (B8/B9) | Half-day | LED fix |
| **P2** | **Re-apply `apply_grid_sync` on detected engine restart** (generation counter on SL's pid or an engine-identity probe) | NEW ✅ (G6) | Half-day | — |
| **P2** | **Collapse the four state enums onto `sl_loop_states.py`**; add `10: Mute`, decide on `5: Overdubbing` | ✅ (C5) | Half-day | — |
| **P2** | **Build the `FakeEngine`** (~80 lines): consumes commands, emits a state feed, and can be told to stop draining its queue so §0 is reproducible in a test | ✅ | Half-day | — |
| **P2** | **Restore the transport-mode probe or delete the `transport` branch** — §J must stop being a lie | ✅ (C2) | Quick fix | — |
| **P3** | Delete dead code: `_osc_send`, `LED_YELLOW_BLINK`, `num_loops`, `ACTIVE_RECORD`, `set_count_in`, `anchor_phase`+`display_bpm` imports, `beat_and_bar`, `beat_and_bar_from_transport` and their tests | ✅ | Quick fix | — |
| **P3** | Extract `SlClient` shared by the four OSC processes | ✅ | Half-day | — |
| **P3** | Pass `GridState` to session-level functions instead of excavating it from a footswitch | ✅ (B12) | Quick fix | — |
| **P3** | One module identity: make production import `scripts.sooperlooper.*` | ✅ (F1) | Quick fix | — |
| **P3** | `rtmidi` callback instead of the 2 ms busy-poll | ✅ (F4) | Half-day | queue serialization |
| — | ~~Drop `need_cmd oscsend` from `wire_connect`~~ | ❌ | — | Falsified; do it as hygiene if you like, but not as a fix |
| — | ~~`_clear_loop` must call `note_loop_content(loop, False)`~~ | ❌ | — | Contradicts `DECISIONS.md` and the test that encodes it |
| — | ~~Delete `test_apc_footswitch.py:120-133` as bug-cementing~~ | ❌ | — | It encodes the spec. Fix its stray `on_pad_up()` instead |

---

## 6. Disagreements and judgment calls

**1. The rewrite is premature, and the estimate is optimistic.**
Not because the target architecture is wrong — `loop_model.py` + `led_table.py`
+ `sl_session.py` + `sl_client.py` is a sound decomposition and I'd endorse it
as a destination. But the evidence does not support it as the *next action*.
The reported symptoms are caused by an orphaned engine (§0) and two
deterministic bugs (B4, B5/B6) that are together about a day's work. Rewriting
700 lines to fix symptoms whose actual cause is a jackd restart is architecture
for its own sake — and it would have *hidden* §0 for another two evenings,
because a rewritten control layer sending `record` into a dead nonrt queue
behaves identically.

The ~400-lines-replacing-~700 estimate is also light. It omits: the
`FakeEngine`, rewriting `test_apc_footswitch.py` (343 lines), the shared
`sl_client.py` retrofit into four separate live processes, and the fact that
`sl_session.py` at "~150 lines" must absorb `reset_all_loops`, `stop_all_loops`,
grid-establishment callbacks, engine reconfiguration, and re-registration. Call
it 600–700 new lines and a week, not 400 and two days. That may still be worth
it — but price it honestly.

**Sequence I'd argue for:** P0 + P1 this week (about a day, all narrow, all
independently verifiable) → run a real session → *then* decide on the rewrite
with the engine actually alive and the pads actually truthful. You will have
much better information about which races are real.

**2. The single `queue.Queue` thread is correct, and does not cost latency.**
Pushing back on the framing in the prompt: it does not add latency against the
2 ms MIDI poll, because the poll loop already runs every 2 ms and would drain
the queue in the same pass. The OSC handler's job becomes `q.put(...)` —
microseconds — and the state mutation happens ≤2 ms later on the main thread.
Today's path does the mutation on an arbitrary OS-scheduled thread, whose
start latency on a loaded Pi is not obviously better than 2 ms. So: no latency
cost, and it removes four classes of race. Endorsed — but as P2, after the
deterministic bugs, because it fixes the *rarer* half of the problem.

I would also swap `ThreadingOSCUDPServer` for `BlockingOSCUDPServer` on a
single thread rather than keeping thread-per-datagram and enqueueing from it.
That eliminates the 320 thread spawns/second as well, which the review
correctly flagged as the wrong primitive at that rate.

**3. "Serialize state mutation" as backlog item #1 is the wrong #1.**
The review's own #1 claims it "fixes reset-still-quantized and
green-with-no-audio at the root". It does not. Both are fully explained by an
engine that is not on the audio bus and not draining its command queue. Serialize
the threads and both symptoms persist unchanged. Item #1 should be: *make the
watchdog detect and name the orphan.*

**4. On "there is no seam to test at, other than a `MagicMock`."**
Agreed, and the review's own recommendation understates its value: the
`FakeEngine` should be able to simulate the *wedge* (accept datagrams, answer
`/get`, never apply `/set` or `/hit`). That is a ~10-line mode flag, and it is
the only way the codebase will ever have a regression test for the failure that
has now cost three evenings.

**5. On tone toward the tests.**
"Delete `test_apc_footswitch.py` wholesale... before touching the
implementation, so the fight is honest" is good advice in general and I'd keep
it — but the review's evidence for it (§4-H) is its single clearest factual
error. Deleting a suite on the strength of a misread test is how correct
behaviour gets lost. Rewrite against pure functions, yes; but port each existing
test's *intent* deliberately rather than discarding the file.

**6. Solo-project calibration.**
The review is largely well-calibrated for a one-person appliance project — it
does not demand CI, coverage gates, or type checking. Two places it applies
big-team standards: "four hand-rolled OSC clients" (real duplication, but four
independently-restartable processes with no shared dependency is a legitimate
robustness choice on an appliance) and the module-identity complaint (F1 — real,
but it has never actually bitten). Both belong at P3, which is where I've put
them.

---

*Audit method: every file the review cites was read in full (`apc_footswitch.py`,
`sl_grid_state.py`, `sl_grid_sync.py`, `sl_loop_states.py`, `sl_bench_listener.py`,
`sooperlooper-apc-bench.py`, `sl-watchdog.py`, `sl-health.py`, `sl_hud_monitor.py`
head, `sl_hud_state.py`, `wire-jack-graph.sh`, `restart-sooperlooper.sh`,
`stop-all-loops.sh`, `reset-all-loops.sh`, `spike-internal-sync-phase.py`,
`test_apc_footswitch.py`), all dead-code claims grep-verified across
`scripts/`, `patch_browser/` and `tests/`, the eight looper test files run
(58 pass, 0.17 s), and the live appliance inspected read-only over ssh
(`ps`, `jack_lsp`, `which`, `stat`, `cat` of the alarm file). Nothing on the Pi
was started, stopped, or written.*
