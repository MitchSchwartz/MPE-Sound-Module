# USB dual-output monitoring — clock architecture

*Last updated: 2026-08-25 (America/Toronto)*

**Status:** Design / feasibility — **not implemented.** No code change is proposed until
**Check 1** below returns. Current `usb-host` host-gated routing is unchanged.

**Problem:** monitoring through the USB round-trip has audible latency, so desk play wants
the **local DAC and the USB capture stream live at the same time**. That means two sinks —
and two sinks means two clocks.

Related: [`USB-AUDIO-HOST.md`](USB-AUDIO-HOST.md) · [`USB-AUDIO-PASSTHROUGH-PLAN.md`](USB-AUDIO-PASSTHROUGH-PLAN.md) · [`USB-MULTICHANNEL-STEMS.md`](USB-MULTICHANNEL-STEMS.md) · [`USB-SESSION-RECORD.md`](USB-SESSION-RECORD.md)

---

## Summary

| Question | Answer |
|---|---|
| Is dual output a JACK limitation? | **No** — JACK sums fan-out fine. It is a *policy* limit: `jackd` binds one device and the watchdog moves it |
| What is the real constraint? | **Clock domains.** Two sinks on different references must be reconciled somewhere |
| Can it be built without a resampler on the Pi? | **Possibly yes** — two arrangements avoid it, each requiring a *different* class of local DAC |
| What blocks a decision today? | Whether `jackd` wedges holding the UAC2 PCM open (Check 1) |
| Does this affect the stems plan? | **Yes** — stems inherit whatever clocking this settles |

---

## The principle

> Every sample must be produced and consumed against **exactly one time reference**.
> Where two references exist, the difference is absorbed either by **resampling**
> (continuous, costs CPU, inaudible) or by **glitching** (discrete, free, permanent).
> There is no third option.

This is arithmetic, not engineering quality. "Building it well" therefore does not mean
compensating well — it means **arranging for a single reference**, and where that is
genuinely impossible, deciding *where the resampler lives* rather than letting one appear
by accident.

Corollary that drives the design below: **the resampler does not have to live on the Pi.**

## Why today's design has no clock problem

`jackd` binds exactly one ALSA card, and host-gated routing *moves* that binding rather
than splitting it ([`restart-audio-graph.sh`](../scripts/restart-audio-graph.sh),
[`uac2-stall-watchdog.sh`](../scripts/uac2-stall-watchdog.sh)). One card means one clock
means zero boundaries.

This is a **real property of the current architecture, not an accident** — worth stating
plainly, because the graph-move that reads as clumsy is precisely what makes the drift
problem not exist. Removing it is the actual cost of dual output.

## Sync modes of the two interfaces

| | Sound Blaster Play! 3 | Scarlett |
|---|---|---|
| USB sync mode | **Adaptive** | **Asynchronous** |
| Feedback endpoint | none | yes |
| Reference | borrowed from the host's USB frames | own crystal |
| Who absorbs rate error | the device, internally, in hardware | the host, on instruction |
| Can it be a clock **master**? | No | Yes |
| Can it be a clock **follower**? | Yes | No |
| Is drift **observable** from the Pi? | **No** — no feedback endpoint to read | Yes |

Source for the Sound Blaster: [`low-latency-512-256-spec.md`](../Documents/specs/low-latency-512-256-spec.md)
— `Endpoint 0x01 OUT (ADAPTIVE)`, *"no feedback endpoint. The device slaves to whatever
rate the host delivers and absorbs clock drift internally."*

**There is no such thing as a DAC without a clock.** A converter cannot emit analog
without a rate. The question is only *whose* reference it is and *who adapts*. An adaptive
device is the closest thing to "clockless" — it has a clock, it simply does not insist
on it.

---

## The three arrangements

