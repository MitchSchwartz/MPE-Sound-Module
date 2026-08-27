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
| Pitch bend, ch N | Same bend, **scaled** `in_range/48`, to every active member channel of that device. **Never to the master channel** — see §5.3 |
| Channel pressure | Broadcast to that device's active member channels |
| CC 1 / 11 / 74 | Broadcast (they are zone-wide for a classic device) |
| CC 64 sustain | **Forward to the master channel unchanged.** Surge already holds a note while either its member channel or the master channel is held, so zone-wide sustain works natively — the router must NOT own this (§5.7) |
| RPN 0/0 (bend range) | Update that device's declared range; do not forward |
| Program change | **Filtered by default** — not forwarded to Surge (§7, OPEN-1) |
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
| 0 | **Spike** (§5.8) | Router-hop latency measured | Measured, not estimated. The other three spikes are answered — see §5 |
| 1 | **Pure translator** | `midi_translate.py` — events in, events out, no I/O | Unit tests pass; no hardware needed |
| 2 | **Router daemon** | Generalise `mpe-pressure-remap.py`; ROLI profile preserved | ROLI still behaves exactly as today (regression gate) |
| 3 | **Classification + hot-plug + display** | MCM detection, device table, re-classify on plug, read-only device list in the UI | Plug/unplug both kinds in any order, 20×; UI always shows what the router decided |
| 4 | **Boot path** | Surge always reads Midi Through; router always runs | Cold boot with: no controller / classic only / MPE only / both |
| 5 | **Ear pass** | Mitch, both controllers | Bend depth correct on both; chords and pedal correct |
| 6 | **Docs + measurement** | Update this doc with measured latency; note in `docs/` | Latency delta recorded, not estimated |

Phase 1 is the bulk of the logic and needs **no Pi and no hardware** — the
translator is a pure function over MIDI events. That is deliberate: it means
the hard part is testable in CI and by ear only once.

---

## 5. Research findings (2026-08-26) and remaining spikes

Sourced from the Surge XT source (`surge-synthesizer/surge`, main) and the MPE
specification. Three of the four original spikes are answered; one remains.

### 5.1 No OSC path to change MPE mode or bend range — **ANSWERED: no**
`src/surge-xt/osc/OpenSoundControl.cpp` contains no `mpe` handler at all. The
OSC surface is `/patch`, `/param/…`, `/mod/…`, `/wavetable/…`, `/tuning/…`,
`/pbend` (a bend *value*, not a range), and `/doc…`. MPE mode and MPE bend
range are startup flags only.

**Consequence:** Option 1 (global mode switch) cannot avoid a Surge restart,
and therefore cannot avoid dropping audio. This is now the decisive argument
for Option 2 rather than a preference.

### 5.2 Master-channel notes DO sound — **ANSWERED: they play**
`SurgeSynthesizer::playNote()` has no channel filter when `mpeEnabled` is true;
it plays any channel including the master. `getMpeMainChannel()` concerns which
channel supplies zone-wide expression, not note suppression.

**Consequence:** a mis-classified classic device is **not silent** — it plays,
with the wrong bend depth. That is the "works but sounds wrong" failure shape,
and it is invisible without a display. See OPEN-2.

### 5.3 Master-channel pitch bend is a dead path in Surge — **NEW CONSTRAINT**
`--mpe-pitch-bend-range=48` sets the **member-channel** range
(`storage.mpePitchBendRange`). Master-channel bend uses a separate
`mpeGlobalPitchBendRange`, which the source's own comment describes as broken
since smoothing was added; channel-0 bend falls through to the generic global
pitch modulation path instead.

**Consequence:** the router must never translate a classic device's bend onto
the master channel and expect the configured range to apply. Bend must be
written to that device's active member channels. This was already the plan's
intent; it is now a hard requirement rather than a stylistic choice.

### 5.4 MCM bytes — **CONFIRMED**
```
Bn 64 06   CC 101 = RPN MSB 0x06
Bn 65 00   CC 100 = RPN LSB 0x00
Bn 06 mm   CC 6   = Data Entry MSB = member channel count
```
`n = 0` (channel 1) = lower zone; `n = F` (channel 16) = upper zone; any other
channel is invalid. `mm = 0` disables the zone; `mm = 1..15` enables it with
that many member channels. RPN **6**, distinct from RPN 0/0 (bend sensitivity).
On MCM receipt the spec has the receiver default master bend to 2 semitones and
member bend to 48 — consistent with Surge's defaulting, though that sub-detail
is attested from secondary sources rather than the primary PDF.

