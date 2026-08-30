# Grumpy review — lifecycle, process, connection and event-loop ownership

**Branch:** `refactor/looper-ownership-2026-08-30` @ `41d8541`
**Date:** 2026-08-30 · fresh-context adversarial review, read-only
**Dimension:** who owns the MIDI port, the OSC session, the event loop, the
threads, the supervisors, and the state files.
**Governed by:** `Documents/reviews/CHARTER-looper-ownership-2026-08-30.md`.
Tests are **not** canon; `DECISIONS.md` and `device_facts.py` are.

Everything below is quoted with `file:line`. Where I measured, I say what
machine I measured on. Where I could not verify without the appliance, I say
so rather than guessing — three findings are explicitly marked unproven.

---

## 1. First impressions

This is not vibe-coded. `apc_link.py`, `midi_subscription.py`, `sl_probe.py`,
`running_code.py` and `sl_limits.py` are some of the best-documented operational
code I have read: each one names the incident that produced it, the measurement
that settled it, and the wrong answer that was tried first. `sl_probe.py`'s
"restore is in a `finally` because the version that restored at each `return`
leaked on the exception path" is exactly the standard the charter asks for.

The defect is not craft. It is that **the craft was applied one incident at a
time.** Each module owns its own fault beautifully and nobody owns the
composition. The result is a bench main loop with ten near-identical
copy-pasted poll blocks, three LED writers that overwrite each other in a fixed
order nobody wrote down, an OSC cache that outlives the engine it describes,
and a re-registration timer called from thirteen sites because no module will
admit to owning it.

The recurrence Mitch names in the charter is real and it is structural: **every
one of the P0s below is a call-order accident, not a logic error.** You cannot
find them by reading a module. You can only find them by reading the sequence.

---

## 2. Severity summary

| # | Severity | Finding | Where |
|---|---|---|---|
| F1 | 🔴 P0 | `reopen_apc` repaints the multigrid, then blanks 56 of its 64 pads and all 8 scene LEDs — and the diff cache makes it permanent | bench `509–511` |
| F2 | 🔴 P0 | The documented orphan remedy (`sl-restart`) never emits `looper.engine.started`, so the bench never learns the engine restarted | `restart-sooperlooper.sh` |
| F3 | 🔴 P0 | `SlOscSession.last` survives an engine restart; `register_auto_update` is change-only, so the cache is never corrected | `sl_osc_session.py:118–129` |
| F4 | 🔴 P0 | `verify_or_exit` raises `SystemExit` inside the HUD thread; `except Exception` does not catch it and Python silences it — the HUD dies with no log | `looper_session.py:36–49` |
| F5 | 🟡 P1 | `LinkHealth` checks only `has_reader`; a reopen that gets input but not output reports "pads live again" over a dead LED path | `apc_link.py:180–190` |
| F6 | 🟡 P1 | `PacedMidiOut.pump` drops the whole queue on any write error and relies on a *different* signal to repaint | `apc_link.py:107–118` |
| F7 | 🟡 P1 | `on_looper_engine_started` re-zeroes the engine phase twice and never tells `GridState` | bench `320–324` |
| F8 | 🟡 P1 | Multigrid LED polling costs **~63 µs × ~485 Hz** — measured 3.15 % of an x86 core, est. ~9 % of a Pi 5 core — to discover nothing changed | measured, below |
| F9 | 🟡 P1 | `collect_jack_graph_health` converts `MeterXrunCounter`'s deliberate `None` back into the last known total | `looper_health.py:409` |
| F10 | 🟡 P1 | `sl-health` prints `PASS sync config` unconditionally, including `sync_source=None tempo=None` | `sl-health.py:158–160` |
| F11 | 🟡 P1 | `maybe_reregister` repairs silently and unconditionally from 13 call sites; a subscription that drops all night is invisible | `sl_osc_session.py:211–221` |
| F12 | 🟡 P1 | `SlOscSession` has no `close()`; the OSC server thread and its UDP port are released only by process death | `sl_osc_session.py:79–105` |
| F13 | 🟡 P1 | `hud_thread` is `daemon=False` behind a `join(timeout=5.0)` — the timeout is not a bound | `looper_session.py:67, 160` |
| F14 | 🟡 P1 | `reopen_apc` opens the **output** port by an index taken from the **input** enumeration | bench `487–495` |
| F15 | 🟡 P1 | Watchdog JACK repair has no fight limit; the governor repair does. A stuck graph forks ~32 processes incl. 15 `jack_connect` client registrations every 10 s | `sl-watchdog.py:527–547` |
| F16 | 🟢 | `tick_faders()` is called from 1 of 10 loop branches | bench `610` |
| F17 | 🟢 | Two env-var names for the HUD state file | `sl_hud_state.py:11` vs `health_source_liveness.py:73` |
| F18 | 🟢 | `HudWriter._registered_at` records "I asked", not "it happened" | `sl_hud_monitor.py:107–109` |

---

## 3. Who owns the MIDI port?

**Answer today: nobody. Four objects hold pieces of it and none of them holds
the whole.**

| Piece | Held by | Notes |
|---|---|---|
| `rtmidi.MidiIn` / `MidiOut` handles | `run_bench` locals `midi_in`, `raw_midi_out` | closures only; no object, no `close()` |
| Write pacing + per-model encoding | `PacedMidiOut` (`apc_link.py:52`) | correct and well-argued |
| "Do we still have the device" | `LinkHealth` (`apc_link.py:140`) | reader-only, see F5 |
| Reopen + repaint | `reopen_apc` closure (bench `471`) | see F1, F14 |
| Kernel truth | `midi_subscription.port_subscriptions` | the one honest instrument here |

### F1 🔴 — the reconnect paints the surface and then erases it

This is the answer to "can the surface come back blank or stale after a
reconnect". It can, and it does, deterministically, under `MPE_SL_MULTIGRID=1`.

`scripts/sooperlooper-apc-bench.py:499–512`:

```python
        midi_out.reset()
        for _n in range(GRID_ROWS * GRID_COLS):
            midi_out.send_message([0x90, _n, LED_OFF])
        by_note = apply_view(
            midi_out, gestures=gestures, view=view, multigrid=multigrid
        )
        if slot_surface is not None:
            slot_surface.repaint(force=True)
            slot_surface.repaint_scenes(force=True)
        transport_leds.repaint()
        return True
```

`transport_leds.repaint()` is `scripts/sooperlooper/apc_transport.py:410–421`:

```python
    def repaint(self) -> None:
        """Re-assert every transport LED, ignoring the cache. ..."""
        self._last_vel.clear()
        self.clear_unwired_surfaces()
```

and `clear_unwired_surfaces` is `apc_transport.py:423–430`:

```python
    def clear_unwired_surfaces(self) -> None:
        """Darken scene launch 1–7 and grid rows 1–7 (not wired until P3)."""
        from apc_grid import RESERVED_GRID_NOTES

        for note in self._scene_launch_notes:
            self._set_led(note, SCENE_LED_OFF)
        for note in RESERVED_GRID_NOTES:
            self._set_led(note, LED_OFF)
```

I resolved the note sets rather than trusting the names:

```
RESERVED_GRID_NOTES = 8..63          # rows 1-7 — the entire multigrid matrix
resolve_scene_launch_notes("mk2") = (112..119)   # the multigrid scene launchers
```

Under multigrid those surfaces are **wired**. The docstring's "not wired until
P3" is stale by two features.

**Failure sequence — deterministic, no race required:**

1. APC stalls its bulk endpoint (`-EPIPE`) and re-enumerates. This is the
   documented, measured behaviour of this device — `apc_link.py:6–14`.
2. `LinkHealth.poll` sees no reader, calls `reopen_apc`.
3. Bench 503–504 blanks all 64 pads. 509 repaints all 64 from matrix state and
   sets `SlotSurface._painted` to the full 64-entry desired map
   (`slot_leds.py:110–111`: `if previous is None: return sorted(desired.items()), desired`).
