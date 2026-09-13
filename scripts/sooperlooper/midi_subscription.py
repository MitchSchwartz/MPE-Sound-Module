"""Is OUR client subscribed to the port we opened?

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
real subscription graph in /proc/asound/seq/clients.

2026-09-13 — THE CHECK ITSELF HAD THE SAME SHAPE. It asked "does anything read
from any port of a client whose name contains 'APC'?". After a USB drop,
`mpe-pressure-remap` — which binds every MIDI device that appears — subscribed
to the APC's *Notes* port within seconds. That was a reader, so `LinkHealth`
logged "APC link RESTORED — pads live again" and stopped reopening, while the
bench's own input was connected to nothing. Measured on the appliance at 16:18
and again at 16:22 after a replug: the replug could not help, because the
remapper always won the race. A reading identical whether the pads work or not.

So the question is now narrow enough that nobody else can answer it:

  * **which port** — the exact port rtmidi opened, matched by client and port
    NAME, because the client number changes on every re-enumeration (32, 44,
    36 in one afternoon);
  * **which subscriber** — a client carrying the name this process gave its
    own rtmidi objects, which includes the pid. The pid matters: the 08-27
    race is a *previous* bench still subscribed, and a name without it would
    credit the dying process.

There is deliberately no way to ask "is anyone subscribed?" any more.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

SEQ_CLIENTS = Path("/proc/asound/seq/clients")

_CLIENT_RE = re.compile(r'^Client\s+(\d+)\s*:\s*"(.*?)"')
_PORT_RE = re.compile(r'^\s+Port\s+(\d+)\s*:\s*"(.*?)"')
_CONNECTING_RE = re.compile(r"^\s+Connecting To:(.*)$")
_CONNECTED_RE = re.compile(r"^\s+Connected From:(.*)$")
_ADDR_RE = re.compile(r"(\d+):(\d+)")
#: rtmidi appends the ALSA address to a port's name: "... Control 36:0".
_TRAILING_ADDR_RE = re.compile(r"\s+\d+:\d+$")


def own_client_names(pid: int | None = None) -> tuple[str, str]:
    """(input, output) ALSA client names for this process's rtmidi objects.

    Pass them as `name=` to `rtmidi.MidiIn` / `rtmidi.MidiOut`. The default
    name is "RtMidiIn Client" for every process on the box, which is exactly
    why the kernel graph could not say whose subscription it was.
    """
    pid = os.getpid() if pid is None else pid
    return f"mpe-looper-apc-in-{pid}", f"mpe-looper-apc-out-{pid}"


def split_rtmidi_port(port_name: str) -> tuple[str, str]:
    """rtmidi's "Client:Port N:M" -> ("Client", "Port"), address dropped.

    The address is dropped on purpose: it is what changes when the device
    re-enumerates, and the names are what survive it.
    """
    client, sep, port = port_name.partition(":")
    if not sep:
        return client.strip(), ""
    return client.strip(), _TRAILING_ADDR_RE.sub("", port).strip()


def _parse(text: str) -> tuple[dict[int, str], dict[tuple[int, int], dict]]:
    clients: dict[int, str] = {}
    ports: dict[tuple[int, int], dict] = {}
    client_id: int | None = None
    current: dict | None = None
    for line in text.splitlines():
        client = _CLIENT_RE.match(line)
        if client:
            client_id = int(client.group(1))
            clients[client_id] = client.group(2)
            current = None
            continue
        port = _PORT_RE.match(line)
        if port and client_id is not None:
            current = {"name": port.group(2), "to": [], "from": []}
            ports[(client_id, int(port.group(1)))] = current
            continue
        if current is None:
            continue
        connecting = _CONNECTING_RE.match(line)
        if connecting:
            current["to"] += [int(c) for c, _p in _ADDR_RE.findall(connecting.group(1))]
            continue
        connected = _CONNECTED_RE.match(line)
        if connected:
            current["from"] += [int(c) for c, _p in _ADDR_RE.findall(connected.group(1))]
    return clients, ports


def port_subscriptions(port_name: str, *, reader: str, writer: str,
                       path: Path = SEQ_CLIENTS) -> tuple[bool, bool]:
    """(we_read, we_write) for the one device port rtmidi opened.

    we_read  — the client named `reader` is subscribed to the port's output,
               i.e. THIS process receives its pad presses.
    we_write — the client named `writer` feeds the port's input, i.e. THIS
               process can light its LEDs.

    Anyone else's subscription — the pressure remapper's, a previous bench's,
    a subscription on the device's other port — counts for nothing.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # No procfs (a container, a Mac, a unit test) — cannot verify, and
        # refusing to start on that basis would be worse than the bug.
        return True, True

    device, port = split_rtmidi_port(port_name)
    clients, ports = _parse(text)
    for (client_id, _index), info in ports.items():
        if clients.get(client_id, "").lower() != device.lower():
            continue
        if info["name"].lower() != port.lower():
            continue
        we_read = any(clients.get(c) == reader for c in info["to"])
        we_write = any(clients.get(c) == writer for c in info["from"])
        return we_read, we_write
    return False, False


def wait_for_subscription(port_name: str, *, reader: str, writer: str,
                          timeout_s: float = 3.0, poll_s: float = 0.1,
                          path: Path = SEQ_CLIENTS) -> tuple[bool, bool]:
    """Poll until our reader is subscribed, or the timeout expires.

    Subscription is not always instantaneous after `open_port`, and the whole
    problem is a race, so a single immediate check would itself be flaky.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        we_read, we_write = port_subscriptions(
            port_name, reader=reader, writer=writer, path=path
        )
        if we_read or time.monotonic() >= deadline:
            return we_read, we_write
        time.sleep(poll_s)
