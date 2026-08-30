# Grumpy review — control identity: note numbers, variant resolution, capability claims

**Branch:** `refactor/looper-ownership-2026-08-30` @ `41d8541`
**Dimension:** control identity — note numbers, variant resolution, hardware capability claims
**Reviewer:** fresh-context adversarial pass, read-only. No product code or test was modified.
**Date:** 2026-08-30
**Governing:** `Documents/reviews/CHARTER-looper-ownership-2026-08-30.md`

Files read in full: `apc_panel.py`, `apc_transport.py`, `apc_grid.py`, `apc_mode.py`,
`apc_faders.py`, `apc_leds.py`, `apc_link.py`, `led_table.py`, `device_facts.py`,
`midi_subscription.py`, `slot_leds.py`, `scripts/probe-apc-buttons.py`,
`scripts/sooperlooper-apc-bench.py`, `scripts/sooperlooper/README.md`. Read in part:
`slot_surface.py`, `slot_matrix.py`, `track_gesture.py`, `tests/test_apc_transport.py`,
`tests/test_scene_row_mapping.py`.

Suite state at review time: `test_apc*` 107 tests OK, `test_scene_row_mapping` 19 tests OK.
**126 green tests while the bank arrows are structurally dead on the attached hardware.**
That number is the finding, not the reassurance.

---

## Verdict, up front

`apc_panel.py` is **mostly holding the line, and the exceptions are worse than the spec
says.** The file itself is excellent: measured facts, the vertical-flip trap written down
once, import-time asserts that actually run (lines 82–85), and pure functions instead of
call-site arithmetic. Every module that consumes it does import from it rather than
re-deriving — `apc_transport` genuinely does route `scene_row_for_note`,
`scene_launch_index_to_row` and `scene_row_to_launch_index` straight through to `apc_panel`
instead of re-implementing them. `track_gesture` uses `pad_note()` everywhere. That is real
discipline and it should be said plainly.

What breaks is the boundary. The spec (D1) names **three** note literals outside
`apc_panel`. There are **seven named note-defining constants outside it, covering nineteen
distinct note numbers**, plus a hardcoded range in the probe and a duplicated note formula
in `slot_surface`. Two of the constants the spec missed — `ARROW_NOTES_MK2` and
`ARROW_NOTES_MK1` — are not merely "defined in the wrong file". `ARROW_NOTES_MK2` **collides
head-on with the mk2 scene column**, and the collision has silently disabled the entire
banking layer on the device that is plugged in right now.

The headline: **on the attached APC mini mk2, the Up/Down/Left/Right bank buttons do
nothing, and tracks 9–15 cannot be reached from the surface.** The banner printed at
startup claims otherwise. Nothing in 126 green tests notices.

---

## Findings by severity

### 🔴 P0-1 — `ARROW_NOTES_MK2` collides with the mk2 scene column; banking is dead on the attached hardware

`apc_panel.py:78` declares the mk2 scene column:

```python
SCENE_COLUMN_MK2: tuple[int, ...] = tuple(range(0x70, 0x78))   # 0x70..0x77
```

`apc_transport.py:94` independently declares the mk2 arrows:

```python
ARROW_NOTES_MK2 = (0x70, 0x71, 0x72, 0x73)  # up, down, left, right
```

`0x70`–`0x73` are the same four physical buttons in both claims. Executed:

```
mk2 arrow notes : {'0x70': 'up', '0x71': 'down', '0x72': 'left', '0x73': 'right'}
mk2 scene notes : ['0x70','0x71','0x72','0x73','0x74','0x75','0x76','0x77']
collision       : ['0x70', '0x71', '0x72', '0x73']
  note 0x70 (up)    -> scene_press_row = 7   => arrow branch reachable? False
  note 0x71 (down)  -> scene_press_row = 6   => arrow branch reachable? False
  note 0x72 (left)  -> scene_press_row = 5   => arrow branch reachable? False
  note 0x73 (right) -> scene_press_row = 4   => arrow branch reachable? False
```

The bench event loop resolves the collision by position, not by policy. The scene branch is
first, at `sooperlooper-apc-bench.py:676–693`, and it `continue`s:

```python
        scene_row = (
            scene_press_row(n, scene_notes=scene_launch_notes,
                            apc_label=apc_label, shift_held=routing_shift)
            if down is not None else None
        )
        if scene_row is not None:
            ...
            continue
```

`handle_arrow` is not called until `sooperlooper-apc-bench.py:722`, forty-five lines later
and after three other `continue`s. It is unreachable for `0x70`–`0x73` on mk2.

Held-Shift does not rescue it: `is_stop_all('mk2', 0x72)` is False (stop-all is `0x77`), so
`scene_press_row` falls straight through to `row_for_scene_note` and returns row 5. The
documented **Shift + Left/Right nudge** is consumed by the scene launcher too.

`handle_arrow` at `:424–431` is the **only** caller of `set_view` in the bench (verified by
grep across the repo). So on mk2:

- the viewport is frozen at `offset = 0` for the life of the session;
- tracks 9–15 of 15 are unreachable from the surface;
- `bank_delta_for_arrow`, `PAGE_STEP`, `NUDGE_STEP`, `GridView.scrolled` and
  `MAX_VIEW_OFFSET` are all dead code in production;
- the startup banner at `:450–457` prints `bottom row -> 8 of 15 tracks (Up/Down page 8,
  Shift+Left/Right nudge 1)` — a complete, correct-looking line over a dead feature. That
  is the appliance's signature defect shape, per `AGENTS.md` and `apc_link.py`: *a reading
  identical whether it is working or broken.*

When multigrid is off (`MPE_SL_MULTIGRID=0`, the default) the press is not even logged —
`slot_surface` is `None`, so the branch falls to `elif args.dump_midi` and vanishes.

**And a test locks it in.** `tests/test_apc_transport.py:154–169`:

```python
class ArrowBankingTests(unittest.TestCase):
    def test_variant_resolution_matches_the_transport_notes_path(self) -> None:
        mk2 = resolve_arrow_notes("APC mini mk2 MIDI 1")
        self.assertEqual(sorted(mk2), sorted(ARROW_NOTES_MK2))
```

It asserts the resolver returns the colliding tuple, and it never compares that tuple
against `SCENE_COLUMN_MK2`. This is exactly the charter §2 case: *"A test whose failure
would not have caught the bug it is named for is a test that needs rewriting, even if it
passes today."* The test is named for arrow banking and would pass if arrow banking were
deleted entirely.

