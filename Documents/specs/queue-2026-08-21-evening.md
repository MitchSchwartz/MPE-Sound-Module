# Queue as of 2026-08-21 evening

## Where things stand

**The shipping claim is withdrawn and there is currently no quotable number.** That is the
most important fact in this file.

Two findings changed the ground under everything:

1. **Stream-start variance** (`stream-start-variance-2026-08-21.md`). The xrun rate is set
   when the stream opens and holds for its life. `n=15` inside one stream is 15 correlated
   observations of **one draw**, not 15 samples. Every tight error bar in this project
   measured the wrong axis.
2. **256 x 3 is bimodal across streams** — six streams at 2-5/min, two at 18-22/min. The
   commercial problem is **two products at power-on**, not a noisy mean. A user gets one
   stream per power-on and cannot re-roll it.

And a correction: **T12 did not refute alignment** — the 192-vs-256 comparison also changed
period size by 25%, so it could not separate the two. See
`t12-alignment-confounded-2026-08-21.md`.

**Working model:** period size sets the **rate**; frame alignment sets the **between-stream
variance**.

## The alignment grid

At 48 kHz a full-speed USB frame carries 48 samples, so aligned periods are multiples of 48.
**No power of two aligns.** Every measurement in this project has been on the misaligned
grid:

| period | ms | USB frames | aligned | total @ n3 |
|---:|---:|---:|---|---:|
| 240 | 5.00 | 5.00 | **yes** | 15 ms |
| 256 | 5.33 | 5.33 | no | 16 ms |
| 384 | 8.00 | 8.00 | **yes** | 24 ms |
| 512 | 10.67 | 10.67 | no | 32 ms |
| **1008** | **21.00** | **21.00** | **yes** | **63 ms** |
| 1024 | 21.33 | 21.33 | no | 64 ms |

**1008 x 3 is a drop-in aligned replacement for 1024 x 3 — 63 ms against 64 ms, a 1.6%
difference.** If alignment removes bad draws, that is a free win on the shipping
configuration with no latency cost and no hardware change.

## Queue

| # | task | Pi time | why |
|---|---|---|---|
| **1** | **T14 — 1024x3, condition D, 8 loops, 10 streams x 3 runs** | ~1 h | Restores or replaces the shipping claim. **1024 is misaligned (21.33 frames) and has only ever been measured as single streams.** If it is bimodal like 256, there is a bad draw nobody has seen — and it would present as a bad gig. |
| **2** | **T12b — 240x3 vs 256x3, 10 streams each** | ~1 h | Isolates alignment. 15 ms vs 16 ms — **6.25% apart**, against the 33% gap that confounded T12. Either answer closes the question. |
| 3 | **T15 — 1008x3 vs 1024x3, condition D, 8 loops, 10 streams each** | ~1 h | Only if T12b confirms alignment. The drop-in swap above. |
| 4 | **T16 — 384x3, condition A, 10 streams** | ~30 min | Only if T12b confirms. 24 ms against 512x3's 32 ms — a 25% latency cut with better determinism, on hardware already owned. |
| 5 | Scarlett baseline — ladder at defaults, **10 streams per cell** | ~1 h | Hardware gate. MSD mode off, Sound Blaster unplugged (Tier 1 outranks it), confirm 480M enumeration first. |
| — | Ather Audio conversation | none | No Pi time. Ask: does Ather Core run on ARM at all (the site never says), and does PTP work on the Pi 4's NIC given its weak hardware timestamping. Those two answers decide whether it is a path or a dead end. |
| 6 | Phase 3 levers — `threadirqs`, `lowlatency=N`, `isolcpus` | — | Only against a protocol that can detect their effect. |

**T14 goes first** because "we have no shippable number" is the worst state to be in, and
because 1024's misalignment means the bad mode may exist and be unmeasured.

## Protocol — applies to every task above

**Sample stream starts, not minutes.** `N streams x k runs`, restarting jackd between
streams. `measure-latency-run.sh --restart-between` exists for this.

**Report both variances, always:**
- **within-stream sd** — what the old protocol measured
- **between-stream sd and the shape** — unimodal or bimodal, and the range

**A mean alone is not a result any more.** A configuration whose mean is acceptable but
whose bad mode reaches 20/min is not shippable. Report the worst stream, not just the
average one.
