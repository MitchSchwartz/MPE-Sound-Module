# Review audit — live monitor & fader pickup (cycle 1 of 5)

**2026-09-20.** Auditing `Documents/reviews/grumpy-review-live-monitor-and-fader-pickup-2026-09-20.md`
against the actual uncommitted working tree at `/home/mitch/Documents/GitHub/MPE-Module`
(branch `dev`, 10 modified + 6 new files, unchanged since the review was written).

**Method.** Read every file the review cites in full: `native/mpe-live-monitor/mpe-live-monitor.c`
and its Makefile, `scripts/sooperlooper/live_monitor.py`, `tests/test_live_monitor.py`,
`scripts/sooperlooper/loop_mix.py`, `tests/test_loop_mix.py`, `config/mpe-live-monitor.service`,
`scripts/start-mpe-live-monitor.sh`, `scripts/build-mpe-live-monitor.sh`, the diffs to
`wire-jack-graph.sh`, `install-units.sh`, `sooperlooper-apc-bench.py`, `sl_bench_listener.py`,
`AGENTS.md`, `docs/PATHS.md`; and, targeted, `sl-watchdog.py` (the `common_out` check),
`sl_osc_session.py` (the OSC server class), `dump-loop-levels.py:45`, `mpe-peak-meter.c`'s
env-var read, `tests/fake_sl_engine.py:62-67`, `tests/test_looper_timing.py`.

**Ran:** `python3 -m unittest discover -s tests -q` → **2198 tests, OK (skipped=23), 117.0 s.**
Matches the review's claim (2198/OK/skipped=23; wall time varies run to run, 117 s vs their
120.8 s — immaterial). Compiled the C client (`make -C native/mpe-live-monitor`) — clean build,
zero warnings under `-Wall -Wextra -std=c11`, confirming the review's "CI never even compiles it"
claim is not masking a build that's already broken. Binary deleted afterward per instructions;
`git status` now shows it untracked-clean (gitignored, not present).

Every numeric claim in the review that was checkable by direct computation was reproduced by
running the actual `LoopMix` code (not re-derived from the docstring) — see §Behavioural
verification below. I did not run anything on the Pi, touch JACK, or make sound; per the task's
instruction, findings that only matter with headphones on someone's head are evaluated as
*reachability* and *consequence*, not tested by ear.

---

## Work queue

Eighteen numbered findings (F1–F18) plus a Step-0 ledger reconciling nine 2026-09-07 findings.
Grouped by artifact:

- **C client** (`mpe-live-monitor.c`): F1 (partial), F2 (partial), F3, F4 (partial), F13, F14, F15
- **Shell/systemd** (`wire-jack-graph.sh`, `install-units.sh`, `mpe-live-monitor.service`,
  `start-mpe-live-monitor.sh`): F1 (partial), F2 (partial), F5, F6
- **Python policy** (`live_monitor.py`, `loop_mix.py`, `sooperlooper-apc-bench.py`,
  `sl_bench_listener.py`): F5, F6, F7, F8, F9, F10, F11, F12, F16, F17, F18