4. 510 repaints scene LEDs 112–119 and sets `_scene_painted = desired`.
5. **511 writes `LED_OFF` to notes 8–63 and `SCENE_LED_OFF` to 112–119.**
6. Next tick, `slot_surface.repaint()` diffs against `_painted` — which still
   claims those 56 pads are lit — and **sends nothing**.

The player's surface is now row 0 only. Rows 1–7 and every scene button are
dark, and they stay dark until a pad's *colour* genuinely changes, which for an
idle set is never. `slot_surface.repaint`'s own docstring
(`slot_surface.py:532–537`) explains precisely why this is fatal — "the diff
cache describes a surface that no longer exists" — and then the cache is
falsified two lines later by a different object.

**The same collision fires at startup, permanently, on the scene LEDs.**
Bench `378` paints them; bench `438–446` constructs `TransportButtonLeds`, whose
constructor calls `clear_unwired_surfaces()` at `apc_transport.py:405`, blanking
them again — after `_scene_painted` has recorded them as lit. The matrix
recovers on the first tick only because `_painted` starts as `None`
(`slot_surface.py:84`) and the first `repaint()` is therefore a full paint. The
scene LEDs have no such luck: `repaint_scenes()` is never forced again.

So under multigrid **the scene launch buttons have been dark since the session
started**, and nobody has noticed because a dark scene button is also what a
correctly-idle scene button looks like. That is the 2026-08-15 shape on an LED.

**Fix direction.** The charter's Stage 3 already says "delete
`clear_unwired_surfaces`". Confirmed — delete it, and give the reserved-surface
blanking to whoever owns those notes: `GridView`/`SlotSurface` when multigrid is
on, nothing at all when it is off (startup already blanks 0–63 at bench
`266–267`). Interim safety: move `transport_leds.repaint()` *before* the surface
repaints in `reopen_apc`, and force `repaint_scenes(force=True)` once after
`TransportButtonLeds` is constructed. Neither is the real fix.

**Regression test that would have caught it** (charter §3 question 3): drive
`reopen_apc` against a recording `midi_out` under `multigrid=True` and assert
the *last* write to every note in 8..63 and 112..119 is not `LED_OFF`. Today no
test composes those two objects.

### F5 🟡 — `LinkHealth` declares the pads live on half the evidence

`midi_subscription.port_subscriptions` returns both directions and documents
why (`midi_subscription.py:44–48`): `has_reader` = we receive presses,
`has_writer` = LEDs can light. `LinkHealth` throws half of it away —
`apc_link.py:180`:

```python
        has_reader, _has_writer = port_subscriptions(self._key)
```

and again in the post-reopen confirmation, `apc_link.py:186`:

```python
            has_reader, _ = port_subscriptions(self._key)
            if has_reader:
                self._healthy = True
                self._log("APC link reopened — pads live again")
```

If the input subscription takes and the output one does not, this logs "pads
live again", clears the loss state, and stops checking. Every subsequent LED
write goes into a port with no writer. Presses work; the surface is frozen.
`run_bench` already knows this matters — it warns at startup
(bench `181–183`, "no writer … LEDs will not light") — and then the recovery
path forgets. **The startup check and the recovery check disagree about what
"healthy" means.** That is the ownership defect, and it is also literally the
defect shape `apc_link.py`'s own module docstring was written about.

Fix: `LinkHealth` should carry both, report `healthy = reader and writer`, and
name which half failed in the log line.

### F6 🟡 — `pump()` drops the repaint and delegates recovery to a signal that may not fire

`apc_link.py:107–118`:

```python
            try:
                self._out.send_message(self._queue.popleft())
            except Exception:
                # A write during re-enumeration raises. Drop the backlog: it
                # describes a surface that no longer exists, and LinkHealth is
                # about to force a full repaint anyway.
                self._queue.clear()
                return sent
```

The comment asserts a causal link that the code does not enforce. `LinkHealth`
forces a repaint only when `/proc/asound/seq/clients` shows **no reader**. Any
other write failure — a full ALSA output buffer, a transient `-EAGAIN`, an
`ENOSPC` on the sequencer queue, all plausible on a contended full-speed chain
shared with a Scarlett streaming audio — clears a queue that may be holding a
64-message repaint mid-flight, while the subscription remains intact and
`LinkHealth` stays `healthy`. Result: a half-painted surface with no recovery
trigger, indistinguishable from a correctly-painted one.

Fix: distinguish the exception classes, or at minimum set a `needs_repaint`
flag on drop that the loop honours. Silently discarding a repaint is the one
thing a compositor must never do.

### F14 🟡 — the output port is opened by an input-list index

Startup, bench `134–150`:

```python
    ports_in = midi_in.get_ports()
    idx = next((i for i, n in enumerate(ports_in) if port_hint.lower() in n.lower()), None)
    ...
    midi_in.open_port(idx)
    ...
    midi_out.open_port(idx)
```

Reopen, bench `487–495`:

```python
            ports = midi_in.get_ports()
            new_idx = next(
                (i for i, n in enumerate(ports) if port_hint.lower() in n.lower()),
                None,
            )
            ...
            midi_in.open_port(new_idx)
            raw_midi_out.open_port(new_idx)
```

`MidiIn.get_ports()` and `MidiOut.get_ports()` are independent enumerations.
Any input-only ALSA client ordered before the APC shifts the two lists relative
to each other, and `raw_midi_out.open_port(new_idx)` then opens a *different*
device — or raises, which `reopen_apc`'s `except Exception` swallows into
`return False`, retrying every 2 s forever. `reopen_apc`'s own docstring says
"the device comes back with … usually a new rtmidi port index, so the index
resolved at startup is worthless — re-resolve by name" and then re-resolves by
name on one list and applies it to two.

**Unproven here:** I cannot enumerate this appliance's MIDI ports from a laptop,
so I do not know whether the two lists currently differ. The correctness of the
code must not depend on that. Fix: resolve each direction against its own
`get_ports()`, and refuse (loudly) if either name lookup fails.

---

## 4. Who owns the OSC session?

**One object, `SlOscSession`, owns the port and the client. Nothing owns the
cache's validity, and thirteen call sites share the re-registration timer.**

### F3 🔴 — a cache that outlives the engine it describes

`sl_osc_session.py:107–129`:

```python
    def _store(self, loop_index: int, control: str, value: float) -> None:
        self.last[_cache_key(int(loop_index), control)] = float(value)
    ...
    def cached(self, ctrl: str, loop: int = 0):
        """Last value delivered by auto-update. Never blocks."""
        return self.last.get(_cache_key(loop, ctrl))
```

`self.last` is written on every auto-update and **never cleared, invalidated, or
timestamped**. There is no `invalidate()`, no `close()`, no epoch counter.

The repo states the governing engine fact itself, in `sl_hud_monitor.py:96–98`:

> `register_auto_update` delivers on CHANGE only

Compose the two:

1. Engine restarts. Every loop is now state 0 (Off), length 0, and the tempo is
   `configure-grid-sync.sh`'s default.
2. Our subscriptions died with the old process. `maybe_reregister` restores them
   within 15 s — the subscription heals.
3. The engine's values do not *change* after restart, so no auto-update is sent.
4. `self.last` still holds the pre-restart values, forever.

Everything downstream then reads confidently wrong state:

* `HudWriter._from_sl` (`sl_hud_monitor.py:181–200`) reads `cached("state", n)`
  and `cached("loop_pos", n)`, finds loop 3 "playing", and writes
  `playing: True` into `~/.mpe_sl_hud_state.json` with a fresh `updated_at`.
  The staleness guard in `sl_hud_state.read_sl_hud_state` is defeated because
  the file is being freshly written with stale *content*.
* `SlBenchStateListener` never fires, so `TrackGesture.sl_state` and
  `SlotSurface._sl_states` keep their old values and the pads stay solid green.
  Per `DECISIONS.md` §L and the README, **solid green is a promise that the
  engine says there is audio in that loop.** After an engine restart that
  promise is a lie with no expiry.

`seed_tempo` is the only re-read in the whole session and it is guarded to fire
exactly once per process lifetime — `sl_osc_session.py:207–209`:

```python
    def seed_tempo(self) -> None:
        if self.cached("tempo", -1) is None:
            self.get("tempo", -1)
```

Once tempo has ever been cached, it is never re-asked. So the BPM the HUD shows
after an engine restart is the *previous* session's BPM.

**Fix direction.** The session must own an engine epoch. On any evidence the
engine went away — the `looper.engine.started` event, an OSC timeout, or a
re-registration that follows a gap — `self.last.clear()` and re-`get()` the
controls that matter, rather than waiting for a change that will never come.
The cheap version: make `cached()` return `None` for entries older than N
seconds, so absent instrumentation looks absent (DECISIONS 2026-08-15 rule 1)
instead of looking like confident stale truth.

### F11 🟡 — the re-registration timer has thirteen callers and no owner

The coordinator's live journal line is real and I confirmed the mechanism.

**Call-site count, verified — the coordinator's "11 in the bench" is 10.**
`grep -rn maybe_reregister`:

* `scripts/sooperlooper-apc-bench.py` — **10**: lines 611, 635, 648, 661, 694,
  706, 716, 726, 738, 761 (one per branch of the main loop)
* `scripts/sooperlooper/sl_hud_monitor.py:241` — the standalone HUD loop
* `scripts/sooperlooper/looper_session.py:46` and `:136` — the threaded HUD and
  `--hud-only`

= **13 call sites**, funnelling through two wrappers
(`SlBenchStateListener.maybe_reregister`, `sl_bench_listener.py:81–83`;
`HudWriter.maybe_reregister_session`, `sl_hud_monitor.py:107–109`) into one
implementation.

**Confirmed: no caller gates on a health signal.** Both wrappers are
unconditional pass-throughs, and the implementation gates on elapsed time alone
— `sl_osc_session.py:211–221`:

```python
    def maybe_reregister(self) -> None:
        if (time.monotonic() - self._last_register) < REREGISTER_S:
            return
        if self._hud_registered:
            self.register_hud()
        ...
        self._last_register = time.monotonic()
```

**Cost × cadence (DECISIONS 2026-08-18 rule 1).** `register_bench` sends 4
messages per loop (`state`, `loop_len`, `loop_pos`, `wet` —
`sl_osc_session.py:170–187`) × 15 loops = 60, plus 1 for `register_hud`. Sixty-one
UDP datagrams every 15 s ≈ **4 packets/s**. I could not measure `pythonosc` here
(not installed on this laptop), but this is not a fork and no plausible
per-message cost makes 4/s matter: **the CPU objection does not stand, and the
2026-08-18 rule is not violated in its letter.** What *is* violated is rule 1
itself — the poll was added without stating cost × cadence anywhere, so nobody
knows the engine-side number either. SooperLooper must re-walk its auto-update
registration table 61 times per interval; that cost is unmeasured and the only
honest answer is "not measured".

**The real finding is the measurement-integrity one, and I agree with the
coordinator's framing.** The repair is unconditional and silent, so:

* a session that has held its subscriptions all night, and
* a session that has been losing and re-establishing them every 15 s

produce **identical output**. There is no counter, no "re-registered after a
gap" log, no field in any state file. `DECISIONS.md` 2026-08-15 rule 1 —
"absent instrumentation must look absent" — is inverted here: *present* repair
looks absent. That is the same shape as `e111719`, where a health probe
performed an invisible destructive action on a 10 s timer for a day.

**The 15 s timer is load-bearing.** `sl_hud_monitor.py:241` carries the comment
`# survive an engine restart`, and `should_reregister`'s docstring
(`sl_hud_monitor.py:104`) is explicit: "Re-subscribe after an engine restart
(register_auto_update is change-only)". Do **not** delete it. It is the only
thing that reconnects the HUD and the pads after `mpe-sooperlooper.service`
restarts, and F2/F3 show that even *with* it the recovery is incomplete.

**Fix direction, in order:**

1. **One owner.** `SlOscSession` owns "keep the subscription alive". Give it a
   `tick(now)` called from exactly one place — the bench idle branch — and
   delete the other twelve call sites. Thirteen defensive calls is the
   signature of no owner; the diff should read like `sl_limits.py`.
2. **Make the repair report itself.** Count re-registrations, and log/publish
   only when one follows evidence of an actual gap. A silent heal is a lost
   fault.
3. **Make it event-driven where an event exists.** `LooperEngineEventWatch`
   already knows when the engine restarted (bench `330`). Re-register on that
   edge and keep the timer as the backstop for the paths that emit no event —
   which, per F2, is the one path Mitch actually uses.
4. **Stop printing on every pass.** `sl_osc_session.py:188–191` prints
   unconditionally inside `register_bench`; that is the journal spam. Print on
   first registration and on change only.

### F12 🟡 — the session that refuses a held port never releases one

`SlOscSession.start` (`sl_osc_session.py:79–105`) binds the UDP port, refuses
loudly if it cannot (good — that message is a model of the genre), starts

```python
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
```

and there the lifecycle ends. There is **no `close()`, no `shutdown()`, no
`server_close()`** anywhere in the class or in any caller. Compare
`sl-health.py:78–80`, which does it correctly:

```python
    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
```

Consequence: the socket is released only when the process dies. The
`_install_sigterm_handler` docstring (`looper_session.py:70–91`) exists
*specifically* because "systemd SIGKILLed the group, and started the replacement
in the same second" — and the replacement's first act is
`SlOscSession.start()`, which will `SystemExit` with "A previous looper session
is probably still running" if the old socket is still bound. The session hardens
against the symptom and does not close the resource that causes it.

Fix: `SlOscSession.close()` → `shutdown()` + `server_close()` + `join`, called
from `run_session`'s `finally` alongside the HUD teardown.

### Thread-safety of the cache (minor, but name it)

`SlOscSession.get` (`sl_osc_session.py:132–143`) **pops the cache key before
asking**:

```python
        key = _cache_key(loop, ctrl)
        self.last.pop(key, None)
```

`get` is reachable from the OSC server thread's peers on two different threads:
the HUD thread (`register_auto_updates` → `seed_tempo`) and the bench main
thread (`maybe_reregister` → `seed_tempo`). Concurrent calls race on one key,
and for the 0.4 s timeout window `cached("tempo", -1)` returns `None` to
whichever thread is reading — so `HudWriter._from_sl` returns `None` and the HUD
skips its write. Harmless today (the reader's stale window is 5 s), but it is an
unsynchronised shared mutable cache read by three threads with no lock and no
documented discipline. When F3 is fixed by adding invalidation, this becomes
load-bearing.

Worse in one specific case: `seed_tempo`'s blocking `get` (0.4 s, polling at
20 ms) runs **on the bench main thread** via `maybe_reregister`. If the engine is
down, that is a 400 ms stall of pad handling, LED pumping and hold timing, every
15 s — precisely when the player is most likely to be mashing pads asking why
nothing works.

---

## 5. The bench main loop

`run_bench`'s loop is `while True:` at bench `583`, with **ten branches**, nine
of which end in a hand-copied block of the same four-to-five poll calls. There
is no `try`/`finally` anywhere in `run_bench`; the only exits are the
`--measure-latency` returns at `595`/`604`.

Full enumeration, cadences and costs are in **§ Poll loop order** at the end.

### F8 🟡 — the multigrid LED path costs ~9 % of a Pi 5 core to discover nothing changed

**Measured** (this x86 laptop, `python3 3.14.4`, real `SlotRuntime`/`SlotSurface`
with 15 tracks, mk2 scene notes, steady state, 20 000 iterations each):

| Call | µs/call | % core @485 Hz (x86) | Pi 5 est. (×3) |
|---|---|---|---|
| `poll_track_gestures(multigrid=True)` | 1.6 | 0.08 % | 0.24 % |
| `slot_surface.poll_hold` | ~0.0 | ~0 | ~0 |
| `slot_surface.poll_hold_led` | ~0.0 | ~0 | ~0 |
| **`slot_surface.poll_led_repaint`** | **42.8** | **2.08 %** | **6.2 %** |
| **`slot_surface.repaint_scenes`** | **18.5** | **0.90 %** | **2.7 %** |
| **total** | **63.0** | **3.06 %** | **~9.2 %** |

