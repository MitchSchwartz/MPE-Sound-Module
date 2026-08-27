# Classic MIDI instrument compatibility — plan

**Status:** draft, not started. **Author:** drafted 2026-08-26.

"Classic MIDI" here means a channel-based controller: one MIDI channel for all
notes, pitch bend applied to every sounding note, ±2 semitones by default, no
per-note expression. Ordinary keyboards, most hardware synths acting as
controllers, MIDI-out from a DAW, arpeggiators, drum pads.

The appliance today is MPE-only **by construction**, not by configuration. This
plan is about making it play both without the user thinking about it.

---

## 1. Why it does not work today

Three independent blockers, each in a different file. All are load-bearing.

**A. Surge is unconditionally in MPE mode.**
[`scripts/start-surge-cli.sh:167`](../scripts/start-surge-cli.sh) launches with
`--mpe-enable --mpe-pitch-bend-range=48` on every start, with no branch. A
classic controller sends notes and bend on one channel into a synth that is
interpreting channel layout as an MPE zone, and interpreting bend against a
48-semitone range instead of 2. Bend is therefore ~24× too wide, and channel-1
notes land on what MPE treats as the master channel.

**B. Routing is gated on a ROLI being present.**
[`scripts/start-mpe-pressure-remap.sh`](../scripts/start-mpe-pressure-remap.sh)
exits 0 unless `lsusb` shows vendor `2af4`. When a ROLI *is* attached, the
remapper writes to ALSA "Midi Through" and
[`start-surge-cli.sh:113`](../scripts/start-surge-cli.sh) points Surge at **that
port only** — so a classic keyboard plugged in alongside is not heard at all.
When no ROLI is attached, Surge falls back to `--all-midi-inputs`, so the
classic keyboard *is* heard, but still in MPE mode (blocker A). Whether a
second controller works at all currently depends on what else is plugged in.

**C. Nothing knows what kind of device is attached.**
There is no device registry and no notion of "this port is MPE, that port is
classic". `mpe-pressure-remap.py` matches ROLI by USB vendor ID and ALSA client
name; everything else is unclassified.

---

## 2. Design options

### Option 1 — Global mode switch, restart Surge
A setting ("MPE / Classic") that relaunches Surge with different flags.

*For:* smallest change; no new translation logic; certainly correct per mode.
*Against:* audio drops on every switch (Surge restart); cannot use an MPE
controller and a classic keyboard at once; the user has to know and choose. It
also puts a mode flag in the boot path, which is where blocker B already lives.

### Option 2 — Translate classic MIDI into MPE at the router *(recommended)*
Generalise the existing remapper from "ROLI pressure remap" into a MIDI router
that normalises **every** input to MPE before it reaches Surge. Surge stays in
MPE mode permanently and never restarts.

Per classic device: allocate a member channel per sounding note, scale incoming
channel bend from its declared range (default ±2) to Surge's ±48, and broadcast
channel-wide messages to that device's active member channels.

*For:* no restart, no audio drop, no mode setting to get wrong; MPE and classic
controllers work simultaneously; it extends an architecture already in
production rather than adding a parallel one; the translation is pure and
therefore unit-testable with no hardware.
*Against:* real logic to get right (voice allocation, note stealing, pedal);
adds a hop for devices that currently bypass the remapper entirely.

### Option 3 — Hybrid
Option 2 with a per-device "pass through untranslated" escape hatch.

**Recommendation: Option 2**, with Option 3's bypass as a kill switch
(`MPE_MIDI_TRANSLATE=0`) rather than a designed feature. It is the only option
where a user can plug in any keyboard and have it work with no setting.

---

## 3. Architecture

```
classic keyboard ─┐
                  ├─→ midi-router ─→ ALSA "Midi Through" ─→ Surge (MPE, ±48)
ROLI / MPE ───────┘   (classify, translate, remap pressure)

APC / control surfaces ──→ looper stack directly (NOT routed through this)
```

The router replaces `mpe-pressure-remap.py`'s daemon role and keeps its
existing pressure-remap behaviour as one device profile among several.

**Surge stops being launched with `--midi-input=<Midi Through>` conditionally
on ROLI** — it always reads Midi Through, and the router is always the only
writer. That removes blocker B's "depends what else is plugged in" behaviour.

### Device classification, in priority order
1. **MPE Configuration Message** — an MPE device announces its zone with RPN
   0x0006 on channel 1. This is the standards-correct signal and requires no
   device list. Treat any device that sends it as MPE.
2. **Known-device table** — USB vendor/product + ALSA client name, for devices
   that are MPE but do not send MCM (the ROLI path today).
3. **Default: classic.** An unknown keyboard is far more likely to be an
   ordinary one, and classic-treated-as-MPE is the failure that sounds broken
   (bend 24× too wide). MPE-treated-as-classic merely loses expression.

Classification must be **re-evaluated on hot-plug**, not only at boot.

### Translation rules (classic → MPE)
| Input | Output |
|---|---|
| Note on, ch N | Allocate a free member channel; note on there |
| Note off | Release that note's member channel |
| Pitch bend, ch N | Same bend, **scaled** `in_range/48`, to every active member channel of that device |
| Channel pressure | Broadcast to that device's active member channels |
| CC 1 / 11 / 74 | Broadcast (they are zone-wide for a classic device) |
| CC 64 sustain | Hold member-channel release until pedal up — the router owns this, not Surge |
| RPN 0/0 (bend range) | Update that device's declared range; do not forward |
| Program change | Route to patch selection, not to Surge (see §7) |
| Clock / transport | Unchanged — existing `midi-clock-in.py` path |

### Channel allocation
- Member channels 2–16 per the MPE zone Surge is configured for.
- Round-robin over least-recently-released, so a repeated note does not reuse a
  channel still ringing its release.
