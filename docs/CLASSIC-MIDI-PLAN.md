# Classic MIDI instrument compatibility — plan of record

**Status:** phases 0 and 1 complete; phase 3's pure half (classification) done
ahead of phase 2, since it needs no hardware either. Design settled 2026-08-26
against the Surge XT source and the MPE specification (§7); router-hop latency
measured 2026-08-28 and negligible (§7.8). **Next: phase 2**, the router daemon
— which needs a ROLI stream capture for its regression gate.

"Classic MIDI" means a channel-based controller: one MIDI channel for all notes,
pitch bend applied to every sounding note, ±2 semitones by default, no per-note
expression. Ordinary keyboards, hardware synths used as controllers, MIDI out of
a DAW, arpeggiators, drum pads.

The appliance is MPE-only **by construction**, not by configuration. The goal is
that any keyboard plugged into a cold-booted appliance plays correctly, at the
same time as an MPE controller, with no setting for the user to get wrong.

---

## 1. Why it does not work today

Three independent blockers, each in a different file, all load-bearing.

**A. Surge is unconditionally in MPE mode.**
[`start-surge-cli.sh:167`](../scripts/start-surge-cli.sh) launches with
`--mpe-enable --mpe-pitch-bend-range=48` every time, with no branch. A classic
controller's bend is then read against 48 semitones instead of 2 — about 24×
too wide.

**B. Routing depends on what else is plugged in.**
[`start-mpe-pressure-remap.sh`](../scripts/start-mpe-pressure-remap.sh) exits 0
unless `lsusb` shows vendor `2af4`. With a ROLI attached, the remapper writes to
ALSA "Midi Through" and [`start-surge-cli.sh:113`](../scripts/start-surge-cli.sh)
points Surge at **that port only** — a classic keyboard alongside it is silent.
With no ROLI, Surge falls back to `--all-midi-inputs` and the keyboard is heard,
still in MPE mode. So whether a second controller works at all is a function of
what else happens to be connected.

**C. Nothing classifies devices.** `mpe-pressure-remap.py` matches ROLI by USB
vendor ID and ALSA client name. Everything else is unclassified.

---

## 2. Decision

**Generalise `mpe-pressure-remap.py` into a MIDI router that normalises every
input to MPE before Surge sees it.** Surge stays in MPE mode permanently and is
never restarted to change MIDI behaviour.

```
classic keyboard ─┐
                  ├─→ midi-router ─→ ALSA "Midi Through" ─→ Surge (MPE, ±48)
ROLI / MPE ───────┘   classify → translate → pressure remap

APC / control surfaces ──→ looper stack directly (NOT through the router)
```

Surge always reads Midi Through, and the router is always its only writer. That
removes blocker B's dependence on what is attached. The existing ROLI pressure
remap becomes one device profile among several.

**Why not a global MPE/Classic mode switch.** It was the smaller change, and it
is not viable: Surge exposes **no OSC path** to change MPE mode or bend range
(§7.1), so every switch means restarting Surge and dropping audio, and two
controllers of different kinds can never work at once. The router is the only
approach that satisfies §9.

**Kill switch:** `MPE_MIDI_TRANSLATE=0` bypasses translation. An escape hatch,
not a supported mode.

---

## 3. Design

### 3.1 Classification, in priority order
1. **MPE Configuration Message** — RPN 6 on channel 1 or 16 (§7.4). The
   standards-correct signal; needs no device list.
2. **Known-device table** — USB vendor/product plus ALSA client name, for MPE
   devices that never send MCM. This is the current ROLI path.
3. **Default: classic.** An unknown keyboard is far more likely to be ordinary,
   and classic-treated-as-MPE is the failure that sounds broken.

Re-evaluated on **hot-plug**, not only at boot.

### 3.2 Translation, classic → MPE

| Input | Output |
|---|---|
| Note on, ch N | Allocate a member channel; note on there |
| Note off | Release that note's member channel |
| Pitch bend | Scaled `in_range/48`, written to that device's **active member channels**. Never to the master channel (§7.3) |
| Channel pressure | Broadcast to that device's active member channels |
| CC 1 / 11 / 74 | Broadcast — zone-wide for a classic device |
| CC 64 sustain | **Forwarded to the master channel unchanged.** Surge holds a note while either its member channel or the master is held, so zone-wide sustain works natively (§7.7) |
| RPN 0/0 | Update that device's declared bend range; do not forward |
| Program change | **Dropped** (§5, OPEN-1) |
| Clock / transport | Untouched — existing `midi-clock-in.py` path |

Default incoming bend range is **±2 semitones**; most classic controllers never
declare one (§7.5).

