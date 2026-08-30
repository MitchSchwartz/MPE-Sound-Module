"""Semantic pad colour -> the bytes a particular APC actually wants.

The mk1 and the mk2 do not share an LED protocol, and until 2026-08-28 the
bench sent mk1 bytes to both. On the mk2 that produces a surface that is
technically lit and practically unreadable, which is how it was reported:
"pads barely light up, and I'm seeing blue."

WHY. On the mk1 a pad note-on carries the colour in the velocity, on channel 0,
with seven meaningful values (see `led_table`). On the mk2 the pad LEDs are
full RGB and the message splits in two:

    channel  = behaviour (brightness, or pulse/blink rate)
    velocity = index into a fixed 128-entry colour palette

Both facts below are read from Akai's own protocol document, "APC mini mk2 -
Communications Protocol (v1.0)", not inferred from behaviour:

  * MIDI channel 0 (status 0x90) is **10% brightness**. Every LED write in this
    codebase hardcodes 0x90, which is the whole of "barely light up" — the
    surface was being driven at a tenth of its output. 0x96 is 100%.
  * Velocity is a palette index, so the mk1 enum lands somewhere arbitrary:
    LED_GREEN (1) is #1E1E1E, near-black; LED_RED (3) is #FFFFFF, white;
    LED_YELLOW (5) is #FF0000, red. Nothing in 0..6 is blue, so the blue seen
    on the device does NOT come from this codebase — see `probe_messages`.

The mk2's *button* LEDs (0x64-0x77) are driven here with 0x90 and velocity
0=off, 1=on, 2=blink, matching `SCENE_LED_*` and `TRACK_LED_*`, so buttons get
no translation and only the 8x8 RGB grid does.

For a while this said the button LEDs ARE single-colour as established fact,
then — after that turned out to be a vendor document nobody had checked — that
it was unestablished. It is established now, and it went the way the document
said: `device_facts.apc.scene.led_observed`, `.apc.track.led_observed` and
`.apc.buttons.single_colour`, all MEASURED 2026-08-29 over five probe rounds
with a positive control on the grid pads. Green on the scene column, red on the
track row, three states each, channel axis exhausted. So the buttons genuinely
need no translation — and that is now a measurement rather than a coincidence.

The `0x64-0x77` range in the note above is the part that is still vendor
recall. The note *numbers* live in `control_registry`; what is measured is that
the notes the probe painted lit the buttons Mitch named.
"""

from __future__ import annotations

from control_registry import GRID_NOTE_MAX, GRID_NOTE_MIN
from led_table import (
    LED_GREEN,
    LED_GREEN_BLINK,
    LED_OFF,
    LED_RED,
    LED_RED_BLINK,
    LED_YELLOW,
    LED_YELLOW_BLINK,
)

#: Note range of the 8x8 RGB grid. Identical on both models. These were a
#: second copy of `apc_panel.GRID_NOTE_MIN/MAX`, written in a different base so
#: a grep for one missed the other; both now come from the registry.
PAD_NOTE_MIN = GRID_NOTE_MIN
PAD_NOTE_MAX = GRID_NOTE_MAX

# --- mk2 wire facts, from the protocol document -----------------------------

#: Solid, full output. The default 0x90 is 10% and reads as unlit in daylight.
MK2_SOLID = 0x96

#: Blinking 1/8. Blink carries MEANING here — "queued, lands on the next bar"
#: (see `led_table`) — so it must stay a blink on the mk2 rather than collapse
#: to solid. The device owns the rate, and whether it free-runs without an
#: incoming clock is NOT established from the document: confirm on hardware.
MK2_BLINK = 0x9D

#: Palette indices, by hex value in the document's velocity->RGB chart.
MK2_BLACK = 0     # #000000
MK2_RED = 5       # #FF0000
MK2_YELLOW = 13   # #FFFF00
MK2_GREEN = 21    # #00FF00

#: Semantic colour -> (status byte, palette index).
MK2_PAD_ENCODING: dict[int, tuple[int, int]] = {
    LED_OFF: (MK2_SOLID, MK2_BLACK),
    LED_GREEN: (MK2_SOLID, MK2_GREEN),
    LED_GREEN_BLINK: (MK2_BLINK, MK2_GREEN),
    LED_RED: (MK2_SOLID, MK2_RED),
    LED_RED_BLINK: (MK2_BLINK, MK2_RED),
    LED_YELLOW: (MK2_SOLID, MK2_YELLOW),
    LED_YELLOW_BLINK: (MK2_BLINK, MK2_YELLOW),
}


def is_pad_note(note: int) -> bool:
    return PAD_NOTE_MIN <= note <= PAD_NOTE_MAX


def translate(message, apc_label: str | None):
    """Rewrite one outbound MIDI message for `apc_label`. Never raises.

    Anything that is not a channel-0 note-on to a grid pad passes through
    untouched: button LEDs already speak the same dialect on both models, and
    a message this function does not understand is a message it must not
    mangle. mk1 (and an unknown model) is the identity, so the bytes on the
    wire are byte-for-byte what they were before this module existed.
    """
    if apc_label != "mk2":
        return message
    try:
        status, note, velocity = message[0], message[1], message[2]
    except (TypeError, IndexError):
        return message
    if len(message) != 3 or status != 0x90 or not is_pad_note(note):
        return message
    encoded = MK2_PAD_ENCODING.get(velocity)
    if encoded is None:
        # An unmapped value would land on an arbitrary palette colour. Leaving
        # it alone at least keeps the fault where it started instead of
        # inventing a colour the caller never asked for.
        return message
    status_byte, palette = encoded
    return [status_byte, note, palette]


def probe_messages(note: int = 0):
    """Messages for an eyeball test of one pad: off, then each mk2 colour.

    Returned as (label, raw_mk1_message) so the caller can push them through
    the same translation the bench uses, rather than a second code path that
    could be right where the real one is wrong.
    """
    return [
        ("off", [0x90, note, LED_OFF]),
        ("green", [0x90, note, LED_GREEN]),
        ("green blink", [0x90, note, LED_GREEN_BLINK]),
        ("red", [0x90, note, LED_RED]),
        ("red blink", [0x90, note, LED_RED_BLINK]),
        ("yellow", [0x90, note, LED_YELLOW]),
        ("yellow blink", [0x90, note, LED_YELLOW_BLINK]),
    ]
