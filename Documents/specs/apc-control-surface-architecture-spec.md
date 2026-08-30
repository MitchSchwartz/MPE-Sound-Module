# APC control surface: one map, one owner, one writer

Status: Proposal — 2026-08-29
Author: bench session, at Mitch's request after three consecutive LED changes
went wrong in three different ways.

## 1. The symptom, stated as Mitch reported it

> "It feels like functions that either hard code buttons, or buttons that are
> not abstracted and are being directly referenced. It feels like not good
> fundamentals — because otherwise these should be really obvious changes to
> make, but this is turning out to be really hard and total whack-a-mole,
> which is almost always the sign of bad code structure."

That reading is correct. Three requests — stop lighting track 8 on Shift, blink
red on clear-all, show a colour when a scene row is fully playing — took two
deploys, produced one regression (the Stop All blink disappeared entirely), and
still leave an unanswered question (why Shift itself does not light). None of
those are hard problems. The difficulty came from the structure, and the
structure is diagnosable.

## 2. Diagnosis — four specific defects, each of which produced a bug this week

**D1. Note ownership is declared but not enforced.**
`apc_panel.py` opens with Rule 2: *"No other module defines a note number. They
import from here."* `apc_transport.py` then defines `NOTE_TRACK8_MK2 = 0x6B`,
`NOTE_TRACK8_MK1 = 0x37`, and `MK1_TRACK_OVERLAP_NOTES`. The file created to
end this class of bug is already bypassed, because a rule in a docstring cannot
fail a build.

**D2. No control has an owner for its light.**
Five modules write LEDs independently: `slot_surface`, `apc_transport`,
`track_gesture`, `apc_link`, `sooperlooper-apc-bench`. Concretely,
`TransportButtonLeds.clear_unwired_surfaces()` blanket-darkens every scene note
while `SlotSurface.repaint_scenes()` paints those same notes. Two writers, one
LED, and which one you see depends on call order in the event loop. There is no
place to look to find out who owns a button.

**D3. Hardware capability lives in prose, not in code.**
That scene-launch buttons are single-colour green and track-select single-colour
red is a *comment* in `led_table.py`. Nothing checks it. So a change promising
yellow scene buttons and a red Stop All passed 1575 tests and shipped, because a
velocity is just an int. This is the same failure shape as the peak meter: a
value that looks identical whether it is right or meaningless.

**D4. One button's behaviour is spread across four unrelated files.**
For the scene button alone: press handling in `apc_panel.scene_press_row`,
colour policy in `slot_matrix.scene_row_led`, painting in
`slot_surface.repaint_scenes`, blanket-darkening in `apc_transport`. Nothing
references anything else. "What does this button do, and what does it show?"
has no answer short of reading four files and simulating the event loop.

D1 and D4 are why changes are hard to find. D2 is why they don't stick. D3 is
why some of them were impossible from the start and nothing said so.

## 3. The deeper defect: facts about the device have no home

Section 2 is about code structure. It is not the whole problem, and on
2026-08-29 Mitch named the rest of it:

> "All buttons have LED. Before the set of changes you made, maybe two or three
> rounds ago, I even specified that. This is the point. We have a very poor
> mental model of each one of these, at least you do."

He is right, and the history is checkable. `git log -S "Shift has no LED"` puts
the claim's origin at 618a19e. It came from a vendor protocol document. It was
never measured. It was copied into five docstrings, each of which reads as
established fact to the next reader. On 2026-08-29 I used it to tell Mitch that
what he was asking for was physically impossible — twice — and then propagated
it a sixth time in 568f459.

He had told me the opposite, in conversation, several sessions earlier. That
statement lived in a chat log and nowhere else. The wrong fact was in the repo,
in five files, being read aloud. **The repo outranked the man holding the
device, because the repo was written down and he was not.**

No amount of registry or compositor fixes that. A perfectly-structured control
surface built on an unmeasured claim is wrong faster and more consistently.

### 3.1 The fact base — `scripts/sooperlooper/device_facts.py`

Every fact about the hardware gets recorded once, with **how it was
established**, in tiers:

    MEASURED   observed on this appliance, with a date
    OWNER      Mitch stated it about his own hardware
    VENDOR     read from a manufacturer document
    INFERRED   reasoned from another fact

Five rules, each one written against a specific way this went wrong:

1. **Recorded once.** Other modules cite the fact id in a comment; they do not
   restate the claim. Five restatements is how a single unverified sentence
   became load-bearing in five places.
2. **Provenance is mandatory.** A fact with no tier and no date is not a fact.
3. **MEASURED and OWNER outrank VENDOR and INFERRED, always.** When they
   conflict, the lower tier is wrong until re-measured. It does not get to
   stand because it is written more confidently or more recently.