### 3.3 Channel allocation
- Member channels 2–16, matching the zone Surge is configured for.
- Round-robin over **least-recently-released**.
- **Do not immediately reuse a just-released channel** — a release tail is still
  sounding on it, and reassigning it re-modulates a note the player has already
  let go (§7.6).
- On exhaustion, steal by an **explicit** policy (oldest by default), never by
  undefined behaviour.
- Voice count is also capped by the poly governor. Decide which layer owns it
  (§6, risk 3) before writing the allocator.

---

## 4. Phases

Each phase ends at a gate. No phase starts before the previous gate passes.

| # | Phase | Deliverable | Gate |
|---|---|---|---|
| ~~0~~ | **Latency spike — DONE** | +0.053 ms p50, +0.115 ms p99; `translate()` 2.87 µs | Passed. [`classic-midi-router-hop-2026-08-28.md`](measurements/classic-midi-router-hop-2026-08-28.md) |
| ~~1~~ | **Pure translator — DONE** | `scripts/midi_translate.py` + 37 tests | Passed. Mutation-checked: un-scaled bend, sustain to member channels, and immediate channel reuse each fail the suite, so it is not passing vacuously |
| ~~2~~ | **Router daemon — MECHANISM DONE, OFF BY DEFAULT** | `scripts/midi_router.py` + per-source dispatch in `mpe-pressure-remap.py`; behind `MPE_ROUTE_CLASSIC` | Gate **partly** passed: MPE path proven byte-identical over all 128 status bytes × 4 floors; port selection with the flag off proven identical to the old daemon. **Not yet passed:** no hardware run, no ear pass |
| 3 | **Classification + hot-plug + display** | ~~MCM detection, device table~~ **done** (`midi_device.py`, 18 tests); still to do: re-classify on plug, read-only device list in the touch UI | Plug/unplug both kinds in any order, 20×; the UI always shows what the router decided |
| 4 | **Boot path + override** | Surge always reads Midi Through; router always runs; manual classification override | Cold boot with: nothing / classic only / MPE only / both |
| 5 | **Ear pass** | Mitch, both controllers | Bend depth correct on both; chords and pedal correct. **This is the ROLI's only regression check** — treat it as blocking, not confirmatory |
| 6 | **Close out** | Measured latency and classification results written to `docs/measurements/` | Numbers recorded, not adjectives |

### Phase 2 gate note — the ROLI regression proof (2026-08-28)

The original gate was "ROLI behaviour unchanged, proven by byte-identical output
on a recorded stream." **There is no such recording and there will not be one
soon:** the LUMI is not a controller Mitch is currently playing (four capture
windows returned zero messages for exactly that reason — not a device fault).

Rather than fake the gate, it is split and its weakness stated:

- **Classic path (APC Notes):** gated by real data — the committed golden stream.
  This is the path being added, so it carries the real risk.
- **ROLI path:** gated **by construction**. The router selects the transform per
  source device; the ROLI source must resolve to `remap_midi_message` with the
  same arguments it uses today, asserted by a unit test on the dispatch table.
  That proves the *routing decision* is unchanged. It does **not** prove the
  audible result is unchanged.

**Accepted risk:** a ROLI regression that lives below the dispatch decision
(fan-out ordering, message batching, timing) would pass phase 2 and only surface
at the phase 5 ear pass. That is why phase 5 is blocking for the ROLI. If the
ROLI comes back into regular use before phase 2 lands, capture a stream and
restore the stronger gate — the capture tool is already written.

Phase 1 holds the bulk of the logic and needs **no hardware** — the translator is
a pure function over MIDI events. That is deliberate: the hard part is testable
in CI, and the ear pass at phase 5 is a single confirmation rather than the
instrument used to debug.

---

## 5. Decisions taken

### OPEN-1: Program Change — **dropped at the router by default**

Nothing in this codebase handles PC today, which is not the same as PC being
harmless: Surge reads Midi Through, so a PC from a classic keyboard reaches it
directly. Either Surge acts on it — changing the patch out from under the patch
browser, whose UI then shows something that is not loaded — or it ignores it.
Both point the same way, and which is true is a five-minute check, not a design
input.

Classic keyboards emit PC casually: at power-up, when their own local preset
changes, from anything sequencing them. A stray PC changing the sound mid-take
is a worse failure than the absence of a feature nobody currently has.

Mapping PC to patch selection is a good feature later, behind a per-device
opt-in, once the browser can accept an external selection without desyncing.
Out of scope here.

### OPEN-2: Device UI — **read-only list in phase 3, override in phase 4**