**Which claim is wrong is unmeasured and must not be guessed.** `SCENE_COLUMN_MK2` is
consistent with `apc_panel`'s asserts and with `NOTE_STOP_ALL_CLIPS_MK2 = 0x77`;
`ARROW_NOTES_MK2` is flagged UNVERIFIED in its own comment (`apc_transport.py:86–92`) and in
`README.md:64–68`. Rule 3 says measure, do not reason. But the collision itself is a
structural fact that needs no hardware: **two controls cannot share a note in one variant,
and nothing in the build says so.**

Fix direction: the registry rejects a duplicate `(variant, note)` at import; until the
arrows are measured with `--dump-midi`, `resolve_arrow_notes` returns `{}` for mk2 and the
bench logs "bank arrows unmapped on mk2 — see README" once at startup, so the failure is
loud instead of silent.

---

### 🔴 P0-2 — The output port is opened with an index taken from the *input* port list

`sooperlooper-apc-bench.py:134–151`:

```python
    midi_in = rtmidi.MidiIn()
    midi_out = rtmidi.MidiOut()
    ports_in = midi_in.get_ports()
    idx = next((i for i, n in enumerate(ports_in) if port_hint.lower() in n.lower()), None)
    ...
    midi_in.open_port(idx)
    ...
    midi_out.open_port(idx)
    port_name = ports_in[idx]
```

`midi_out.get_ports()` is **never called anywhere in the bench** (verified by grep across
`scripts/`). `idx` is resolved against `MidiIn`'s port list and then used to open `MidiOut`.
rtmidi builds those two lists from different ALSA capability filters
(`SND_SEQ_PORT_CAP_READ` vs `_WRITE`), so they are not the same list and their indices are
not guaranteed to correspond. The coordinator's live capture shows both lists also contain
`Midi Through`, `Scarlett 4i4 USB`, `LUMI Keys BLOCK`, `mpe-looper:sooperlooper` and
several `RtMidiIn/Out Client` entries — and an `RtMidiIn Client` appears in the *output*
enumeration while an `RtMidiOut Client` appears in the *input* one. Any asymmetry ahead of
the APC entries shifts the two lists relative to each other.

The reopen path does it again, `:487–495`:

```python
            ports = midi_in.get_ports()
            new_idx = next((i for i, n in enumerate(ports) if port_hint.lower() in n.lower()), None)
            ...
            midi_in.open_port(new_idx)
            raw_midi_out.open_port(new_idx)
```

This one matters more, because re-enumeration is a **measured, frequent** event on this
appliance — `apc_link.py:5–13` records the device number climbing to 24 in one morning and
four session starts in six leaving the pads dead. Every recovery re-rolls the dice.

By contrast `scripts/probe-apc-buttons.py:86–92` gets this right — it resolves against
`midi_out.get_ports()`, its own list. Two scripts, two different answers to "which port is
the APC".

Failure mode if the indices diverge: pad presses arrive normally (input is correct) and LED
writes go to another device — `Midi Through`, or worse, the Scarlett or the LUMI. Silent.
`has_writer` would not catch it (see P1-1).

I have **not** verified that the indices currently diverge on the Pi; I could not, from
here. That is the point — the code is correct only by coincidence, and nothing measures the
coincidence. Fix direction: resolve the output index from `midi_out.get_ports()` and assert
the two resolved names are equal, refusing to start if not — the same shape as the existing
`wait_for_subscription` refusal at `:163–177`, which is the right instinct already present
in this file.

---

### 🔴 P0-3 — Port selection cannot tell Control from Notes, and neither can the health check

`sooperlooper-apc-bench.py:107` and `:135`:

```python
    port_hint = os.environ.get("MPE_APC_MIDI_PORT", "APC")
    idx = next((i for i, n in enumerate(ports_in) if port_hint.lower() in n.lower()), None)
```

The live device presents **two** ports under one client:

```
'APC mini mk2:APC mini mk2 Control 28:0'
'APC mini mk2:APC mini mk2 Notes 28:1'
```

Both contain `apc`. `next()` takes whichever enumerates first. Nothing in the codebase
contains the string `Control` or `Notes` as a port discriminator — grep confirms. The
correct port is selected **by ALSA enumeration order alone**, and the `28:0` / `28:1`
suffixes are not stable across reboot or hotplug.

This is not hypothetical for this device. `apc_mode.py:3–11` exists *because* Notes mode
"moves the pads to a separate ALSA port", and that misdiagnosis already cost a debugging
session on 2026-08-28. If the bench ever opens `Notes` instead of `Control`, the grid is
dead in normal mode and alive in Notes mode — the exact inverse of what
`grid_silent_reason()` tells Mitch to do, so the advice on screen would be actively wrong.

**The health check cannot detect it either.** `midi_subscription.py:47–63` matches at
*client* level and accumulates across every port of that client:

```python
        client = _CLIENT_RE.match(line)
        if client:
            in_device = device_substring.lower() in client.group(2).lower()
            continue
        if not in_device:
            continue
        if _CONNECTING_RE.match(line):
            has_reader = True
```

`device_key = port_name.split(":")[0]` (`:159`) is `"APC mini mk2"` for **both** ports. So a
subscription to the Notes port satisfies `has_reader` for the Control port. The module
written specifically to end "a reading identical whether it is working or broken" has that
bug inside it, one level down.

Two smaller defects in the same function: its docstring says *"for the first client matching
the name"* but the loop never breaks, so it is "any client matching" — and `port_subscriptions`
returns `(True, True)` when `/proc/asound/seq/clients` is unreadable (`:44–46`), which is
defensible on a laptop and is an in-band failure on the Pi.

Fix direction: the port identity is `(client name, port name)`, not a substring of a
concatenation. Match `Control` explicitly, verify subscription on the specific port index,
and make the bench print the port it chose and the port it rejected.

---

### 🟡 P1-1 — Four independent derivations of "which APC is this", and one path that produces a label no consumer understands

**The alphabet is inconsistent.** Two families of function take a variant, and they accept
different vocabularies:

| Family | Accepts | Sites |
|---|---|---|
| `resolve_*(port_name, *, variant=...)` | `mk2 / mkii / 2` and `mk1 / 1 / original / mini`, then port-name sniff | `apc_transport.py:109–115`, `:149–156`, `apc_faders.py:55–62` |
| `f(apc_label)` | **exactly** `"mk2"`, everything else falls through to mk1 | `apc_panel.py:90`, `:131`, `apc_transport.py:178`, `:185`, `apc_leds.py:95`, `apc_transport.py:434`, `:445`, `:476` |

The first family normalises on the way out (`return NOTE_SHIFT_MK2, ..., "mk2"`), which
saves this in the normal path. The exception is `sooperlooper-apc-bench.py:184–190`:

```python
    if shift_note <= 0 or stop_all_note <= 0:
        shift_note, stop_all_note, apc_label = resolve_apc_transport_notes(
            port_name, variant=apc_variant
        )
    else:
        apc_label = apc_variant or "env"
```

Set `MPE_APC_SHIFT_NOTE` and `MPE_APC_STOP_ALL_NOTE` and the resolver is skipped entirely;
`apc_label` becomes the **raw env string** — `"env"`, or `"mkii"`, or `"MK2"`. Every
consumer in the second family compares `== "mk2"` and therefore silently answers *mk1*:

- `midi_out.apc_label = apc_label` (`:193`) → `apc_leds.translate` returns identity
  (`apc_leds.py:95`) → mk1 velocities onto mk2 RGB pads → this is precisely the
  2026-08-28 regression *"pads barely light up, and I'm seeing blue"*, restored;
- `resolve_scene_launch_notes("env")` → `SCENE_COLUMN_MK1` = `0x52..0x59`, notes the mk2
  does not send;
- `resolve_stale_lamp_note("env")` → `None`, so `0x6B` is never cleared;
- `Mk1ShiftGhostFilter` is not constructed (`:275` tests `== "mk1"`), so neither variant's
  path runs;
- meanwhile `resolve_arrow_notes(port_name, ...)` (`:397`) and `resolve_fader_ccs(port_name,
  ...)` (`:334`) re-derive from **`port_name`**, not from `apc_label`, and would correctly
  say mk2.

**So yes — two modules can disagree about which variant is attached, and the disagreement
is reachable through documented-looking env vars.** `MPE_APC_SHIFT_NOTE`,
`MPE_APC_STOP_ALL_NOTE` and `MPE_APC_VARIANT` appear nowhere else in the repo — not in the
README, not in `/etc/mpe/mpe.env`, not in a unit file, not in a test. Undocumented escape
hatches with no coverage. Severity is 🟡 rather than 🔴 only because nothing currently sets
them.

Independent derivations of the variant, counted: `resolve_apc_transport_notes`
(`apc_transport.py:145–157`), `resolve_arrow_notes` (`:98–117`), `resolve_fader_ccs`
(`apc_faders.py:48–65`), and `probe-apc-buttons.py:94`. **Four copies of the same
port-name sniff.** Three are near-identical eight-line blocks whose docstrings each
advertise that they mirror one of the others — which is the tell.

**On the coordinator's specific question about prefix shadowing: no such bug exists, and I
want that recorded as verified rather than left ambiguous.** There is no `'apc mini' in
name` test anywhere in the codebase (grep across `scripts/` and `tests/` returns only the
*explicit-variant* keyword `"mini"` at `apc_faders.py:57`, `apc_transport.py:111` and
`:151`, which is matched against the `variant` argument, never against the port name). The
discriminator is `if "mk2" in name or "mkii" in name` with mk1 as the fallback.
`'APC mini mk2:APC mini mk2 Control 28:0'.lower()` contains `mk2`, so both live ports
classify as **mk2, correctly**. The structural weakness is the other way round: **mk1 is an
untested fallback, not a positive match.** Anything unrecognised — an unnamed port, a
future mk3, a port_hint that matched a non-APC device — is declared a mk1 and gets mk1 note
numbers with no warning.

---

### 🟡 P1-2 — `apc_panel` and `apc_transport` give two different answers for mk1 Track 8

`apc_panel.py:75`:

```python
#: Bottom row of eight, left to right.
TRACK_BUTTON_NOTES_MK1: tuple[int, ...] = tuple(range(0x64, 0x6C))    # 0x64..0x6B
```

`apc_transport.py:40–42`:

```python
NOTE_TRACK8_MK2 = 0x6B
# 0x37 = grid row 6 col 7 on mk1 — NOT a side-button-only note (see module doc).
NOTE_TRACK8_MK1 = 0x37
```

The canonical file says mk1 track button 8 is **`0x6B`**. `apc_transport` says it is
**`0x37`**. `apc_transport.py:51` adds a third claim:

```python
# mk1 Track Select 1–8 share notes with grid row 6 (0x30–0x37).
MK1_TRACK_OVERLAP_NOTES = tuple(range(0x30, 0x38))
```

and `ARROW_NOTES_MK1 = (0x40, 0x41, 0x42, 0x43)` (`:95`) is a fourth description of the mk1
bottom button row. `0x64..0x6B` is also byte-for-byte the range the code and the spec both
give for the **mk2** track row (`apc_leds.py:26`, spec §3.3), and `0x30..0x37` is admitted
in its own comment to be grid pads. Four mutually inconsistent claims about one row of
eight buttons, none measured, none asserted, all shipping.

The smell is a hex/decimal confusion somewhere upstream — `0x40` = 64 and `0x64` = 100 — but
I am not going to reason my way to an answer on a panel where reasoning has produced three
wrong answers already (`apc_panel.py:8–13`). **This row must be measured with
`--dump-midi`, not derived.** What I can assert without hardware is that the repo currently
holds four contradictory claims and nothing fails when they disagree.

Note also: `apc_panel` has **no** `TRACK_BUTTON_NOTES_MK2` and **no** mk2 panel drawing. The
canonical map contains an ASCII picture of a device that is not plugged in, and no picture
of the one that is. The mk2 track row exists only as a lone constant in `apc_transport`
(`0x6B`) and as a hardcoded `range(0x64, 0x6C)` inside the probe script.

---

### 🟡 P1-3 — Five prose citations point at `device_facts` ids that do not exist

Rule 1 of `device_facts.py` — *"Other modules cite its id in a comment. They do not restate
it — five restatements is how this happened"* — is violated by exactly five restatements,
citing two fact ids that were never created:

