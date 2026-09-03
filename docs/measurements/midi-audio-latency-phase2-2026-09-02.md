# Phase 2 — the DAC leg, and the corrected offset

**Date:** 2026-09-02
**Harness:** [`scripts/measure-dac-loopback.sh`](../../scripts/measure-dac-loopback.sh)
**Phase 1:** [`midi-audio-latency-phase1-2026-09-02.md`](midi-audio-latency-phase1-2026-09-02.md)

## Why a cable was needed

jackd runs with no `-I`/`-O`, and both default to `0`, so every DAC's USB
transfer and conversion time is reported by JACK as zero — an unset parameter,
not a measurement. No amount of querying JACK recovers it; only a physical
loopback does.

**This was measured on the belief that it fed the looper-grid offset. It does
not** — see the correction below. It is a real number for a real quantity (what
a player hears), and it is used for that.

## Readings

Both stable over a 12 s window. Temporary duplex server, period 256 × 3 @ 48 kHz.

| loopback | extra latency beyond JACK's declaration | drift over window |
|---|---|---|
| KA1 out → Scarlett 4i4 in (ch 3) | **163 frames** (3.40 ms) | 1 frame — two unsynced USB clocks |
| Scarlett out → Scarlett in (ch 3) | **197 frames** (4.10 ms) | 0 frames — single clock |

## Solving the pair

Each reading is a *sum* of two converters, so neither isolates the KA1 alone:

```
KA1_DAC      + Scarlett_ADC = 163
Scarlett_DAC + Scarlett_ADC = 197
------------------------------------------------
KA1_DAC - Scarlett_DAC      = -34 frames     EXACT, no assumption
```

The KA1's output path is **34 frames faster than the Scarlett's** — a clean
differential that stands regardless of how the converters split internally.

For absolutes one assumption is unavoidable: splitting the Scarlett's 197 evenly
between its own ADC and DAC, which is the convention `jack_iodelay` itself
recommends (`use 98 for the backend arguments -I and -O`).

```
Scarlett_ADC ≈ Scarlett_DAC ≈ 98 frames
KA1_DAC      ≈ 163 - 98     =  64 frames = 1.34 ms
```

**Sensitivity:** a split anywhere from 40/60 to 60/40 puts the KA1 between 45
and 84 frames. So `64 ± 20` frames (± 0.4 ms). Plausible physically: the KA1 is full-speed
USB (1 ms service interval), so ~1.3 ms of transfer plus converter group delay
is the expected size.

## CORRECTION, same day: this measurement does not belong in the offset

Mitch asked why a loopback was informing an offset whose signal never leaves the
graph. It should not have been. **The conclusion first written in this document
was wrong, and shipped for roughly twenty minutes.**

SooperLooper is a JACK client fed by Surge inside the graph:

```
Surge XT:out_1
   system:playback_1        <- the speakers (and the loopback measured above)
   mpe-looper:loop0_in_1    <- what is actually RECORDED
```

The recorded audio never reaches the converter. Everything downstream of that
input port is therefore **not** in the path the offset aligns:

| leg | frames | in the looper's path? |
|---|---|---|
| MIDI in → Surge out | 156 | **yes — the entire answer** |
| Surge out → converter (`period × periods`) | 192 | no — after the looper tap |
| DAC hardware | 64–98 | no — after the looper tap |

Both excluded legs are also **common-mode**: the live signal and the loop's own
playback traverse them identically, so they cancel at the ear as well as in the
recording. Compensating for them fires MIDI early against a delay that is not
there.

This also indicts the ORIGINAL model. `period × periods` is the playback
ringbuffer — the wrong leg, not merely an incomplete one. It was right only by
coincidence of magnitude (192 against a true 156).

| model | ms | verdict |
|---|---|---|
| `period × periods` (before today) | 4.00 | wrong leg, ~0.75 ms too much |
| full path to the ear (my first fix) | 9.29 | **wrong, ~3× too much** |
| Surge leg alone (correct) | **3.25** | ships |

