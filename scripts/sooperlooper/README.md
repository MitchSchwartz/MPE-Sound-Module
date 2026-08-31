# SooperLooper looper scripts

**Branch:** `dev` · Pi binary: `~/src/sooperlooper-1.7.9/src/sooperlooper`

## Clock — settled (spec §K, DECISIONS 2026-08-15)

**SooperLooper's own internal sync.** `sync_source = -3` plus an explicit
`tempo`. No timebase master, no extra process, no JACK transport.

A grid needs three things — tempo, unit, and **phase**. `Engine::set_tempo`
zeroes `_quarter_counter` and `_tempo_counter` (verified, `engine.cpp:2174-2178`),
so **re-sending the tempo is the phase reset**. That is why the JACK timebase
master was never needed: its only job was phase.

**One owner, one seam (2026-08-30).** `GridState` holds all three quantities;
`sl_grid_sync.apply_established_grid(send, grid, …)` is the only thing that
tells the engine about them, and `establish_grid_clock` has no other caller.
Both are enforced by `tests/test_clock_tail_ownership.py`, which also fails if
any module outside `sl_grid_sync` writes `/set ["tempo", …]` at all — because
writing the tempo *is* moving the downbeat, and the four places that did it
by hand had three different bugs between them (a missing phase mark after an
engine restart, a stale bar count on the phase re-anchor, a song load that
never carried a bar count to begin with).

The seam sends **smart_eighths off, then `eighth_per_cycle`, then `tempo`** —
tempo last precisely because it is the phase reset — and marks the bench's
phase zero in the same call, so the two halves cannot drift apart.

**A song carries its own grid.** The manifest stores `bars` and `cycle_s`
alongside `bpm` (additive, no version bump; absent they read 1 and are derived,
which is what every song saved before 2026-08-30 did). `save_song` reads the
unit off the engine — `/get "eighth_per_cycle"` is answered at
`engine.cpp:1898-1899`, and `cycle = eighth_per_cycle * 30 / tempo` at
`engine.cpp:2310` — and `load_song` restores through the same seam. Before
this, loading a song re-established at the default one bar, so a song whose
first take read as four bars came back quantizing to a quarter of the take.

**The first take defines the grid.** It records free-form and instant, with no
bar to count in to; its length yields the tempo. Every later clip counts in and
quantizes. Standard looper workflow — Boss RC-20, JamMan, Ableton, Loopy Pro all
work this way. After establishment the defining clip is ordinary and can be
deleted like any other.

The JACK-transport path is **deleted, not deprecated** (`e279d6f`). Anything
describing a timebase master, `start-jack-timebase.sh` or `spike-jack-transport.py`
is stale — those files are gone.

## APC 16-track clip row (Ableton-style, banked)

| Row | APC notes | Tracks | Role |
|---|---|---|---|
| **0** (bottom) | 0–7 | 8 visible of 16 | Clip pads — record/play/stop lives here |
| 1–7 | — | — | Reserved (per-track controllers, scenes — future) |
| **Faders 1–8** | CC 48–55 | the 8 visible tracks | Track level |
| **Master fader** | CC 56 | all 16 | Loop-mix master |
| **Up / Down** | arrows | — | Page the viewport by 8 |
| **Shift + Left / Right** | arrows | — | Nudge the viewport by 1 |

Mapping: `apc_grid.py` (`GridView`) · pads: `track_gesture.py` · bench: `../sooperlooper-apc-bench.py`

**What a button does is a row, not a branch** (2026-08-30, charter stage 5).
`control_registry.py` says which physical control sends which note;
`binding_table.py` says what happens when you press it, as one row per
(control, gesture, layer, mode). The bench event loop looks the row up — it does
not embody it. To answer "what does this pad do on press, on hold, under
Shift?", read `binding_table.BINDINGS`; nothing else has an opinion.

Three things fall out that could not be said before:

* **Two rows cannot both match an event.** `assert_no_binding_collisions`
  refuses the table at import and names both source lines. Combined with
  `control_registry.assert_no_collisions` (one note, one control) there is no
  "first match wins" anywhere, so there is no order to get wrong.
* **A dead button is reportable without hardware.** `unreachable(variant)`
  returns the rows whose control has no established note, with the reason and
  the line. On the mk2 that is exactly the four bank arrows.