```
scripts/sooperlooper/led_table.py:45      device_facts.apc.scene.led_colours / .apc.track.led_colours
scripts/sooperlooper/apc_leds.py:31       device_facts.apc.scene.led_colours
scripts/sooperlooper/apc_transport.py:368 device_facts.apc.scene.led_colours
scripts/sooperlooper/slot_matrix.py:330   device_facts.apc.scene.led_colours
scripts/probe-apc-buttons.py:10           device_facts.apc.scene.led_colours / .track.led_colours
```

Executed:

```
apc.scene.led_colours -> KeyError 'apc.scene.led_colours'
apc.track.led_colours -> KeyError 'apc.track.led_colours'
ids: ['apc.buttons.all_have_leds', 'apc.buttons.channel_response',
      'apc.buttons.single_colour', 'apc.grid.mk2_encoding', 'apc.probe.positive_control',
      'apc.scene.led_observed', 'apc.scene_column.bottom_is_0x59', 'apc.shift.led',
      'apc.track.led_observed']
```

Worse than dangling: **all five describe the claim as vendor-tier and unmeasured, and it is
neither any more.** The real facts are `apc.scene.led_observed`, `apc.track.led_observed`
and `apc.buttons.single_colour`, all **MEASURED 2026-08-29** across five probe rounds with
a positive control. Under charter §2 that is tier 1 versus tier 5 — **the comments are the
defect.**

The most load-bearing instance is `slot_matrix.py:326–332`:

```python
    Mitch asked for yellow here and got blink instead, on the grounds that the
    scene buttons are green-only. That ground is not solid — see
    `device_facts.apc.scene.led_colours`, which is vendor-tier and unmeasured.
    If the probe shows these buttons can do yellow, this should become yellow,
    which is what was asked for in the first place.
```

The probe has run. The answer is no. `apc.buttons.single_colour` (MEASURED) records channel
exhausted, velocity swept, SysEx RGB rejected, with a positive control on the grid pads.
This docstring hands the next reader an open question that was closed the day before, and
points them at a `KeyError` to find out.

This also means the charter's own §6 (*"Scene-button and track-button colours stay UNKNOWN
and must be recorded as UNKNOWN"*) and the spec's §3.3 table (both rows UNKNOWN / VENDOR)
are **stale against tier 1**. `device_facts.unmeasured()` now returns `[]`. Per charter §2
the higher tier wins, so those documents need correcting too — I am flagging it rather than
editing, since the charter is not mine to amend.

Fix direction: replace every prose restatement with a live lookup —
`fact("apc.buttons.single_colour")` — so a wrong id is an `ImportError` at start-up and a
superseded claim propagates by itself.

**On the specific brief:** `led_table.py` no longer asserts single-colour capability. It
did, and `git show 7e1544f` records the exact softening:

```diff
-# APC side buttons are single-colour — not the grid RGB velocity table.
-# Scene Launch (Stop All, etc.): green only. Track Select: red only.
+# What we currently SEND to the side buttons. Not a statement about what
+# they can show: see `device_facts.apc.scene.led_colours` and ...
```

So spec D3's named instance is genuinely remediated — but the remediation turned one
assertion into five hedges pointing at nothing. The restatement count did not go down.

---

### 🟡 P1-4 — No `device_facts` LED fact records which notes were painted

`apc.scene.led_observed` and `apc.track.led_observed` (MEASURED, 2026-08-29) describe "the
scene buttons" and "the track row" without naming a variant or a note number. The only way
to learn that "the track row" meant `0x64..0x6B` is to read
`scripts/probe-apc-buttons.py:69` — a hardcoded `list(range(0x64, 0x6C))` that is itself an
uncited note literal. `apc.shift.led` does it right (*"Shift (mk2 0x7A)"*); the other two do
not. Rule 2 makes provenance mandatory; on a two-variant surface the **control identity is
part of the provenance**. Fix: add the variant and the note set to both claims, or make the
fact reference a registry control id.

---

### 🟡 P1-5 — Four independent "is Shift down" latches, and the comment claiming there is one is wrong

`sooperlooper-apc-bench.py:398–401`:

```python
    # One shift latch for the whole event loop. ShiftHoldCombo keeps its own
    # `_shift_down`, so a second combo watching the same note would need its own
    # feed and could disagree with the first about whether Shift is held.
    shift_held = False
```

The comment names the hazard and then the file ships four of them:

| # | Owner | Declared | Fed at |
|---|---|---|---|
| 1 | bench module-local `shift_held` | `bench:401` | `bench:720–721` |
| 2 | `Mk1ShiftGhostFilter._shift_down` | `apc_transport.py:232` | `:243–250` (via `bench:654`) |
| 3 | `ShiftHoldCombo._shift_down` | `apc_transport.py:288` | `:307–308` (via `bench:733`) |
| 4 | `TransportButtonLeds._shift_down` | `apc_transport.py:398` | `:440–450` (via `bench:734`) |

Plus a fifth derived latch, `stop_all_took_shift` (`bench:403`, `:672`), which snapshots #1.

They are fed from **different points in the same event loop, with `continue` statements
between them.** Concretely, #1 is set at `:721`, after the scene branch (`:676`), the
slot-surface branch (`:695`) and the reserved-grid branch (`:707`) have each had a chance to
`continue`; #3 and #4 are fed at `:733–734`, after `handle_arrow` has had another. Anything
consumed earlier updates some latches and not others.

Can they disagree? Yes, demonstrably, on mk2. `stop_all_note = 0x77` **is a member of**
`scene_launch_notes = 0x70..0x77`. Press Stop All without Shift: `scene_press_row` returns
row 0, the scene branch `continue`s, and `transport_leds.note_event` / `track_reset.note_event`
never see the press — so #3 and #4 believe Stop All is up while it is physically down. The
subsequent release is swallowed the same way. That the resulting behaviour happens to match
the documented "Shift must be held FIRST" rule (`apc_panel.py:148–152`) is luck, not design:
the rule is enforced by the order of `if` statements in a 150-line event loop, which is
exactly what `scene_press_row` was written to stop.

There is also a **latent divergence in the ghost window**, which has three copies:

```python
apc_transport.py:81   MK1_GHOST_SHIFT_S = _env_float("MPE_APC_MK1_GHOST_S", 0.0)
apc_transport.py:82   MK1_GHOST_STOP_S = MK1_GHOST_SHIFT_S  # alias
apc_transport.py:230  Mk1ShiftGhostFilter.__init__(..., ghost_s: float = MK1_GHOST_SHIFT_S)
```