4. **A VENDOR or INFERRED fact may never be used to tell Mitch something is
   impossible.** "The document says green-only" is not "your device cannot do
   this." If a claim matters enough to refuse a request over, it matters enough
   to measure first. `Fact.refuse_with()` raises if this is attempted, so the
   rule is executable rather than aspirational.
5. **A contradiction is an event.** When an observation disagrees with a
   recorded fact, the old claim is superseded in place with both dates and the
   reason visible. The history of having been wrong is the useful part — it is
   what tells the next reader which sources to distrust.

### 3.2 Capture discipline — the part that actually failed

Rules 1-5 govern facts already in the file. The failure was upstream of that:
Mitch stated a fact and nobody wrote it down.

**When Mitch states something about his own hardware, it is recorded in
`device_facts.py` in the same session, at OWNER tier, before the work
continues.** Not at the end, not "if it turns out to matter." A fact stated in
conversation and not committed is gone at the next context boundary, and what
survives is whatever the repo happens to say — which may be wrong.

This is the cheapest rule here and the one that would have prevented the whole
week: one commit, three lines, at the moment he said it.

### 3.3 Current state of knowledge

| Control | Notes | LED | Tier |
|---|---|---|---|
| 8x8 grid | 0x00-0x3F | mk2 RGB: channel = brightness, velocity = palette | MEASURED 2026-08-28 |
| mk1 scene column | 0x52 top .. 0x59 bottom | — | MEASURED 2026-08-27 |
| All buttons | — | every button has an LED, Shift included | OWNER 2026-08-29 |
| Scene launch colours | mk1 0x52-0x59, mk2 0x70-0x77 | ~~UNKNOWN~~ **green only; off/on/blink** | ~~VENDOR~~ **MEASURED 2026-08-29** |
| Track select colours | 0x64-0x6B | ~~UNKNOWN~~ **red only; off/on/blink** | ~~VENDOR~~ **MEASURED 2026-08-29** |
| Bank arrow notes | mk1 0x40-0x43 recall; **mk2 unknown** | — | VENDOR — see `apc.bank_arrows.notes` |
| Fader CCs | 48-55 + 56 | — | VENDOR — see `apc.faders.ccs` |

**Superseded in place, 2026-08-30** (rule 5 — the old claim stays visible). The
two UNKNOWN rows were what `scripts/probe-apc-buttons.py` existed to settle. It
ran on 2026-08-29: five rounds, with a positive control on the grid pads, read
off the device by Mitch. The answer went the way the vendor document said, and
it is now ours: `apc.scene.led_observed`, `apc.track.led_observed`,
`apc.buttons.channel_response` and `apc.buttons.single_colour` — the last
CLOSED as a bounded negative. Channel axis exhausted, velocity swept, SysEx RGB
rejected. **All colour-carrying UI must live on the 8x8 grid**, and we may now
say so on authoritative grounds.

The last two rows are new, and they are the questions this table did not know
it had: identity claims that never made it into `device_facts` at all, and so
stayed load-bearing for shipped features without ever passing rule 4. Recorded
2026-08-30 at VENDOR tier with a resolution path. Migration stage 0 is done for
colours and still open for these two.

## 4. Prior art

**SooperLooper itself** (`src/midi_bind.hpp`, essej/sooperlooper). A binding is
a **data row**, not code: `MidiBindInfo` carries `channel, type, command,
control, param, instance, lbound, ubound, data_min, data_max, style`, with
`serialize()`/`unserialize()`, held in `MidiBindings` as a map keyed by
`(chcmd << 8) | param`, loadable and savable as a `.slb` file. Adding a control
is adding a row. Notably SL's bindings are **input only** — there is no feedback
side. That is exactly the half we keep getting wrong, so SL is a good model for
the input half and no help at all for the output half.

**Ableton's control surface framework.** `ControlElement` / `ButtonElement`
abstract one physical control; `ControlSurfaceComponent` subclasses (Transport,
Session, Mixer) **claim** controls via setters — `transport.set_play_button(...)`
— and the component that owns a control is the only thing that drives its
light. Modes reassign ownership rather than adding conditionals at each write
site. The ownership model is the part worth stealing: it is precisely D2.

**Bitwig's controller API.** `HardwareSurface` declares hardware elements once;
a light is bound to a **state supplier**, and the framework diffs and flushes to
the device. Scripts never send LED bytes. That is D2 solved at the write level,
and it also gives free diffing — which we currently reimplement by hand in at
least two places (`_last_vel`, `_scene_painted`).