* **An unbound control is written down.** All eight track-select buttons carry
  `noop` rows. Before this, a wrong note number and a button nobody touched
  produced identical silence.

Timing stays where it is: a HOLD row records the threshold's env var and which
module counts the milliseconds (`ShiftHoldCombo`, `SlotSurface.poll_hold`,
`TrackGesture.poll_hold`), and a test asserts the bench actually feeds that env
var into that module — so the number in the table cannot drift from the number
that runs.

**Tracks run left to right on one line.** The APC is eight columns wide, so it
is a *viewport* onto sixteen tracks, not a container for them. This replaced
the row-0/row-3 split (2026-08-16), where fader N drove loops N *and* N+8
because both sat in the same column. Under the viewport a column holds exactly
one track, so a fader means one track — which is what makes "one clip at a time
per column" and per-column patch state expressible at all. Neither is
expressible while two unrelated loops share a column.

Banking **clamps, never wraps**: running off the end and stopping is something
a player can feel without looking; a wrap silently teleports the bank mid-jam.

**A bank change drops all eight fader anchors.** The faders have no motors, so
after banking, fader 0 still sits where the *previous* track's level left it
while now addressing a different track. Applying the next movement as a delta
from that stale anchor jumps the new track's level — the exact jump relative
pickup exists to prevent. So the next touch of each fader re-anchors and
changes nothing. Stored levels are untouched: tracks scrolled off-screen keep
playing at the level they were left at, and only the binding moved.

Every track keeps a gesture whether or not it is on screen — a banked-off
track keeps playing, keeps receiving engine state and keeps its pending intent;
only its pad binding goes away (`note=None`) and comes back on the next bank
change. Banking clears the whole clip row before repainting: a pad left lit by
the previous bank is a track the player believes is running and isn't, and it
is the one failure here that can't be debugged from the surface.

🔴 **Banking does not work on the mk2, and has never worked.** The recalled
arrow notes `0x70–0x73` are scene buttons 1–4 (`device_facts.apc.buttons.note_sets`,
MEASURED 2026-08-29), so the viewport is pinned at offset 0 and tracks 9–15 of
15 cannot be reached from the surface at all. As of 2026-08-30 the mk2 arrow
notes are recorded as **unknown** in `control_registry` rather than replaced
with another guess, `resolve_arrow_notes` returns `{}` for mk2, and the startup
banner says so instead of advertising the feature. The mk1 tuple (`0x40–0x43`)
is still unverified recall; it collides with nothing, so it stands. See
`device_facts.apc.bank_arrows.notes`.

**How it used to fail, and how it fails now.** Until stage 5 the mechanism was
statement order: the bench's scene branch took those notes and `continue`d
forty-five lines before `handle_arrow` was reached. Nothing could see that —
reachability was a property of the event loop's `if` sequence, and no test read
statement order. Since 2026-08-30 the arrows have no note at all, so
`binding_table.unreachable("mk2")` returns their eight rows with the reason and
the source line, and `tests/test_binding_table.py` fails the moment a NEW
binding joins them. The button is still dead; the difference is that the repo
says so out loud, without a device.

To close it: stop the session, run `sooperlooper-apc-bench.py --dump-midi`,
press Up/Down/Left/Right, and record the four notes at MEASURED tier in
`device_facts.py` and as rows in `control_registry.CONTROLS`. Five minutes and
one pair of eyes. Do not fill them in by reasoning — reasoning has produced
three wrong answers about this panel already.

**No bank indicator yet.** With eight of fifteen showing, nothing on the
surface says which half you are on — the bench prints it, the hardware does
not. Row 3 just freed up; that is the candidate. (The earlier note here said
"the mk2 arrows have LEDs" — nothing has been measured about the arrows'
lamps, and per `apc.buttons.single_colour` any lamp on them would be single
colour anyway.)

⚠️ **Fader CC numbers also unverified** — `device_facts.apc.faders.ccs`,
VENDOR tier. Resolved per variant in `apc_faders.py` from `control_registry`.
Confirm with `--dump-midi` and move each fader. The failure is silent: a wrong
CC is indistinguishable from a fader nobody touched.

