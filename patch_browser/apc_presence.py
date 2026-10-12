"""Is an APC connected? The one answer the looper session's supervision asks.

The APC is optional hardware. ``mpe-looper-session`` exists to drive it, so the
session has nothing to do without one. Until 2026-10-11 it treated a missing APC
as a crash: exit 1, ``Restart=always``, a new process every 10.5 s. The restart
counter reached 3535. Each of those starts opened an OSC session and a HUD
thread (and with it a ``jack_cpu_load`` JACK client), then tore them down again
about 0.3 s later. The timing evidence is in issue #154.

The repo already handles optional hardware with udev start/stop (the foot pedal:
``config/99-foot-pedal.rules``). The APC now works the same way:

* ``config/99-apc-looper.rules`` starts the unit when an APC card appears and
  stops it when the card goes.
* The session checks for the card *before* it opens anything. With no card it
  exits ``APC_ABSENT_EXIT``, and the unit lists that status in
  ``RestartPreventExitStatus``, so systemd does not restart it.
* The periodic reconcile in surge-watchdog (via ``calibration_teardown``) asks
  ``apc_present()`` before starting the session, so it cannot rebuild the loop
  at its own 30 s period.

This is a file read of ``/proc/asound/cards``. It does not fork, open an ALSA
sequencer client, or touch JACK, so the reconcile can afford to call it.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

# sysexits.h EX_UNAVAILABLE. Also used as RestartPreventExitStatus= and
# SuccessExitStatus= in config/mpe-looper-session.service. A test keeps the two
# in step.
APC_ABSENT_EXIT = 69

DEFAULT_PORT_HINT = "APC"

# Hot-plug grace for the session's startup check. udev starts the unit on the
# card's `change` event, which follows /proc/asound/cards by design, so this
# normally passes on the first read. It is bounded and runs once per start.
# It is not a poll.
PRESENCE_WAIT_S = 3.0
PRESENCE_STEP_S = 0.25


def cards_path() -> Path:
    return Path(os.environ.get("MPE_ASOUND_CARDS", "/proc/asound/cards"))


def port_hint() -> str:
    """Same hint, same default, as the bench's port lookup (MPE_APC_MIDI_PORT)."""
    return os.environ.get("MPE_APC_MIDI_PORT", DEFAULT_PORT_HINT).strip() or DEFAULT_PORT_HINT


def apc_in_cards(cards_text: str, hint: str | None = None) -> bool:
    """True when any ALSA card's name line contains the hint (case-insensitive).

    ``/proc/asound/cards`` gives each card two lines: `` N [id ]: driver - short``,
    then the long name. A USB APC reads ``APC MINI`` or ``APC mini mk2`` in both.
    The match covers the whole text, the same way the bench matches port names.
    """
    needle = (hint or port_hint()).lower()
    return needle in cards_text.lower()


def apc_present(*, cards_file: Path | None = None, hint: str | None = None) -> bool | None:
    """True or False from the card list. None when the list cannot be read.

    None is a real third answer. Callers decide what an unreadable card list
    means; it never quietly turns into "absent".
    """
    path = cards_file or cards_path()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return apc_in_cards(text, hint)


def wait_for_apc(
    *,
    timeout_s: float = PRESENCE_WAIT_S,
    step_s: float = PRESENCE_STEP_S,
    cards_file: Path | None = None,
    hint: str | None = None,
    sleep=time.sleep,
    monotonic=time.monotonic,
) -> bool | None:
    """Bounded startup check: present, absent after ``timeout_s``, or None if unreadable."""
    deadline = monotonic() + max(0.0, timeout_s)
    while True:
        state = apc_present(cards_file=cards_file, hint=hint)
        if state is not False:
            return state
        if monotonic() >= deadline:
            return False
        sleep(step_s)