| | **A — single device** | **B — Pi is master** | **C — host is master** |
|---|---|---|---|
| Status | shipped today | proposed | proposed |
| `jackd` bound to | the one local DAC | the local DAC | the UAC2 gadget |
| Second sink | none (graph moves instead) | gadget, via gated bridge | local DAC, via gated bridge |
| Clock reference | the local DAC | the local DAC | the PC |
| Who absorbs rate error | nobody — no boundary | the **host DAW** | the **local DAC's PLL** |
| Resampler on the Pi | none | none | none |
| Requires local DAC to be | either | **asynchronous** | **adaptive** |
| Local monitoring while recording | **no** | yes | yes |
| Recorded take is sample-exact | n/a | no (host reconciles) | **yes** — it is the reference |

Arrangements B and C are **mutually exclusive in hardware**. B needs a local DAC with a
readable clock to slave the gadget to; C needs a local DAC with no opinion, which will
follow. The Scarlett can only do B; the Sound Blaster can only do C.

### Arrangement C — the adaptive path

Counterintuitively the **simpler** of the two, and the one that costs least.

```
PC (owns the gadget bus) ─────→ clock master
        │
   jackd clocked by the gadget
        │
        ├──→ UAC2 ─────────────→ PC          sample-exact: it IS the reference
        └──→ bridge ──→ Sound Blaster        PLL follows for free, in hardware
```

An adaptive device locks its converter PLL to the average incoming data rate — exactly the
job an adaptive-resampling bridge (`zita-ajbridge`, `alsa_out`) would otherwise be hired
for. **The Sound Blaster already contains one**, on the far side of the cable, at zero Pi
CPU. This is the same mechanism T5 described as *"absorbs clock drift internally."*

Residual: monitoring pitch is dictated by the PC's crystal, a few ppm from nominal. One
cent is ~580 ppm, so this is inaudible by roughly three orders of magnitude.

This also answers the obvious objection to host-as-master — *"it makes the instrument
hostage to the PC."* What is handed to the PC here is only the **rate**, not the latency
and not the availability. Nobody can hear a rate.

### Arrangement B — the async path

The Scarlett's crystal stays master; `jackd` is already clocked by it today. The gadget
must then be paced by `jackd` rather than by the PC's USB frame clock, and the host DAW
absorbs the residual — which every DAW already does for any asynchronous interface.

Viability depends on whether `f_uac2` can pace the device→host stream from the Pi's ALSA
clock (**Check 3**).

---

## The blocking question

Arrangement C requires something to hold the UAC2 PCM open **permanently** — which is
exactly what host-gated routing exists to prevent.

But note what was actually root-caused. [`USB-AUDIO-PASSTHROUGH-SPIKE.md`](USB-AUDIO-PASSTHROUGH-SPIKE.md)
§32 identifies the wedge as *"a **Surge/JUCE** ALSA writer stall"* — a property of that
writer, not of the gadget. **Whether `jackd` wedges the same way has never been tested**,
because the architecture has never needed `jackd` to hold the gadget.

If `jackd` is immune, arrangement C is available and most of the complexity here
evaporates. If it wedges, C is dead and the choice is B or the status quo.

This is the single most decisive unknown in the design.

---

## What it costs

The catch is not technical — it is I/O. Arrangement C wants the **Sound Blaster** as the
monitor, and [`PLAN-2026-08-21-evening.md`](measurements/archive/PLAN-2026-08-21-evening.md)
records that *"the Scarlett earns its place on I/O grounds (MIDI DIN…)"*. Asynchronous is a
fixed device property, not a mode, so the Scarlett cannot be made to follow.

> **The cheaper interface enables the simpler architecture; the better interface requires
> the more complex one.**

That is a decision about whether MIDI DIN and preamps outweigh clock simplicity — not a
technical obstacle, and not one to settle inside this doc.

---

## Design rules that fall out

1. **The mode must know which arrangement it is in and refuse the mismatch.** Arrangement
   B on an adaptive DAC, or C on an async DAC, must **fail loudly**, not silently degrade
   into a second clock domain. Rule −1 shape: a wrong clock arrangement and a right one
   are indistinguishable by ear until a take is ruined.
2. **Never let a bridge report drift through its exit status.** Underrun and drift counts
   need a channel separate from liveness — a bridge that dies and a bridge that is
   silently eating 40 ms must not look alike.
