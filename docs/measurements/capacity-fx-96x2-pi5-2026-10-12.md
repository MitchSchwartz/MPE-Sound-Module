# Voice ceiling with the FX host, 96 x 2, Pi 5 (2026-10-12)

*Issue #154. Pi 5 on `dev` @ `1f1b5e5` (#157 meter fix, #159 looper crash-loop fix, FX host with five effects). Kernel 6.18.39+rpt-rpi-2712.*

## Answer

| patch | old ceiling | (a) FX bypassed | (b) all five, depth 1.0 | FX cost |
|---|---|---|---|---|
| Cloud Horn | 5 (Pi 4, 1024 x 3, V9); 5 (Pi 5 ear tune, 128 x 2) | 18 rare (1 event in 40 s), 19 and 20 overrun | 18 rare (2 events in 60 s), 19 overruns (12 events in 50 s) | not resolvable: both conditions share the same boundary |
| Crystals | 3 (Pi 4, V9); 6 (Pi 5 ear tune) | **11** clean for 50 s; 12 overruns | **10** clean for 30 s; 11 rare (2 events in 50 s) | about 1 note |
| Closed Hat | 13 (Pi 4 confirm failed at 13) | no ceiling at or below 48 | no ceiling at or below 48 | none measurable |

"Rare" means isolated graph overruns at about one per 20 to 40 s; "overruns" means at least one per 10 s. The boundary is a gradient over one or two notes, not a step. The counts are screening-grade (10 s ramp windows, 20 s confirms), well short of V9's 60 s.

**Closed Hat is not a useful load in this protocol.** Its voices end while the key is still held (peak 0.000, Surge CPU flat at 7 to 8% from 4 to 48 notes), so the "no ceiling" says nothing about capacity.

**FX all-on is cheap.** `mpe-fx-host` rose from 2.4 to 2.8% of a core (more in (b) than (a) in 19 of 19 equal-N pairs). At equal N, `dsp_percent` was +0.35 points higher in (b) (median of 12 clean pairs, range −0.13 to +0.61). One Crystals note costs about 3.7 points and one Cloud Horn note about 2.1, so the load cost is 0.1 to 0.2 notes. The 1-note Crystals difference sits inside the boundary noise. Most of the chain's cost is already paid in (a), because the drive filters run while bypassed (`fx_dsp.c`).

## The constants

| constant | verdict | evidence |
|---|---|---|
| `JACK_BASELINE=40` | holds as an idle bound | idle before every window: 7.0 to 13.2, FX on or off |
| `LIMIT_HARD=50` (floor at raw 90) | **too loose: the falsifier fired** | overruns begin at `dsp_percent` median 51 to 55 (Crystals 11 to 12, Cloud Horn 18 to 20), with `dsp_max` as low as 53.5 in an overrunning window. At raw 55 the curve still allows 38 voices against measured ceilings of 10 to 18 |
| `REST_CAP=48` | far above capacity | the highest sustained ceiling measured is 18 |
| `FLOOR=4` | fine | 4 notes reads 21 (Cloud Horn) and 26 (Crystals) |

The meter reads about 50% when the graph starts to miss deadlines because `fCPULoad` is a smoothed figure and the overruns are single-cycle tails. #154 measured Surge at 220 µs p50 and 594 µs max at idle: the tail is three times the median. So a load curve tuned to reach the floor at 90 cannot act in time; the useful range runs from idle (about 12) to about 55.

**Constants alone will not fix the governor**, for two reasons this run measured directly:

1. **Note-on spikes trip the xrun reflex at low load.** Every Cloud Horn and Crystals window had xruns in its 2 s note-on lead-in, even at 4 notes (excluded from the window counts, as in V9; the "attack" column below). In the Cloud Horn governor cell, notes added one per second caused overruns on single notes at raw 15 to 26, timed to the note steps (11:14:38.37, 39.38, 45.38, 46.38, 54.38). Each one sent the limit `48 -> 3 reason=emergency`, and the cell spent its 35 s at 3 to 6 voices.
2. **Lowering the limit does not shed held voices.** In the Crystals governor cell the curve stepped 48 → 26 (`reason=high`, raw 50 to 70) without ever reaching the 16 sounding notes. Then the emergency dropped it 26 → 3 at raw 92, and the load stayed at 70 with 13 to 17 overrun events after the drop. This is the allocation-denial mechanism in `docs/POLY-GOVERNOR.md`, confirmed with the corrected meter.

