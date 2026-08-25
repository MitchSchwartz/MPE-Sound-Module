# USB multichannel stems (per-clip + loop master)

*Last updated: 2026-08-16 (America/Toronto)*

**Status:** Design / feasibility — **not implemented.** Current `usb-host` profile ships **stereo mix only** ([`USB-AUDIO-HOST.md`](USB-AUDIO-HOST.md)).

**Use case:** Desk tether to a laptop/DAW where the host receives **individual loop stems** (one per APC clip) **plus a loop-bus master**, over the same USB-C cable used today for stereo passthrough.

**Clocking dependency:** stems are the dual-sink problem at 34 channels — live synth on the
local DAC while loops go to USB. Whatever **[`USB-DUAL-OUTPUT-CLOCK.md`](USB-DUAL-OUTPUT-CLOCK.md)**
settles, stems inherit. Do not build §2 (JACK routing) before that is resolved.

Related: [`USB-AUDIO-PASSTHROUGH-PLAN.md`](USB-AUDIO-PASSTHROUGH-PLAN.md) · [`scripts/sooperlooper/README.md`](../scripts/sooperlooper/README.md) · [`docs/measurements/sooperlooper-eval-2026-08-14.md`](measurements/sooperlooper-eval-2026-08-14.md)

---

## Summary

| Question | Answer |
|---|---|
| Feasible over USB? | **Yes** — bandwidth and Pi CPU are not blockers at 48 kHz |
| Does per-clip delivery multiply looper CPU? | **No** — all 16 loops already run inside one SooperLooper process |
| Main cost vs stereo mix? | USB bandwidth (linear with channel count), JACK fan-out, host DAW setup |
| Live synth on USB? | **Optional add-on** — not in the default layout below (fail-open analog path unchanged) |

---

## What “master” means

MPE runs **parallel fail-open** audio: live Surge → playback directly; loops → SooperLooper → `common_out` → playback. The APC **master fader scales the loop mix only**, not live synth ([`Documents/DIRECTION.md`](../Documents/DIRECTION.md) · [`scripts/sooperlooper/README.md`](../scripts/sooperlooper/README.md)).

| Bus | JACK source | Contents |
|---|---|---|
| **Clip stem *N*** | `mpe-looper:loopN_out` | Loop *N* only (stereo pair per clip) |
| **Loop master** | `mpe-looper:common_out` | All loops summed (post per-track faders, post master fader) |
| **Live synth** | `Surge XT:out` | Direct to `system:playback` — **not** on USB in the default layout |

**Default USB layout:** 16 stems + loop master. Live stays on **Sound Blaster analog** (gig-feel path, fail-open).

**Full performance mix** (`system:playback` = live + loops) is also routable to USB, but then stems + master **will not sum cleanly** in the DAW (live would double if Surge were also sent separately). Prefer `common_out` as “master” for stem workflows.

---

## Proposed channel map

Host sees **one multichannel UAC2 capture device**. Two reasonable layouts:

### A — 16 stereo stems + stereo master (34 channels)

| USB channels | Source |
|---|---|
| 1–2 | `loop0_out` |
| 3–4 | `loop1_out` |
| … | … |
| 31–32 | `loop15_out` |
| 33–34 | `common_out` (loop master) |

### B — 16 mono stems + stereo master (18 channels)

| USB channels | Source |
|---|---|
| 1 | `loop0_out` (L only, or L+R mono downmix in JACK) |
| … | … |
| 16 | `loop15_out` |
| 17–18 | `common_out` |

**Recommendation:** start with **layout A** if the host and gadget config support it cleanly; **layout B** if macOS channel-mask enumeration is awkward.

Optional future row: add **channels 35–36** = `Surge XT:out` for live-over-USB desk monitoring (still keep analog fail-open for gigs).

---

## Signal chain (target)

```
[Roli] → Surge XT ──┬──→ system:playback ──→ Sound Blaster (live, fail-open)
                    │
                    └──→ loop0_in … loop15_in
                              │
                         SooperLooper
                              │
         ┌────────────────────┼────────────────────┐
         │                    │                    │
    loop0_out … loop15_out   common_out            │
         │                    │                    │
         └────────┬───────────┘                    │
                  ▼                                │
         UAC2 multichannel gadget                  │
                  │                                │
                  └── USB-C ──→ [Host DAW: 17 tracks]
```

Today’s `usb-host` graph uses **`common_out` → playback only**; per-loop outs are disconnected from playback ([`scripts/sooperlooper/wire-jack-graph.sh`](../scripts/sooperlooper/wire-jack-graph.sh)). Stems mode **fans per-loop outs to the gadget** instead of (or in addition to) summing at the DAC.

---

## Feasibility

### SooperLooper already exposes the ports

Eval measurement (16 loops, `-l 16`):

| Measure | Value |
|---|---|
| Processes | 1 (loops are internal instances) |
| JACK ports on `mpe-looper` | 68 (16 × stereo in + stereo out, plus `common_in/out`) |
| DSP load (whole graph) | ~8.8% |
| Mix today | Internal sum → `common_out` only |