Settled by §7.2: Surge does **not** filter master-channel notes, so a
mis-classified classic device is not silent — it plays, with bend ~24× too wide.
The appliance would be confidently wrong with no way to see what it thinks is
attached. That is the failure shape this project keeps losing time to: a reading
that looks identical whether it is right or wrong.

So the display ships **with** classification, not after it: device name,
classification, and the bend range in use.

The manual override follows in phase 4, deliberately after classification has
been observed against real devices. Shipping the override first invites working
around a classifier bug instead of fixing it; shipping the display first means a
classifier bug is reported rather than silently endured.

### OPEN-4: Dual-role control surfaces (APC in instrument mode) — **ANSWERED 2026-08-28**

§2's diagram routes the APC around the router, on the assumption that a control
surface is never an instrument. The APC mini mk2 has an instrument/note mode,
which appeared to break that assumption: the same device would be the looper's
control surface *and* a classic MIDI keyboard.

**It does not break it. The APC exposes two separate ALSA ports:**

```
  [2] APC mini mk2:APC mini mk2 Control   32:0
  [3] APC mini mk2:APC mini mk2 Notes     32:1
```

Capture of the Notes port (`scripts/capture-midi-stream.py`, 6.7 s of hand
playing, 2026-08-28):

| property | measured |
|---|---|
| messages | 187 (94 note_on, 93 note_off) |
| kinds | note_on / note_off only — no CC, no bend, no aftertouch |
| channel | 1 only (1-based) |
| notes | 36–96, 31 distinct |
| velocity | **fixed at 127** — the pads are not velocity sensitive |

The Control port's clip-launch grid is notes 0–63; the Notes port is 36–96 on a
different port entirely. **Separation is by port, not by channel or note range** —
the best of the three cases §2 anticipated. No mode flag, no user decision, and
the looper's note-number-based handling on the Control port is untouched.

Consequences for the design:

- The router binds the **Notes** port as a classic source. The **Control** port
  keeps going straight to the looper, exactly as §2's diagram has it.
- Fixed velocity 127 means the APC carries **no continuous expression at the
  source**. The translator's bend/pressure paths are inert for this device; per-note
  expression has to come from elsewhere (the pressure remapper's floor, or a
  second controller). This is a property of the hardware, not a gap in the router.
- The capture is committed as `tests/fixtures/apc-mini-mk2-notes-2026-08-28.jsonl`
  and exercised by `tests/test_midi_golden_apc.py`.

**One hardware behaviour the synthetic tests did not anticipate:** the APC emits
`note_on 59` **twice with no `note_off` between** (a pad double-strike it does not
clean up). The translator's retrigger path handles it by releasing the held note
before re-allocating, which is what MPE requires — but this was found by real
capture, not by design. The golden test now pins it. The fixture is also
deliberately *unbalanced* (note 62 was still held when the window closed); tests
assert the translator's held state mirrors the source's rather than assuming zero.

### OPEN-3: Multi-timbral — out of scope
Two controllers share one Surge patch. Per-controller sounds means multiple
Surge instances. The router's device identity is the hook that work would need.

---

## 6. Risks

1. **Regressing the ROLI path.** It works and it is the primary instrument. It
   must stay a preserved profile with its own tests, and phase 2's gate is
   "unchanged", not "still works".
2. **CPU.** Per-event work in Python on the hot path. Event rates are low
   (hundreds/sec, not thousands) and the daemon already exists, so this is not a
   periodic-subprocess situation — but: no forking, no per-event subprocess, no
   polling faster than the existing `RECONNECT_POLL_S`.
3. **Fighting the poly governor.** Channel allocation and voice limiting both
   cap sounding notes. If the router steals a channel the governor has already
   muted, notes hang. Decide the owner explicitly before writing the allocator.
4. **Stuck notes.** The classic failure of any channel-allocating translator: a
   note-off for a note whose channel was stolen. Needs all-notes-off on device
   unplug and on router restart. Sustain does **not** contribute so long as §3.2
   is followed — CC64 goes to the master channel and Surge owns the hold. A
   router that also deferred note-offs would be a second mechanism racing the
   first.
5. **Silent mis-classification.** Produces "works but sounds wrong". Mitigated by
   the phase-3 display, not by care.

---

## 7. Research findings (2026-08-26)

From the Surge XT source (`surge-synthesizer/surge`, main) and the MPE spec.

**7.1 No OSC control of MPE — confirmed.** `src/surge-xt/osc/OpenSoundControl.cpp`
has no MPE handler. The surface is `/patch`, `/param/…`, `/mod/…`,
`/wavetable/…`, `/tuning/…`, `/pbend` (a bend *value*, not a range), `/doc…`.
MPE mode and bend range are startup flags only. *This is what rules out the
mode-switch design.*