**Master = loops only.** It scales the loop mix, not the live synth: the
audio graph runs `Surge → system:playback` in parallel with
`Surge → SooperLooper → common_out → playback`, and the live path must fail open
(DECISIONS.md). Live level stays on the per-patch Vol fader on the touch screen.
**The master is arithmetic, not a bus control.** An engine-global `/set wet`
would be the obvious mapping, but every global this system sends is a *setting*
(`tempo`, `sync_source`, `fade_samples`) — nothing has ever written a level at
engine scope, so that control is unproven, and OSC drops a message to a control
the engine lacks in silence. So the master is a factor in `wet_for()` and moves
all 16 loops over per-loop `wet`, which is proven live. Loops sum into
`common_out` through plain `jack_connect` with no gain or limiter stage, so
scaling all 16 is exactly equal to scaling the bus. One master move is 16 OSC
messages, capped by the same coalescer as the rest.

**Faders don't move on their own.** They have no motors, so at startup their
physical positions mean nothing. Levels are seeded from the engine's reported
`wet`. The first touch **anchors** relative pickup (no jump); movement after
that applies delta from where you grabbed. Output is smoothed (~45 ms default,
`MPE_APC_FADER_SMOOTH_MS`) so fast drags track cleanly.

Level composition lives in one place, `loop_mix.wet_for()`:
`wet = taper(user gain) × taper(master) × auto_law(active loops)`. The three
contributions meet in one multiply, always recomputed in full from state we own,
so nothing compounds. The `loop_gain/N` backstop
(DECISIONS.md) is off by default (`MPE_SL_LOOP_GAIN_LAW=1`).

**One composer, and one named exception — corrected 2026-08-30.** This
paragraph used to end "the point of the seam is that nothing else ever writes
`wet`." That was not true when it was written. `looper_songs.load_song` sends
`/sl/N/set ["wet", …]` directly, once per restored track, and `save_song` reads
it back — song load/save has owned the level on its own axis the whole time.

It is not a bug to be closed by routing it through the seam, because it cannot
reach the seam. Songs are loaded by the **touch browser**
(`touch-patch-browser.service`); `LoopMix` lives in the **bench**
(`mpe-looper-session.service`). Every column gain, the master position and the
active-loop count are in the other process. So the invariant is the narrower
one that is actually true:

| | |
|---|---|
| **Composer** | `loop_mix.wet_for()` — the only place contributions are combined, and the sole writer inside the bench process |
| **Exception** | `looper_songs.load_song`, restoring the level a song was saved at. That call and nothing else, in any process |
| **Echo detection** | Two questions, not one: is this the settled level (`wet_for()` within `WET_ECHO_TOLERANCE`), *or* is it something the sender actually put on the wire (`CoalescingSender.was_emitted`, last `EMITTED_HISTORY`=8 values)? The second is not optional — the sender smooths, so a master move ramps and the engine echoes every intermediate value. Without it those steps read as foreign writes and walk `user_gain` down (MEASURED 2026-08-31, hardware: `wet` at a fixed master of 1.0 read 0.9959 / 0.9604 / 0.9262 / 0.8604 over four cycles; in `tests/test_master_ramp_echo.py` the same rig drags a column gain 127 → 87, about −8 dB) |
| **Reconciliation** | The bench *subscribes* to `wet` (`sl_osc_session` `register_auto_update` — a read, not a write) and `LoopMix.seed_from_engine` adopts any value it did not ask for: it backs out master and law, adopts the implied column position and re-arms pickup. The foreign write is absorbed, not fought |
| **Also gated** | `sl_probe` can write `wet` as a command-path probe *only* if `MPE_SL_PROBE_CONTROLS` names it — it is not in the default chain (`rec_thresh,dry`), and `AUDIO_PATH_CONTROLS` exists to warn when a probe lands in the audio path. It restores after every write |
| **Enforced by** | `tests/test_track_state_ownership.py::WetOwnershipTests`. A third writer fails the build naming its file, line and function |
| **Not an exception** | `remote_fader` carries the touch UI's Vol position across the process boundary as a *fader position*, and the bench replays it through `handle_cc` — the same entry the hardware master fader uses. It composes nothing and writes nothing; the master gain it moves is still multiplied in by `wet_for()`. `tests/test_remote_fader.py::TestBenchWiring` fails if that path ever calls the sender or the composer directly |