**Recommendation.** Leave `BASELINE` at 40 for now. Do not ship new numbers from this run. The evidence says the floor belongs near raw 55, not 90, and the rest cap near 18, not 48. One candidate is `BASELINE 20 / HARD 35 / REST_CAP 18`: the curve gives 48 voices at raw 20, 21 at raw 40, 6 at raw 50 and 4 at raw 55 (cap 48), or 18 / 10 / 5 / 4 with cap 18. That candidate needs its own pre-registered governor cell. The attack-spike reflex needs a design decision first (ignore xruns within ~50 ms of a note-on burst, or cull on step-down), because no curve can see a single-cycle spike. That decision is Mitch's.

## Per-window results

xruns (meter) is the `meter.state` delta: jackd notifies once per client that missed, so one event usually counts 2 to 4. jackd events are distinct `JackEngine::XRun` timestamps in jackd's journal. Attack xruns are meter counts during the 2 s lead-in, not in the window. CPU % is utime+stime ticks of one core. `ALSA: xrun` lines: 0 for the whole session; every event was a graph overrun.

| window | cond | N | s | xruns (meter) | jackd events | attack | idle before | dsp med | dsp max | peak max | fx-host % | Surge % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Cloud Horn cal | a | 4 | 5 | 0 | 0 | 12 | 11.1 | 21.0 | 21.1 | 0.033 | 2.4 | 15.2 |
| Cloud Horn cal | b | 4 | 5 | 0 | 0 | 8 | 13.0 | 21.3 | 24.0 | 0.027 | 2.8 | 15.6 |
| Cloud Horn ramp | a | 20 | 10 | 1 | 1 | 28 | 11.5 | 54.1 | 74.0 | 0.074 | 2.5 | 48.1 |
| Cloud Horn ramp | b | 20 | 10 | 6 | 3 | 24 | 11.7 | 53.1 | 57.1 | 0.060 | 2.9 | 48.0 |
| Cloud Horn fill | a | 19 | 10 | 1 | 1 | 20 | 11.3 | 50.8 | 63.3 | 0.079 | 2.6 | 46.0 |
| Cloud Horn fill | b | 19 | 10 | 0 | 0 | 21 | 11.7 | 51.1 | 54.2 | 0.051 | 2.9 | 46.0 |
| Cloud Horn confirm | b | 19 | 20 | 14 | 7 | 20 | 11.7 | 51.0 | 76.1 | 0.044 | 3.0 | 46.0 |
| Cloud Horn confirm | b | 19 | 20 | 10 | 5 | 20 | 12.2 | 51.1 | 76.9 | 0.065 | 2.9 | 46.0 |
| Cloud Horn confirm | b | 18 | 20 | 0 | 0 | 24 | 13.2 | 49.0 | 51.8 | 0.064 | 2.9 | 43.8 |
| Cloud Horn supp | a | 18 | 20 | 1 | 1 | 12 | 11.4 | 49.2 | 67.1 | 0.080 | 2.6 | 44.0 |
| Cloud Horn supp | b | 18 | 20 | 2 | 2 | 21 | 11.6 | 49.1 | 62.2 | 0.062 | 2.9 | 44.1 |
| Cloud Horn supp | b | 18 | 20 | 0 | 0 | 20 | 11.8 | 49.6 | 51.7 | 0.059 | 2.9 | 44.0 |
| Cloud Horn supp | a | 18 | 20 | 0 | 0 | 20 | 11.4 | 48.5 | 53.3 | 0.072 | 2.6 | 43.8 |
| Cloud Horn governor | a | ramp to 23 | 35 | 15 | 5 | n/a | 11.3 | 21.1 | 27.2 | 0.031 | 2.4 | 15.4 |
| Cloud Horn governor | b | ramp to 23 | 35 | 12 | 3 | n/a | 11.8 | 20.0 | 25.9 | 0.028 | 2.8 | 15.7 |
| Crystals cal | a | 4 | 5 | 0 | 0 | 4 | 11.0 | 26.5 | 26.7 | 0.018 | 2.4 | 21.0 |
| Crystals cal | b | 4 | 5 | 0 | 0 | 4 | 11.5 | 26.7 | 27.0 | 0.014 | 2.6 | 21.2 |
| Crystals ramp | a | 10 | 10 | 0 | 0 | 4 | 11.5 | 48.2 | 48.4 | 0.026 | 2.5 | 43.0 |
| Crystals ramp | b | 10 | 10 | 0 | 0 | 8 | 11.7 | 48.5 | 51.0 | 0.027 | 2.8 | 43.1 |
| Crystals ramp | b | 12 | 10 | 2 | 2 | 8 | 11.7 | 55.7 | 58.6 | 0.032 | 2.9 | 50.3 |
| Crystals ramp | a | 12 | 10 | 3 | 1 | 4 | 11.4 | 55.4 | 59.1 | 0.027 | 2.5 | 50.5 |
| Crystals fill | a | 11 | 10 | 0 | 0 | 8 | 11.1 | 51.8 | 54.3 | 0.036 | 2.5 | 46.8 |
| Crystals fill | b | 11 | 10 | 0 | 0 | 8 | 11.6 | 52.2 | 58.1 | 0.013 | 2.8 | 46.7 |
| Crystals confirm | a | 11 | 20 | 0 | 0 | 8 | 11.8 | 51.9 | 57.2 | 0.036 | 2.5 | 46.8 |
| Crystals confirm | b | 11 | 20 | 3 | 1 | 8 | 11.8 | 51.9 | 53.5 | 0.029 | 2.8 | 46.6 |
| Crystals confirm | b | 11 | 20 | 2 | 1 | 8 | 11.4 | 51.9 | 56.4 | 0.022 | 2.9 | 46.6 |
| Crystals confirm | a | 11 | 20 | 0 | 0 | 4 | 11.4 | 51.6 | 57.8 | 0.035 | 2.5 | 46.5 |
| Crystals confirm | b | 10 | 20 | 0 | 0 | 8 | 11.8 | 48.4 | 49.0 | 0.026 | 2.8 | 43.0 |
| Crystals governor | a | ramp to 16 | 28 | 37 | 17 | n/a | 11.6 | 61.2 | 89.6 | 0.037 | 2.5 | 47.4 |
| Crystals governor | b | ramp to 16 | 28 | 24 | 13 | n/a | 11.5 | 61.6 | 91.8 | 0.038 | 2.9 | 47.4 |
| Closed Hat, 12 windows N = 4 to 48 | a, b | | | 0 | 0 | 0 | 7.0 to 7.7 | 9.4 to 11.1 | ≤ 15.4 | 0.000 | 2.2 to 2.8 | 4.0 to 8.0 |
| Closed Hat governor | a, b | ramp to 52 | 64 | 0 | 0 | n/a | 7.3, 7.5 | 10.1, 10.5 | 13.7, 12.4 | 0.000 | 2.4, 2.7 | 6.3, 6.4 |