`Mk1ShiftGhostFilter.consume` uses the **per-instance** `self._ghost_s` (`:264`), while
`TransportButtonLeds._mk1_ghost_stop` reads the **module global** `MK1_GHOST_STOP_S`
(`:483`). Executed:

```
MK1_GHOST_SHIFT_S = 0.0 ; MK1_GHOST_STOP_S = 0.0 ; filter default ghost_s = 0.0
after setting module MK1_GHOST_SHIFT_S = 0.08:
  MK1_GHOST_STOP_S (used by TransportButtonLeds) = 0.0     <- unchanged
  Mk1ShiftGhostFilter default arg                = 0.0     <- unchanged
```

Both derived values are frozen at import. Construct the filter with a non-default `ghost_s`
— which is what a test or a re-enabled `MPE_APC_MK1_GHOST_S` would do — and the filter and
the LED driver disagree about whether the same note is a ghost. One number, three homes,
none of them the owner. Currently harmless because the window is 0.0 and disabled; harmful
the moment anyone re-enables it, which is the documented purpose of keeping the mechanism
(`apc_transport.py:76–79`).

---

### 🟢 P2-1 — `slot_surface` re-derives the grid note formula

`slot_surface.py:571–573`:

```python
        for row in range(GRID_ROWS):
            for col in range(8):
                self._midi_out.send_message([0x90, row * 8 + col, LED_OFF])
```

`apc_grid.pad_note()` exists (`apc_grid.py:56–60`) and raises on out-of-range; this inline
copy does not, and hardcodes `8` beside an imported `GRID_ROWS`. `track_gesture.py:897` and
`:937` do it correctly via `pad_note`. One site out of three.

### 🟢 P2-2 — `apc_leds` duplicates the grid note bounds

`apc_leds.py:50–51` declares `PAD_NOTE_MIN = 0x00` / `PAD_NOTE_MAX = 0x3F`;
`apc_panel.py:65–66` already declares `GRID_NOTE_MIN = 0` / `GRID_NOTE_MAX = 63`. Same two
numbers, two homes, written in two bases so a grep for one misses the other.

### 🟢 P2-3 — An unrecognised control press is completely silent

`bench:722` (`handle_arrow` returns False) and `bench:538–541`
(`handle_cc` → `if fader is None: return`) both drop unmatched events with no log, no
counter, nothing. The final fall-through at `:753` only reports notes that are *already*
known to be clip pads. So a wrong arrow note or a wrong fader CC produces behaviour
identical to "the user did not touch it" — the failure shape `AGENTS.md` names nine times.
Fix direction: one rate-limited "unmapped control: note/cc N" line. It costs nothing and
turns both UNVERIFIED warnings in the README into a five-second check.

### 🟢 P2-4 — `test_scene_row_mapping` pins the mk1 table and not the mk2 one

`tests/test_scene_row_mapping.py:44–46`:

```python
    def test_mk2_has_the_same_shape(self) -> None:
        self.assertEqual(len(SCENE_COLUMN_MK2), 8)
```

The mk1 column gets its exact note values asserted twice (`:37`, `:43`). The mk2 column —
the one attached to the appliance — gets a length check. Nothing in the suite pins
`0x70..0x77`, `0x7A`, or the mk2 track row.

---

## Direct answers to the brief

**1. Every note literal defined outside `apc_panel.py` — is the spec's list of three complete?**

**No.** Seven named note-defining constants live outside it, covering nineteen distinct note
numbers, plus one hardcoded range and one duplicated formula:

| # | Definition | Location | Spec named it? |
|---|---|---|---|
| 1 | `NOTE_TRACK8_MK2 = 0x6B` | `apc_transport.py:40` | yes |
| 2 | `NOTE_TRACK8_MK1 = 0x37` | `apc_transport.py:42` | yes |
| 3 | `MK1_TRACK_OVERLAP_NOTES = range(0x30, 0x38)` | `apc_transport.py:51` | yes |
| 4 | `ARROW_NOTES_MK2 = (0x70,0x71,0x72,0x73)` | `apc_transport.py:94` | **no — and it collides** |
| 5 | `ARROW_NOTES_MK1 = (0x40,0x41,0x42,0x43)` | `apc_transport.py:95` | **no** |
| 6 | `PAD_NOTE_MIN = 0x00` | `apc_leds.py:50` | **no** |
| 7 | `PAD_NOTE_MAX = 0x3F` | `apc_leds.py:51` | **no** |
| 8 | `list(range(0x64, 0x6C))` inline | `probe-apc-buttons.py:69` | **no** |
| 9 | `row * 8 + col` inline | `slot_surface.py:573` | **no** |

Also outside, and identity-bearing even though not note numbers:
`CC_FADER_BASE_MK2/MK1 = 48`, `CC_MASTER_MK2/MK1 = 56` (`apc_faders.py:33–38`); the mk2
status bytes `MK2_SOLID = 0x96`, `MK2_BLINK = 0x9D` (`apc_leds.py:56`, `:62`); the SysEx
identity bytes in `apc_mode.py:38–47`. Test files carry decimal note literals too —
`ShiftHoldCombo(shift_note=122, target_note=119, ...)` at `tests/test_apc_transport.py:143`
is `0x7A` / `0x77` written in base 10, which any grep for `0x7A` misses.

**2. Is the variant a single resolved value passed down, or re-derived?**

Re-derived, in four places: `apc_transport.resolve_apc_transport_notes` (`:145`),
`apc_transport.resolve_arrow_notes` (`:98`), `apc_faders.resolve_fader_ccs`
(`apc_faders.py:48`), `probe-apc-buttons.py:94`. Three of them are near-identical copies of
the same eight-line precedence block. The bench holds a single `apc_label` and then passes
`port_name` + raw `apc_variant` to two of the four resolvers anyway (`:334`, `:397`) instead
of the label it already resolved. **Yes, two modules can disagree** — see P1-1 for the
reachable path.

**3. Which branches does real hardware exercise?**

Attached device: APC mini mk2, aconnect client 28, card 3, verified 2026-08-30.

