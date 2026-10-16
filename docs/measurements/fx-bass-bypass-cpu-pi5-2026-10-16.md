# Bass-bypass crossover CPU cost, Pi 5 (2026-10-16)

*PR #185 (`feat/bass-bypass` @ `08ee959`). Offline DSP bench on the Pi 5: nothing joined JACK, no sound, no service or level change.*

## Answer

*(filled in after the run, below the pre-registration)*

## Pre-registration (written and committed before any Pi run)

```
Question:       on the Pi 5, how many ns per stereo frame does the bass-bypass
                crossover add (Off vs 120 Hz) with (a) one effect (chorus,
                depth 1.0), (b) all seven (depth 1.0), (c) chorus ticked at
                depth 0? Converted to % of one core at 48 kHz and to voices.
Claim class:    magnitude with interval (a ranking of Off vs 120 Hz per case),
                n = 10 paired processes per case (Off and 120 Hz adjacent in
                time, order alternating per repeat)
Premises:
  | premise                                          | verified how                         | when       |
  | Off and 120 Hz differ only in fx_engine_set_bass | read the driver and fx_dsp.c process | 2026-10-15 |
  | the split is settled at 120 Hz while timed       | driver asserts bass_mix==1, bass_hz==120, bass_warm==1 after the timed span, else halts | every run |
  | depth 0 runs the parked split                    | driver asserts bass_warm==1, bass_mix==0, bass_hz==10 | every run |
  | Off runs no split                                | driver asserts bass_warm==0, bass_mix==0 | every run |
  | /home is exec-mountable on the Pi                | findmnt: / rw,noatime (no noexec)    | 2026-10-15 |
  | CPU1 at a fixed clock                            | governor performance, 2.4 GHz cpu0-3 | 2026-10-15, restamped by the harness |
Instruments:    CLOCK_MONOTONIC around N periods of fx_engine_process at
                96 frames (the live period), after a 3 s untimed warm-up;
                ns/frame = elapsed / frames. Written for this run.
Conformance:    negative: an impossible case, an impossible corner (5 Hz) and a
                run killed at 0.3 s must each exit non-zero with no RESULT line
                (harness halts otherwise). Positive: all seven at depth 1.0
                (Off) must cost measurably more than bypass (mask 0) in every
                repeat, with an all-seven/bypass ratio of the same order as the
                laptop's.
Impossible if:  ns/frame <= 0 or non-finite (driver halts); split state not the
                label's (driver halts); 120 Hz cheaper than Off in all 10
                repeats of a case; all seven <= bypass.
Pilot:          one run of every config at 2 s audio, every field read, before
                the full run. Required (new harness).
Prediction:     +0.3 to +1.1 dsp points per case, i.e. +62 to +229 ns/frame
                (1 dsp point at 96 x 2 / 48 kHz = 1% of the 20,833 ns frame
                budget = 208 ns/frame), 0.1 to 0.5 of a voice (Crystals
                771 ns/frame per voice, Cloud Horn 437).
Falsifier:      a median delta above 229 ns/frame in any case (the split costs
                more than predicted: worth optimising before merge), or below
                62 ns/frame (cheaper than predicted: negligible).
Cheaper check:  the laptop --bench (+17 to +58 ns with all seven, +13 to +21
                bypassed, noisy) is free but on a different CPU; it cannot
                give the Pi's absolute ns.
Shortest form:  one Off/120 pair per case.
Why not that:   the laptop delta was noisier than itself (+17 to +58), and the
                Pi is live with the audio graph on cores 2-3, so one pair cannot
                separate the split from scheduling noise. 10 paired repeats at
                20 s of audio each fit in about 2 minutes of CPU1.
```

### Why not the stock `fx-selftest --bench`

It measures the same engine with the same Off/120 Hz switch, but it runs every effect and engine section in one process (over 2 minutes on the laptop, more on the Pi), at 256-frame periods, and Off, 120 Hz and gliding run back to back in that order with no repeat. Ten repeats would not fit the 10-minute window, and a fixed order confounds the split with drift. The scratch driver keeps the bench's method (noise input, `fx_engine_set_bass` the only difference) and changes three things: one config per process, the live 96-frame period, and the state assertions above. It is built without `-DFX_SELFTEST`, as the client is, so the selftest's mutant branches inside `bass_frame` are not in the timed path. The selftest itself was built on the Pi with the Makefile flags to confirm the PR source compiles there.

### The two configs, side by side

| quantity | Off | 120 Hz |
|---|---|---|
| binary, input noise, seed | same | same |
| mask, depth | per case | same |
| period, warm-up, timed frames | 96, 3 s, 20 s | same |
| `fx_engine_set_bass` | 0 | 120 |
| CPU, priority | CPU1, nice 19, SCHED_OTHER | same |

Only the crossover differs. In case (c) the 120 Hz side runs the split parked at 10 Hz, which is the path that keeps the filters warm while an effect is ticked at depth 0.