- On exhaustion, steal the oldest sounding note. **The poly governor already
  caps voices** — the router must not fight it; see §6 risk 3.

---

## 4. Phases

Each phase ends at a gate. No phase starts before the previous one's gate.

| # | Phase | Deliverable | Gate |
|---|---|---|---|
| 0 | **Spikes** (§5) | Answers to the four unknowns | All four answered; any "no" reshapes the plan |
| 1 | **Pure translator** | `midi_translate.py` — events in, events out, no I/O | Unit tests pass; no hardware needed |
| 2 | **Router daemon** | Generalise `mpe-pressure-remap.py`; ROLI profile preserved | ROLI still behaves exactly as today (regression gate) |
| 3 | **Classification + hot-plug** | MCM detection, device table, re-classify on plug | Plug/unplug both device kinds in any order, 20× |
| 4 | **Boot path** | Surge always reads Midi Through; router always runs | Cold boot with: no controller / classic only / MPE only / both |
| 5 | **Ear pass** | Mitch, both controllers | Bend depth correct on both; chords and pedal correct |
| 6 | **Docs + measurement** | Update this doc with measured latency; note in `docs/` | Latency delta recorded, not estimated |

Phase 1 is the bulk of the logic and needs **no Pi and no hardware** — the
translator is a pure function over MIDI events. That is deliberate: it means
the hard part is testable in CI and by ear only once.

---

## 5. Spikes — answer these before building

1. **Does Surge XT in MPE mode sound notes sent on the master channel?**
   Decides whether "classic device, no translation" is even partially usable,
   and therefore how bad a mis-classification is.
2. **Can MPE mode / bend range be changed at runtime over Surge's OSC port?**
   Surge is launched with `--osc-in-port=53280`. If mode is settable live,
   Option 1 becomes cheap enough to keep as a fallback. If not, Option 2 is the
   only no-restart path — which is what this plan assumes.
3. **What is the added latency of the router hop for devices that currently
   bypass it?** There is a production precedent (the ROLI path already goes
   through Python), so the cost is likely acceptable — but it must be measured,
   not assumed. `scripts/sooperlooper/measure_midi_osc_latency.py` is the
   existing instrument.
4. **Do real classic controllers actually declare bend range via RPN 0/0?**
   Most do not; they assume ±2. Confirms the default and whether a per-device
   override is needed in the UI.

---

## 6. Risks

1. **Regressing the ROLI path.** It works today and is the primary instrument.
   The ROLI profile must be a preserved special case with its own tests, and
   phase 2's gate is explicitly "unchanged behaviour", not "still works".
2. **CPU.** Per-event work in Python on the hot path. Event rates are low
   (hundreds/sec worst case, not thousands) and the daemon already exists, so
   this is not a periodic-loop-subprocess situation — but the router must do no
   forking, no per-event allocation of subprocesses, and no polling faster than
   the existing `RECONNECT_POLL_S`.
3. **Fighting the poly governor.** Channel allocation and voice limiting are two
   different mechanisms that both cap sounding notes. If the router steals a
   channel the governor has already muted, notes will hang. Decide explicitly
   which layer owns voice count.
4. **Stuck notes.** The classic failure of any channel-allocating translator: a
   note-off arrives for a note whose channel was stolen. Needs an all-notes-off
   safety on device unplug and on router restart.
5. **Silent mis-classification.** A device classified wrongly produces "works
   but sounds wrong", which is the failure shape this project keeps getting
   caught by. Classification must be **visible** — logged, and surfaced in the
   touch UI, so the user can see what the appliance thinks is attached.

---

## 7. Open decisions

- **OPEN-1: Program change.** Classic instruments send PC freely. Mapping it to
  patch selection is powerful and also means an errant keyboard can change the
  sound mid-take. Proposal: off by default, opt-in per device.
- **OPEN-2: Per-device UI.** Does the touch browser get a MIDI devices screen
  (showing classification, with an override), or is classification automatic and
  invisible? Risk 5 argues for at least a read-only display.
- **OPEN-3: Multi-timbral.** Two controllers currently share one Surge patch.
  Separate sounds per controller is a much larger change (multiple Surge
  instances) and is explicitly **out of scope** here — but the router's device
  identity is the hook it would eventually need.

---

## 8. Test strategy

- **Phase 1 is where the coverage lives.** The translator is pure: given a
  stream of input events and a device profile, assert the exact output stream.
  Bend scaling, allocation, stealing, pedal, and stuck-note recovery are all
  table tests with no hardware.
- **Golden streams:** capture real MIDI from a classic keyboard once
  (`amidi -d`), replay it in tests forever.
- **Regression:** a recorded ROLI stream must translate to byte-identical output
  before and after phase 2.
- **Hot-plug:** scripted plug/unplug loops, asserting no stuck notes and correct
  re-classification.
- **What tests cannot cover:** whether it *sounds* right. That is phase 5, and
  it is one ear pass at the end, not a per-phase dependency — the whole point of
  the pure-translator split.

---

## 9. Acceptance criteria

1. A classic keyboard plugged into a cold-booted appliance plays, with correct
   bend depth, with no setting changed by the user.
2. An MPE controller behaves exactly as it does today — per-note bend, pressure
   remap intact.
3. Both attached at once, both play correctly, simultaneously.
4. Either can be unplugged and replugged in any order with no stuck notes and
   no restart.
5. Surge is never restarted to change MIDI mode.
6. The appliance can tell the user which kind of device it thinks is attached.

---

## 10. Out of scope

Multi-timbral / per-controller patches; MIDI output to external instruments;
MIDI learn for parameter mapping; DIN MIDI hardware; General MIDI program maps.