Per-loop outs exist today; they are **unused** in the listen path and add graph clutter — exactly the ports a stem layout would use ([`sooperlooper-eval-2026-08-14.md`](measurements/sooperlooper-eval-2026-08-14.md)).

### USB bandwidth (@ 48 kHz, 16-bit)

| Layout | Approx. throughput | vs USB 2.0 isochronous (~24 MB/s) |
|---|---|---|
| Stereo mix (today) | ~0.19 MB/s | trivial |
| 18 ch (layout B) | ~1.7 MB/s | trivial |
| 34 ch (layout A) | ~3.2 MB/s | trivial |

Bandwidth is **not** the binding constraint.

### Overhead vs single stereo feed

| Layer | Stereo mix today | Multichannel stems |
|---|---|---|
| Loop DSP | Same | Same — loops already run |
| JACK graph | 1 stereo connection to playback | 17+ connections to gadget sink |
| USB | 2 channels | 18 or 34 channels (linear) |
| Host | 1 capture track | 17 tracks to arm/map |
| Latency class | ~40–65 ms desk estimate | Same ballpark; larger isochronous packets |

Separate outs may **skip** internal summing to `common_out` on the wire, but **add** connection traversal and a wider ALSA write. Net Pi CPU impact is expected to stay modest relative to Surge + 16 active loops.

---

## Implementation sketch (when built)

Not scheduled — checklist for a future profile (e.g. `usb-host-stems`).

### 1. UAC2 gadget — multichannel playback

Today: stereo only in [`scripts/setup-usb-audio-gadget.sh`](../scripts/setup-usb-audio-gadget.sh) (`p_ssize=2`, `p_chmask=3`).

Change: set channel count and `p_chmask` for 18- or 34-channel playback @ 48000 Hz. Verify on **Linux + Windows** first; **macOS** UAC2 channel masks are historically finicky ([`USB-AUDIO-PASSTHROUGH-PLAN.md`](USB-AUDIO-PASSTHROUGH-PLAN.md)).

### 2. JACK routing

Extend or sibling [`wire-jack-graph.sh`](../scripts/sooperlooper/wire-jack-graph.sh):

- Keep **Surge → `system:playback`** (fail-open).
- Connect **`loopN_out_*` → gadget capture ports** (channel map above).
- Connect **`common_out_*` → master gadget channels**.
- Do **not** connect per-loop outs to `system:playback` (same rule as today’s rewire pass).

May need a **multichannel JACK driver** or ALSA bridge from JACK `system` ports to the gadget card — exact wiring depends on whether the gadget exposes one multichannel PCM device or requires `jackd` `-d` on that card.

### 3. Profile / env

```bash
# Conceptual — not in mpe.env today
MPE_AUDIO_PROFILE=usb-host-stems
MPE_USB_STEM_LAYOUT=stereo   # stereo | mono
MPE_USB_STEM_CHANNELS=34
```

Host-gated routing ([`USB-AUDIO-HOST.md`](USB-AUDIO-HOST.md)) should apply the same way: do not hold UAC2 open without an active host consumer.

### 4. Host

- Linux: `arecord -l` → multichannel `hw:N,0`; REAPER ALSA input with 17 channels.
- Document channel → track map in host README (one-time DAW template).

---

## Caveats

| Risk | Mitigation |
|---|---|
| macOS multichannel UAC2 | Spike on target hardware before committing to layout A |
| Host setup friction | Ship a REAPER track template; not “one armed track” like stereo passthrough |
| Stems + master level | `common_out` is the post-fader loop sum; stems are pre-master per-loop outs — DAW “master” track matches bus, stem tracks need their own faders |
| Gig mode | Unchanged — `standalone` + Sound Blaster; stems profile is **desk only** |
| Live on USB | Optional extra 2 ch; default leaves live analog only |

---

## When to use which USB mode

| Mode | Profile | Host receives |
|---|---|---|
| **Stereo passthrough** | `usb-host` | One mixed stream (today) |
| **Session record (pedal return)** | `usb-host-session` | Mic / RC-5 return ([`USB-SESSION-RECORD.md`](USB-SESSION-RECORD.md)) |
| **Multichannel stems** | `usb-host-stems` (proposed) | 16 clips + loop master |

---

## Open questions

1. **Mono vs stereo stems** — APC product default? Mono saves USB channels and host CPU.
2. **Pre- vs post-fader stems** — SooperLooper `loopN_out` vs wet/dry taps; confirm against engine behavior before locking map.
3. **Gadget + JACK period** — reuse `MPE_JACK_BUFFER` defaults or widen for 34-channel writes under load?
4. **Touch UI** — badge / settings row for stems mode, or desk-only env flag?

---

## References

- Stereo USB path (implemented): [`USB-AUDIO-HOST.md`](USB-AUDIO-HOST.md)
- Looper graph wiring: [`scripts/sooperlooper/wire-jack-graph.sh`](../scripts/sooperlooper/wire-jack-graph.sh)
- SooperLooper eval (ports, CPU): [`docs/measurements/sooperlooper-eval-2026-08-14.md`](measurements/sooperlooper-eval-2026-08-14.md)
- Loop master ≠ live: [`Documents/DIRECTION.md`](../Documents/DIRECTION.md) §Gain staging / master fader