*Exercised by the attached hardware:* the mk2 arm of `resolve_apc_transport_notes` via the
port-name path; `resolve_scene_launch_notes("mk2")`; `resolve_stale_lamp_note("mk2")` →
`0x6B`; `is_stop_all("mk2", …)`; `scene_column("mk2")`; `apc_leds.translate` mk2 branch and
`MK2_PAD_ENCODING`; `resolve_fader_ccs` mk2 branch; `apc_mode.parse_mode_sysex`.
`resolve_arrow_notes` mk2 branch runs but its output is **never consumed** (P0-1).

*Pure speculation on the current appliance — no branch here is exercised:* every mk1 path.
`Mk1ShiftGhostFilter` is never even constructed (`bench:275` gates on `apc_label == "mk1"`),
so `mk1_shift_ghost_notes`, `MK1_TRACK_OVERLAP_NOTES` and `Mk1ShiftGhostFilter.consume` are
dead. Likewise `_darken_mk1_shift_ghost_surfaces` (`apc_transport.py:432`), `_mk1_ghost_stop`
(`:474`), `NOTE_TRACK8_MK1`, `ARROW_NOTES_MK1`, `TRACK_BUTTON_NOTES_MK1`, `CC_*_MK1`.

*Historically measured, though no mk1 is attached now:* `SCENE_COLUMN_MK1` and
`NOTE_STOP_ALL_CLIPS_MK1` rest on `apc.scene_column.bottom_is_0x59`, MEASURED 2026-08-27 by
direct observation. Those two are evidence-backed. `NOTE_SHIFT_MK1 = 0x62` is corroborated
by the `aseqdump` capture cited at `apc_transport.py:66–70`. The mk1 **track row** and the
mk1 **arrows** have no evidence at any tier.

Per charter §5 I am not proposing deletion of the mk1 paths. I am recording that everything
mk1 except the scene column and Shift is unexercised, unmeasured, and internally
contradictory (P1-2).

**4. Prose capability claims, their tier, and whether code depends on them**

| Claim | Where | Real `device_facts` tier | Code depends on it? | Rule-4 defect? |
|---|---|---|---|---|
| "side buttons single-colour: scene green, track red" | `led_table.py` **before** `7e1544f`; now softened at `:41–47` | MEASURED (`apc.buttons.single_colour`, 2026-08-29) | `SCENE_LED_*` / `TRACK_LED_*` are 0/1/2 only | **No — now backed by MEASURED.** Was a rule-4 defect when written; the fact caught up |
| "Stop All is green-only in hardware, so red had to live somewhere" | `apc_transport.py:161–174` (`resolve_stale_lamp_note` docstring) | MEASURED | drives the design of the whole stale-lamp workaround | No — but the docstring still argues from the *vendor* framing |
| "mk2 button LEDs `0x64-0x77` … velocity 0=off/1=on/2=blink" | `apc_leds.py:26–28` | MEASURED for behaviour; the **note range** is vendor-only | `translate()` passes all non-pad notes through untouched | note range is VENDOR and load-bearing (P1-2) |
| "scene buttons green-only … that ground is not solid, unmeasured" | `slot_matrix.py:326–332` | MEASURED, and closed | `scene_row_led` returns blink instead of yellow | **Inverted rule-4 defect** — refuses to close a question tier 1 has already closed |
| "these rest on a vendor document" ×5 | `led_table.py:45`, `apc_leds.py:31`, `apc_transport.py:368`, `slot_matrix.py:330`, `probe-apc-buttons.py:10` | ids **do not exist** | no | **Yes — five uncheckable citations, P1-3** |
| "arrow notes UNVERIFIED" | `apc_transport.py:86–92`, `README.md:64–68` | no fact recorded at any tier | `handle_arrow` / `set_view` | **Yes — VENDOR-tier recall is load-bearing for a shipped feature, P0-1** |
| "fader CCs believed to agree (48–55 + 56), not confirmed" | `apc_faders.py:8–13`, `README.md:74–75` | no fact recorded at any tier | `handle_cc` → every level change | **Yes — VENDOR-tier recall is load-bearing, and its failure is silent** |
| "Shift has no LED" | eliminated | superseded by `apc.buttons.all_have_leds` (OWNER) + `apc.shift.led` (MEASURED, OPEN) | nothing lights Shift | No — correctly retired, and the supersession is recorded. **This is the file working.** |

The clean rule-4 reading: **the arrows and the fader CCs are the two live rule-4 violations.**
Both are unmeasured recall, both are load-bearing for shipped behaviour, and neither has a
`Fact` object that `refuse_with()` could raise on — they bypass the fact base entirely,
which is the loophole. Rule 4 is executable only for claims that made it into
`device_facts.py`; a guess that never got recorded is still free to be load-bearing.

**5. How many places decide "is shift down"?**

Four latches plus one snapshot; see P1-5. They can disagree, and one path where they do is
reachable on mk2 today.

**6. Are the fader CCs and arrow notes still unverified? What breaks, and would anything detect it?**

Still unverified — no `device_facts` entry exists for either, and the README's two ⚠️ blocks
(`:64–68`, `:74–75`) stand. Nothing in the suite or the runtime can detect a wrong value:

- Wrong fader CC → `fader_for_cc` returns `None` → `handle_cc` returns silently
  (`bench:540–541`). Indistinguishable from an untouched fader.
- Wrong arrow note → `handle_arrow` returns False → falls through to the pad lookup, which
  misses, and the final `elif is_clip_note(n)` does not fire for a non-pad note. Nothing
  printed. Indistinguishable from an unpressed button.
- On mk2 the arrows are already dead from the collision (P0-1), so a *correct* arrow note
  and a *wrong* one produce identical observable behaviour. The one thing that would have
  detected the collision — comparing the arrow tuple against the scene column — is exactly
  what no test does.

`--dump-midi` (`bench:618–619`, `_format_midi` at `:70–84`) is the only instrument, and it
requires a human at a terminal pressing each control. That is a fine instrument and it is
not wired into anything.

---

## Design input for the registry (spec §5.1)

The spec's sketch is close. Four changes, each one earned by a defect above.