3. **Tier detection currently selects the clock master, silently.** Tier 1 (Sound Blaster)
   means the reference is the Pi's USB controller; tier 2 (Scarlett) means it is the
   Scarlett's crystal. That is significant hidden coupling in a script that reads as
   though it only chooses an output — see also the tier-2 skip below.
4. **The playing path never depends on the recording path.** Fail-open analog monitoring
   survives the PC being absent, in every arrangement.

## Related defect found while scoping

[`detect-audio-device.sh:127-129`](../scripts/detect-audio-device.sh#L127) skips **Tier 2
(generic USB audio)** whenever the profile is `usb-host` or `usb-host-session`. On a Pi 5
with a Scarlett and no Sound Blaster, idle therefore falls through to **Tier 3, the Pi
headphone jack** — not the interface actually in use.

This is independent of everything above and looks like a live bug, not merely a blocker
for dual output. **Not yet confirmed on hardware.**

---

## Decision checks — cheapest first

All three are read-only and none disturbs a measurement in flight.

| # | Check | Answers | Cost |
|---|---|---|---|
| **1** | Does `jackd` wedge while holding the UAC2 PCM open, the way Surge/JUCE did? | Arrangement **C** viable or dead | bounded test, no soak |
| **2** | Scarlett sync mode straight from `lsusb -v` **descriptors** | Confirms **B** is on the table | ~30 s |
| **3** | Does `f_uac2` pace device→host from the Pi's ALSA clock or from USB SOF? | Arrangement **B** viable or dead | kernel source / `modinfo`, ~30 min |

Check 3 is only needed if Check 1 fails.

**Drift measurement is deliberately *not* a first step.** It measures the symptom of a
clock arrangement before establishing which arrangement is being built. It becomes
meaningful only afterwards, as verification that the chosen arrangement holds — and the
threshold should then be expressed as **expected discontinuities per take length**, not in
ppm, because that is the quantity a player actually experiences.

---

## Corrections to prior documents

- [`USB-AUDIO-PASSTHROUGH-PLAN.md`](USB-AUDIO-PASSTHROUGH-PLAN.md) §Clock master states
  *"the USB host **typically** clocks the isochronous stream (host is master)"* and asks a
  Phase 0 spike to confirm no drift. **That confirmation never happened** — the spike was
  consumed by the Surge/JUCE writer stall. The assumption has been inherited unmeasured
  ever since and is now flagged in place.
- [`MEASUREMENT-DISCIPLINE.md`](measurements/MEASUREMENT-DISCIPLINE.md) records
  *"Scarlett unimodal ⇒ adaptive clock lock, n=3 — a conclusion withdrawn hours later."*
  **Do not rebuild that inference.** "The Scarlett is asynchronous" is safe: it is a device
  property readable from USB descriptors, the same evidence class as the Sound Blaster's
  captured `(ADAPTIVE)` line. "Therefore drift is locked or absent" is the withdrawn claim
  and must stay separate.

## Open questions

1. Does `jackd` holding the gadget survive where Surge did not? (Check 1)
2. If arrangement C ships, what happens on PC **unplug** — the master disappears and
   `jackd` must fall back to the local DAC. Rare transition, but it is a graph restart.
3. Does the stall watchdog still have a job under C, or does gating move entirely to the
   bridge?
4. Under C, is the bridge's added ALSA client affordable at 128×2 on Pi 5? Measure.
5. Stems (34 ch) multiply the bridge's width — does the arrangement still hold, and does
   `p_hs_bint`/`req_number` need revisiting?

## Bearing on the stems plan

[`USB-MULTICHANNEL-STEMS.md`](USB-MULTICHANNEL-STEMS.md) assumes live synth stays on the
local DAC while loops go to USB — which is **exactly this dual-sink problem**, at 34
channels instead of 2. Whatever this doc settles, stems inherit.

One reassurance worth recording: all stems and the loop master traverse **one** bridge into
**one** gadget PCM, so they remain sample-locked *to each other* regardless of arrangement.
Only the bundle-vs-DAW-timeline relationship is at issue.
