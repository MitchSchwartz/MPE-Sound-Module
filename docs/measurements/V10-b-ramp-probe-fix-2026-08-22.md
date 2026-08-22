# V10-b — ramp probe fix

*2026-08-22 (America/Toronto)* · branch `docs/v8-patch-capacity`

## Problem

Closed Hat @ 15 voices × 8 s: `measure-capacity-ramp.sh` reported **xruns_delta=0**;
`measure-confirm-at-voices.sh` (latency-run path) reported **275** — same patch, same
`midi-load-hold.py`, same window length.

## Eliminated (Mitch + offline check)

| candidate | verdict |
|---|---|
| Different softmode | **Dead** — both set `MPE_JACK_SOFTMODE=0` (`measure-capacity-ramp.sh` `_enable_strict`, `measure-latency-run.sh` `_enable_strict_xrun_reporting`) |
| Different meter source | **Dead** — both call `mpe_meter_xruns_read` → `mpe_meter_assert_live` |
| Duration sensitivity (60 s vs 8 s) | **Dead for this split** — V9-c diagnostic: **275 xruns @ 8 s** on confirm harness |

## Root cause (confirmed)

Two defects in legacy `_xruns_delta`:

1. **Blind fallbacks** — `mpe_meter_xruns_read || start=0` / `|| end=0` converted meter
   blindness into **0 − 0 = clean**. Same failure shape as V8-b auto-pick and peak-meter
   shutdown bugs.

2. **Two-sample delta** — one read before load, one after `sleep`, no 2 s load lead-in, no
   per-second liveness. `measure-latency-run` samples every second for the full window after
   lead-in; the ramp did not.

## Fix

`_xruns_delta` now:

- Requires `MPE_PEAK_METER=1` and live `meter.state`
- Starts `midi-load-hold`, **2 s lead-in**, then `start_xr`
- **Per-second** `mpe_meter_xruns_read` for `PROBE_SEC` (abort on blind or meter restart)
- Aborts entire ramp on probe failure — **no `sustained_clean` emitted**

Log header: `probe=v10b`.

## Validation

```bash
sudo ./scripts/measure-v10b-validate.sh
```

**Pass criterion:** Closed Hat @ 15 × 8 s → **xruns_delta > 0** (V9-c confirm reference: 275 @ 8 s).

## Policy

- **×2 ship evidence** unchanged — V9-b/d used confirm harness only, never ramp.
- Ramp usable for **screening** after V10-b validation; **policy floors** still require
  `measure-confirm-at-voices.sh` @ 60 s.