Loop rate measured: `time.sleep(0.002)` actually takes **2.062 ms** here, so the
idle loop runs at ~485 Hz, not 500.

The ×3 Pi 5 factor is an estimate, flagged as such — I have no Pi measurement.
Even at ×1 this is 3 % of a core spent on comparisons.

Where it goes — `slot_surface.py:539–557`:

```python
        messages, painted = matrix_messages(
            self._view,
            self._rt.tracks(),          # dict(self._tracks) — a 15-entry copy
            ...
            gesture_leds=self._gesture_leds(),   # another dict
        )
```

```python
        for index, note in enumerate(self._scene_launch_notes):
            row = scene_launch_index_to_row(index)
            desired[note] = scene_row_led(
                self._rt.tracks(), row, sl_states=self._sl_states
            )
```

`SlotRuntime.tracks()` is `return dict(self._tracks)` (`slot_runtime.py:135–136`)
— a fresh copy every call, and it is called **inside the 8-iteration scene
loop**. Per idle iteration that is ~10 copies of the track dict plus a fresh
64-entry `desired` map from `matrix_messages`, ~4 800 dict allocations per
second, to produce an empty message list.

This is not a subprocess, so DECISIONS 2026-08-18 is not violated in its letter.
It lands on the same number the rule was written about — **9 % of a core** —
and rule 1 ("compute the product; put it in the PR") was not honoured: no commit
states this cost. Under multigrid this is the single largest CPU consumer in the
looper stack, and it is pure recomputation.

**Caveat, stated plainly:** `MPE_SL_MULTIGRID` defaults to `"0"` (bench `273`),
so this cost is opt-in. I could not check the appliance's environment from here.
Given the last three commits are multigrid work, I assume it is on; if it is
off, F8 drops to 🟢 and F1's multigrid half drops with it. **Someone with the Pi
should confirm `MPE_SL_MULTIGRID` before this is prioritised.**

Fix direction (cheap, no restructuring): hoist `self._rt.tracks()` out of the
scene loop; give `SlotRuntime` a read-only view instead of copying; and gate
`poll_led_repaint`/`repaint_scenes` on a dirty flag set by `on_state`,
`on_loop_len`, `press`, `poll_grid_wait` and the blink-phase clock, so a steady
surface costs one integer compare. The blink phase is the only thing that
genuinely needs a timer, and it changes at ~4 Hz, not 485 Hz.

### F16 🟢 — `tick_faders()` runs in one branch of ten

Bench `607–615`, the idle branch, is the only place `tick_faders()` appears.
The nine event branches (`632`, `645`, `658`, `691`, `703`, `713`, `723`, `737`,
`758`) omit it. Fader *moves* are safe because `handle_cc` calls
`faders.tick(now=now)` itself (bench `552`), but a smoothed ramp still settling
after the last CC freezes for as long as the loop stays out of the idle branch —
e.g. during a burst of pad presses at the end of a fader sweep. Add it to the
shared block, or better, delete the nine copies (below).

### The copy-paste is the finding

Nine branches each end in some permutation of:

```python
            poll_holds()
            poll_transport_leds()
            maybe_track_transport()
            state_listener.maybe_reregister()
            poll_engine_events(time.monotonic())
            continue
```

and one of them (bench `731–741`, the transport branch) reverses the first two
for no stated reason:

```python
            track_reset.note_event(n, down)
            transport_leds.note_event(n, down)
            maybe_track_transport()      # <-- before poll_holds, unlike all others
            poll_holds()
```

**This is the structural defect of the loop.** Every new poll must be added in
ten places, every ordering question must be answered ten times, and a divergence
like the one above is invisible in review. The whole tail should be one
`service_tick(now)` called once at the bottom of the loop body, with the event
handling above it — which also fixes F16 by construction and makes the ordering
dependencies below expressible as one commented sequence instead of folklore.

---

## 6. Ordering dependencies that are load-bearing and undocumented

Ranked by what breaks if you reorder.

| # | Dependency | Consequence if reversed | Documented? |
|---|---|---|---|
| **D1** | `transport_leds.repaint()` (bench 511) runs **after** `slot_surface.repaint(force=True)` (509) | **F1** — 56 pads + 8 scene LEDs stranded dark, permanently | No — and it is wrong as written |
| **D2** | `TransportButtonLeds(...)` (bench 438) constructed **after** `slot_surface.repaint_scenes(force=True)` (378) | Scene LEDs dark from startup | No — wrong as written |
| **D3** | `state_listener.attach_surface(slot_surface)` (388) **before** `state_listener.register(...)` (389) | State updates arriving in the gap never reach the surface; a loop that changes state during registration is missing from the matrix until it changes again | No |
| **D4** | `midi_out.apc_label = apc_label` (193) **before** the first LED write (267) | 64 pads written in the wrong dialect | **Yes** — bench 191–192. This is the model. |
| **D5** | `poll_track_gestures` (527) **before** `slot_surface.poll_led_repaint()` (532) | `matrix_messages` reads `fs.current_led()` for the active lane (`slot_surface.py:524–530`); a stale blink phase paints last tick's colour | Obliquely — `poll_track_gestures.__doc__` says "still advance blink phase — `SlotSurface.repaint` reads `current_led()`" |
| **D6** | `link_health.poll()` (525) **before** `midi_out.pump()` (526) | A reopen's queued repaint waits one iteration (~2 ms). Benign, but it is why the order was chosen | No |
| **D7** | `poll_hold_led` (530) writes raw to `midi_out` **outside** `_painted`; `poll_led_repaint` (532) then diffs against a `_painted` that does not know | The hold blink survives only because the repaint that follows it is blind to it. Any change to the diff cache silently kills the blink | Half — `slot_surface.py:326–329` explains the *other* branch |
| **D8** | `maybe_track_transport()` before `poll_holds()` in the transport branch only (736–737) | Nothing observable; the asymmetry is the hazard | No |
| **D9** | `establish_grid_clock` → `grid.mark_phase_zero` → `set_grid_active` (bench 232–236) | Bench bar line and engine phase diverge | **Yes** — bench 233–234 |
| **D10** | `mute_quantized 0` → `mute_on` → `pause_on` → `mute_quantized 1` (`track_gesture.py:960–963`) | Depends on SL draining its nonrt queue in order | **Yes** — 957–959 |
| **D11** | `HudWriter` thread started (looper_session 141) **before** `run_bench` (147) | HUD registers `tempo` before the bench sends `apply_grid_sync`'s default tempo; both write the same engine from two threads | No |

D1, D2 and D3 are the ones to fix. D5, D7 and D9 are the ones to write down.

---

## 7. Three supervisors — who owns what

| Supervisor | Process | Cadence | Exclusive responsibility | Repairs | Never does |
|---|---|---|---|---|---|
| `sl-watchdog.py` | own unit | 10 s | **Host + JACK graph.** Is the engine on JACK; is `common_out → playback`; xrun rate; CPU governor | JACK reconnect, governor re-pin | Never restarts the engine; never touches MIDI or LEDs |
| `sl-health.py` | hand-run, one-shot | on demand | **Adjudication for a human.** Does the command path drain; do all 15 loops accept writes | Nothing | Not a daemon; must never be on a timer |
| `looper_session` internals | in-process | 2 s / 1 s / 15 s | **The control surface and the subscription.** MIDI link (`LinkHealth`), grid re-apply (`LooperEngineEventWatch`), OSC subscriptions (`maybe_reregister`) | Reopen APC, re-apply grid, re-subscribe | Never touches JACK or the engine's lifetime |

**They do not fight, and the one place they could was deliberately fixed.**
`sl_probe.check_command_path` (`sl_probe.py:132–139`) derives each prober's write
target from a caller-supplied seed so `sl-health` and `sl-watchdog` cannot
collide, and treats "moved, but not where we put it" as *proof of liveness*
rather than a wedge — `sl_probe.py:167–171`. This is the single best piece of
concurrency reasoning in the repo. Credit where due.

Remaining overlaps, in descending order of concern:

