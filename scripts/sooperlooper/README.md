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

## APC 16-loop clip grid (target layout)

| Row | APC notes | Loops | Role |
|---|---|---|---|
| **0** (bottom) | 0–7 | 0–7 | Clip pads |
| **3** | 24–31 | 8–15 | Clip pads |
| 1, 2, 4–7 | — | — | Per-loop controllers (future) |
| **Faders 1–8** | CC 48–55 | column: N *and* N+8 | Loop level |
| **Master fader** | CC 56 | loop bus | Loop-mix master |

Mapping: `apc_grid.py` · 16-pad footswitch: `../sooperlooper-apc-bench.py` (rows 0 + 3)

**Faders:** one fader per grid *column*, so fader N moves both clips stacked in
that column (loop N on row 0 and loop N+8 on row 3). Eight faders cover all 16
loops with no bank button. This is a starting semantic, not the final one —
per-loop control needs either banking or the reserved controller rows.

⚠️ **CC numbers unverified against hardware.** They are resolved per variant in
`apc_faders.py` (mirroring how `apc_transport.py` resolves Shift/Stop-All, which
*do* differ between mk1 and mk2). Confirm with `--dump-midi` and move each fader.

**Master = loop bus only.** It scales `common_out`, not the live synth: the
audio graph runs `Surge → system:playback` in parallel with
`Surge → SooperLooper → common_out → playback`, and the live path must fail open
(DECISIONS.md). Live level stays on the per-patch Vol fader on the touch screen.
The engine-side control name is `MPE_SL_MASTER_CONTROL` — also unverified.

**Faders don't move on their own.** They have no motors, so at startup their
physical positions mean nothing. Levels are seeded from the engine's reported
`wet`, and a fader stays inert until it crosses the value it is supposed to be
at. A fader that appears dead has not been picked up yet — sweep it.

Level composition lives in one place, `loop_mix.wet_for()`:
`wet = taper(user gain) × auto_law(active loops)`. The `loop_gain/N` backstop
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

**Touch HUD:** `start-sooperlooper-hud-monitor.sh` → bar/beat derived from the
grid tempo and SL's reported loop position (`~/.mpe_sl_hud_state.json`). Pure
Python, no JACK client.

**APC bench:** `start-sooperlooper-apc-bench.sh` — OSC state listener on port **9953** (all loops incl. 0).

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