## Governor journal during the governor cells

Live constants (startup line: `hard=50.0 jack_base=40.0 rest_cap=48 floor=4 emergency_poly=3`). The poly-state file was set to ceiling 64 / effective 48 for each cell and restored afterwards.

- **Cloud Horn (a):** `48 -> 3 reason=emergency cpu=15.6`, then `4 -> 3` at 17.8 and 21.1, `5 -> 3` at 20.9. No `reason=high` step: load never got above 27. Every emergency follows a single-note overrun by under 80 ms.
- **Cloud Horn (b):** `48 -> 3` at 15.2, `4 -> 3` at 16.8, `6 -> 3` at 25.7. Same pattern.
- **Crystals (a):** 22 `reason=high` steps, 48 → 26, from raw 50.2 to 70.0 over 6.1 s, then `26 -> 3 reason=emergency cpu=92.4` at 11:20:28.07, 20 ms after the first overrun. Load stayed near 70 for the remaining 11 s; the window logged 17 overrun events.
- **Crystals (b):** the same, `reason=high` from raw 49.3; first overrun at 11:21:04.28.
- **Closed Hat:** no transitions.

## Pre-registration (written before the pilot, unedited)

**Question:** at the live config (96 x 2, 48 kHz, FX host on the trunk, looper and meter on the graph), what is the held-note ceiling of Crystals, Cloud Horn and Closed Hat with (a) FX bypassed (`chain 0 0`) and (b) all five on at full depth (`chain 31 1.0`)? Do `JACK_BASELINE=40`, `LIMIT_HARD=50`, `FLOOR=4` hold?