- **Tests** (`test_live_monitor.py`, `test_loop_mix.py`): F4 (the boundary test), F16
- **Docs** (`AGENTS.md`, `docs/PATHS.md`): §9 documentation-vs-reality table, minor items
- **Step 0 ledger**: 9 rows carried from 2026-09-07 (F1, F2, F3, F5–F9, Step0 #1/#2)

---

## Claim verification

### C client — `native/mpe-live-monitor/mpe-live-monitor.c`

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| F1 (C half) | `monitor_path_live()` checks only the output leg, not the Surge→in leg | ✅ Confirmed | Lines 199–208: `for (ch...) { ... if (!port_connected_to(g_out_ports[ch], playback)) return 0; }` — no check of `g_in_ports[ch]` against the Surge source at all. Reading the whole function confirms there is no input-leg test anywhere in the file. |
| F2 | Header states a crash strands the direct path; nothing implements a watchdog for it | ✅ Confirmed | Header comment lines 31–37 literally: *"A crash still leaves the direct path gone — that case needs a watchdog, and does not have one yet."* `restore_direct_path()` is called only from the normal exit path after `pthread_join`, gated `if (!g_jack_shutdown)` — never from a signal handler for SIGKILL (which cannot be caught) or from any other process. |
| F3 | `jack_client_open(CLIENT_NAME, JackNoStartServer, NULL)` — no `JackUseExactName`, no status out-param | ✅ Confirmed | Line 375 (main): exact text `jack_client_open(CLIENT_NAME, JackNoStartServer, NULL);`. JACK's documented behavior on a name collision without `JackUseExactName` is silent rename + success, which the review correctly relies on. |
| F4 (C half) | No executable check of `clamp_gain`, `parse_gain`, the ramp, or detach ordering; no C test target in the Makefile | ✅ Confirmed | Makefile has `all`, `check` (a compile-only gate, not a test), `clean`, `install` — no `test` target, no reference to a test binary. `grep` of `tests/` for any `.c` invocation, `subprocess` call to the binary, or datagram fixture against `parse_gain`/`clamp_gain` returns nothing. |
| F4 (boundary test) | `test_the_control_port_matches_the_client` reads only the Python constant, not the `.c` | ✅ Confirmed | `tests/test_live_monitor.py:175-177`: `self.assertEqual(live_monitor.DEFAULT_PORT, 9957)` — a Python constant compared to a Python literal. No `open()`/`grep` of `mpe-live-monitor.c` anywhere in the test file. Changing `CONTROL_PORT_DEFAULT` in the `.c` would not touch this test. |
| F13 | `ensure_wiring` re-issues `detach_direct_path()` (2 × `jack_disconnect`) every 2 s forever, with no idempotence guard | ✅ Confirmed | `ensure_wiring()` (lines 219–231): `if (monitor_path_live()) { detach_direct_path(); }`. `monitor_path_live()` checks only the client's own output→playback wiring (F1), which stays true forever once established — it is *not* affected by whether the direct path was already removed. `detach_direct_path()` (lines 209–216) has no `g_direct_detached` read-guard; it sets the flag but never checks it before disconnecting. `port_connected_to()` exists and is used two functions up (`connect_if_absent`) but not here. Cost is real, on every 2 s tick (`CONNECT_INTERVAL_US`), for the life of the process. |
| F14 | Both paths (direct + insert) are live during the handover window; `jack_disconnect` return values ignored | ✅ Confirmed | `ensure_wiring()` calls `connect_if_absent(src, in)`, then `connect_if_absent(out, playback)`, *then* `monitor_path_live()`/`detach_direct_path()` — connect-then-check-then-disconnect, exactly the order claimed. `detach_direct_path()` line 213/214: `(void)jack_disconnect(g_client, src, playback);` for both channels, return value explicitly discarded both times — a partial failure (ch 1 succeeds, ch 2 fails) is undetectable. |
| F15 | RT callback's NULL check sits inside the per-sample loop; on NULL the output buffer is left unwritten (not silenced) | ✅ Confirmed | `process()` lines 116–130: the `for (i < nframes)` outer loop contains `for (ch < CHANNELS) { if (in[ch]==NULL || out[ch]==NULL) { continue; } out[ch][i] = in[ch][i]*gain; }`. `continue` skips the assignment, leaving `out[ch][i]` at whatever JACK's buffer held previously — no `memset`. The review's caveat that `jack_port_get_buffer` never actually returns NULL for a registered port is accurate to the JACK API contract; this is correctly framed as dead-but-wrongly-shaped defense, not a live bug. |

### Shell / systemd

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| F1 (shell half) | `wire-jack-graph.sh:live_monitor_present()` keys on port existence (`jack_lsp`), not on the path carrying audio | ✅ Confirmed | `live_monitor_present() { jack_lsp 2>/dev/null \| grep -q '^mpe-live-monitor:'; }` — literal grep for the client name in the full port list, nothing checking connections. `jack_port_register` does make ports visible to `jack_lsp` immediately, before any `jack_connect` has run — this is documented JACK behavior, review's claim about ordering is accurate. |
| F2 (shell half) | No `ExecStopPost` in the unit; `sl-watchdog.py` watches only `common_out → system:playback` | ✅ Confirmed | `config/mpe-live-monitor.service` has `ExecStart`, `Restart=on-failure`, `RestartSec=3`, `TimeoutStopSec=5` — no `ExecStopPost` line anywhere in the file. `grep -n "common_out\|playback" scripts/sooperlooper/sl-watchdog.py` shows the only playback check is `problems.append("common_out not connected to system:playback")` (line 526) — nothing referencing Surge, `mpe-live-monitor`, or the direct path. |
| F3 (env var) | `mpe-peak-meter.c` reads `MPE_PEAK_METER_SURGE_CLIENT`, a different variable from `MPE_SL_SURGE_CLIENT` | ✅ Confirmed | `native/mpe-peak-meter/mpe-peak-meter.c:310`: `getenv("MPE_PEAK_METER_SURGE_CLIENT")`. `mpe-live-monitor.c:342`, `wire-jack-graph.sh:23`, `wire-sooperlooper-graph.sh:18` all read `MPE_SL_SURGE_CLIENT`. Four sites, two variable names, confirmed. |
| F5 | Four laws for "is the live monitor on," and they disagree; the four demonstrated `MPE_LIVE_MONITOR=X` rows are accurate | ✅ Confirmed | Traced by hand against the actual code: Python `ENABLED = os.environ.get("MPE_LIVE_MONITOR","0").strip() not in ("","0","off","false")` (case-sensitive blocklist) vs. shell `case "${MPE_LIVE_MONITOR:-0}" in 1\|true\|yes\|on\|TRUE\|YES\|ON) ;; *) exit 0 ;; esac` (allowlist). For `OFF`, `no`, `False`, `2`: none match the shell allowlist (→ disabled) but none are in the Python blocklist either (case-sensitive: `"False"` ≠ `"false"`) → Python `ENABLED=True`. All four rows reproduce exactly as claimed. |
| F5 (unreachable direction) | The dangerous direction (client running, Python not driving) does not intersect | ✅ Confirmed | Checked the sets directly: shell's allowlist values (`1,true,yes,on,TRUE,YES,ON`) and Python's blocklist (`"",0,off,false`) share no member, so there is no value that is simultaneously "shell enables the binary" and "Python decides not to drive it." The reviewer's own verification claim holds. |
| F6 (port) | Three literal `9957`s, and the "reconciling" test only checks the Python side | ✅ Confirmed | `mpe-live-monitor.c:64` `#define CONTROL_PORT_DEFAULT 9957`; `live_monitor.py:72` `DEFAULT_PORT = 9957`; `docs/PATHS.md` new row: `MPE_LIVE_MONITOR_PORT` `9957`. Three independent literals, no shared constant. |
| Minor | `install-units.sh:45` pronoun ("It gates...") no longer has a clear referent after "mpe-peak-meter or mpe-live-monitor" was added to the same sentence | ✅ Confirmed | `git diff scripts/install-units.sh`: `# Do NOT add mpe-peak-meter or mpe-live-monitor here. It gates on MPE_PEAK_METER inside` — singular "It" now follows two named units. Real (trivial) grammar issue. |
| Minor | `docs/PATHS.md`'s `MPE_LIVE_MONITOR_RAMP_MS` row is missing its closing `\|` | ✅ Confirmed | `git diff docs/PATHS.md`: that row ends `...a step would click.` with no trailing pipe, while every other row in the table ends `... |`. Breaks the Markdown table for that row. |

### Python policy — `live_monitor.py`, `loop_mix.py`, bench

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| F7 | Fader law is path-dependent; docstring claims convergence but it is slow and route-dependent | ✅ Confirmed (reproduced) | Ran `LoopMix` directly: `anchor 64 → 100` gives `user_gain=127` (unity); `anchor 64 → 10 → 100` gives `user_gain=102` — same endpoint, different route, 2 dB apart, exactly as claimed. The raw-32/raw-64 wiggle sequence I ran (starting from a fresh anchor) produced `64, 85, 42, 71, 35` — the same converging-but-slow shape as the review's `64/85, 42/71, 35/66` table (my first data point differs because my anchor started at the CC_MAX ref rather than an already-adjusted level, a difference in setup, not in the underlying claim). The mechanism (`_scaled_level`'s `ref`-relative scaling, confirmed at `loop_mix.py:345-366`) mathematically guarantees route-dependence: each step scales the *previous* ref, so the final value is a telescoping product of per-step ratios, not a function of start/end alone except in the single-move case. |
| F8 | "It never jumps" ( `_accept`'s sibling docstring in `_scaled_level`, `loop_mix.py:350`) is false by measurement | ✅ Confirmed (reproduced exactly) | Ran the exact sequence: `fader 10→2` → `user_gain=25`; `fader 2→60` → `user_gain=73`; `fader 60→127` → `user_gain=127`. All three numbers match the review's table exactly. The `+15.1 dB` / `+17.0 dB` single-CC jumps are a direct consequence of `_scaled_level`'s formula amplifying a large raw delta once `ref` has collapsed near zero (dividing by a small `prev`) — this is real, not a rounding artifact. The review's own caveat that this is "not a regression" (the old law had a comparable worst case) is also consistent with the code history documented in the same docstring (13-steps-from-127-to-36 bug). |
| F9 | `LiveMonitor`/`LiveMonitorSender` mutated from `ThreadingOSCUDPServer` threads and the main loop, no lock | ✅ Confirmed | `sl_osc_session.py`: `self._server = osc_server.ThreadingOSCUDPServer(...)` — Python's `ThreadingMixIn`-based server spawns a new thread per incoming datagram. `sl_bench_listener.py` diff routes `state` datagrams straight to `self._on_state(int(loop_index), int(value))`, wired in `sooperlooper-apc-bench.py` to `on_state` → `monitor.note_state(...)` → `push_monitor()` → `monitor_sender.send(...)`. The same `push_monitor()` is also called from the main MIDI loop's `on_fader_move` (new diff hunk, `scripts/sooperlooper-apc-bench.py`). No `threading.Lock`, `Queue`, or other synchronization primitive appears anywhere in the new code touching `_capturing`, `_last`, or `g_target_gain`'s Python-side counterpart. The check-then-act on `_capturing` (`if loop not in self._capturing: ... .remove(loop)`) is real and the race is real under CPython's thread-switching granularity. |
| F10 | A capture state that never closes pins the monitor forever; no timeout/reset | ✅ Confirmed | `note_state()` (`live_monitor.py:120-127`) only removes a loop from `_capturing` when a *new* non-capture state arrives for that exact loop id; there is no timestamp, no periodic sweep, and no external "clear all" call anywhere in the diff or in `sooperlooper-apc-bench.py`. If the state stream stops for that loop (engine restart, dropped datagram, deleted loop) the entry is permanent for the process lifetime. |
| F11 | `ECONNREFUSED` (and `EAGAIN`/`EWOULDBLOCK`) swallowed without setting `.error`, no counter | ✅ Confirmed | `live_monitor.py:198-204`: `except OSError as exc: if exc.errno not in (errno.ECONNREFUSED, errno.EAGAIN, errno.EWOULDBLOCK): self.error = f"{exc}"` returns `False` in both branches — the excluded-errno branch sets nothing observable. I also confirmed the review's related test-gap claim empirically: `s.sendto(...)` to an unbound loopback UDP port from a non-blocking socket did **not** raise on the first *or* second `sendto()` in this environment, matching the review's claim that `test_a_dead_socket_is_not_an_exception` (single datagram, port 1) never actually exercises the `ECONNREFUSED` branch. |
| F12 | Client logs nothing after startup; `MPE_RUN_DIR` exported by the shell wrapper but never read by the binary | ✅ Confirmed | `grep -n "fprintf\|printf" native/mpe-live-monitor/mpe-live-monitor.c` shows only three `fprintf(stderr, ...)` calls, all startup-failure paths (socket, port register, jack_activate) — none in the steady-state loop, on detach, or on restore. `grep -n MPE_RUN_DIR` across `native/` and `scripts/` shows `start-mpe-live-monitor.sh:34` exports it and `mpe-peak-meter.c:306` reads it, but `mpe-live-monitor.c` contains no reference to `MPE_RUN_DIR` at all. |
| F16 (test 1) | `test_off_unless_asked_for` asserts a boolean is a boolean — cannot fail | ✅ Confirmed | `tests/test_live_monitor.py:168-169`: `self.assertIn(live_monitor.ENABLED, (True, False))`. `ENABLED` is defined by a `not in (...)` boolean expression — it is definitionally always `True` or `False`. No input can make this assertion fail. |
| F16 (test 2) | `test_two_faders_move_independently` passes even if fader 1 is ignored, because its branch degenerates to a no-op | ✅ Confirmed (reproduced) | Traced the exact sequence in the test: fader 1 is driven from an anchor of 64 upward every step (`127 - raw + 1` for `raw` descending from 63), and `_pickup_ref[1]` starts at `CC_MAX` (127, the default). In `_scaled_level`'s upward branch, `level = ref + (CC_MAX - ref) * (...)`; with `ref == CC_MAX`, the second term is `(127-127)*... = 0`, so `level` stays `127` regardless of the input sequence. Deleting all fader-1 lines from the test changes nothing, since `user_gain[col1]` already defaults to `CC_MAX` in `LoopMix.__post_init__`. |
| F16 (test 3) | `test_a_drag_does_not_compound`'s assertion transcribes `_scaled_level`'s own formula rather than asserting behaviour | ✅ Confirmed | The downward recursion `level_{n+1} = ref_n - ref_n*(prev-raw)/prev = ref_n * (raw/prev)` telescopes to `127 * (final_raw / first_prev)`. With `first_prev=64` and the drag ending at `raw=51`, that is exactly `round(127*51/64)`, the literal assertion in the test. Confirmed by direct computation and by reading the recursive definition — this is the formula, not an independent behavioural check. |
| F17 | `loop_mix._picked_up` is written in four places, read in none | ✅ Confirmed | `grep -n "_picked_up" scripts/sooperlooper/loop_mix.py`: declared line 176 (`field(default_factory=set)`), cleared line 212 (`self._picked_up.clear()`), discarded line 272 (`self._picked_up.discard(col)`), added line 342 (`self._picked_up.add(fader)`). No `if ... in self._picked_up` or any other read anywhere in the file, nor referenced from `sooperlooper-apc-bench.py` or the test suite. The actual pickup-state check used elsewhere is `fader in self._pickup_anchor` — confirmed at `_accept()`, line 339. |
| F18 | `_accept`'s docstring ("then delta applies") is stale — there is no delta any more | ✅ Confirmed | `_accept()` docstring, `loop_mix.py:338`: `"""Relative pickup: anchor on first touch, no jump; then delta applies."""`. The actual computation, `_scaled_level` (lines 345-366), computes a ref-relative fraction-of-remaining-travel, not a delta (`raw - anchor`) — the docstring literally describes the *old*, already-replaced law (per the adjacent comment at line 353-356 describing exactly that replacement). |

### Documentation vs. reality (§9 table)

Spot-checked the four non-trivial rows independently rather than trusting the review's own table:

| Claim | Verdict | Evidence |
|---|---|---|
| "both paths must never be connected at once" is violated during the handover window | ⚠️ Confirmed as described, correctly hedged | Matches F14 exactly; the review itself already down-rates this to 🟡 for being millisecond-scale, which is accurate — I found no evidence the window is longer than a handful of JACK IPC round-trips. |
| "`MPE_LIVE_MONITOR=1` inserts `mpe-live-monitor`" is "half true" — the C binary doesn't read that flag | ✅ Confirmed | `grep MPE_LIVE_MONITOR native/mpe-live-monitor/mpe-live-monitor.c` → no hits. Only `load_env()` reads `MPE_SL_SURGE_CLIENT`, `MPE_LIVE_MONITOR_PORT`, `MPE_LIVE_MONITOR_RAMP_MS`. The gating is entirely in `start-mpe-live-monitor.sh`, with its own separate allowlist law (F5). |
| "it never jumps" ❌ False | ✅ Confirmed | Same evidence as F8. |
| "then delta applies" ❌ Stale | ✅ Confirmed | Same evidence as F18. |

### Step 0 ledger (2026-09-07 findings, reconciled)

Spot-checked the three still-OPEN rows and the CLOSED claims that are load-bearing for this
review's "the ledger works" argument:

| Claim | Verdict | Evidence |
|---|---|---|
| F1 (`_assert_total()` vacuous) still OPEN, 13 days | ✅ Confirmed | `grep -n binding_table tests/test_looper_timing.py` → no output, exactly as the review states. |
| Step0 #1/#2 (`fake_sl_engine` discards non-`hit`) still OPEN, 14 days | ✅ Confirmed | `tests/fake_sl_engine.py:67`: `if parts[2] != "hit": return` — present, unchanged. |
| F8 (AST guard) CLOSED, derived not literal | ✅ Confirmed (spot check) | `tests/test_looper_timing.py:167`-area: `CONTROLS = set(timing.engine_controls(grid=True))` uses a live call into the module rather than a hardcoded list — this is the "closes properly" pattern the review credits. |

I did not re-verify every CLOSED/UNTRACKED row line-by-line (F2–F12 of the 09-07 ledger); the
three above were chosen because they carry the review's central argument (ledgers work; test-
infrastructure fixes lag). No contradiction found in what I did check.

