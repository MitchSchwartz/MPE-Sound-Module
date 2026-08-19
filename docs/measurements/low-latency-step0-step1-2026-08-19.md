# Low-latency work order — Step 0 harness + Step 1 escalation check

**Pi:** `raspberrypi2` · **commit:** `5d43991` (verified on Pi before run) · **2026-08-19**

## Step 0 — harness

Scripts landed on `dev`:

- `scripts/measure-latency-run.sh` — append-only runs with provenance, verified JACK
  period, 60-sample assertion, DSP median/p99, meter xrun count, journal client lines
- `scripts/measure-cyclictest-floor.sh` — wraps spec `cyclictest` command

Self-test (`--self-test`, 10 s, condition A, 512×3): **PASS** — DSP median ~31%,
sample count 10, strict jackd (no `-s`).

**cyclictest:** not installed on Pi (`rt-tests` package missing). Floor not recorded yet —
needs `sudo apt install rt-tests` before Step 2 kernel changes.

**Known gap:** JACK2 on this kernel logs `JackEngine::XRun: client = …` without the
`xrun of at least N msecs` delay line the spec cites. Harness records the client line with
`delay_usec=-1` after case-fix (`5d43991+`). Sub-microsecond delay may require a small
JACK xrun callback probe later.

## Step 1 — is escalation real?

**Protocol:** 512×3, condition **D** (full stack), strict jackd, `midi-load.py` load,
5×60 s back-to-back, temp + throttle each run.

| run | xruns / 60 s | DSP median | DSP p99 | temp | throttled |
|---:|---:|---:|---:|---|---|
| 1 | 13 | 39.64% | 45.53% | 55.0°C | 0x0 |
| 2 | 5 | 39.28% | 43.08% | 55.5°C | 0x0 |
| 3 | 15 | 39.22% | 67.89% | 55.5°C | 0x0 |
| 4 | 16 | 39.17% | 42.94% | 56.0°C | 0x0 |
| 5 | 26 | 39.52% | 43.18% | 55.5°C | 0x0 |

DSP flat ~39% (matches D15: jitter not load). Temperature flat; no throttling.

**Verdict:** not the monotonic **7 → 24 → 29** climb from D15. Run 2 dropped to 5; run 5
spiked to 26 but without a rising temp or run-to-run trend. **Treat escalation as
unproved — proceed to Step 2** (IRQ affinity), not blocked on accumulation hunt.

Pi log: `~/latency-step1-512-D.log`. Buffer restored to **1024×3** after run.

## Next (needs Mitch at reboot)

- Install `rt-tests`, record cyclictest floor
- Step 2: `irqaffinity=0,1` in `/boot/firmware/cmdline.txt` + pin `xhci_hcd` — reboot gate
