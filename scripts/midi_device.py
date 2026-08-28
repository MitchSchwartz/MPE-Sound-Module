"""Which kind of controller is this? Pure classification, no I/O.

Plan: docs/CLASSIC-MIDI-PLAN.md §3.1. Priority order:

  1. MPE Configuration Message (RPN 6) — the standards-correct signal, needs no
     device list and works for hardware nobody has heard of.
  2. Known-device table — MPE devices that never send MCM. The ROLI path today.
  3. Default CLASSIC.

Defaulting to classic is deliberate. An unknown keyboard is far more likely to
be an ordinary one, and the two errors are not symmetric: classic-treated-as-MPE
bends 24x too wide (plan §1.A), while MPE-treated-as-classic merely loses
per-note expression. Surge does not filter master-channel notes (plan §7.2), so
a misclassified device PLAYS, wrongly, rather than falling silent — which is why
every result carries a ``reason`` the UI can show. A classification you cannot
see is one you cannot debug.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from scripts.midi_translate import (
    CC_DATA_ENTRY_MSB,
    CC_RPN_LSB,
    CC_RPN_MSB,
    CONTROL_CHANGE,
)

KIND_MPE = "mpe"
KIND_CLASSIC = "classic"

RPN_MPE_CONFIGURATION = 6
LOWER_ZONE_MASTER = 0   # MIDI channel 1
UPPER_ZONE_MASTER = 15  # MIDI channel 16

# Devices that are MPE but do not announce it. Matched case-insensitively
# against the ALSA client name.
KNOWN_MPE_PORT_PATTERNS: tuple[str, ...] = (
    r"lumi",
    r"seaboard",
    r"roli",
    r"linnstrument",
    r"osmose",
    r"continuum",
)
# USB vendor IDs, lowercase hex. 2af4 = ROLI.
KNOWN_MPE_USB_VENDORS: frozenset[str] = frozenset({"2af4"})

REASON_MCM = "announced MPE (MCM)"
REASON_KNOWN_NAME = "known MPE device (name)"
REASON_KNOWN_USB = "known MPE device (USB id)"
REASON_DEFAULT = "no MPE signal — treated as classic"
REASON_OVERRIDE = "manual override"


@dataclass(frozen=True)
class Classification:
    """What the router decided, and why. ``reason`` is shown in the UI."""

    kind: str
    reason: str
    member_channels: int | None = None

    @property
    def is_mpe(self) -> bool:
        return self.kind == KIND_MPE


class MpeConfigDetector:
    """Watches a device's stream for an MPE Configuration Message.

    MCM is RPN 6 on channel 1 (lower zone) or 16 (upper), sent as three CCs
    (plan §7.4)::

        CC 101 = 6      RPN MSB
        CC 100 = 0      RPN LSB
        CC 6   = count  member channels; 0 disables the zone

    RPN state is per channel: a device may address the lower zone while an RPN
    is half-selected on another channel, and mixing them would read a bend-range
    message as a zone declaration.
    """

    def __init__(self) -> None:
        self._rpn: dict[int, tuple[int | None, int | None]] = {}
        self.member_channels: int | None = None

    @property
    def seen(self) -> bool:
        """True once a zone-ENABLING MCM has been seen.

        ``mm = 0`` disables the zone, so it is an MCM but not evidence of an
        MPE device — treating it as such would classify a device as MPE at the
        moment it said it was not.
        """
        return bool(self.member_channels)

    def feed(self, msg: list[int]) -> bool:
        """Consume one message. Returns True if it completed an MCM."""
        if len(msg) < 3 or msg[0] & 0xF0 != CONTROL_CHANGE:
            return False
        channel = msg[0] & 0x0F
        cc, value = msg[1], msg[2]
        msb, lsb = self._rpn.get(channel, (None, None))

        if cc == CC_RPN_MSB:
            self._rpn[channel] = (value, lsb)
            return False
        if cc == CC_RPN_LSB:
            self._rpn[channel] = (msb, value)
            return False
        if cc == CC_DATA_ENTRY_MSB:
            if (msb, lsb) != (RPN_MPE_CONFIGURATION, 0):
                return False
            if channel not in (LOWER_ZONE_MASTER, UPPER_ZONE_MASTER):
                return False  # MCM is only valid on a zone master
            if not 0 <= value <= 15:
                return False
            self.member_channels = value
            self._rpn.pop(channel, None)
            return True
        return False


def name_looks_mpe(port_name: str) -> bool:
    lowered = (port_name or "").lower()
    return any(re.search(p, lowered) for p in KNOWN_MPE_PORT_PATTERNS)


def classify_port(
    port_name: str,
    *,
    usb_vendors: frozenset[str] | set[str] | None = None,
    detector: MpeConfigDetector | None = None,
    override: str | None = None,
) -> Classification:
    """Decide what a port is. Cheap and repeatable — call again on hot-plug."""
    if override in (KIND_MPE, KIND_CLASSIC):
        return Classification(kind=override, reason=REASON_OVERRIDE)
    if detector is not None and detector.seen:
        return Classification(
            kind=KIND_MPE,
            reason=REASON_MCM,
            member_channels=detector.member_channels,
        )
    if name_looks_mpe(port_name):
        return Classification(kind=KIND_MPE, reason=REASON_KNOWN_NAME)
    if usb_vendors and KNOWN_MPE_USB_VENDORS & {v.lower() for v in usb_vendors}:
        return Classification(kind=KIND_MPE, reason=REASON_KNOWN_USB)
    return Classification(kind=KIND_CLASSIC, reason=REASON_DEFAULT)
