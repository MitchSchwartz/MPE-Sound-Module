# The crackle was the graph probe, not load

*Measured on `raspberrypi2`, 2026-08-18, at `512 x 3 @ 48000` (10.67 ms deadline).*

**Reported:** audible crackle while playing, after a deploy, at a buffer size that had
been fine before.

**Root cause:** `surge-watchdog` ran `jack_lsp` every 10 s to answer *"is Surge still on
the graph?"*. `jack_lsp` answers by **registering a JACK client**, which forces jackd to
rebuild its processing order — mid-audio, six times a minute. Introduced that same
morning by the probe-interval change in PR #71.

**Fix:** the probe now reads `wired=` from `/run/mpe/meter.state`, published at 5 Hz by
`mpe-peak-meter` — a compiled client already permanently on the graph. A file read: no
fork, no registration. `jack_lsp` remains as a fallback when the meter is absent, stale
(>5 s), or malformed.

## Result

| State | xruns/min | DSP median |
|---|---|---|
| Reported crackle, Mitch playing | **41** | 41.7 |
| `SDL_AUDIODRIVER=dummy` (pygame fix) | 35 | 44.2 |
| Looper stack stopped | 35 | 47.4 |
| `jack_lsp` probe throttled to 600 s | 6 | 41.0 |
| **Meter-based probe, after reboot** | **0** | 41.9 |

DSP median 41.9 against 41.7 for the original crackling run: same work, zero missed
deadlines.

## Why it was hard to see

**Load said nothing.** The graph missed 41 deadlines per minute with DSP never above
55% — nearly half the budget unused. Every instinct to add buffer or reduce voices was
aimed at the wrong variable.

**The xrun channel was dead.** `jackd -s` (softmode) suppresses xrun reporting, so
`journalctl` said nothing and every reading of `0` meant *not measured*. Two things
changed that:

1. `jack_set_xrun_callback` in the compiled meter (spec Q10) — xruns countable on the
   shipping configuration for the first time, published as `xruns=` in `meter.state`.
2. `scripts/midi-load.py` — a deterministic MPE performance, so DSP load is reproducible
   between conditions.

## Method note: the human was the confound

Four comparisons early in the session were invalid because playing intensity varied
between windows — including one that looked like a dramatic win and was simply a quiet
window. `surge-xt-cli` CPU and DSP median are the control; a run whose control does not
match is discarded, not interpreted.

`scripts/midi-load.py 75` at `VOICES=3` produces DSP median ~42%, matching Mitch playing.
Every comparison after it was introduced is trustworthy; none before it are.

## Two confident conclusions that were wrong

Both were corrected by measurement, and both are recorded because the reasoning that
produced them was plausible.

- **"pygame holding the onboard PCM is the crackle."** `pygame.init()` initialises the
  SDL mixer, so the touch UI, boot splash and calibration loader each held the Pi's
  onboard headphone jack open, streaming 44.1 kHz silence on a second clock domain. A
  real defect, fixed, and it violates DECISIONS.md 2026-08-14 — but 41 -> 35 xruns is
  noise, not a cure.
- **"the looper stack causes 94% of xruns."** Drawn from a run that stopped the looper
  *and both watchdogs* together, and credited the looper. Sorting every run by watchdog
  state instead: watchdog ON gave 24/31/35/35/40, watchdog OFF gave 2/6/9/10.

**The looper's true cost is therefore unknown.** Every measurement of it was taken while
the probe dominated. It must be re-measured before it counts against the SooperLooper
adopt/kill verdict (D15) — it may be cheap. The stack is currently opt-in
(`install-units.sh`), which was decided on those void numbers.

## Noise floor

Single 60 s runs vary ±30% (identical configuration measured 24 and 31). Differences
below ~2x are not interpretable without repeated measures. The probe effect is 17x.

## Reproducing

```sh
python3 scripts/midi-load.py 75 &   # deterministic load, DSP median ~42%
sleep 8                             # skip the start transient
scripts/xrun-corr.sh 60             # xruns + DSP, 1 Hz, side by side
```
