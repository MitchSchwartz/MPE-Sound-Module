# Output dB meter — design and cost

*Written 2026-08-16 (America/Toronto), before it was built. Measurements below are from the live appliance; cost figures are estimates and flagged as such.*

**The ask:** a normal output meter on the touch UI showing total system output in dBFS, so patch levels can be set by eye and clipping is visible before it is audible.

**Related:** [`LATENCY-SPIKE.md`](LATENCY-SPIKE.md) · [`AGENTS.md`](../AGENTS.md) §Audio output safety

## As built (updated 2026-10-11)

The design below shipped as `native/mpe-peak-meter`, with two changes on 2026-10-11:

- **The tap is post-FX.** The meter's inputs follow the fan-out source that
  `patch_browser/audio_routing.py` names: `mpe-fx-host:out_N` while the FX host
  carries the trunk, otherwise `Surge XT:out_N`. Exactly one of them, never both
  (both would sum and read up to 6 dB hot). Before this, the tap sat on Surge,
  so the FX host's added peaks (phaser up to +5.4 dB) and its ±1.0 clamp were
  invisible: audible clipping read as −3 dBFS. The C mirror is checked against
  the authority over every fake-JACK graph in `tests/test_audio_routing.py`.
- **Clip latch.** A window whose sample peak is ≥ 0.999 writes `clip=1`, held
  for 8 writes (1.6 s), on that same post-FX signal. The UI outlines the bar
  and fills it red while it is up.

What did **not** move: `wired=` / `online=` still mean "Surge's outputs are on
the graph" (`audio-engine.sh`'s on-graph probe), and `surge_playback=` still
means "can you hear yourself play" (`sl-watchdog`). Neither depends on where
the meter listens.

The meter is still pre-hardware-mixer: in device mode the Vol fader moves the
DAC, after the meter, and Surge sits at a fixed −9 dB
(`DEVICE_MODE_SURGE_TRIM_DB`) to leave headroom for chords and effects.

---

## Why this is cheaper than it looks

Two measured facts, both of which remove the cost people usually assume:

| Measured | Consequence |
|---|---|
| `touch_patch_browser.py` already runs `clock.tick(30)` | The UI **already repaints continuously**. The usual objection — "a live meter forces constant redraw" — is a cost already being paid. A dirty-rect meter widget is noise against it. |
| JACK graph holds **3 clients** (`Surge XT`, `mpe-looper`, `system`) | Adding a fourth is the only real risk, and the graph is small. |

The appliance runs `256 × 3 @ 48 kHz` (~5.33 ms/period) with **0 xruns**. That period budget is the constraint everything below respects.

## Where to tap

*(Original design. As built, the Surge leg is the fan-out source: see above.)*

Connect the meter's two input ports to the **same sources as `system:playback_1/2`**:

```
Surge XT:out_1        ─┐
mpe-looper:common_out_1 ├─→ meter:in_1   (JACK sums multiple connections
                        │                 into one input port)
Surge XT:out_2        ─┐
mpe-looper:common_out_2 ├─→ meter:in_2
```

JACK sums multiple connections into a single input port, so the meter sees exactly what the DAC sees — including the looper sum. That is what makes it a *system* meter rather than a per-client one.

Connect it as a **leaf**: inputs only, nothing downstream depends on it. Lowest-risk topology in a real-time graph.

## Cost

Estimates, not measurements — see *De-risk first* below.

| Item | Estimate |
|---|---|
| Peak + RMS over 512 floats/callback, 187.5 callbacks/s | ~0.06% of one core |
| Extra JACK client scheduling (~20 µs/cycle context switching) | ~0.4% of one core |
| tmpfs write at 30 Hz (~50 bytes) | negligible |
| UI widget | already repainting; dirty-rect only |
| **Total** | **< 0.5% of one core**, against load ~1.5 on 4 cores |

**Latency:** one period (5.33 ms) + up to one UI frame (33 ms) ≈ **40 ms**. Meters read as instantaneous below ~100 ms, so this is comfortably fine.

**The cost that matters is not CPU.** It is adding a client to a graph currently at 0 xruns. Treat that as the risk, not the arithmetic.

## Implementation

**Must be C, not Python.** A Python process callback at 5.33 ms invites GIL contention and GC pauses — that turns a meter into xruns. ~150 lines plus a systemd unit.

**No file I/O in the process callback.** The standard split:

- **RT callback:** compute block peak and RMS, store into a lock-free atomic / mmap'd shared page. No syscalls, no allocation, no locks.
- **Non-RT thread:** read the atomic at 30 Hz, write `/run/mpe/meter.state` in the existing `KEY=value` style used by `jack.state` and `engine.state` (see `mpe_state_write_atomic` in `scripts/lib/audio-engine.sh`).
- **UI:** read that file per frame.

Fits the existing runtime-state pattern rather than inventing IPC.

## The detail that decides whether it is actually useful

**Peak-hold must live in the RT callback, not the UI.** Record max-since-last-read and reset on read.

Without it, a single-sample clip landing between two 30 Hz polls is invisible — and catching exactly that event is the point. This is the difference between a meter that looks right and one that reports the thing you built it for. A meter that silently misses clips is worse than no meter, because it is trusted.

**Clip detection:** sample peak ≥ 0.999 latches a clip flag held ~1.5 s so it is visible at 30 fps. True-peak / inter-sample detection is overkill — sample peak is what the DAC actually clips on.

**Slow-decay peak marker** (the falling line on a hardware meter). Instantaneous bars are hard to read; the held peak is what tells you how much headroom a patch is using, which is the stated purpose.

**Scale:** −60 → 0 dBFS. `20·log₁₀(peak)`, floored at −60 to avoid `log(0)`.

## De-risk first — before writing the meter

Build a **stub client**: opens two ports, returns immediately, computes nothing. Connect it as a leaf and watch `xruns` for ten minutes under a real 16-loop session.

That isolates *"can this graph take a fourth client"* from *"is my meter code correct."* If the answer is no, that is worth knowing before the meter exists rather than after — and the failure would otherwise look like a meter bug.

Per the appliance's own measurement habit: the stub run is the experiment, the meter is the implementation. Do not bundle them.

## Open questions

- Does the touch UI have screen space, or does the meter need a mode/overlay?
- Should it read pre- or post-`amixer`? The tap above is **pre**-hardware-mixer, so it shows what the software graph produces, not what leaves the DAC. Post-mixer level cannot be measured in JACK at all — worth stating on the UI so the reading is not misread as speaker output.
- **Vol fader vs DAC:** on a card with one unambiguous hardware volume the touch **Vol** fader *is* the DAC level (post-graph, so this meter does not move with it) and Surge sits at a fixed −9 dB; otherwise it trims Surge `amp/volume`. See `patch_browser/device_volume.py`.
- Peak only, or peak + RMS/LUFS? Peak answers "am I clipping"; RMS answers "is this patch as loud as that one". The stated need is both, so probably both bars.
