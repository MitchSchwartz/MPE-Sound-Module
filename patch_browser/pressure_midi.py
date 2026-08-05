"""MPE MIDI pressure remapping helpers (pure functions for tests + daemon)."""

from __future__ import annotations

from patch_browser.patch_pressure import remap_pressure_7bit

# Remapper writes remapped MIDI here; Surge reads the same port via --midi-input=0.
REMAP_OUTPUT_PORT_NAME = "Midi Through Port-0"

# MPE member channels 2–16 → MIDI status low nibble 1–15.
MPE_MEMBER_CHANNEL_MIN = 1
MPE_MEMBER_CHANNEL_MAX = 15

SKIP_PORT_SUBSTRINGS = (
    "surge xt",
    "midi through",
    "through port",
    "rtmidi output",
    "rtmidi input",
)

ROLI_PORT_SUBSTRINGS = (
    "lumi",
    "seaboard",
    "roli",
)


def find_remap_output_port_index(port_names: list[str]) -> int | None:
    """Return RtMidi OUT port index for the ALSA Midi Through sink."""
    target = REMAP_OUTPUT_PORT_NAME.lower()
    for index, name in enumerate(port_names):
        if target in name.lower():
            return index
    return None


def is_mpe_member_channel(status_byte: int) -> bool:
    if status_byte < 0x80:
        return False
    ch = status_byte & 0x0F
    return MPE_MEMBER_CHANNEL_MIN <= ch <= MPE_MEMBER_CHANNEL_MAX


def should_skip_midi_port(name: str) -> bool:
    lower = name.lower()
    return any(token in lower for token in SKIP_PORT_SUBSTRINGS)


def is_roli_controller_port(name: str) -> bool:
    lower = name.lower()
    if should_skip_midi_port(name):
        return False
    return any(token in lower for token in ROLI_PORT_SUBSTRINGS)


def list_roli_input_port_names(port_names: list[str]) -> list[str]:
    """Physical ROLI ALSA inputs the remapper should listen on (order-stable)."""
    return [name for name in port_names if is_roli_controller_port(name)]


def normalize_midi_bytes(message) -> list[int]:
    """Coerce RtMidi callback payloads to a flat list of MIDI bytes."""
    if message is None:
        return []
    if isinstance(message, (bytes, bytearray)):
        return [int(b) & 0xFF for b in message]
    if isinstance(message, int):
        return [message & 0xFF]
    if not isinstance(message, (list, tuple)):
        return []
    # Some RtMidi builds pass (bytes, delta) as a single sequence.
    if (
        len(message) == 2
        and not isinstance(message[0], int)
        and isinstance(message[0], (list, tuple, bytes, bytearray))
    ):
        return normalize_midi_bytes(message[0])
    out: list[int] = []
    for item in message:
        if isinstance(item, int):
            out.append(item & 0xFF)
        elif isinstance(item, (list, tuple, bytes, bytearray)):
            out.extend(normalize_midi_bytes(item))
    return out


def remap_midi_message(message, floor: float) -> list[int]:
    """Return message with channel/poly pressure remapped on MPE member channels."""
    data = normalize_midi_bytes(message)
    if not data:
        return data
    status = data[0]
    if status < 0x80:
        return data
    hi = status & 0xF0
    if hi == 0xD0 and len(data) >= 2 and is_mpe_member_channel(status):
        out = list(data)
        out[1] = remap_pressure_7bit(data[1], floor)
        return out
    if hi == 0xA0 and len(data) >= 3 and is_mpe_member_channel(status):
        out = list(data)
        out[2] = remap_pressure_7bit(data[2], floor)
        return out
    return data