### 5.5 Classic controllers and RPN 0/0 — **ANSWERED: usually not sent**
Bend range is typically a local setting on the keyboard and never transmitted.
The GM convention is to assume **±2 semitones** absent an RPN 0/0. So the
router must default to ±2 and treat a received RPN 0/0 as a correction, and a
per-device override is worth having for keyboards set to something else with no
way to announce it.

### 5.6 Prior art — **MPE Emulator** (attilammagyar.github.io/mpe-emulator)
An existing open-source classic→MPE translator. Worth reading before writing
allocation logic. Its documented design decisions:
- Configurable zone (lower/upper) and member-channel count.
- **Explicit** excess-note policy: never / steal lowest / highest / oldest /
  newest. Not undefined behaviour.
- **Deliberately avoids immediate reuse of a just-released channel**, so a
  still-decaying release tail is not pitch-bent or re-modulated by the next
  note. This is a real pitfall and directly informs §3's allocation rule.
- Sustain is opt-in deferral of note-off, with immediate note-off the default.
- Bend range is a router-side setting, not assumed from either end.

### 5.7 Sustain in Surge — **ANSWERED: handle it on the master channel**
Surge releases a note only when *both* its member channel and the master
channel are un-held:
```cpp
bool noHold = !channelState[channel].hold;
if (mpeEnabled) noHold = noHold && !channelState[0].hold;
```
So CC64 on the master channel holds the whole zone natively, and CC64 on a
member channel additionally holds that channel.

**Consequence:** the router should forward CC64 to the master channel and do
nothing else. The original plan had the router owning sustain and deferring
note-offs itself — that would duplicate logic Surge already implements
correctly and is the kind of second mechanism that produces stuck notes. Drop
it.

### 5.8 Still open — latency of the router hop
Unmeasured, and the one spike research cannot answer. There is a production
precedent (the ROLI path already traverses a Python daemon), so the cost is
probably acceptable — but it must be measured with
`scripts/sooperlooper/measure_midi_osc_latency.py`, not assumed. Phase 0.

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
   safety on device unplug and on router restart. Note that sustain is NOT a
   contributor here as long as §5.7 is followed — CC64 goes to the master
   channel and Surge owns the hold. A router that also deferred note-offs would
   be a second mechanism racing the first.
5. **Silent mis-classification.** A device classified wrongly produces "works
   but sounds wrong", which is the failure shape this project keeps getting
   caught by. Classification must be **visible** — logged, and surfaced in the
   touch UI, so the user can see what the appliance thinks is attached.

---

## 7. Decisions

### OPEN-1: Program Change — **RECOMMEND: filter at the router by default**

Nothing in this codebase handles Program Change today. That is not the same as
PC being harmless: Surge reads Midi Through, so a PC from a classic keyboard
reaches Surge directly. Either Surge acts on it — changing the patch out from
under the patch browser, whose UI then shows something that is not loaded — or
it ignores it. Both outcomes argue for the same thing, and which one is true is
a five-minute check rather than a design input.

Classic keyboards emit PC casually: on power-up, on preset changes made for the
keyboard's own local sound, from a connected sequencer. A stray PC mid-take
changing the sound is a much worse failure than losing a feature nobody has
today.

Recommendation: **the router drops Program Change by default.** Mapping PC to
patch-browser selection is a genuinely nice feature, but it belongs behind a
per-device opt-in, after the browser can accept an external selection without
desyncing. Not in this plan's scope.

### OPEN-2: Device UI — **RECOMMEND: read-only display now, override next**

§5.2 settles this. A mis-classified classic device is not silent — it plays,
with bend roughly 24× too wide. The appliance would be doing something
confidently wrong with no way to see what it thinks is attached. That is the
exact failure shape this project keeps losing days to: a reading that looks the
same whether it is right or wrong.

Recommendation: **a read-only MIDI devices list in the touch browser** —
device name, classification (MPE / Classic), and the bend range in use — landed
in phase 3 alongside classification itself, not deferred. Cheap, and it makes
the invisible visible.

A **manual override** (force this device to Classic/MPE) should follow in phase
4, once classification has been observed against real devices. Shipping the
override first would invite working around a classifier bug instead of fixing
it; shipping the display first means any classifier bug is *reported* rather
than silently endured.

### OPEN-3: Multi-timbral — unchanged, out of scope
Two controllers currently share one Surge patch. Separate sounds per controller
means multiple Surge instances and is a much larger change. The router's device
identity is the hook it would eventually need.

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