**F15 🟡 — the JACK repair has no fight limit, and it forks.**
`sl-watchdog.py:527–547` runs `bash wire-jack-graph.sh connect` on every cycle
where `looper_playback` is false. That script does 15 × `jack_connect` + 15 ×
`oscsend` + `bash` (`wire-jack-graph.sh:76–98`). **Every `jack_connect` registers
a real JACK client**, which is exactly the cost DECISIONS 2026-08-18 rule 9
re-priced from "1.16 % of a core, affordable" to "35 xruns/min, the single
largest xrun source on the appliance". On a graph that will not come back, that
is ~32 forks and 15 client registrations **every 10 s, forever**, plus up to 4 s
inside `wait_for_playback_via_meter`. The governor repair in the same file has a
fight limit for precisely this reason (`sl-watchdog.py:105–115`,
`GOVERNOR_FIGHT_LIMIT`) — *"A drift repaired over and over is a fight with
something that keeps winning, and quietly re-pinning forever would hide it"* —
and the JACK repair, which is orders of magnitude more expensive, has none.
Fix: give the graph repair the same fight limit and back off to alarm-only.

**Two implementations of the xrun counter.** `sl-watchdog.XrunCounter`
(`sl-watchdog.py:240–319`) and `patch_browser.looper_health.MeterXrunCounter`
(`looper_health.py:271–343`) read the same `/run/mpe/meter.state`, with
near-identical `_read` methods and identical staleness reasoning. Two owners of
"how many xruns", and — see F9 — only one of them keeps the answer honest.

**No supervisor owns "is the looper session itself alive".** `sl-watchdog`
watches the engine and the graph. Nothing watches `mpe-looper-session` beyond
systemd's `Restart=always`, and F4 shows a way for its HUD half to die while the
unit stays green.

---

## 8. Measurement integrity — other instances of "the same broken or fine"

`DECISIONS.md` 2026-08-15. I hunted the shape across every health/probe/status
path. Four confirmed, plus the two already counted above (F3, F11).

### F9 🟡 — the HUD's xrun count is the same number whether the meter is alive or dead

`MeterXrunCounter.poll` is written correctly and says why —
`looper_health.py:332–335`:

```python
        # A meter that has stopped writing looks identical to a quiet one. It is
        # not: report None so the caller raises a problem instead of "healthy".
        if self.stale_after_s > 0 and (time.time() - updated) > self.stale_after_s:
            return None
```

It has exactly one caller, `looper_health.py:405–411`:

```python
    xruns = tracker.xrun_counter.poll()
    cpu = tracker.cpu_reader.read()
    tracker.sample(
        cpu_load_pct=cpu,
        xruns_total=xruns if xruns is not None else tracker._session_xruns,
        now_s=now,
    )
```

The `None` is converted straight back into the last known total, and
`JackGraphHealth.sample` then does `self._session_xruns = max(self._session_xruns, ...)`
(`looper_health.py:138`), so `snapshot()["xruns"]` reads identically whether the
peak meter is publishing or has been dead for an hour. **The counter's entire
design is discarded at its only call site.**

Same file, same shape, for DSP load: `cpu_reader.read()` returns `None` when the
`jack_cpu_load` child has wedged (`looper_health.py:240–244`), `sample` skips the
histogram, and `snapshot()` keeps reporting the *last* window's `max_pct` and
`p95_pct` unchanged. A dead probe and a steady load look the same.

`sl-watchdog` gets this right for the same source — `sl-watchdog.py:431–433`
appends `"xrun counter blind: …"` to `problems`. Fix: add a `seeable`/`source`
field to `JackGraphHealth.snapshot()` and let the HUD render `n/a`, per
DECISIONS 2026-08-15 rule 1.

### F10 🟡 — `sl-health` prints PASS on a check it never evaluates

`sl-health.py:158–160`:

```python
        src = p.get("sync_source", loop=-1)
        tempo = p.get("tempo", loop=-1)
        print(f"PASS  sync config  sync_source={src} tempo={tempo}")
```

`Probe.get` returns `None` on timeout (`sl-health.py:78`). There is no
conditional and no `failures += 1`. An engine that answers nothing on both
globals prints:

```
PASS  sync config  sync_source=None tempo=None
```

In a tool whose entire premise is "exit 0 = engine accepts commands", a hard
`PASS` on an unevaluated result is the exact failure this file was written to
end. It also costs up to 3 s of blocking (two 1.5 s timeouts) to produce a line
that cannot fail. Fix: `FAIL` when either is `None`, and say which.

### F4 🔴 — the liveness gate that fails silently

`looper_session.py:34–57`:

```python
def _hud_thread_main(stop: threading.Event, writer: HudWriter) -> None:
    try:
        from patch_browser.health_source_liveness import verify_or_exit
        verify_or_exit("looper-session")
        ...
    except Exception as exc:
        print(
            f"looper-session: FATAL HUD thread failure ({exc!r}) — "
            f"exiting for Restart=always",
            flush=True,
        )
        os._exit(1)
    finally:
        writer.close()
```

`verify_or_exit` raises `SystemExit(1)` (`health_source_liveness.py:123`).
`SystemExit` derives from `BaseException`, not `Exception`, so `except Exception`
does not catch it — and CPython's `threading.excepthook` **silently ignores
SystemExit raised in a thread**. I verified the exact behaviour rather than
reasoning about it:

```
$ python3 -c '<thread raising SystemExit(1) inside try/except Exception/finally>'
finally ran
thread alive after: False
main still running — SystemExit in a thread did NOT kill the process
```

No traceback. No log line. No `os._exit(1)`. The `finally` runs, so
`jack_cpu_load` is cleaned up correctly — and then the HUD thread is simply
gone, the bench keeps running, systemd reports the unit healthy, and
`~/.mpe_sl_hud_state.json` goes stale. The touch UI's bar/beat sweep stops with
no explanation anywhere.

The trigger is not exotic: `specs_for_role("looper-session")` requires
`/run/mpe/meter.state` fresh within 3 s **and** `/run/mpe/engine.state`
(`health_source_liveness.py:85–95`). A looper session that starts before
`mpe-peak-meter` is publishing hits this on the boot path — which is the case
the check exists for.

The module's own docstring: *"A counter whose source is missing or stale must
fail loudly at start — not report 0 forever."* It fails **silently**, and it
does so in the one place in the codebase where it is called from a thread.

Fix: `except BaseException` (or catch `SystemExit` explicitly) in
`_hud_thread_main`. Note also the inconsistency: `--hud-only`
(`looper_session.py:120–142`) and `sl_hud_monitor.main` (`sl_hud_monitor.py:231`)
never call `verify_or_exit` at all, so the gate exists only on the path where it
cannot work.

### F17 🟢 — one file, two env-var names

`patch_browser/sl_hud_state.py:11` reads `MPE_SL_HUD_STATE_FILE`;
`patch_browser/health_source_liveness.py:73` reads `MPE_SL_HUD_STATE`. Override
one and the liveness check inspects a file nobody writes. A knob with two names
is a source of truth with two homes — the charter's whole subject.

### F18 🟢 — `_registered_at` records the request, not the outcome

`sl_hud_monitor.py:107–109`:

```python
    def maybe_reregister_session(self) -> None:
        self._sl.maybe_reregister()
        self._registered_at = time.monotonic()
```