**Claim class:** threshold per patch, n = 1 stream (same jackd PID throughout, asserted). Screening windows 10 s, confirms 20 s x 2 per condition, interleaved a b b a.

**One variable:** only the FX datagram differs between (a) and (b). Patch, N, velocity (20), MPE modulation, Surge amp, poly limit (64), governor (stopped), jackd PID, looper and meter are held. The order alternates per N, and every window re-sends its datagram once a second.

**Conformance:** the meter reader must refuse a stale `updated`, `dsp_percent=invalid` and `dsp_percent=2690` (negative controls; all three PASS in every run). Poly limit and Surge amp are read back over OSC after every patch load (positive controls; PASS). Any blind read, a meter restart, a jackd PID change, a peak above 0.5, or notes on the remapper's voice tracker halts the run and restores. FX positive control: fx-host CPU (b) > (a) at equal N.

**Predictions and outcome:**

| # | prediction | outcome |
|---|---|---|
| 1 | Cloud Horn 15 ± 5, Crystals 8 ± 4, Closed Hat > 25 | Cloud Horn 18 (in range), Crystals 10 to 11 (in range), Closed Hat uninformative |
| 2 | FX cost 0 to 2 notes; `dsp_percent` +2 to +6 at equal N | 0 to 1 note held; **load half missed**: +0.35 points |
| 3 | idle with all five on stays below 25 | held: max 13.2 |
| 4 | last clean `dsp_max` 60 to 90; first overrun reaches ≥ 90 | **falsified**: overruns at `dsp_max` 53.5 to 76.9 |
| 5 | governor cell shows `reason=high` before the first xrun | Crystals held; **Cloud Horn falsified** (emergency at raw 15.6, no `high` step) |

Falsifiers fired: "overruns in windows whose `dsp_max` < 60" (Crystals confirm b11 at 53.5, b12 at 58.6, a12 at 59.1) and "governor cell xruns before any `reason=high`" (Cloud Horn).

## Method

- One scratch harness ran on the Pi as a single process per run (pilot, full matrix, Cloud Horn supplemental) and was deleted afterwards. No repo file on the Pi was changed. No laptop query reached the Pi while a window was open.
- Patches loaded with a direct `/patch/load`, then the Surge scene amp was set to −41 dB (UI had −23.1 dB) and `polyphony_limit` to 64, both read back. Velocity 20, channel pressure 8 to 40, pitch bend ±3000, notes on MIDI channels 2 to 16 round robin. The repo's `midi-load-hold.py` uses velocity 100 and maps a 16th note onto the manager channel, so it was not used. `measure-capacity-ramp.sh` was not used either: it restarts jackd and Surge per probe, edits `/etc/mpe/mpe.env`, and its patch loader resets Surge's −6 dB device-mode trim to 0 dB.
- Per window: notes on, 2 s lead-in, then per-second reads of `meter.state` with no forks, harness pinned to CPU 1. The ceiling start point came from a 4-note calibration pair (per-note load).
- **Departures from V9:** live config, no per-probe jackd restart; quiet velocity and pressure; 20 s confirms in place of 60 s.
- Peak level never exceeded 0.080 (meter, post-FX); the stop rule was 0.5.

## Restore, verified

Pre- and post-run snapshots differ only in the expected fields. Same unit states (17 units), same jackd PID, Qealchee loaded, Surge amp back at −23.11 dB, poly-state file identical, FX route mask 2 at depth 0.2126 (new stamps from the UI reload), `/etc/mpe/mpe.env` byte-identical (`cmp` and sha256), DAC level unchanged. The governor restarted at the on-load limit 13 and was climbing +1 per 5 s toward 48. `touch-patch-browser` was restarted three times (once per run) to reload the patch through the UI's own path.

## Not verified

- Cloud Horn at 17 notes (not run), so its clean ceiling is "17 or lower, 18 rare".
- The ceilings are screening-grade. With bursty xruns (Fano about 4 in X1), 20 to 60 s per cell cannot certify a clean rate.
- The attack-xrun mechanism (voice initialisation cost at note-on is the likely cause) was not isolated.
- Old ceilings came from a different board, buffer, velocity and governor state, so the comparison is context, not a like-for-like delta.
