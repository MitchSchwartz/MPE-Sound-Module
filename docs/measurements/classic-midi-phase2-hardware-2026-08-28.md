# Phase 2 router — first hardware run

**Date:** 2026-08-28
**Appliance:** pi5, `feat/classic-midi-translator` @ 849e906
**Device:** APC mini mk2 in Notes mode (Shift + Scene 7)

## Result: PASS

Mitch played the APC Notes pads and reported: *"notes confirmed working,
sounds correct."*

First time any byte has travelled from a classic device into Surge through
the router. Everything prior to this was proven against a recording in CI.

## Signal chain, verified before the test

```
APC Notes 32:1 → 134:0 (router in) → router → 131:0 → Midi Through 14:0 → 130:1 (Surge)
```

## Bindings the router chose

```
Listening: Scarlett 4i4 USB MIDI 1  [classic: no MPE signal — treated as classic]
Listening: APC mini mk2 Notes 32:1  [classic: no MPE signal — treated as classic]
Listening: LUMI Keys BLOCK MIDI 1   [mpe: known MPE device (name)]
```

`APC mini mk2 Control` and `Midi Through` correctly excluded
(`ROUTER_PORT_EXCLUSIONS`).

## A prediction that did NOT hold

Before the run I predicted classic devices would sound weak, dull, or silent,
reasoning that the APC sends no channel pressure and fixed velocity 127, while
`remap_midi_message` only rescales pressure that already exists rather than
injecting any — so on a patch where pressure drives amplitude there would be
nothing to drive it.

**That did not happen. It sounded correct.** The prediction was wrong, or at
least does not apply to the patch that was loaded.

Consequences:

- Synthesising pressure from velocity is **not** a prerequisite for the classic
  path and is removed from the critical path. It was previously described as
  blocking a meaningful ear pass; that was overstated.
- The question is not fully closed: only one patch was tested, and it is not
  recorded which. A patch that routes pressure to amplitude may still be silent.
  **Do not treat this single pass as coverage of all patches.**
- Revisit only if a specific patch is silent or lifeless with a classic device,
  and identify that patch by name when it happens.

## What actually happens instead (Mitch, 2026-08-28)

> "Full velocity is instantly registered and full pressure is achieved quickly
> on Bode strings which usually has a slower attack. It just comes in with full
> attack."

So the failure mode is the opposite of the one predicted: not silence, but
**no attack shaping**. The APC's fixed velocity 127 drives the envelope to full
immediately, and a patch whose character depends on a slow attack loses it.
Every note also arrives identical -- the pads cannot express otherwise.

**Decision: accept as the default, do not fix as part of this implementation.**
Mitch's reasoning, which sets the bar for revisiting it:

> "This isn't the best outcome, but it's maybe the best default unhandled
> outcome in as much as it's working and could be optimized later rather than
> it's not working and needs to be fixed as part of implementation."

This is a *known expressive limitation of velocity-less pad hardware*, not a
defect in the router. Optimisation, if it ever happens, belongs in a later pass
-- e.g. a per-source velocity curve, or synthesising a pressure ramp so the
envelope is driven over time rather than instantly. Neither is scheduled.

## Still not covered

- Latency under a live audio graph (phase 0 measured the hop on an idle machine).
- Fan-out cost: one input bend can become up to 15 output messages. The APC
  sends no bend, so this run did not exercise it at all.
- Hot-plug re-classification (phase 3).
- The ROLI path: unchanged by construction and proven byte-identical in CI, but
  **not exercised by ear in this run**. Phase 5 remains blocking for it.
