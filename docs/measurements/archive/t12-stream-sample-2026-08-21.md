# T12 stream sampling — USB frame alignment (2026-08-21)

**Protocol:** `measure-stream-sample.sh` · 10 jackd restarts × 3 windows each · condition A ·
post-hygiene Pi · **`meter_live=1`** on all counted windows (256: 30/30, 192: 32/32 — stream 01
had 5 runs from a pre-fix retry; analysis uses **last 3**).

Logs: `~/t12-streams-{256,192}-A-stream-NN.log` on raspberrypi2.

## Per-stream means (xruns / 60 s, mean of 3 windows)

### 256 × 3 — misaligned (5.33 USB frames)

| stream | per-run | stream mean |
|---:|---|---:|
| 01 | 4 2 2 | 2.67 |
| 02 | 2 2 4 | 2.67 |
| 03 | 4 4 6 | 4.67 |
| 04 | 6 0 0 | 2.00 |
| 05 | 0 2 10 | 4.00 |
| 06 | 12 32 11 | **18.33** |
| 07 | 8 2 6 | 5.33 |
| 08 | 7 26 33 | **22.00** |
| 09 | 4 4 4 | 4.00 |
| 10 | 6 4 5 | 5.00 |

### 192 × 3 — aligned (exactly 4 USB frames)

| stream | per-run | stream mean |
|---:|---|---:|
| 01 | 15 10 24 *(last 3 of 5)* | 16.33 |
| 02 | 10 20 17 | 15.67 |
| 03 | 15 8 18 | 13.67 |
| 04 | 14 6 12 | 10.67 |
| 05 | 12 15 12 | 13.00 |
| 06 | 6 19 21 | 15.33 |
| 07 | 22 17 12 | 17.00 |
| 08 | 13 15 14 | 14.00 |
| 09 | 15 5 12 | 10.67 |
| 10 | 10 17 13 | 13.33 |

## Between-stream summary

| config | stream-mean avg | min | max | spread | sd (stream means) |
|---|---:|---:|---:|---:|---:|
| **256 × 3** | **7.07** | 2.00 | 22.00 | **20.0** | **6.68** |
| **192 × 3** | **13.97** | 10.67 | 17.00 | 6.33 | **2.05** |

Within each stream, counts are tight (±0–2 typical except stream 06/08 at 256).

## Verdict

**USB frame alignment at 192×3 does not collapse xrun rate.** Aligned periods are **uniformly
worse** (~14/min every stream) vs misaligned 256×3 (**bimodal**: six streams at 2–5/min, two
catastrophic at 18–22/min).

The T12 hypothesis — misalignment causes stream-start lottery and alignment fixes it — is
**refuted** on this evidence. Alignment **narrows** between-stream spread (sd 2.0 vs 6.7) by
**raising the floor**, not by removing bad draws.

**Commercial read:** 256×3 is not a single product. It is two products: ~4/min streams and
~20/min streams, chosen at power-on with no user recourse. Mean 7/min understates the risk.

**Within-stream axis confirmed:** each stream’s 3 windows cluster (e.g. 256 stream 09:
4/4/4). **Between-stream axis confirmed:** 10 restarts → 10 different rates.

## Next (not run here)

- Phase 3 levers (threadirqs, etc.) only against this baseline — one variable at a time
- Investigate why streams 06/08 at 256 open hot (phase at stream start still live, but
  alignment is not the fix)
- Re-run 192 stream 01 after harness `rm -f` fix (duplicate-tag bug on retry)

*Last updated: 2026-08-21 (America/Toronto)*
