#!/usr/bin/env python3
"""Load a Surge patch the way the touch UI does. Usage: load-patch-osc.py PATCH.fxp

Measurement harnesses load patches through here (scripts/lib/measure-run-as-user.sh
mpe_load_patch_osc). The stage gain goes through the UI's volume authority before
the load: device mode keeps DEVICE_MODE_SURGE_TRIM_DB, trim mode uses the saved
fader. A fresh PatchLoader would otherwise send unity, 6 dB above the UI in
device mode (2026-10-12 capacity run). The DAC is read, never written.

Refuses while the touch UI runs (UI_UNITS): the UI owns the stage and re-sends it
every second, so a patch loaded here would play at the UI patch's gain, which
for a cut patch could be tens of dB too loud. Harnesses stop the UI first
(`mpe_unit_stop_for_measure`, which starts it again on exit).
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from patch_browser.measure_patch import load_patch_like_ui  # noqa: E402

UI_UNITS = ("touch-patch-browser.service", "patch-browser.service")


def ui_running() -> str | None:
    """The UI unit that is active, or None. No systemctl (not the appliance): None."""
    for unit in UI_UNITS:
        try:
            result = subprocess.run(["systemctl", "is-active", "--quiet", unit],
                                    check=False, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return unit
    return None


def main() -> None:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} PATCH.fxp", file=sys.stderr)
        sys.exit(2)
    path = Path(sys.argv[1]).resolve()
    if not path.is_file():
        print(f"not found: {path}", file=sys.stderr)
        sys.exit(1)
    unit = ui_running()
    if unit is not None:
        print(f"refusing: {unit} is running and owns the stage gain; stop it first "
              f"(mpe_unit_stop_for_measure {unit})", file=sys.stderr)
        sys.exit(3)
    from patch_browser.patch_loader import PatchLoader, UdpStageSender

    if not load_patch_like_ui(PatchLoader(stage_sender=UdpStageSender()), path):
        sys.exit(1)
    print(f"loaded {path.name}", flush=True)


if __name__ == "__main__":
    main()