```python
@dataclass(frozen=True)
class Control:
    id: str                       # "scene_launch_4", "bank_up", "fader_master"
    kind: Kind                    # GRID | SCENE | TRACK | MODIFIER | BANK | FADER
    notes: dict[str, int | None]  # {"mk1": 0x55, "mk2": 0x73}; None = "this
                                  # variant has no such control" — NOT absent,
                                  # so a gap is a stated fact, not an oversight
    cc: dict[str, int | None] = field(default_factory=dict)   # faders live here
    led: Led | None = None
    owner: str | None = None      # exactly one module, checked at import
    fact_ids: tuple[str, ...] = ()   # keys into device_facts.FACTS, looked up
                                     # at import — a wrong id is ImportError
    evidence: dict[str, str] = field(default_factory=dict)
                                  # {"mk2": "measured 2026-08-29 probe r1",
                                  #  "mk1": "UNVERIFIED — vendor recall"}
```

```python
@dataclass(frozen=True)
class Led:
    colours: tuple[Colour, ...]
    modes: tuple[Mode, ...]
    fact_id: str                  # the MEASURED/OWNER fact that establishes this
```

The four departures from the spec's sketch:

1. **`notes` is a total map over variants, with an explicit `None`.** `resolve_stale_lamp_note`
   returning `None` for mk1 is currently encoded as an `if` in a function; it belongs in the
   data. A control that is genuinely absent on a variant must be distinguishable from one
   nobody has filled in yet.
2. **`fact_ids` are looked up, not quoted.** This is the whole of P1-3. A citation that
   cannot be wrong is not a citation. `Control.__post_init__` calls
   `device_facts.fact(fid)` for every id and lets the `KeyError` become an `ImportError`.
3. **`evidence` is per variant, not per control.** The mk2 scene column is MEASURED and the
   mk1 arrows are recall; one `established=` string cannot say both, and today that is
   precisely why `ARROW_NOTES_MK2` reads as authoritative as `SCENE_COLUMN_MK2`.
4. **`cc` sits in the same table as `notes`.** Faders are controls. Keeping them in
   `apc_faders.py` is how they ended up with a fourth private copy of the variant sniff.

Alongside it, one resolved value replaces four derivations:

```python
@dataclass(frozen=True)
class Surface:
    """The attached device, resolved ONCE. Nothing re-sniffs a port name."""
    variant: str            # canonical: exactly "mk1" or "mk2". Never "env", never "mkii".
    in_port: str            # full name, incl. "Control" — see P0-3
    out_port: str           # resolved against MidiOut.get_ports(), asserted equal to in_port

    def note(self, control_id: str) -> int | None: ...
    def control_for(self, note: int) -> Control | None: ...
```

`Surface.resolve()` is the only place a port name is parsed and the only place a variant is
decided. Everything downstream takes a `Surface`, never a `port_name` and never a bare
label string. `apc_leds.translate`, `resolve_scene_launch_notes`, `resolve_stale_lamp_note`,
`is_stop_all` and `scene_column` all become `Surface` methods or registry lookups, which
retires the `== "mk2"` alphabet problem by construction.

---

## The invariant tests — one per defect, each one a build the code can fail

A rule a build cannot fail is not a rule. Each of these fails **today**, on the code as it
stands, against the defect named.

| # | Test | Fails today on |
|---|---|---|
| **T1** | `test_no_two_controls_share_a_note_in_one_variant` — for each variant, assert `len(notes) == len(set(notes))` across the whole registry, and name both control ids in the failure message | **P0-1.** `bank_up` and `scene_launch_1` both claim mk2 `0x70` |
| **T2** | `test_no_note_literal_outside_the_registry` — AST-walk every module under `scripts/sooperlooper/` and `scripts/*apc*.py`; flag any int constant in `0x00..0x7F` assigned to a name matching `NOTE|NOTES|_MK[12]$`, or passed as `note=`, outside the registry. Allow-list by control id, not by file | **P0-1, P1-2, P2-1, P2-2.** 7 constants + 2 inline sites |
| **T3** | `test_every_registry_control_is_reachable_from_the_event_loop` — feed a synthetic note-on for every registry control through the bench dispatcher with a fake MIDI in/out, assert each one reaches its declared `owner` | **P0-1.** `bank_up/down/left/right` reach `slot_surface`, not the banking layer. This is the test that would have caught the dead arrows without any hardware |
| **T4** | `test_output_port_is_resolved_against_the_output_enumeration` — fake `MidiIn.get_ports()` and `MidiOut.get_ports()` with **deliberately different orderings** containing the real strings, assert the opened out-port *name* equals the opened in-port name | **P0-2.** `midi_out.open_port(idx)` uses the MidiIn index |
| **T5** | `test_port_selection_is_unambiguous` — given both `'APC mini mk2:APC mini mk2 Control 28:0'` and `'…Notes 28:1'` in **either order**, assert the Control port is chosen; and given only `Notes`, assert the bench refuses to start rather than half-working | **P0-3.** `next(... if "apc" in n.lower())` takes whichever is first |
| **T6** | `test_subscription_check_is_per_port_not_per_client` — `/proc/asound/seq/clients` fixture where client 28 has a reader on port 1 (Notes) and none on port 0 (Control); assert `has_reader is False` | **P0-3.** `port_subscriptions` accumulates across the client's ports |
| **T7** | `test_variant_label_is_canonical_everywhere` — for every input the resolvers accept (`mk2, mkii, 2, mk1, 1, original, mini, "", None, "env", "MK2"`), assert the emitted label is exactly `"mk1"` or `"mk2"`, and assert every `apc_label` consumer agrees with the registry for that label | **P1-1.** `apc_label = apc_variant or "env"` at `bench:189` |
| **T8** | `test_variant_is_resolved_exactly_once` — AST-assert that `"mk2" in` / `"mkii" in` appears in exactly one function in the codebase | **P1-1.** Four copies |
| **T9** | `test_every_fact_citation_resolves` — grep every source file for `device_facts.<id>` and assert `id in device_facts.FACTS` | **P1-3.** 5 citations, 2 non-existent ids |
| **T10** | `test_no_unmeasured_claim_is_load_bearing` — for every `Control` whose `evidence[variant]` is not MEASURED/OWNER, assert either no binding depends on it, or the registry marks it `provisional=True` and the bench logs it at startup. This is `Fact.refuse_with()` extended to controls | **P0-1 + the fader CCs.** Arrows and faders are unmeasured and load-bearing |
| **T11** | `test_led_capability_is_enforced_by_tier` — requesting a colour outside `Led.colours` **raises** when `Led.fact_id` is MEASURED/OWNER and **warns** otherwise. Charter §5, spec §5.4 as amended | Nothing enforces capability today; `scene_row_led` returning `SCENE_LED_BLINK` is policy in a docstring |
| **T12** | `test_one_shift_latch` — AST-assert at most one `_shift_down`-shaped attribute across the transport modules; or, behaviourally, drive a Stop-All-then-Shift sequence through the dispatcher and assert all shift consumers report the same value after every event | **P1-5.** Four latches, divergent on mk2 Stop All |
| **T13** | `test_ghost_window_has_one_home` — assert `Mk1ShiftGhostFilter` and `TransportButtonLeds` read the same value after it is changed at runtime | **P1-5.** Three import-frozen copies |
| **T14** | `test_unmapped_control_is_logged` — send a note and a CC that match nothing; assert exactly one log line names each | **P2-3.** Both are silent |