**Phase 2 remains a valid measurement of a real quantity** — what a player hears,
end to end. It is kept in `config/output-latency.conf` and surfaced through
`total_output_latency_ms()` for display and diagnostics. It is deliberately not
wired to the offset, and `test_offset_is_unaffected_by_the_dac` pins that.

## What a player hears — period 96 × 2 on the KA1

| leg | frames | ms | provenance |
|---|---|---|---|
| MIDI in → Surge audio out | 156 | 3.25 | measured, Phase 1 (n=30, model fits to 3 frames) |
| Surge out → converter | 192 | 4.00 | JACK-declared, `period × periods` |
| KA1 DAC hardware | 64 | 1.34 | measured, this doc |
| **total** | **412** | **8.58** | |
| **the old model** | 192 | 4.00 | `period × periods` alone |

This is the **monitoring** figure — what reaches the ear. It is not the offset.

## What shipped

- [`config/output-latency.conf`](../../config/output-latency.conf) — the measured
  per-device hardware term, keyed by `usb:VID:PID`. A device absent from this
  file contributes **zero and is reported as unmeasured**, never guessed from a
  similar-looking one.
- [`patch_browser/midi_sync.py`](../../patch_browser/midi_sync.py) —
  `looper_alignment_latency_ms()` is the offset: **Surge's leg alone**.
  `total_output_latency_ms()` sums all three legs for display only. The DAC term
  is resolved from the card the graph is **actually bound to** (via
  `jack.state`), not the configured one, because an absent selection
  legitimately falls through to another tier.
- [`tests/test_output_latency_model.py`](../../tests/test_output_latency_model.py)
  — including `test_offset_excludes_the_playback_ringbuffer` and
  `test_offset_is_unaffected_by_the_dac`, which pin the correction so this
  mistake cannot be reintroduced quietly.
- [`scripts/measure-midi-audio-latency.py`](../../scripts/measure-midi-audio-latency.py)
  now **refuses to run** without `MPE_ALLOW_GRAPH_PROBE=1`. An allowlist entry is
  a comment; a tool that merely says it is diagnostic gets called by something
  eventually.

Two existing tests changed. Both encoded `period × periods` as the whole model.
`test_the_displayed_offset_is_the_applied_offset` kept its invariant — displayed
equals applied — and only its stale literal moved, `-4 ms` → `-3 ms`.

## Instrument failures worth remembering

Three, all silent, all found the hard way:

1. **`jack_iodelay` produced 0 bytes.** It redraws one line with `\r` and
   block-buffers when stdout is not a terminal. All four capture channels
   reported "no convergence" when nothing had ever been flushed. Fixed with a
   pty. *The instrument and its failure shared a channel.*
2. **The restore reported success and left the appliance DOWN** — `surge-xt-cli`
   inactive, looper failed. It started three units three seconds apart; a client
   cannot attach before the graph is accepting. Restore now waits on
   `system:playback_*` then `Surge XT:out_1`, and shouts the recovery command.
3. **The restore's own health check was blind.** It ran bare `jack_lsp` as root,
   which cannot see the graph without dropping to its owner, and reported
   `surge_port=0` for a Surge running perfectly. A false "the instrument is
   dead" costs nearly as much as missing a real one.

## The lesson worth keeping

Every instrument in this exercise was audited, controlled and correct. The
positive control recovered an injected delay to one sample; the negative control
halted; the attack bias measured 0.000 ms. **And the conclusion was still wrong,
because nobody asked which path the number was supposed to describe.**

Instrument conformance answers "is this reading true?" It cannot answer "is this
the right quantity?" That question has to be asked separately, against the actual
signal topology — here, one `jack_lsp -c` away the entire time.

## Open

- **Phase 1's period-256 cell is still unexplained** — strict low/high
  alternation every other trial. The scaling law rests on periods 96 and 192 and
  should be re-measured before being trusted far outside that range.
- **The KA1's 64 frames inherits the even-split assumption.** Removing it needs a
  third converter of known latency, and is not worth a cable for ±0.4 ms.
- Devices other than the KA1 and Scarlett contribute **zero** until measured.
