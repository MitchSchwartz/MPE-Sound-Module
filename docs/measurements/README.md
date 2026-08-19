# Measurements

Bench results from experiments on the reference Pi. One file per run, named
`<experiment>-<YYYY-MM-DD>.md`.

**Why this exists:** a measurement without its conditions recorded is not
reproducible, and an unreproducible number becomes a claim that outlives what
it described. Record the conditions even when they seem obvious — especially
governor, buffer/periods, power source, and the exact software versions.

## Standard conditions

Unless a run says otherwise, use the fixed conditions Phase 1 was measured
under, so numbers stay comparable:

| Setting | Value |
|---|---|
| Profile | `standalone` |
| `MPE_JACK_BUFFER` | 256 |
| `MPE_JACK_PERIODS` | 3 |
| Sample rate / depth | 48 kHz, 24-bit |
| CPU governor | `performance` (pin it — `ondemand` makes latency numbers non-baseline) |
| Power | USB-C supply, **not** GPIO jumpers |

Check RT with **`mpe rt status`**, which reads the audio thread. Do **not** use
`mpe sysinfo` — it reads process-level scheduling, which is `SCHED_OTHER` by
design under JACK and reports a false negative on a healthy appliance.

## Current templates

- [`TEMPLATE-sooperlooper-eval.md`](TEMPLATE-sooperlooper-eval.md) — Session A/B
  of the SooperLooper adoption test. Plan lives in OM-Repo
  `internal/projects/mpe-synth-launch/research/looper-vetting.md` §7.

## Completed runs

- [`looper-p0-latency-calibration.md`](looper-p0-latency-calibration.md) — P0 input_latency ear procedure (do before seam tuning)
- [`seam-weld-spike-2026-08-18.md`](seam-weld-spike-2026-08-18.md) — Option B/E/Tier 2 archaeology; stop-then-weld is current (2026-08-19)
- [`sooperlooper-eval-2026-08-14.md`](sooperlooper-eval-2026-08-14.md) — Session A
  **continue**, Session B **inconclusive** (Pi bench, branch `docs/sooperlooper-eval`).