T1, T2 and T3 are Stage 1 and need no hardware. **T3 is the one that matters most** — T1 and
T2 catch the collision as a data error, but T3 is the test that says "this button does
nothing", which is the sentence nobody was able to write for six weeks.

---

## Complete note inventory

Every APC note number named anywhere in the codebase. **Tier** is per `device_facts` rules;
`—` means no fact exists at any tier and the value is recall from a vendor document.

| Control id | mk1 | mk2 | Currently defined in (file:line) | Evidence tier |
|---|---|---|---|---|
| `grid_pad[row][col]` | `0x00`–`0x3F` | `0x00`–`0x3F` | `apc_panel.py:65-66` (`GRID_NOTE_MIN/MAX`); formula `apc_grid.py:56`; duplicated bounds `apc_leds.py:50-51`; inline formula `slot_surface.py:573` | **MEASURED** — `apc.grid.mk2_encoding` (2026-08-28) for mk2; mk1 by observation |
| `scene_launch_1` (row 7) | `0x52` | `0x70` | `apc_panel.py:70`, `:78` | mk1 **MEASURED** `apc.scene_column.bottom_is_0x59` (2026-08-27); mk2 **MEASURED** by probe r1 (2026-08-29), note set recorded only in `probe-apc-buttons.py:68` |
| `scene_launch_2` (row 6) | `0x53` | `0x71` | `apc_panel.py:70`, `:78` | as above |
| `scene_launch_3` (row 5) | `0x54` | `0x72` | `apc_panel.py:70`, `:78` | as above |
| `scene_launch_4` (row 4) | `0x55` | `0x73` | `apc_panel.py:70`, `:78` | as above |
| `scene_launch_5` (row 3) | `0x56` | `0x74` | `apc_panel.py:70`, `:78` | as above |
| `scene_launch_6` (row 2) | `0x57` | `0x75` | `apc_panel.py:70`, `:78` | as above |
| `scene_launch_7` (row 1) | `0x58` | `0x76` | `apc_panel.py:70`, `:78` | as above |
| `stop_all_clips` (row 0) | `0x59` | `0x77` | `apc_panel.py:72`, `:79`; asserted `:84-85` | mk1 **MEASURED** (2026-08-27, direct observation); mk2 vendor + probe-lit |
| `shift` | `0x62` | `0x7A` | `apc_panel.py:73`, `:80` | mk1 **MEASURED** (`aseqdump`, `apc_transport.py:66-70`); mk2 **MEASURED** (`apc.shift.led`, LED OPEN, note confirmed) |
| `track_select_1..7` | `0x64`–`0x6A` **(disputed)** | `0x64`–`0x6A` | mk1: `apc_panel.py:75`; mk2: nowhere as a constant — hardcoded `probe-apc-buttons.py:69` | mk2 **MEASURED** for LED behaviour (`apc.track.led_observed`, 2026-08-29, notes recorded only in the probe source); **mk1 —** |
| `track_select_8` | **`0x6B` vs `0x37` — contradiction** | `0x6B` | mk1: `apc_panel.py:75` says `0x6B`, `apc_transport.py:42` says `0x37`; mk2: `apc_transport.py:40` | mk2 **MEASURED**; **mk1 — and self-contradictory (P1-2)** |
| `mk1_track_status` (alias of grid row 6) | `0x30`–`0x37` | n/a | `apc_transport.py:51` | **—** admitted in its own comment to be grid pads |
| `bank_up` | `0x40` | `0x70` **← collides with `scene_launch_1`** | `apc_transport.py:95`, `:94` | **—** UNVERIFIED (`apc_transport.py:86-92`, `README.md:64-68`) |
| `bank_down` | `0x41` | `0x71` **← collides with `scene_launch_2`** | `apc_transport.py:95`, `:94` | **—** UNVERIFIED |
| `bank_left` | `0x42` | `0x72` **← collides with `scene_launch_3`** | `apc_transport.py:95`, `:94` | **—** UNVERIFIED |
| `bank_right` | `0x43` | `0x73` **← collides with `scene_launch_4`** | `apc_transport.py:95`, `:94` | **—** UNVERIFIED |
| `stale_lamp` (nothing lights it; cleared only) | none | `0x6B` | `apc_transport.py:40`, returned `:178-180` | mk2 note **MEASURED**; the "keep it dark" policy is a design decision, `apc_transport.py:161-174` |

Not notes, listed because they are control identities under the same ownership rule:

| Control id | mk1 | mk2 | Defined in | Tier |
|---|---|---|---|---|
| `fader_1..8` | CC 48–55 | CC 48–55 | `apc_faders.py:33`, `:37` | **—** UNVERIFIED (`apc_faders.py:8-13`, `README.md:74-75`) |
| `fader_master` | CC 56 | CC 56 | `apc_faders.py:34`, `:38` | **—** UNVERIFIED |
| mk2 mode SysEx | n/a | `F0 47 7F 4F 62 00 01 <mode> F7` | `apc_mode.py:35-47` | **MEASURED** for `0x01` (2026-08-28 capture); `0x00`/`0x02` seen, undecoded, correctly reported as unknown |

**Count: 7 note-defining constants outside `apc_panel.py`, naming 19 distinct note numbers**
(`0x6B`; `0x30`–`0x37`; `0x70`–`0x73`; `0x40`–`0x43`; `0x00`; `0x3F`), plus 1 hardcoded note
range inline in `scripts/probe-apc-buttons.py:69` and 1 duplicated note formula in
`scripts/sooperlooper/slot_surface.py:573`. The spec named three of the seven.