**7.2 Master-channel notes sound — confirmed.** `SurgeSynthesizer::playNote()`
has no channel filter when `mpeEnabled`. `getMpeMainChannel()` concerns which
channel supplies zone-wide expression, not note suppression. *A mis-classified
device plays, wrongly, rather than failing loudly.*

**7.3 Master-channel bend is a dead path — confirmed.**
`--mpe-pitch-bend-range=48` sets the **member** range
(`storage.mpePitchBendRange`). Master bend uses a separate
`mpeGlobalPitchBendRange` which the source's own comment describes as broken
since smoothing was added; channel-0 bend falls through to generic global pitch
modulation. *Bend must be written to member channels.*

**7.4 MCM bytes — confirmed.**
```
Bn 64 06   CC 101 = RPN MSB 0x06
Bn 65 00   CC 100 = RPN LSB 0x00
Bn 06 mm   CC 6   = Data Entry MSB = member channel count
```
`n=0` (ch 1) lower zone, `n=F` (ch 16) upper zone; any other channel invalid.
`mm=0` disables, `mm=1..15` enables with that many member channels. RPN **6**,
distinct from RPN 0/0. The spec additionally has MCM receipt reset master bend
to 2 and member bend to 48 — consistent with Surge's defaults, but attested from
secondary sources rather than the primary PDF.

**7.5 Classic controllers rarely declare bend range — likely.** Bend range is
usually a local keyboard setting, never transmitted. GM convention is to assume
**±2** absent RPN 0/0. Corroborated across secondary sources; not verified
against primary GM spec text.

**7.6 Prior art — MPE Emulator** (attilammagyar.github.io/mpe-emulator), an
existing classic→MPE translator. Read it before writing the allocator. Its
notable decisions: an **explicit** excess-note policy (never / lowest / highest
/ oldest / newest); **deliberate avoidance of immediate channel reuse** so
release tails are not re-modulated; sustain deferral opt-in with immediate
note-off as default; bend range as a router-side setting.

**7.7 Sustain — confirmed.** Surge releases a note only when both its member
channel and the master channel are un-held:
```cpp
bool noHold = !channelState[channel].hold;
if (mpeEnabled) noHold = noHold && !channelState[0].hold;
```
*So forward CC64 to the master channel and do nothing else.*

**7.8 Router-hop latency — measured 2026-08-28, negligible.** +0.053 ms p50,
+0.115 ms p99 on Pi 5 idle; `translate()` itself is 2.87 µs, so the cost is ALSA
transport rather than the translation. About 1% of one 64×2 JACK period and two
orders of magnitude below perceptible. Full result and method:
[`classic-midi-router-hop-2026-08-28.md`](measurements/classic-midi-router-hop-2026-08-28.md).

`measure_midi_osc_latency.py`, which this plan originally named, measures
pad-down → OSC for the bench and is the wrong shape for a forwarding hop;
`scripts/spike-router-hop-latency.py` was written for it instead. Unmeasured:
cost under a live audio graph, and the fan-out case where one input bend becomes
up to 15 output messages.

---

## 8. Test strategy

- **Phase 1 carries the coverage.** The translator is pure: given an input event
  stream and a device profile, assert the exact output stream. Bend scaling,
  allocation, stealing, pedal pass-through and stuck-note recovery are table
  tests with no hardware.
- **Golden streams.** Capture real MIDI from a classic keyboard once
  (`amidi -d`) and replay it in tests forever.
- **ROLI regression.** A recorded ROLI stream must translate to byte-identical
  output before and after phase 2.
- **Hot-plug.** Scripted plug/unplug loops asserting no stuck notes and correct
  re-classification.
- **What tests cannot cover:** whether it sounds right. That is phase 5 — one
  ear pass at the end, which is the point of the pure-translator split.

---

## 9. Acceptance criteria

1. A classic keyboard plugged into a cold-booted appliance plays, with correct
   bend depth, with no setting changed by the user.
2. An MPE controller behaves exactly as it does today — per-note bend, pressure
   remap intact. **Verified by ear at phase 5, not by recorded stream** (see the
   phase 2 gate note).
3. Both attached at once, both correct, simultaneously.
4. Either unplugged and replugged in any order: no stuck notes, no restart.
5. Surge is never restarted to change MIDI mode.
6. The appliance shows which kind of device it thinks is attached.

---

## 10. Out of scope

Multi-timbral / per-controller patches; MIDI output to external instruments;
MIDI learn; DIN MIDI hardware; General MIDI program maps; Program Change to
patch selection.
