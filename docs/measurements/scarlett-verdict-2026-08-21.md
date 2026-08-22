# The Scarlett is not the answer — the bottleneck is the Pi (2026-08-21)

Reading of `scarlett-step1-256x3-condA-2026-08-21.md`.

## Two results, one good and one bad

| 256x3, condition A | Scarlett 4i4 (async, 480M) | Sound Blaster (adaptive, 12M) |
|---|---|---|
| shape | **unimodal**, 40.0-85.5/min | **bimodal**, ~2-5 vs ~18-22/min |
| mean | **69.7/min** | 7.1/min |
| worst stream | 85.5/min | ~22/min |

**Good: the mechanism question is closed.** Bimodality disappeared on a device with an
asynchronous endpoint and a feedback channel. The stream-start lottery was the Sound
Blaster's **adaptive clock lock**, not frame phase. The alignment hypothesis is not
supported, and the aligned-period drop-in table (240/480/1008) can be dropped.

**Bad: the Scarlett is roughly 10x worse at 256.** Better interface, worse result. It
matches the ear test — 256 crackles badly — and it means the transport swap did not move
the cliff.

## What this rules out

The problem is **not** the dongle's clock mode, not its full-speed transport, and not
frame alignment. Those were the three leading candidates and all three are now excluded
by a device that fixes all of them and performs worse.

**The bottleneck is on the Pi.** That is consistent with everything else measured:
callbacks never miss their deadline at any buffer size, yet the buffer empties anyway,
and callback lateness sits at a fixed ~900 us that does not scale with period.

## Leading hypothesis for why *worse*, not merely *not better*

**High speed trades delivery granularity for servicing rate.** Full speed delivers once
per 1 ms; high speed uses 125 us microframes — **eight times as many transfer opportunities
per second**. `snd-usb-audio` packs packets into URBs, and the smaller the period, the fewer
packets per URB and the more URB completions the host controller must service.

Every one of those completions is work for **xhci IRQ 30 — which is unmovable and pinned to
CPU0.** If the binding term is the Pi's capacity to service that interrupt promptly, then a
high-speed device at a small period demands far more of exactly the resource that is
already scarce.

Two aggravating factors on this specific device:

- **4 playback channels at 24-bit** (S32_LE, 144-byte packets) against the Sound Blaster's
  2. Roughly double the payload for a stereo synth that uses two of them.
- **`snd_usb_audio.lowlatency=Y`** submits URBs on demand rather than deep-queuing. On a
  high-speed device that means many more small submissions — more completions, more
  interrupt servicing.

**Status: hypothesis, not measurement.** It fits every observation but has not been tested.

## Consequences for the plan

**`lowlatency=N` is un-demoted, for the Scarlett specifically.** It was demoted after T13
because it acts on URB queue *depth*, and T13 showed depth is not the binding term. But if
the problem here is submission *rate*, batching more packets per URB reduces completions
directly. Different mechanism, same knob. **15 minutes, and it is now the cheapest test on
the list.**

**Channel count is worth checking.** If jackd is opening 4 playback channels to use 2, the
device may be configurable to a 2-channel altsetting — halving payload for free.

**Step 2 (cyclictest under real conditions) is now the most important measurement in the
project.** The ~900 us versus the 209-320 us floor is ~600 us unexplained, it is
device-independent, and two devices with opposite transport characteristics have now both
failed at small buffers. If that gap is generic scheduler latency, the remaining levers are
`threadirqs`, `isolcpus`/`nohz_full`, and PREEMPT_RT — all Pi-side, none device-side.

**The alignment line of work is closed.** T15, T16, T17 and the aligned drop-in table are
withdrawn. No further Pi time on frame phase.

## Product reading

The Scarlett still earns its place on the product grounds already argued — MIDI DIN,
phantom power, real outputs, one cable into gear the musician already owns. **It does not
buy lower latency on this Pi.**

Mitch's ear test stands as the practical summary on the Scarlett: **1024 good, 512 crackles
on heavy patches, 256 unusable.** That is the same ceiling as the dongle. Whatever is
limiting this instrument is not the interface.
