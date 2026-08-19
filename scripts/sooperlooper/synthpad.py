"""Drive the APC bench with synthetic pad presses — criterion 42 self-test.

Connects a virtual ALSA port to the bench's MIDI input so a latency run can be proven
to produce non-zero output without anyone touching the instrument. See AGENTS.md,
"Never ask Mitch to run a test you could have run yourself".

    python3 scripts/looper-session.py --measure-latency 100 > /tmp/lat.txt 2>&1 &
    python3 scripts/sooperlooper/synthpad.py 180
    grep '^live:' /tmp/lat.txt
"""
import re
import subprocess
import sys
import time

import rtmidi

out = rtmidi.MidiOut()
out.open_virtual_port("synthpad")
time.sleep(0.6)

listing = subprocess.run(["aconnect", "-l"], capture_output=True, text=True).stdout
# The client is named for the process ("RtMidiOut Client"); "synthpad" is the PORT
# name and appears on an indented line under it.
src = None
current = None
for line in listing.splitlines():
    m = re.match(r"client (\d+):", line)
    if m:
        current = m.group(1)
    elif "synthpad" in line and current:
        src = current
print("virtual client:", src)
if src is None:
    sys.exit("could not find virtual port")

# Find the bench input by asking where the APC is routed, rather than hardcoding a
# client number — ALSA renumbers on every replug.
dest = None
in_apc = False
for line in listing.splitlines():
    m = re.match(r"client (\d+): '(.*?)'", line)
    if m:
        in_apc = "APC" in m.group(2)
    elif in_apc:
        c = re.search(r"Connecting To: (\d+):(\d+)", line)
        if c:
            dest = f"{c.group(1)}:{c.group(2)}"
if dest is None:
    sys.exit("APC is not routed anywhere — is the bench running?")
print("bench input:", dest)

rc = subprocess.run(["aconnect", f"{src}:0", dest], capture_output=True, text=True)
print("aconnect rc:", rc.returncode, rc.stderr.strip())
time.sleep(0.5)

N = int(sys.argv[1]) if len(sys.argv) > 1 else 6
# Rotate across all eight pads. Hammering one walks it into tail capture, where the
# gesture is consumed and no OSC is sent at all.
for i in range(N):
    note = i % 8
    out.send_message([0x90, note, 127])
    time.sleep(0.05)
    out.send_message([0x80, note, 0])
    time.sleep(0.6)
print(f"sent {N} synthetic pad presses")
time.sleep(1.0)