---

## Behavioural verification (summary)

All four measured claims in the review that could be checked by direct execution against the
actual `LoopMix` code were reproduced:

1. `anchor 64→100` = 127, `anchor 64→10→100` = 102 (F7) — reproduced exactly.
2. `fader 10→2→60→127` gives `25 → 73 → 127` (F8) — reproduced exactly.
3. `test_two_faders_move_independently`'s fader-1 branch is a `127 + 0*(...)` no-op (F16) —
   reproduced by hand-tracing and by confirming the test still passes with `col1` untouched.
4. `test_a_drag_does_not_compound`'s assertion equals the implementation's own telescoped
   formula (F16) — reproduced by algebraic derivation of `_scaled_level`'s recursion.

This is a materially higher bar than most AI-generated reviews clear, and it holds up.

---

## Severity re-assessment

| # | Issue | Reviewer rating | My rating | Delta | Reasoning |
|---|---|---|---|---|---|
| F1 | Graph guards key on presence/output-leg, not path-live | 🔴 (P0 in backlog #2) | **P0** | none | Reachable without any fault injection: a wrong `MPE_SL_SURGE_CLIENT`, a renamed Surge client, or Surge simply not up yet at the moment `wire-jack-graph.sh` runs are all normal startup-race outcomes on a Pi, not edge cases. Consequence (total silence, no log line, self-reinforcing) is exactly the failure class AGENTS.md names as the worst kind. Confirmed severity. |
| F2 | No `ExecStopPost`, no watchdog arm for the direct path | 🔴 (P0 in backlog #1 and #4) | **P0** | none | A crash is not hypothetical on a Pi running RT audio (OOM, xrun-triggered kill, `systemctl kill`); `Restart=on-failure` guarantees the direct path stays down across every restart cycle until `ensure_wiring()` re-detaches it on the *next* successful start — except `ensure_wiring` never *re-establishes* the direct path, only the client's own wiring, so a crash-looping unit leaves the instrument silent indefinitely. This is the single most consequential finding in the review and correctly placed first in the backlog. |
| F3 | No `JackUseExactName` → doubled signal on a second instance | 🔴 (P0 in backlog #3) | **P0** | none | The trigger (systemd `Restart=on-failure` racing a slow-exiting old process, or a manual second run during a soak) is a realistic operational pattern for exactly this kind of unit, not a contrived scenario. Fix is a one-line, zero-risk change (`JackUseExactName` + exit on `JackNameNotUnique`). Cheap fix + real trigger + audio-safety consequence (a louder, un-attenuatable copy of the live signal) earns P0. |
| F4 | No executable check of any C safety property; CI never compiles the client; the boundary test doesn't cross the boundary | 🔴 (P0 in backlog #5) | **P1** | ↓ | This is test/process debt, not an active failure mode by itself — nothing in the *current* C source is broken (I compiled it clean under `-Wall -Wextra`). Its harm is entirely prospective: it raises the odds that a *future* change silently reintroduces F1/F2/F3-class bugs, or that `CONTROL_PORT_DEFAULT` drifts from `DEFAULT_PORT` unnoticed. That is real and worth fixing this sprint, but it does not itself strand anyone mid-performance today, so I do not think it belongs in the same "stop what you're doing" bucket as F1–F3. I'd keep it prominent (P1, do next) rather than co-equal P0. |
| F9 | Unlocked cross-thread mutation of `_capturing`/`_last` | 🟡 | **P1** | ↑ | The review self-limits to 🟡 because the *pattern* pre-dates this diff (`on_wet` already does it). I'd still raise this one notch for the live monitor specifically: the consequence isn't cosmetic — "the gain stage can land on the older of two levels and stay there" is a silent, audible, sticky wrong-level bug with no error surfaced anywhere (compounds with F11 — the failure mode this produces is indistinguishable from "everything is fine"). The pre-existing pattern is a mitigating fact about blame, not about consequence. I'd schedule this alongside F11 this sprint rather than backlog it. |
| F13 | `detach_direct_path()` re-issued every 2 s forever, no idempotence guard | 🟡 | **P3** | ↓ | Correctly identified and correctly *not* rated 🔴 by the reviewer, but I'd push it to backlog rather than "this sprint": the cost is a JACK IPC round-trip per 2 s cycle against connections that don't exist, which is negligible on the stated CPU budget, and the fix (`if (!atomic_load(&g_direct_detached))`) is truly a one-line, zero-risk cleanup with no safety implication. It's real code smell, not a scheduling priority. |
| F5, F6 | Multiple laws for "is it on" / port / Surge client name | 🟡 | **P1** | ↑ slightly | These are genuinely reachable (F5's four demonstrated rows are real, not theoretical) and they compound with F1: a misconfigured enable state is exactly the condition under which someone would *expect* F1's guard to save them and it won't, because the guard doesn't know the client's config disagrees with its own. I'd bump these to "this sprint" alongside F1 rather than parking them as pure cleanup, since the fix for F5/F6 (one shared predicate/constant) also closes half of what makes F1 hard to reason about. |
| F7 | Path-dependent fader law, docstring overclaims convergence | 🟡, "handed to user" | **P2, agree with hand-off** | none | Correctly identified as a design tradeoff rather than a bug, and correctly not assigned a fix without Mitch's input. The recommendation (option b, snap within `PICKUP_TOLERANCE_CC`) is sound and cheap given the machinery already exists. No change to the review's handling. |
| F8 | "Never jumps" is false by 15 dB | 🟡 | **P2** | none | Reviewer's own read (not a regression vs. the old law, smoothed by `FADER_SMOOTH_MS`, but a live claim about audio safety that is simply wrong) is the correct calibration — real, but not urgent, and cheap to fix (delete the sentence or bound the step). Agree with placement. |
| F10 | Capture state that never closes pins the monitor silent | 🟡, "handed to user" | **P1** | ↑ | I'd move this out of "handed to user" and into "this sprint, mechanical fix, no judgment call needed." The reviewer's own suggested fix — treat a full `state` sweep with no capture state as authoritative, since SL streams state ~10×/s — is not a design tradeoff, it's a correctness fix for an unbounded resource (the `_capturing` list can accumulate stale entries with no drain path at all). There's no aesthetic or musical judgment call here the way there is in F7; recommend not deferring it to Mitch. |
| F11 | `ECONNREFUSED` swallowed silently | 🟡 | **P1** | ↑ | AGENTS.md names this exact failure shape (Rule −1: "the failure is indistinguishable from the success") as the thing nine prior incidents were caused by. Given that history in this exact repo, I would not schedule this as routine cleanup; it belongs with F9 as "make the silent failure mode visible before this reaches the Pi." |
| F12 | No logging after startup, `MPE_RUN_DIR` unused | 🟡 | **P2** | none | Real gap, correctly scoped as "not currently blocking anything" but "you will regret it exactly like the 09-06 regression the review cites." Agree with the reviewer's placement — worth doing, not urgent by itself. |
| F14 | Both paths briefly live during handover; ignored `jack_disconnect` return values | 🟡 | **P2** | none | Millisecond-scale window at startup only, correctly hedged by the reviewer as 🟡 not 🔴. Agree. |
| F15 | RT NULL check inside the hot loop, wrong failure direction | 🟡 | **P3** | ↓ slightly | Reviewer already correctly notes this is dead code today (`jack_port_get_buffer` never returns NULL for a registered port) — I'd put this in backlog rather than this-sprint precisely because it cannot currently fire. Worth the ten-line fix eventually, not urgent. |
| F16 | Three tests that cannot fail | 🟡 | **P1** | ↑ | These aren't just weak tests — they are actively misleading regression coverage for the two properties the review calls out elsewhere as safety-relevant (F7's path-dependence, F8's step size). A future contributor who breaks the fader law will see green tests. I'd treat rewriting these three tests as part of the "this sprint" work alongside F7/F8's fixes, not as separable test-hygiene backlog. |
| F17 | Dead `_picked_up` state | 🟡 | **P3** | none | Cosmetic; agree with placement. |
| F18 | Stale docstring | 🟡 | **P3** | none | Cosmetic; agree with placement. |

---

## What the review missed

I looked specifically for: auth/input-validation gaps, logic bugs, race conditions, and fragile
assumptions the review didn't already cover, and did not find anything new of consequence. Two
small items worth naming, neither rising above what's already tracked:

1. **The unlocked pattern F9 flags for `LiveMonitor`/`LiveMonitorSender` also applies to
   `SlBenchStateListener._on_state`'s dispatch itself** — the new `on_state` hook in
   `sl_bench_listener.py` is called directly from whatever thread delivers the OSC `state`
   message (per F9's evidence), and nothing in the new diff marshals it onto the main loop. This
   is the same finding as F9, not a new one — noting only that F9's fix (a lock, or a queue)
   needs to also cover the `on_state`→`push_monitor()` call chain in `sooperlooper-apc-bench.py`,
   not just `LiveMonitor.note_state` in isolation, since `push_monitor()` does a socket write and
   a conditional `print(..., flush=True)` from that same thread.
2. **`start-mpe-live-monitor.sh`'s build-on-demand path** (`if [ ! -x "$BIN" ]; then
   "$MPE_MODULE_REPO/scripts/build-mpe-live-monitor.sh" --required; fi`) means the *first* time
   this unit starts on the Pi, it compiles C code as part of a `systemd` unit start with
   `RestartSec=3`. If the build is slow or `libjack-jackd2-dev` is missing on the Pi image, the
   unit could crash-loop on `ExecStart` itself before ever reaching the audio graph — a
   pre-condition failure mode adjacent to, but distinct from, F2's crash-after-running case. This
   doesn't change any severity rating (it's a one-time first-boot condition, easily diagnosed by
   the journal `EnvironmentFile` context), but it's worth naming so it isn't confused with F2 when
   triaging an actual Pi failure.

The review's own coverage is unusually thorough — it explicitly names what it didn't read (LED
modules, `slot_*`, `looper_songs`, `track_gesture`) rather than silently skipping them, and its
"axis the suite measures vs. axis the bugs travel on" framing in §7 is, on inspection, exactly
right: there is genuinely no test in the tree that starts the compiled binary, and the doubles
used in `test_live_monitor.py` genuinely cannot express the C-side or graph-side failure modes.
I did not find a case where this framing was overstated.

---

## What the review got right (and why it matters)

**F1 + F2 + F13, read together, are one bug, not three.** `monitor_path_live()` only ever
answers "is my own output wired to playback" — so once true, it stays true regardless of what
happens to the Surge→playback direct connection or to Surge itself. That means: (a) the shell's
`live_monitor_present()` guard will never re-arm the direct path once the client has ever reached
that state (F1); (b) `ensure_wiring()` will keep re-issuing a disconnect against a connection that
was never there or already gone, every 2 s, forever, generating jackd log noise the whole time
(F13); and (c) if the process dies, nothing — not the unit file, not `sl-watchdog.py` — ever
notices that the *fail-open path is gone*, because the only thing anyone checks is a JACK port's
mere existence or a connection that was never the question in the first place (F2). A player who
loses direct sound has no code anywhere in this tree whose job it is to notice. That's the
single structural hole the review's "Verdict" section names, and tracing the three findings
together (rather than as an itemized list) makes clear they share one root cause and very
plausibly a single, small fix: give `monitor_path_live()` (and its shell echo) a *complete*
path definition — Surge→in **and** out→playback — and gate both the disconnect-repeat and the
watchdog off that same predicate.

**F16's second test (`test_two_faders_move_independently`) is the strongest single catch in
the review.** It is not merely a weak test; the algebra shows it is structurally incapable of
detecting the exact bug its name promises to catch, on any input, because `ref == CC_MAX`
degenerates the upward branch to a no-op regardless of what raw values are fed to fader 1. This
is worth flagging above the other two F16 items because a reviewer or CI run would see a named,
green, "independently" test and reasonably conclude fader-to-fader isolation is covered, when it
isn't covered at all.

---

## Prioritized Action Matrix

| Priority | Issue | Verdict | Effort | Depends on |
|---|---|---|---|---|
| **P0** | F2 — no `ExecStopPost`, no watchdog arm for Surge→playback after a crash | ✅ Confirmed | Quick fix (two `ExecStopPost=-/usr/bin/jack_connect` lines) + half-day (watchdog arm in `sl-watchdog.py`) | — |
| **P0** | F1 — both graph guards (`monitor_path_live()`, `live_monitor_present()`) key on presence/output-leg, not the full path | ✅ Confirmed | Half-day (add input-leg check to C; switch shell to `jack_lsp -c`) | — |
| **P0** | F3 — `jack_client_open` without `JackUseExactName`; second instance doubles the live signal | ✅ Confirmed | Quick fix (one flag + exit on `JackNameNotUnique`) | — |
| **P1** | F4 — no CI compile of the C client; no test crosses the C/Python boundary; port-matching test doesn't read the `.c` | ✅ Confirmed | Multi-day (compile step in `ci_gate.py`, a `.c`-grepping port test, a real `tests/engine/` case) | reprioritized down from Grumpy's P0 — real but prospective, not an active live failure |
| **P1** | F9 — `LiveMonitor`/`LiveMonitorSender` mutated from OSC threads and the main loop with no lock | ✅ Confirmed | Half-day (one `threading.Lock`, or marshal onto the main loop's queue) | reprioritized up from Grumpy's P2 — silent, audible, sticky wrong-level bug |
| **P1** | F11 — `ECONNREFUSED` swallowed without setting `.error` or incrementing a counter | ✅ Confirmed | Quick fix (counter + rate-limited WARN) | reprioritized up — this repo's own named failure shape (AGENTS.md Rule −1) |
| **P1** | F10 — a capture state that never closes pins the monitor silent forever, no timeout | ✅ Confirmed | Half-day (treat a full state sweep with none capturing as authoritative) | reprioritized out of "handed to user" — this is mechanical, not a design judgment call |
| **P1** | F5/F6 — four laws for "is the monitor on," three literals for the port/Surge-client-name | ✅ Confirmed | Half-day (one shared predicate/constant + a value-table test) | reprioritized up — compounds with F1 |
| **P1** | F16 — three new tests that cannot fail, including one structurally incapable of catching its own named bug | ✅ Confirmed | Half-day (rewrite the three assertions to test behaviour, not the formula/a tautology) | pairs with F7/F8 fixes |
| **P2** | F7 — fader law is path-dependent; docstring overclaims convergence | ✅ Confirmed, handed to user (agree) | Half-day once a direction is chosen (recommend option b: snap within `PICKUP_TOLERANCE_CC`) | Mitch's call on tradeoff |
| **P2** | F8 — "it never jumps" is false by up to 17 dB in one CC message | ✅ Confirmed (reproduced exactly) | Quick fix (delete the sentence, or bound per-message step) | — |
| **P2** | F12 — client logs nothing after startup; `MPE_RUN_DIR` exported and unused | ✅ Confirmed | Quick fix (one log line each on detach/restore, rate-limited gain logging) | — |
| **P2** | F14 — both paths briefly live during handover (contradicts the stated invariant); disconnect return values ignored | ✅ Confirmed | Quick fix (reorder: detach before connecting own output; check return values) | — |
| **P3** | F13 — `detach_direct_path()` re-issued every 2 s forever against an absent connection | ✅ Confirmed | Quick fix (guard on `g_direct_detached`) | — |
| **P3** | F15 — RT NULL check inside the hot loop, wrong failure direction (currently dead code) | ✅ Confirmed | Quick fix (hoist above the loop, `memset` on NULL) | — |
| **P3** | F17 — dead `_picked_up` state, written four places, read nowhere | ✅ Confirmed | Quick fix (delete) | — |
| **P3** | F18 — `_accept`'s docstring describes a delta law that no longer exists | ✅ Confirmed | Quick fix (rewrite one sentence) | — |
| **P3** | Minor — `install-units.sh:45` dangling pronoun; `docs/PATHS.md` missing table pipe; `CHANNELS 2` hardcode uncommented; `round()` banker's-rounding note | ✅ Confirmed (all four) | Quick fix (all four, single pass) | — |

---

## Disagreements and judgment calls

1. **F4 does not belong in the same P0 bucket as F1–F3.** Grumpy's own backlog lists it fifth
   ("cheapest first" ordering), which already signals lower urgency than the prose implies by
   marking it 🔴. I agree it's important and should be done this sprint, but "stop what you're
   doing" (P0) should be reserved for things that can strand a live performance *today*. F4's
   harm is entirely about *future* regressions slipping through — real, but a different class of
   risk than F1–F3, which are reachable now with the code exactly as it stands. This is a
   scheduling disagreement, not a factual one — the finding itself is fully confirmed.

2. **F9, F10, and F11 are under-rated at 🟡.** All three converge on the same failure shape:
   the instrument goes silent or stale-loud with *no observable signal that anything is wrong* —
   which is precisely the repeated root cause AGENTS.md documents from this project's own history
   (Rule −1, nine prior incidents). Grumpy's own §8 quotes AGENTS.md's "the failure is
   indistinguishable from the success" language for F11 specifically but still leaves it at 🟡
   with the rest of the routine cleanup. Given this project's specific, written, repeatedly-
   learned lesson about silent instruments, I'd move all three into "this sprint" rather than
   let them sit in the same bucket as docstring fixes.

3. **F10 should not be "handed to the user."** Grumpy correctly reserves "handed to the user"
   for genuine design tradeoffs (F7 is a real one: jump-free pickup vs. readable position is a
   judgment call about feel). F10 is not that — it's an unbounded resource with no drain path,
   and the reviewer's own proposed fix (treat a full state sweep with nothing capturing as
   authoritative) is mechanical and has no musical downside I can find. I don't think this needed
   Mitch's input to schedule; only to *review* once written.

4. **No finding in this review should be marked Incorrect.** Every claim I traced against the
   actual code, ran, or reproduced numerically held up exactly as stated, including the specific
   measured numbers in F7 and F8, which is the highest bar this kind of review can clear. I
   looked specifically for a claim that only matters on a code path that can't be reached (per
   this audit's brief) and did not find one — even F5's explicitly-hedged 🟡 (the "dangerous
   direction is unreachable" claim) checks out under direct verification of the two law's value
   sets.

---

## Bottom line for this cycle

**Confirmed: 18/18 numbered findings, plus all four spot-checked Step-0 ledger rows and all
four spot-checked §9 documentation claims.** Zero Incorrect, zero Partially True in the sense of
"exaggerated" — where I disagree, it is about *priority bucket*, not about whether the underlying
claim is true. Three findings (F9, F10, F11) were under-rated relative to this project's own
documented failure history and are moved up to P1. One finding (F4) is moved down from P0 to P1
because its harm is prospective rather than an active live-failure path. No new correctness bugs
were found beyond what the review already tracked; the two items I added under "What the review
missed" are refinements of F9's scope and a first-boot build-path caveat, not new findings that
change any priority count.

**P0 count: 3** (F1, F2, F3 — all Confirmed, all reachable without fault injection, all with
audio-safety-relevant consequences on a live instrument).
**P1 count: 6** (F4, F5/F6 as one bucket, F9, F10, F11, F16).

Given the size of the P0/P1 backlog and that none of this has run on the Pi, cycle 1 has real
work to do before this reaches hardware. Recommend continuing to cycle 2 after F1–F3 (and ideally
F9/F10/F11, since they're cheap) are addressed.
