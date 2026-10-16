# Bass-bypass crossover CPU cost, Pi 5 (2026-10-16)

*PR #185 (`feat/bass-bypass` @ `08ee959`). Offline DSP bench on the Pi 5: nothing joined JACK, no sound, no service or level change.*

## Answer

**The crossover is cheaper than predicted: +32 to +58 ns per stereo frame, 0.15 to 0.28% of one core, under a tenth of a Crystals voice.** The "below 62 ns" falsifier fired in all three cases. 120 Hz cost more than Off in 10 of 10 pairs in every case.

| case | Off, ns/frame (median, range) | 120 Hz | delta, median (IQR; range) | % of core at 48 kHz | Crystals voices (771 ns) | Cloud Horn voices (437 ns) |
|---|---|---|---|---|---|---|
| (a) one effect (chorus, depth 1.0) | 507.5 (507.2 to 509.8) | 551.1 (533.1 to 554.7) | **+43.5** (+41.7 to +44.4; +23.4 to +47.3) | 0.21 | 0.06 | 0.10 |
| (b) all seven, depth 1.0 | 601.9 (575.2 to 609.8) | 659.2 (643.1 to 662.5) | **+58.2** (+54.8 to +64.4; +52.8 to +85.5) | 0.28 | 0.08 | 0.13 |
| (c) chorus ticked at depth 0 | 481.9 (460.1 to 484.5) | 514.5 (503.3 to 520.2) | **+32.0** (+30.5 to +38.1; +28.6 to +47.6) | 0.15 | 0.04 | 0.07 |
| bypass (mask 0), reference | 482.8 (458.3 to 484.2) | | | 2.3 (absolute) | | |

% of core = ns / 20,833 (one frame at 48 kHz). At 96 x 2, one `dsp_percent` point is the same 208 ns/frame, so the % column is also dsp points. Voices use the capacity doc's per-note costs (`capacity-fx-96x2-pi5-2026-10-12.md`: Crystals 3.7 points, Cloud Horn 2.1).

**Verdict against the prediction** (+0.3 to +1.1 points, 0.1 to 0.5 voice): below the band. Measured +0.15 to +0.28 points, 0.04 to 0.13 of a voice. The worst single pair (all seven, +85.5 ns, 0.41 points) still sits inside the predicted band. Case (c) is the path that keeps the filters warm at depth 0: a third of the cost of all seven, paid whenever an effect is ticked and the knob is down.

**The absolute numbers agree with the live client.** Bypass at 483 ns/frame is 2.3% of a core and all seven at 602 is 2.9%. The capacity run measured `mpe-fx-host` at 2.4% (bypassed) and 2.8% (all five on) on the live graph.

**Observer effect: none seen.** The jackd journal has 0 xrun lines (0 `ALSA: xrun`) in the 10 minutes before (22:47 to 22:57) and 0 during the build, pilot and run (22:57 to 22:58:45). The journal does record xruns, but the last one was at 14:34, so the base rate is zero. This control can show the bench caused no xruns; it cannot resolve a small change in rate.

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

## Conformance and pilot (filled in after the run)

| check | result |
|---|---|
| negative: impossible case (`bogus`) | rc 2, no RESULT: PASS (pilot and full run) |
| negative: impossible corner (5 Hz) | rc 2, no RESULT: PASS (both) |
| negative: run killed at 0.3 s | rc 124, no RESULT: PASS (both) |
| state assertions (split settled at 120 Hz / parked at 10 Hz / not run when Off) | held in all 78 runs; any failure would have halted with no number |
| positive: all seven (Off) > bypass | 10 of 10 repeats, by 93 to 146 ns (median 119). Ratio 1.25. The laptop gave 1.33 in a quiet run (285 / 215 ns) |
| impossible readings | none: every ns/frame > 0, and 120 Hz > Off in every pair |
| pilot (every config, 2 s audio) | every field present, numeric and plausible; output sinks bit-identical to the laptop build, so the Pi ran the same DSP |

The laptop was heavily contended during this session (other agents' builds). Its stock `--bench` returned negative crossover deltas (−267 ns with all seven) and "levels moving" cheaper than "settled", so it is not a usable reference tonight. The plausibility ratio above comes from an earlier quiet laptop run of the same driver.

## Run record (actual state)

- Source: `git archive 08ee959 native/mpe-fx-host`, shipped to a scratch directory on the Pi. The deployed checkout was not touched. `fx_dsp.c` sha256 `7d0af37f…`, `fx_dsp.h` `dd4d5955…`, matching the worktree.
- Built on the Pi: `fx-selftest` with the Makefile's `selftest-bin` flags (`-O2 -Wall -Wextra -std=c11 -Werror -DFX_SELFTEST`), which compiled clean; the driver with the same flags minus `-DFX_SELFTEST`, as the client is built.
- Pi 5 Model B Rev 1.1, kernel 6.18.39+rpt-rpi-2712, gcc 14.2.0. Governor `performance`, CPU1 to 3 at 2,400,000 kHz at start (cpu0 the same in the pre-check). `get_throttled` 0x0 before and after; 59.8 °C at pilot, 62.6 °C at end.
- Every run `taskset -c 1 nice -n 19` (SCHED_OTHER), one process per config. Live graph running throughout: jackd (PID unchanged), Surge, `mpe-fx-host`, looper and meter.
- Timing: pilot 22:57:44 to 22:57:46, full run 22:58:00 to 22:58:43 (local), 70 runs plus 3 negative controls (3 more in the pilot). Each repeat ran bypass, then (c), (a), (b), with Off/120 Hz order alternating per repeat.
- Afterwards the scratch directory was removed (verified absent), and jackd, `mpe-fx-host` and Surge were still active with the same jackd PID.

## Not verified

- The driver times the engine alone. The client's JACK callback adds port and copy overhead, which is the same with the split on or off and is not in these numbers.
- The gliding case (crossover moving every period, coefficients recomputed per chunk) was not measured on the Pi. It is a transient during a knob move, not a steady cost.
- n = 10 pairs in one session, on a live box. The spread came from scheduling on CPU1 (one outlier pair per case). The medians are stable to a few ns, and that is all this run claims.
