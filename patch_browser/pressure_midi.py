"""MPE MIDI pressure remapping helpers (pure functions for tests + daemon)."""

from __future__ import annotations

from patch_browser.patch_pressure import remap_pressure_7bit

VIRTUAL_PORT_NAME = "MPE Light Bus"

# MPE member channels 2–16 → MIDI status low nibble 1–15.
MPE_MEMBER_CHANNEL_MIN = 1
MPE_MEMBER_CHANNEL_MAX = 15

SKIP_PORT_SUBSTRINGS = (
    "mpe light bus",
    "surge xt",
    "midi through",
    "through port",
)


def is_mpe_member_channel(status_byte: int) -> bool:
    if status_byte < 0x80:
        return False
    ch = status_byte & 0x0F
    return MPE_MEMBER_CHANNEL_MIN <= ch <= MPE_MEMBER_CHANNEL_MAX


def should_skip_midi_port(name: str) -> bool:
    lower = name.lower()
    return any(token in lower for token in SKIP_PORT_SUBSTRINGS)


def remap_midi_message(message: list[int], floor: float) -> list[int]:
    """Return message with channel/poly pressure remapped on MPE member channels."""
    if not message:
        return message
    status = message[0]
    if status < 0x80:
        return message
    hi = status & 0xF0
    if hi == 0xD0 and len(message) >= 2 and is_mpe_member_channel(status):
        out = list(message)
        out[1] = remap_pressure_7bit(message[1], floor)
        return out
    if hi == 0xA0 and len(message) >= 3 and is_mpe_member_channel(status):
        out = list(message)
        out[2] = remap_pressure_7bit(message[2], floor)
        return out
    return message
