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
