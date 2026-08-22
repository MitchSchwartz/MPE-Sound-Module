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

**~~Bimodality vanished on the Scarlett.~~ — WITHDRAWN post Step 4 (2026-08-21 late)**

Step 4 (same async Scarlett, n=3): stream 01 **105/min**, streams 02–03 **26–34/min**. The
Step 1 unimodal read is **not stable**. **n=3 cannot establish shape** — one-high/two-low
is unremarkable from bimodal or wide unimodal. **Do not re-read Step 4 as confirming
bimodality back.**

Original Step 1 observation (n=10): all streams 40–86/min, no 2–5/min cluster. That session
data stands; the **inference** (adaptive clock lock closed frame phase) is withdrawn.

→ **Frame-phase alignment:** **unsupported, still unpromising.** HS microframe = 6 samples;
1024/6 = 170.67 misaligned yet clean at 1024×3. No Pi time on alignment tables.

→ **Sound Blaster bimodal vs Scarlett unimodal:** inconclusive as a mechanism proof; device
swap still shows Scarlett ~10× worse mean at 256.

Do not soften: Step 1 was a **null on frame-phase lottery for that n=10 sample**, not a
permanent closure.

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