Common shape across all three: **the map is data, the behaviour is attached to
the map, and exactly one thing talks to the wire.**

## 5. Proposal

### 5.1 One control registry (data)

One module. Every physical control appears once:

```python
Control(
    id="scene_launch_4",
    kind=SCENE,
    notes={"mk1": 0x55, "mk2": 0x73},
    led=Led(colours=(GREEN,), modes=(OFF, ON, BLINK),
            established="akai protocol v1.0, unverified on device"),
    owner="slot_surface",          # exactly one, checked at import
)
```

Rule 2 stops being a docstring and becomes an import-time assertion plus a test:
no note literal outside this module.

### 5.2 One function table (behaviour)

The binding half, SooperLooper-shaped: rows, not conditionals.

```python
Binding(control="stop_all", layer=SHIFT, action="stop_all_clips")
Binding(control="stop_all", layer=SHIFT, hold_s=3.0, action="clear_all_clips")
Binding(control="stop_all", layer=BASE, action="scene_launch_row_0")
```

"What does this button do?" becomes a table lookup. Today it is a search.

### 5.3 One LED compositor (the wire)

Nothing outside the compositor may send a button LED byte. Owners submit
*desired state* for controls they own; the compositor resolves by declared
priority, diffs against what the device was last told, and flushes. This kills
the `clear_unwired_surfaces` vs `repaint_scenes` race by construction: two
writers cannot exist.

The 8x8 grid keeps `apc_leds.translate` as its encoder — that layer is already
correct — and moves behind the same compositor so there is one flush point.

### 5.4 Capability enforced at the boundary

Requesting a colour a control cannot physically show is an error, raised where
it is requested. Then "make the scene button yellow" fails in the test suite in
seconds, instead of surviving a green suite, a deploy, and a listening session
before Mitch says "no, that's worse."

### 5.5 Invariant tests

Three, all cheap, each pinning a defect above:
- no note literal defined outside the registry (D1)
- no button LED write that does not go through the compositor (D2, D4)
- every colour request is within its control's declared capability (D3)

## 6. Migration — each stage shippable, none a big-bang rewrite

0. **Fact base + capability probe.** `device_facts.py` exists (2026-08-29).
   Run `scripts/probe-apc-buttons.py` with the session stopped, record what
   the buttons actually do at MEASURED tier, and correct the code that has been
   asserting otherwise. *Everything after this depends on it being true.*
1. **Registry, data only.** Move every note constant in, including the three in
   `apc_transport`. Add the "no notes outside" test. No behaviour changes.
2. **Compositor.** Route existing writers through it unchanged; add the
   "no writes outside" test. Delete `_last_vel` and `_scene_painted` in favour
   of one diff. First stage that can regress behaviour — the 1575-test suite
   plus a device pass is the gate.
3. **Ownership.** Assign each control exactly one owner; delete
   `clear_unwired_surfaces`, whose whole job was cleaning up after the conflict.
4. **Binding table.** Move press/hold/shift-layer routing into rows.
   **Done 2026-08-30** — `scripts/sooperlooper/binding_table.py`. One
   correction to §5.2 while it was being built: the example rows there are a
   *sequence* (`Binding(control="stop_all", layer=SHIFT, ...)` twice over), and
   a sequence needs a match order, which is the defect wearing a dataclass. The
   shipped table is a **mapping** keyed by `(control, mode, layer, gesture)`
   with collisions refused at import, so the shadowing §5.2 would have
   permitted is not expressible. Two fields the spec did not anticipate: `mode`
   (`MPE_SL_MULTIGRID` genuinely rebinds all 64 pads, and modelling one
   configuration would have described one nobody runs) and `fired_by` (a row
   the router executes and a row that only describes a poller elsewhere are
   different things, and conflating them is how a table becomes prose).
5. **Grid.** Move the 8x8 behind the same compositor. Last, because it is the
   part that currently works.

Stages 0-3 fix everything reported this week. 4-5 are what stop it recurring.

## 7. Honest costs

- It adds no features. Every hour is spent on making future hours cheap.
- Stages 2-3 can break working behaviour. The suite is a good harness for
  logic, and a poor one for "what does the panel look like" — the capability
  probe and a device pass per stage are the real gate.
- It will surface more hardware facts we have been guessing. That is a benefit
  that will feel like a cost, because some of them will be "you cannot have
  what you asked for."

## 8. What this does not fix

Whether a given cue is *good UI* — blink versus colour versus grid — is a
judgement call this architecture makes cheap to change, not one it makes for
you. The value is that trying three options costs three one-line edits instead
of three deploys.