**Why the touch Vol fader is not a second writer.** Surge's amp trim moves the
live synth only. On the multichannel USB out that is channels 1/2 — every loop
stem keeps playing at its recorded level, which is why the fader felt dead once
stems existed. The level that moves the stems is the master gain already inside
`wet_for()`, and it lives in `mpe-looper-session.service`.

Letting the touch UI write `wet` directly, the way `load_song` may, would have
been the short path and the wrong one: `load_song` fires **once**, and
`seed_from_engine` adopts the result afterwards. A volume fader is a continuous
drag, so it would be a *continuous* second writer — `seed_from_engine` would
keep back-computing column gains from a master the other process was still
moving, and the resulting drift would present as a hardware fault. Instead
`remote_fader` ships the fader position (UDP, `master <0-127>`, port
`MPE_LOOPER_CTL_PORT`, default 9956) and the bench feeds it to `handle_cc`. The
socket is non-blocking and drained from the bench's existing idle branch: a
thread would be another scheduler client in a process whose job is to not be
late. If the looper is not running the send fails silently and Vol trims Surge
alone, exactly as before.

**Known consequence, undecided.** The manifest stores the **composed** level —
`save_song` reads the engine's `wet`, which already has master and law in it.
Reload with the master somewhere else and the loops come back at their saved
*absolute* level while the master fader reads whatever it happens to read, so
the fader stops corresponding to the sound until it is next moved. Storing the
column gain instead would keep the fader honest and change how songs already on
the appliance play back. That is a call for Mitch's ear, not a silent fix.

**Grid sync:** two states, not a pile of settings — `set_grid_active(active=)`.
Off until the first take lands (so it records instantly), then on for every clip
after. Free-form throughout: `MPE_SL_SYNC_MODE=freeform`.

**Pad colours:** a solid colour always comes from the engine; a blink is
something asked for and not yet confirmed. Red blink = queued to record. Yellow
blink = queued to stop, waiting for the bar. Green blink = queued to launch.
Red/green alternating = still recording, playback queued. **Solid green means
there is audio in that loop** — see spec §L.

**Transport (Shift + Stop All Clips):** quick release = stop all; hold **3 s** = clear all. Verify note numbers: `sooperlooper-apc-bench.py --dump-midi`.

**Touch HUD:** `mpe-looper-session.service` (HUD thread) → bar/beat derived from the
grid tempo and SL's reported loop position (`~/.mpe_sl_hud_state.json`). Pure
Python, no JACK client.

**APC bench:** merged into `mpe-looper-session.service` — OSC state listener on port **9953** (all loops incl. 0). Debug: `looper-session.py --bench-only`.

## Health and recovery

```bash
mpe looper sl-health      # is the COMMAND path alive, not just the read path?
mpe looper sl-watchdog    # start|stop|status|once — alarms on orphan/wedge
mpe looper sl-rewire      # non-destructive: fix the JACK graph
mpe looper sl-restart     # DESTROYS every recorded loop — human call only
mpe looper sl-bench restart   # pull + restart the APC bench
mpe looper deploy dev     # git sync the Pi (does NOT restart the bench)
```

**If pads go green with no audio, check `sl-health` first.** The engine can
survive a jackd restart as a process while losing its JACK client, at which
point every command is silently discarded and every read-only check still says
healthy. That cost three evenings — spec §M.

## Test clips + smoke (no manual recording)

```bash
mpe looper sl-clips          # on Pi (default)
mpe looper sl-clips local    # laptop clone → tests/fixtures/sooperlooper-loops/
mpe looper sl-smoke          # restart -l 16, load, trigger, VmRSS + jack_cpu_load
mpe looper sl-diagnose       # 45s soak: fan-in, xrun/journal, peak (needs jack-capture)
```

Or directly on the appliance:

```bash
bash scripts/sooperlooper/generate-test-clips.sh
bash scripts/sooperlooper/smoke-16-loops.sh
```

Restarts SooperLooper with `-l 16`, loads fixture WAVs, triggers all loops, prints VmRSS + `jack_cpu_load`.
