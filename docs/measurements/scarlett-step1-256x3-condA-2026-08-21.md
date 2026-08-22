# Scarlett Step 1 — 256×3 condition A stream sample (2026-08-21)

*Last updated: 2026-08-21 (America/Toronto)*

**Config:** Scarlett 4i4 USB card 6 @ 480M (ASYNC + feedback 0x81). Sound Blaster
unplugged. Hub topology per [`scarlett-step0-gate-2026-08-21.md`](scarlett-step0-gate-2026-08-21.md).
**Protocol:** 10 jackd restarts × 2×60 s windows (`measure-stream-sample.sh`). All windows
`meter_live=1`, `throttled=0x0`.

**Pi logs:** `~/scarlett-256-A-streams-stream-{01..10}.log`, `~/scarlett-256-A-streams-index.log`

## Per-stream xruns/min (mean of two 60 s windows)

| stream | run1 | run2 | stream mean (/min) |
|---:|---:|---:|---:|
| 01 | 61 | 41 | **51.0** |
| 02 | 38 | 42 | **40.0** |
| 03 | 68 | 76 | **72.0** |
| 04 | 76 | 74 | **75.0** |
| 05 | 69 | 89 | **79.0** |
| 06 | 62 | 69 | **65.5** |
| 07 | 82 | 89 | **85.5** |
| 08 | 84 | 75 | **79.5** |
| 09 | 69 | 79 | **74.0** |
| 10 | 76 | 75 | **75.5** |

**Between-stream shape:** **unimodal** — every stream 40–86/min. No cluster at 2–5/min, no
cluster at 18–22/min.

| stat | value |
|---|---:|
| mean (10 streams) | **69.7/min** |
| range | **40.0 – 85.5/min** |
| worst stream | **07** (85.5/min) |
| best stream | **02** (40.0/min) |

Within-stream: pairs differ by up to ~20/min (e.g. stream 01: 61 vs 41) but stay in the
same band — no within-stream lottery like the old bad/good mode flip.

## Mechanism verdict (Step 1 question)

**Bimodality vanished on the Scarlett.**

Sound Blaster post-hygiene at 256×3 cond A was **bimodal** (six streams ~2–5/min, two
streams ~18–22/min, mean 7.1). Scarlett ASYNC + feedback endpoint shows **one mode only**,
all high.

→ **The start-phase lottery on the Sound Blaster was the ADAPTIVE clock-lock draw**, not a
universal frame-phase effect. Async clocking removed the two-product power-on problem.

→ **Frame-phase misalignment (256 = 42.67 HS microframes) did NOT produce bimodality here.**
That hypothesis is **not supported** for between-stream variance on this device.

Do not soften: this is a **null on frame-phase lottery**, not a win on rate.

## Rate verdict (plain)

**The Scarlett did not rescue 256×3.** Mean ~70/min vs Sound Blaster stream-sample mean
~7/min *(indicative — different device, different session)*. Matches Mitch's ear: 256 still
crackles; high-speed microframes did not move the cliff.

DSP under harness load: median ~37–44%, p99 ~70–93% (75-voice midi-load) — headroom exists
under *synthetic* load; ear crackle on heavy patches is still an open axis (Step 3).

## Shipping implication (interim)

Scarlett **does not** change what ships at 256. It **does** remove the bad-draw lottery at
power-on. The binding problem at 256 is **Pi-side rate**, not dongle clock mode — redirects
work to cyclictest-under-load (Step 2) and heavy-patch DSP (Step 3), not aligned-period
tables as the first lever.

Steps 4–5 (512/1024 ladder on Scarlett) remain optional confirmation; ear test already
suggests 1024 good / 512 heavy-patch limited.
