"""Did the MIDI port we opened actually get subscribed?

2026-08-27: the session's startup banner printed a complete, correct APC line —
device name, client, port, the whole pad map — while its ALSA sequencer client
was subscribed to nothing. The pads were dead for seventeen minutes with no
error anywhere, twice in one morning.

The banner is built from the port *name lookup*, which succeeds whether or not
the subsequent subscription took. So it reads identically in both cases: the
recurring defect shape on this appliance.

Why the subscription fails: systemd SIGKILLs the previous instance after its
stop timeout and starts the replacement in the same second. The new process
opens its port while the dying one still holds the device, and rtmidi's
`open_port` reports no error.

This module asks the kernel instead of trusting the library. ALSA publishes the
real subscription graph in /proc/asound/seq/clients; a port with no
"Connecting To" line has no reader, whatever the banner says.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

SEQ_CLIENTS = Path("/proc/asound/seq/clients")

_CLIENT_RE = re.compile(r'^Client\s+(\d+)\s*:\s*"(.*?)"')
_PORT_RE = re.compile(r"^\s+Port\s+(\d+)\s*:")
_CONNECTING_RE = re.compile(r"^\s+Connecting To:")
_CONNECTED_RE = re.compile(r"^\s+Connected From:")


def port_subscriptions(device_substring: str, *,
                       path: Path = SEQ_CLIENTS) -> tuple[bool, bool]:
    """(has_reader, has_writer) for the first client matching the name.

    has_reader — something is subscribed to the device's output, i.e. our
    process will receive its pad presses.
    has_writer — something feeds the device's input, i.e. LEDs can be lit.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # No procfs (a container, a Mac, a unit test) — cannot verify, and
        # refusing to start on that basis would be worse than the bug.
        return True, True

    in_device = False
    has_reader = has_writer = False
    for line in text.splitlines():
        client = _CLIENT_RE.match(line)
        if client:
            in_device = device_substring.lower() in client.group(2).lower()
            continue
        if not in_device:
            continue
        if _CONNECTING_RE.match(line):
            has_reader = True
        elif _CONNECTED_RE.match(line):
            has_writer = True
    return has_reader, has_writer


def wait_for_subscription(device_substring: str, *, timeout_s: float = 3.0,
                          poll_s: float = 0.1,
                          path: Path = SEQ_CLIENTS) -> tuple[bool, bool]:
    """Poll until the device has a reader, or the timeout expires.

    Subscription is not always instantaneous after `open_port`, and the whole
    problem is a race, so a single immediate check would itself be flaky.
    """
    deadline = time.monotonic() + timeout_s
    reader = writer = False
    while True:
        reader, writer = port_subscriptions(device_substring, path=path)
        if reader:
            return reader, writer
        if time.monotonic() >= deadline:
            return reader, writer
        time.sleep(poll_s)
