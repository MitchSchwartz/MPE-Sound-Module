"""Decode the APC mini mk2's mode-change SysEx.

Why this exists: the APC's Notes mode is a *global device mode*. Entering
it silences grid output on the Control port entirely and moves the pads
to a separate ALSA port. The looper, which reads the Control port, then
sees nothing -- the grid is simply dead, with nothing on screen saying
why.

That failure has already cost real debugging time (2026-08-28): the grid
was dead, and it was first misdiagnosed as stale note constants in
apc_panel.py. The constants were correct; the device was in Notes mode.

The device announces the change:

    F0 47 7F 4F 62 00 01 <mode> F7
       │  │  │  │  └──┴── payload length (1 byte)
       │  │  │  └──────── message type
       │  │  └─────────── product: APC mini mk2
       │  └────────────── device id (7F = all)
       └───────────────── 47 = Akai Professional

**Only 0x01 is confirmed.** It was observed on entering Notes mode,
emitted ~250 ms after the Shift+Scene 7 press. Values 0x00 and 0x02 were
also seen during mode switching, but which mode each denotes has NOT
been established -- one observation each, no controlled capture. They are
deliberately reported as unknown rather than guessed, because a wrong
label here would be worse than no label: it would tell Mitch the grid
should work when it cannot.
"""

from __future__ import annotations

from typing import NamedTuple

SYSEX_START = 0xF0
SYSEX_END = 0xF7

AKAI_MANUFACTURER_ID = 0x47
APC_MINI_MK2_PRODUCT = 0x4F
MODE_MESSAGE_TYPE = 0x62

# Confirmed by capture 2026-08-28.
MODE_NOTES = 0x01

# Seen but not decoded. Listed so the parser reports them as recognised
# device modes with an unknown meaning, rather than as malformed SysEx.
MODE_OBSERVED_UNDECODED = (0x00, 0x02)

_MODE_LABELS = {MODE_NOTES: "Notes"}


class ApcMode(NamedTuple):
    value: int
    label: str
    grid_available: bool
    confirmed: bool

    def describe(self) -> str:
        if self.confirmed:
            return self.label
        return f"mode 0x{self.value:02X} (unknown)"


def parse_mode_sysex(message) -> ApcMode | None:
    """Return the announced mode, or None if this is not one.

    Tolerant of the trailing F7 being absent: some stacks strip it.
    """
    data = list(message or [])
    if len(data) < 8:
        return None
    if data[0] != SYSEX_START:
        return None
    if data[-1] == SYSEX_END:
        data = data[:-1]
    if len(data) != 8:
        return None
    if data[1] != AKAI_MANUFACTURER_ID or data[3] != APC_MINI_MK2_PRODUCT:
        return None
    if data[4] != MODE_MESSAGE_TYPE:
        return None
    if (data[5], data[6]) != (0x00, 0x01):  # payload length: one byte
        return None
    value = data[7]
    if not 0 <= value <= 0x7F:
        return None
    confirmed = value in _MODE_LABELS
    return ApcMode(
        value=value,
        label=_MODE_LABELS.get(value, f"0x{value:02X}"),
        # The grid reaches the looper in every mode EXCEPT Notes. For an
        # undecoded mode we cannot know, so we do not claim it is dead --
        # only Notes is proven to silence it.
        grid_available=value != MODE_NOTES,
        confirmed=confirmed,
    )


def grid_silent_reason(mode: ApcMode | None) -> str | None:
    """A message explaining a dead grid, or None if it should be alive."""
    if mode is None:
        return None
    if mode.grid_available:
        return None
    return (
        "APC is in Notes mode — the grid is sending to the instrument port, "
        "not the looper. Shift + Scene 7 to return."
    )
