#!/usr/bin/env python3
"""Per-source MIDI transform dispatch.

The remapper daemon historically had exactly one kind of input (a ROLI)
and so applied exactly one transform to everything that arrived. Adding
classic keyboards means the transform depends on *which device* a
message came from, which the daemon's single shared callback could not
express -- every input fed one queue with no source identity.

This module is that missing identity, kept pure so the dispatch decision
is testable without hardware or an audio graph:

    binding  = bind_source(port_name, classification)
    messages = binding.apply(raw, floor)

Two invariants this module exists to protect:

1. **An MPE source's bytes are unchanged.** `apply` on an MPE binding is
   `remap_midi_message` and nothing else -- same function, same argument,
   single message in, single message out. Guarded by
   `test_mpe_binding_is_byte_identical_to_the_legacy_path`.
2. **A classic source fans out.** One input event can become several
   output messages (a note-on mid-bend emits bend *then* note-on), so
   `apply` always returns a list. The daemon must iterate it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from midi_device import (  # noqa: E402
    KIND_CLASSIC,
    KIND_MPE,
    BehaviouralMpeDetector,
    Classification,
    is_router_excluded,
)
from midi_translate import ClassicToMpe  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from patch_browser.pressure_midi import remap_midi_message  # noqa: E402


@dataclass
class SourceBinding:
    """One open input port plus the transform its messages take."""

    port_name: str
    kind: str
    translator: ClassicToMpe | None = None
    detector: BehaviouralMpeDetector | None = None
    on_promote: Callable[[str], None] | None = None
    _seen: int = field(default=0, repr=False)
    _promoted: bool = field(default=False, repr=False)

    @property
    def was_promoted(self) -> bool:
        return self._promoted

    @property
    def is_classic(self) -> bool:
        return self.kind == KIND_CLASSIC

    @property
    def messages_seen(self) -> int:
        return self._seen

    def apply(self, raw: list[int], floor: float) -> list[list[int]]:
        """Transform one inbound message into zero or more outbound ones."""
        self._seen += 1
        if self.translator is None:
            # MPE path: byte-identical to the pre-router daemon.
            out = remap_midi_message(raw, floor)
            return [out] if out else []

        # A device classified classic that behaves like MPE was misjudged.
        # Left alone it still makes sound, so nothing errors -- only its
        # per-note expression collapses, silently. Promote it instead.
        if self.detector is not None and self.detector.observe(raw):
            return self._promote(raw, floor)

        return self.translator.translate(raw)

    def _promote(self, raw: list[int], floor: float) -> list[list[int]]:
        """Switch to the MPE path mid-stream.

        Anything the translator is holding must be released first: those
        notes were allocated onto channels this binding is about to stop
        managing, and nothing else would ever send their note-offs.
        """
        released = self.translator.all_notes_off() if self.translator else []
        self.translator = None
        self.kind = KIND_MPE
        self._promoted = True
        if self.on_promote is not None:
            self.on_promote(self.port_name)
        out = remap_midi_message(raw, floor)
        return released + ([out] if out else [])

    def reset(self) -> list[list[int]]:
        """Release anything held. Called on unplug so a yanked cable
        cannot leave a note sounding forever."""
        if self.translator is None:
            return []
        return self.translator.all_notes_off()


def bind_source(
    port_name: str,
    classification: Classification,
    *,
    on_promote: Callable[[str], None] | None = None,
) -> SourceBinding:
    if classification.kind == KIND_CLASSIC:
        return SourceBinding(
            port_name=port_name,
            kind=KIND_CLASSIC,
            translator=ClassicToMpe(),
            detector=BehaviouralMpeDetector(),
            on_promote=on_promote,
        )
    return SourceBinding(port_name=port_name, kind=KIND_MPE, translator=None)


def select_router_ports(
    port_names, *, route_classic: bool, is_mpe_port: Callable[[str], bool]
) -> list[str]:
    """Which input ports the router binds, in order.

    Pure so the phase 2 gate can be proven without hardware: with
    `route_classic=False` this must return exactly the ports the
    pre-router daemon bound, which is the ROLI ports and nothing else.
    """
    selected = []
    for name in port_names:
        if is_router_excluded(name):
            continue
        if not route_classic and not is_mpe_port(name):
            continue
        selected.append(name)
    return selected


# Reconnect outcomes.
RECONNECT_IDLE = "idle"
RECONNECT_CLOSE = "close"
RECONNECT_REOPEN = "reopen"


def reconnect_decision(desired, connected, *, have_inputs: bool) -> str:
    """What the daemon should do about the current set of ports.

    Pure because the previous version of this decision was ROLI-shaped --
    "no ROLI on the bus" meant "close every input" -- which silently
    became wrong the moment a second kind of device could be bound:
    unplugging the MPE controller would have torn down the classic
    keyboard's port too.

    The decision is now about the *set* of router-eligible ports and has
    nothing to say about which kind of device any of them is.
    """
    desired = tuple(desired)
    connected = tuple(connected)
    if not desired:
        return RECONNECT_CLOSE if connected or have_inputs else RECONNECT_IDLE
    if desired == connected and have_inputs:
        return RECONNECT_IDLE
    return RECONNECT_REOPEN
