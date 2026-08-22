# The Scarlett is not the answer — the bottleneck is the Pi (2026-08-21)

*Amended 2026-08-21 (late): Step 1 alignment closure **withdrawn** — see §Amendment below.*

Reading of `scarlett-step1-256x3-condA-2026-08-21.md`.

## Two results, one good and one bad

| 256×3, condition A | Scarlett 4i4 (async, 480M) | Sound Blaster (adaptive, 12M) |
|---|---|---|
| shape (Step 1, n=10) | **unimodal**, 40.0–85.5/min | **bimodal**, ~2–5 vs ~18–22/min |
| mean | **69.7/min** | 7.1/min |
| worst stream | 85.5/min | ~22/min |

**Good (partially withdrawn — see Amendment):** Step 1 read unimodal Scarlett vs bimodal
Sound Blaster as closing the adaptive-clock-lock vs frame-phase question.

**Bad: the Scarlett is roughly 10× worse at 256.** Better interface, worse result. It
matches the ear test — 256 crackles badly — and it means the transport swap did not move
the cliff.

## Amendment — Step 1 alignment closure withdrawn (post Step 4)

Step 4 on the **same async Scarlett** (post-bundle, n=3 streams): stream 01 **105/min**,
streams 02–03 **26–34/min**. The unimodal shape Step 1 relied on is **not stable** — but
**n=3 cannot establish bimodal vs wide unimodal either**, and the 55.0/min mean is not a
reliable estimate.

**Withdraw:** *"bimodality vanished → Sound Blaster lottery was adaptive clock lock, not
frame phase."* The evidence for that closure is gone.

**Alignment status:** **unsupported, still unpromising** — not reopened for Pi time. At HS,
125 µs microframes = **6 samples**; 256/6, 512/6, **1024/6 = 170.67** are all misaligned,
**including 1024×3 with zero xruns**. Frame phase cannot explain clean 1024 if it drives 256.

**Do not spend Pi time on alignment tables (240/480/1008).** Fill telemetry (Phase D+A) shows
mechanism directly.

## What this rules out (updated)

The problem is **not** the dongle's clock mode or full-speed transport alone. **URB completion
rate at 256 vs 1024 is refuted** (Step 1 inverted: 1024 had *higher* IRQ30/s, zero xruns).
Frame alignment is **unsupported**, not closed.

**The bottleneck is on the Pi** — term still under audit (cushion model, xrun counter, D+A).

## Leading hypotheses (status as of post–Step 4)

| hypothesis | status |
|---|---|
| URB submission *rate* / `lowlatency=N` | **Refuted** (Step 1 inverted 256 vs 1024 IRQ) |
| Frame-phase alignment | **Unsupported, unpromising** — closure withdrawn |
| Producer lateness ~600 µs empties cushion | **Arithmetic weak** — see [`cushion-model-2026-08-21.md`](cushion-model-2026-08-21.md) |
| **Compute-bound at 256** (fixed per-callback cost) | **Strengthened** — dsp_p99 **63–89%** at 256 cond A vs **34.8%** at 1024; see [`PLAN-2026-08-21-evening.md`](PLAN-2026-08-21-evening.md) |

## Consequences for the plan

**`lowlatency=N` remains dropped** (Step 1 kill condition — inverted IRQ, not depth).

**Step 2 cyclictest** still refutes generic scheduler ceiling under load (~429 µs max).

**Next (revised):** **D+A** — DSP p99 ladder + fill telemetry at 1024/512/256, identical
cond A load; then **B** (nperiods sweep at period 1024). **`1024×2` open-check** (~30 s)
whenever Pi is idle.

**Alignment line withdrawn.** T15–T17 and aligned drop-in table stay off the list.

## Product reading

The Scarlett still earns its place on the product grounds already argued — MIDI DIN,
phantom power, real outputs, one cable into gear the musician already owns. **It does not
buy lower latency on this Pi.**

Mitch's ear test stands as the practical summary on the Scarlett: **1024 good, 512 crackles
on heavy patches, 256 unusable.** That is the same ceiling as the dongle. Whatever is
limiting this instrument is not the interface.
