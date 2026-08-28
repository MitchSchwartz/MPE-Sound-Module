# SooperLooper looper scripts

**Branch:** `dev` · Pi binary: `~/src/sooperlooper-1.7.9/src/sooperlooper`

## Clock — settled (spec §K, DECISIONS 2026-08-15)

**SooperLooper's own internal sync.** `sync_source = -3` plus an explicit
`tempo`. No timebase master, no extra process, no JACK transport.

A grid needs three things — tempo, unit, and **phase**. `Engine::set_tempo`
zeroes `_quarter_counter` and `_tempo_counter`, so **re-sending the tempo is the
phase reset**. That is why the JACK timebase master was never needed: its only
job was phase.

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

⚠️ **The arrow-button notes are UNVERIFIED against hardware**, exactly like the
fader CCs. They resolve per variant in `apc_transport.py::resolve_arrow_notes`
through the same port-name path as Shift and Stop-All (which *do* differ
between mk1 and mk2). On mk1 they may be shift-functions of the top button row
rather than notes of their own. Confirm with `--dump-midi` and press each arrow.

**No bank indicator yet.** With eight of sixteen showing, nothing on the
surface says which half you are on — the bench prints it, the hardware does
not. Row 3 just freed up and the mk2 arrows have LEDs; both are candidates.

⚠️ **Fader CC numbers also unverified.** Resolved per variant in
`apc_faders.py`. Confirm with `--dump-midi` and move each fader.

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
(DECISIONS.md) is off by default (`MPE_SL_LOOP_GAIN_LAW=1`); the point of the
seam is that nothing else ever writes `wet`.

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