`maybe_reregister` returns immediately when the bench thread already re-registered
inside the window, but `_registered_at` is updated regardless. The HUD's own
timer therefore records a re-registration that did not happen. Harmless today
because the bench does the work; it is the same "I asked ≠ it happened"
confusion that `DECISIONS.md` 2026-08-15 rule 6 ("solid means confirmed;
blinking means requested") settles for LEDs and that nothing settles for timers.

### Checked and clean — worth recording

* **`SlotRuntime` flush lifecycle.** I suspected a save could be started and
  never promoted from `.part`. It cannot: all three `_ensure_flushed` callers
  (`slot_runtime.py:364`, `474`, `496`) return `OP_WAITING` on `FLUSH_PENDING`,
  which parks the press, which makes the track "awaiting", which
  `poll_pending` polls every tick (`slot_surface.py:358–374`). The `finally`-based
  restore and the `.part` → `os.replace` → `_fsync_dir` sequence
  (`slot_runtime.py:602–658`) are correct, including the durability ordering.
* **State-file writes are atomic.** `HudWriter.poll` (`sl_hud_monitor.py:223–225`)
  and `write_alarm` (`sl-watchdog.py:391–393`) both write `.tmp` then
  `os.replace`. No torn reads are possible. `read_sl_hud_state`
  (`sl_hud_state.py:31–35`) enforces a staleness window and returns `{}` — a
  reader that correctly refuses stale truth. F3 defeats it by keeping the
  *timestamp* fresh while the *content* rots, which is a producer bug, not a
  reader bug.
* **No subprocess fork in any periodic loop.** I checked every timer.
  `jackd_running`/`engine_running` scan `/proc/*/comm` instead of `pgrep`
  (`sl-watchdog.py:154–178`, `322–348`); `read_graph_snapshot` reads
  `meter.state` instead of forking `jack_lsp` (`sl-watchdog.py:182–191`);
  `JackCpuLoadReader` holds one long-lived child instead of forking per sample
  (`looper_health.py:166–208`); `XrunCounter`/`MeterXrunCounter` read tmpfs.
  The 2026-08-18 doctrine has been applied thoroughly and the exception paths
  (F15) are the only forks left.
* **`running_code.py`** is the correct answer to "is this process running the
  deployed code", and `stale_source_files()` is a genuinely novel instrument.
  It is called once, in the startup banner (bench `448`). It should also be
  called from a health path, since its whole value is answering the question
  *later*.

---

## 9. Shutdown and signals

`_install_sigterm_handler` (`looper_session.py:69–99`) is correct in intent and
its docstring is exemplary. The problem is that it hands control to a call stack
with almost no `finally` blocks in it.

**What actually happens on `systemctl stop`:**

1. SIGTERM → handler raises `SystemExit(0)` **in the main thread**, wherever it
   happens to be inside `run_bench`'s `while True`.
2. `run_bench` has **no `try`/`finally`**. `midi_in` and `raw_midi_out` are never
   closed. The pacer's queue is discarded. No LED blanking. The APC keeps
   whatever it was showing.
3. Unwinds to `run_session`'s `finally` (`looper_session.py:157–162`):
   `hud_stop.set()`, `hud_thread.join(timeout=5.0)`, `hud_writer.close()`.
4. `SlOscSession` is never closed (F12). Its `serve_forever` thread is a daemon,
   so it dies with the interpreter — but the UDP port is held until then.
5. Interpreter shutdown waits on all non-daemon threads.

### F13 🟡 — `join(timeout=5.0)` on a `daemon=False` thread is not a bound

`looper_session.py:60–68` creates the HUD thread with `daemon=False`, and
`:160` joins it with a 5 s timeout. If the join times out, `run_session` returns
— and then `threading._shutdown()` blocks on that same non-daemon thread
**with no timeout at all**. The 5 s reads like a bound on shutdown and is not
one. The consequence is exactly the race the SIGTERM handler was written to
prevent: systemd waits out its full stop timeout, SIGKILLs the cgroup, and
starts the replacement in the same second, which opens the APC while the dying
process still holds it — dead pads, twice on 2026-08-27
(`looper_session.py:80–88`).

Worse, after the timeout `run_session` calls `hud_writer.close()`
(`looper_session.py:162`) **concurrently with a still-running HUD thread**. If
that thread is inside `collect_jack_graph_health` → `cpu_reader.read()`, the
sequence `close()` (sets `_proc = None`, kills the child) followed by the
thread's `_spawn()` can leave a **fresh `jack_cpu_load` orphaned** — the precise
mechanism behind the 705 zombie JACK clients (`looper_health.py:254–257`).

Fix: `daemon=True` for the HUD thread (its cleanup is already idempotent and
already runs in `finally`), or keep `daemon=False` and treat a join timeout as
fatal — `os._exit` after logging, rather than returning and calling `close()`
into a live thread.

### Thread inventory — `mpe-looper-session`

| Thread | Created at | Daemon | Owner of its lifetime | Stop signal | Cleanup |
|---|---|---|---|---|---|
| main | process | — | systemd | SIGTERM → `SystemExit` (`looper_session.py:92–94`) | **none in `run_bench`** — F13 |
| `sl-hud-writer` | `looper_session.py:62–69` | **No** | `run_session` | `hud_stop` Event | `writer.close()` twice (idempotent); join timeout is not a bound — F13 |
| OSC `serve_forever` | `sl_osc_session.py:100–103` | Yes | **nobody** | none | none — F12 |
| `JackCpuLoadReader` drain | `looper_health.py:204–207` | Yes | `JackCpuLoadReader` | child process exit | `close()` SIGKILLs the child — correct |
| `jack_cpu_load` (process) | `looper_health.py:192` | — | `JackCpuLoadReader` | SIGKILL only (ignores SIGTERM) | correct, but reachable-concurrently — F13 |

`sl-watchdog` and `sl-health` each additionally run one daemon OSC server
thread; `sl-health` shuts its server down properly (`sl-health.py:78–80`),
`sl-watchdog` never does (`sl-watchdog.py:129–137`) but is a one-shot-or-forever
daemon so it does not matter.

---

## 10. State files

| File | Writer | Cadence | Readers | Atomic? | Verdict |
|---|---|---|---|---|---|
| `~/.mpe_sl_hud_state.json` | `HudWriter.poll` (`sl_hud_monitor.py:222–225`) | ≥0.5 s, or on beat/bar/active change | `patch_browser.sl_hud_state.read_sl_hud_state` → `session_snapshot.py:738`, `looper_clock_monitor.py:62` | **Yes** — `.tmp` + `replace` | Torn writes impossible. **Content can be stale-but-fresh-stamped after an engine restart — F3.** `health.xruns` is unreliable — F9 |
| `~/.mpe_sl_watchdog.json` | `write_alarm` (`sl-watchdog.py:386–393`) | every 10 s cycle | none found in-repo | **Yes** | Correct, including the deliberate heartbeat refresh (`sl-watchdog.py:592–598`) so "still broken" ≠ "watchdog died" |
| `/run/mpe/meter.state` | `mpe-peak-meter` (out of scope) | 5 Hz | `sl-watchdog.XrunCounter`, `looper_health.MeterXrunCounter`, `audio_engine.*_via_meter`, `health_source_liveness` | n/a | **Two in-repo counter implementations of the same file** — one honest, one not (F9) |
| `<run>/session-events` | `mpe_session_event_emit` from `wire-sooperlooper-graph.sh` | on engine start | `LooperEngineEventWatch.poll` (bench, 1 Hz) | append-only | **The one emitter is not on the path Mitch uses — F2** |
| `~/.mpe/looper-clips/*.wav` + `.part` | `SlotRuntime._begin_flush`/`poll_flush` | per slot switch | `SlotRuntime.load` | **Yes** — fsync file, replace, fsync dir | Correct. Best durability code in the repo |

### F2 🔴 — the documented recovery never tells the bench it happened

`looper.engine.started` has exactly **one** emitter:
`wire-sooperlooper-graph.sh:59–61`, the `ExecStartPost` of
`mpe-sooperlooper.service`.

`restart-sooperlooper.sh` — the thing `mpe looper sl-restart` runs, and the
remedy every alarm in `sl-watchdog.py` points at (`sl-watchdog.py:513`, `581`)
and every failure message in `sl-health.py` points at (`sl-health.py:123`) —
**deliberately bypasses systemd**:

```sh
    if systemctl is-active --quiet mpe-sooperlooper.service 2>/dev/null; then
      log "stopping mpe-sooperlooper.service — manual bench owns the engine"
      sudo systemctl stop mpe-sooperlooper.service 2>/dev/null \
```

and then hand-starts the engine with `setsid nohup`, runs
`configure-grid-sync.sh` and `wire-jack-graph.sh connect` itself. `ExecStartPost`
never runs. **No `looper.engine.started` is ever emitted.**

So on the single most important recovery in the system:

* `LooperEngineEventWatch` never fires → `on_looper_engine_started` (bench
  `312–328`) never runs → grid config is whatever `configure-grid-sync.sh` set,
  and the bench's `GridState` still claims the old grid is established.
* `SlOscSession.last` is never invalidated (F3) → every pad keeps its old colour
  and the HUD keeps the old BPM.
* Only `maybe_reregister` recovers, and only the *subscriptions*, silently (F11).

The player runs the documented fix for a wedge, loses every take as warned, and
comes back to a surface still showing the takes that no longer exist. That is
the worst possible outcome for the one operation whose cost is already
data loss.

Fix: emit `looper.engine.started` from `restart-sooperlooper.sh` after its own
`record_path_ok && playback_path_ok` verify — it already has the identical check
at lines 104–110 and already sources nothing that would prevent it. Better:
extract the verify-and-emit tail into one function both scripts call, so there
is one definition of "the engine is up and wired".

### F7 🟡 — the recovery re-zeroes the engine phase and does not tell `GridState`

Bench `312–328`:

```python
        apply_grid_sync(_send, num_loops=num_loops)
        if grid.established and grid.bpm:
            establish_grid_clock(_send, grid.bpm, bars=grid.bars or 1)
            set_grid_active(_send, num_loops=num_loops, active=True)
```

`apply_grid_sync` sends `/set tempo <DEFAULT_BPM>` (`sl_grid_sync.py:196`) and
`establish_grid_clock` sends `/set tempo <grid.bpm>` (`sl_grid_sync.py:235`).
Per the README and `sl_grid_sync.py:230–231`, **re-sending the tempo *is* the
phase reset** (`Engine::set_tempo` zeroes `_quarter_counter`).

So this path zeroes the engine's phase **twice** and never calls
`grid.mark_phase_zero(...)`. Every other caller does:

* `on_grid_established` — bench `235`
* `on_phase_reanchor` — bench `246`
* `stop_all_loops` — `track_gesture.py:966–967`

After an engine restart the bench's bar line is anchored to a downbeat the
engine no longer has, and `grid.next_boundary(...)` — which `SlotRuntime` uses
as its quantization boundary (bench `361`) — schedules launches against a phase
that is off by an arbitrary fraction of a bar. Fix: one line,
`grid.mark_phase_zero(time.monotonic())` after `establish_grid_clock`. Better:
make `establish_grid_clock` take the `GridState` and do both, so the phase reset
and the phase record cannot be separated — there are now four call sites and
three of them remembered.

---

## 11. Verdict

The individual modules are better than most production audio tooling I have
read. The composition is not owned by anyone, and every 🔴 in this review is a
composition bug: two objects writing the same LEDs in an order nobody chose
(F1), a recovery script and an event consumer that were never introduced (F2), a
cache with no invalidation seam (F3), a `BaseException` crossing a thread
boundary into an `except Exception` (F4).

The pattern is consistent enough to name. **This codebase reliably gets the
"what" right and the "when" wrong.** Every module knows what the correct value
is; the defects are all about *at what moment, relative to what else*. That is
what an ownership refactor is for, and it is why the charter's diagnosis —
"a bug that returns is a bug whose owner was never decided" — is correct.

The three things I would fix before touching anything else: make
`reopen_apc` leave a surface that is actually painted (F1); make `sl-restart`
tell the bench it happened (F2); and give `SlOscSession.last` an invalidation
seam so it cannot outlive the engine (F3). The first strands the surface; the
second and third strand the *truth about* the surface, which is worse.

**Priority backlog**

1. **F1** — delete `clear_unwired_surfaces`; one owner per note. Regression test
   asserts no `LED_OFF` lands last on 8–63 / 112–119 after `reopen_apc` under
   multigrid.
2. **F2** — emit `looper.engine.started` from `restart-sooperlooper.sh`; share
   the verify-and-emit tail with `wire-sooperlooper-graph.sh`.
3. **F3** — engine epoch on `SlOscSession`; clear + re-`get` on restart; expire
   `cached()` entries so absence looks like absence.
4. **F4** — `except BaseException` in `_hud_thread_main`; call `verify_or_exit`
   on all three HUD entry points or none.
5. **F11 + the loop refactor** — one `service_tick(now)`, one owner of
   re-registration, and make the repair report itself.

---

## Poll loop order

Bench main loop, `scripts/sooperlooper-apc-bench.py:583–763`. Measured on this
x86 laptop (Python 3.14.4) with real objects; the Pi 5 column is an **estimate**
at ×3 and is labelled as such.

### Idle iteration (`packet is None`, bench 606–615) — the hot path

Measured loop period: `time.sleep(0.002)` takes **2.062 ms** → **~485 Hz**.

| # | Call | bench line | Cadence | Cost (x86, measured) | Pi 5 est. | Ordering dependency |
|---|---|---|---|---|---|---|
| 1 | `midi_in.get_message()` | 605 | every iter | ~1 µs (rtmidi C) | — | must precede everything |
| 2a | `link_health.poll()` | 525 | gated to **2.0 s** | ~0 µs gated; on the 2 s tick, one read + regex of `/proc/asound/seq/clients` | small | **D6** — before `pump()` so a reopen drains this iteration |
| 2b | `midi_out.pump()` | 526 | every iter, ≤1 msg / **1.5 ms** | ~0 µs empty | — | after 2a |
| 2c | `poll_track_gestures` | 527 | every iter | **2.7 µs** single-grid / **1.6 µs** multigrid (15 gestures) | ~8 / ~5 µs | **D5** — must precede 2f |
| 2d | `slot_surface.poll_hold` | 529 | every iter (multigrid only) | ~0.0 µs idle | — | after 2c |
| 2e | `slot_surface.poll_hold_led` | 530 | every iter (multigrid only) | ~0.0 µs idle | — | **D7** — writes outside `_painted` |
| 2f | `slot_surface.poll_led_repaint` | 532 | every iter (multigrid only) | **42.8 µs** | ~128 µs | after 2c and 2e |
| 3a | `transport_leds.poll()` | 555 | every iter | ~0 µs on mk2 (mk1 branch returns early) | — | — |
| 3b | `slot_surface.repaint_scenes()` | 557 | every iter (multigrid only) | **18.5 µs** | ~55 µs | — |
| 4 | `maybe_track_transport()` | 559 | every iter | ~0 µs | — | **D8** — runs *before* `poll_holds` in the transport branch only |
| 5 | `tick_faders()` | 610 | **idle branch only** | ~0 µs while `_target_wet` empty | — | **F16** — missing from 9 branches |
| 6 | `state_listener.maybe_reregister()` | 611 | gated to **15 s** | ~0 µs gated; on tick **61 UDP sends** + possibly a **400 ms blocking `get`** | same | **F11** — called from 13 sites |
| 7 | `poll_engine_events()` | 613 | gated to **1.0 s** | ~0 µs gated; on tick one `stat()`, plus a 64 KB read + parse when the file changed | small | **F2** — the event it waits for is not emitted by `sl-restart` |
| 8 | `time.sleep(0.002)` | 614 | every iter | 2.062 ms actual | — | sets the loop rate |

**Idle total, multigrid on: 63.0 µs of work per 2.062 ms iteration = 3.06 % of
one x86 core, estimated ~9 % of a Pi 5 core.** `MPE_SL_MULTIGRID` defaults to
`0` (bench 273); with multigrid off the same total is **~2.7 µs = 0.13 %**.
The entire cost is the two `slot_surface` repaint calls, and in steady state
both emit zero MIDI.

### Non-idle branches — same block, nine hand-copied variants

| Branch | bench lines | Calls, in order | Differs how |
|---|---|---|---|
| mode SysEx | 621–629 | `poll_engine_events` only | no `poll_holds`, no LED poll |
| short/invalid msg | 631–638 | holds, transport LEDs, track transport, reregister, engine events | no `tick_faders` |
| control change | 643–651 | `handle_cc` then the block | `handle_cc` calls `faders.tick` itself (552) |
| mk1 ghost consumed | 655–663 | the block | — |
| scene launch | 686–696 | `scene_press` then the block | — |
| slot surface note | 698–708 | `note_down`/`note_up` then the block | — |
| reserved grid note | 710–718 | the block | — |
| arrow | 722–729 | `set_view` then the block | `set_view` repaints + rebinds faders together (bench 404–422) |
| **transport (Shift/StopAll)** | 731–741 | **`maybe_track_transport` BEFORE `poll_holds`** | **D8 — the only branch that reverses the order, uncommented** |
| pad / fallthrough | 743–763 | the block | — |

### Other periodic loops in the stack

| Loop | Cadence | Cost | Fork? |
|---|---|---|---|
| HUD thread (`looper_session.py:44–48`) | 100 ms | `writer.poll()`: 15 cache reads + a JSON write at ≤2 Hz | No |
| HUD graph health (`sl_hud_monitor.py:212–216`) | 500 ms | one tmpfs read + one lock-protected float read | No — long-lived child, `looper_health.py:166` |
| HUD re-register (`looper_session.py:45–46`) | 15 s | delegates to F11 | No |
| `sl-watchdog` cycle (`sl-watchdog.py:416–607`) | **10 s** | `/proc` scan ×2, two tmpfs reads, one `check_command_path` (≈0.5 s settle + 2 blocking gets) | **Only on repair paths** — `repair_governor` (`:226`) and `wire-jack-graph.sh` (`:530`). **F15: no fight limit on the latter** |
| `PacedMidiOut` gap | 1.5 ms/msg | 64-pad repaint ≈ 96 ms | No |
| `LinkHealth` | 2.0 s | one `/proc/asound/seq/clients` read | No |

---

## Lifecycle ownership map

| Resource | Owner today | Should be | Recovery path | Gap |
|---|---|---|---|---|
| APC MIDI **in** handle | `run_bench` local `midi_in` | one `ApcLink` object | `reopen_apc` (bench 471) driven by `LinkHealth` | Never closed on shutdown; no `finally` in `run_bench` |
| APC MIDI **out** handle | `run_bench` local `raw_midi_out`, wrapped by `PacedMidiOut` | same `ApcLink` | `reopen_apc` | **F14** — reopened by an index from the *input* enumeration |
| "Do we still have the device" | `LinkHealth` (`apc_link.py:140`) | `LinkHealth`, checking **both** directions | re-ask kernel every 2 s → `on_lost` → confirm | **F5** — reader-only; declares healthy over a dead LED path |
| MIDI write pacing / backlog | `PacedMidiOut` | unchanged — this is right | `reset()` on reopen | **F6** — drops the queue on any write error, recovery keyed to a different signal |
| Per-model LED encoding | `PacedMidiOut.apc_label` | unchanged — one seam, correctly argued (bench 191–192) | set once after variant resolution | None. **D4 is the model for the rest.** |
| Clip-row pad LEDs (row 0) | `TrackGesture._sync_led` via `apply_view` | `SlotSurface` when multigrid, `TrackGesture` otherwise | `apply_view(force)` on bank change + reopen | Two owners depending on a flag |
| Matrix pad LEDs (rows 1–7) | `SlotSurface._painted` **and** `TransportButtonLeds.clear_unwired_surfaces` | `SlotSurface` alone | `repaint(force=True)` | **F1 🔴** — second writer runs last and the diff cache makes it permanent |
| Scene-launch LEDs (112–119) | `SlotSurface._scene_painted` **and** `TransportButtonLeds` (ctor + `repaint`) | `SlotSurface` when multigrid | `repaint_scenes(force=True)` | **F1 🔴** — dark since startup under multigrid |
| Transport LEDs (Shift/StopAll/stale) | `TransportButtonLeds._last_vel` | unchanged | `repaint()` clears cache first — correct | Its `repaint` reaches far beyond its own notes |
| OSC listen port + server thread | `SlOscSession` | unchanged, **plus a `close()`** | none | **F12** — no shutdown; port held until process death |
| OSC engine client | `SlOscSession._client` | unchanged | none needed | — |
| **Engine state cache** (`self.last`) | `SlOscSession` writes it; **nobody owns its validity** | `SlOscSession`, with an epoch + expiry | none | **F3 🔴** — survives an engine restart; `register_auto_update` is change-only so it is never corrected |
| Auto-update subscriptions | shared by 13 call sites | `SlOscSession.tick()`, one caller | `maybe_reregister` every 15 s, unconditional and silent | **F11** — no owner; silent repair hides the fault |
| Engine restart notification | `LooperEngineEventWatch` (bench 330) ← `wire-sooperlooper-graph.sh` only | both restart paths must emit | `on_looper_engine_started` (bench 312) | **F2 🔴** — `sl-restart` never emits, so the recovery consumer never runs |
| Grid tempo / phase anchor | `GridState`; engine phase reset by any `tempo` send | `GridState`, with the send and the mark inseparable | `on_grid_established`, `on_phase_reanchor`, `stop_all_loops` all mark | **F7** — `on_looper_engine_started` resets phase twice and marks neither |
| HUD state file | `HudWriter` | unchanged | atomic replace; reader enforces staleness | Content stale after engine restart (**F3**); `health.xruns` unreliable (**F9**) |
| JACK graph (`common_out → playback`) | `sl-watchdog` | unchanged | `wire-jack-graph.sh connect` + meter verify | **F15** — no fight limit; ~32 forks + 15 `jack_connect` client registrations per 10 s on a stuck graph |
| CPU governor | `sl-watchdog` (opt-in) | unchanged | `systemctl restart mpe-cpu-governor` | None — the fight limit here is the model F15 should copy |
| Engine process lifetime | systemd (`mpe-sooperlooper.service`) / `restart-sooperlooper.sh` by hand | systemd, with the hand path emitting the same event | `run-sooperlooper.sh` reaps strays first — correct | **F2** |
| Xrun count | **two** implementations (`sl-watchdog.XrunCounter`, `looper_health.MeterXrunCounter`) | one, in `patch_browser` | n/a | **F9** — one keeps the blindness signal, one discards it at its only call site |
| `jack_cpu_load` child | `JackCpuLoadReader` | unchanged | SIGKILL in `close()`; respawn with backoff | **F13** — `close()` reachable concurrently with the owning thread after a join timeout |
| HUD thread lifetime | `run_session` | unchanged, but `daemon=True` or fatal-on-timeout | `hud_stop` + join | **F13** — join timeout is not a bound; **F4** — thread can die silently |
| Bench main thread lifetime | systemd via SIGTERM handler | unchanged | `SystemExit` → `run_session` `finally` | `run_bench` has no `finally`: MIDI ports, LEDs and the OSC port are all released by process death alone |

---

*Read in full: `looper_session.py`, `sl_osc_session.py`, `sl_bench_listener.py`,
`apc_link.py`, `midi_subscription.py`, `sl_hud_monitor.py`, `sl_probe.py`,
`sl-health.py`, `sl-watchdog.py`, `looper_engine_events.py`, `running_code.py`,
`scripts/sooperlooper-apc-bench.py`, `patch_browser/looper_health.py`,
`patch_browser/sl_hud_state.py`, `patch_browser/health_source_liveness.py`, and
the four shell scripts. Read in part (for the seams they expose to this
dimension only): `track_gesture.py`, `slot_surface.py`, `slot_runtime.py`,
`slot_leds.py`, `apc_transport.py`, `sl_grid_sync.py`, `loop_mix.py`,
`looper_songs.py`. Not read: `apc_grid.py`, `apc_panel.py`, `apc_faders.py`,
`loop_model.py`, `tail_phase.py`, `sl_grid_state.py` internals, and the test
suite beyond `test_sl_osc_session.py` and `test_sl_hud.py` — those belong to the
LED, note-identity, track-state and clock reviewers.*

*Measurements in this document were taken on the review laptop (x86_64, Python
3.14.4), not on `raspberrypi5`. The Pi 5 columns are ×3 estimates and are
labelled as estimates everywhere they appear. Per `AGENTS.md` and the charter's
§5, nothing here was verified against the appliance and no sound was made.*
