# T13 — the runway model is refuted (2026-08-21)

`~/t13-condA.log`. Condition A, n=15 per cell, 60 s runs. Trap-5 assert passed on every
cell (`jackd period=N ok`, `jackd periods=N ok`), so both the period **and** the period
count were really applied.

## The test

T11 measured a buffer ladder at fixed `n=3`, so **period size and total buffer moved
together** — the design could not tell which one bound. T13 separates them:

| config | period | periods | total buffer | USB frames |
|---|---:|---:|---:|---:|
| 128 x 6 | 128 | 6 | 768 frames = 16 ms | 16 |
| 256 x 3 | 256 | 3 | 768 frames = 16 ms | 16 |

**Identical runway. Different period.** The runway model predicted they would match.

## Result

| config | mean xruns/60 s | clean /15 |
|---|---:|---:|
| 128 x 6 | **713.40** | 0/15 |
| 256 x 3 | **1.53** | 6/15 |

**466x apart at identical total buffer. The runway model is refuted.**

Total buffer is not the binding term. **Period size binds independently.** Whatever is
failing scales with *how often the transport must be serviced*, not with how much
already-rendered audio is queued ahead of it.

### What this retracts

`docs/measurements/t11-condA-ladder-2026-08-21.md` proposed that the cliff was the URB
queue running out of frames — 32 -> 16 -> 8 -> 4 — and that runway was the mechanism. That
was a plausible reading of a confounded ladder. **It is wrong, or at best incomplete.**
The evidence that the drain is *below JACK* still stands (callbacks never missed their
deadline; 6% of periods underran anyway). What is refuted is the specific claim about
which quantity in the transport binds.

### What it changes downstream

- **`nperiods` will not buy low latency.** T7b and the cushion half of T7a are answered:
  more periods at a smaller size is strictly worse than fewer at a larger size, at equal
  latency. Badly worse.
- **`snd_usb_audio.lowlatency=N` drops down the list.** It acts on URB queue depth — the
  runway. Demote it below the IRQ work in `Documents/specs/usb-runway-levers.md`.
- **T12 (USB frame alignment) becomes more interesting, not less.** If servicing *rate* is
  what binds, then how each period lands against the 1 ms frame grid is a live mechanism,
  and 192 x 3 (exactly 4 frames) against 256 x 3 (5.33 frames) is the right question.

## Second finding: 256 x 3 does not reproduce

| run | config | mean xruns/60 s | clean /15 |
|---|---|---:|---:|
| T11 (10:08) | 256 x 3, cond A | **12.10** | 0/15 |
| T13 (12:0x) | 256 x 3, cond A | **1.53** | 6/15 |

**Same configuration, same condition, n=15 each, ~8x apart**, with nothing intentionally
changed between them.

This is the measurement-integrity shape that has bitten this project repeatedly: a number
that looks authoritative and is not stable. **No absolute figure from 2026-08-21 should be
treated as settled until this is explained.** Relative comparisons *within* a single
harness invocation are still usable — 128 x 6 vs 256 x 3 were adjacent cells in the same
run, which is why the refutation above survives.

Leading suspect is **order or hysteresis**: both runs used `--no-restore-buffer` chains and
256 x 3 sat in a different position in each (first in T11, second in T13). That is a
hypothesis, not a finding.

**Do not paper over this.** The re-baseline below is the test.

## Next

1. Let `64 x 8` finish — a third point on the same question. 512 frames total (10.7 ms of
   runway) at a 64-frame period. If period binds, it should be bad **despite** the runway.
2. **IRQ consolidation** — move every movable interrupt off CPU0, leaving xhci and the
   timer. Independent of everything above and the largest single change available.
3. **Re-baseline 512 x 3 and 256 x 3, n=15**, post-cleanup. Establishes the new baseline
   *and* settles whether 256 x 3 is 12.1 or 1.53.

Re-baseline runs should be **separate harness invocations, not a chain**, so order cannot
be the explanation for whatever they show.
