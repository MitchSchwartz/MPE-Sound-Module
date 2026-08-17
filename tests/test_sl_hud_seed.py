"""The HUD writer must seed tempo, not wait for a change that already happened.

SooperLooper's ``register_auto_update`` delivers on CHANGE. The engine's tempo is set
once, by configure-grid-sync.sh, during the engine's own startup. So a HUD writer that
starts after the engine never receives a tempo update, ``cached("tempo")`` stays None,
``_from_sl`` returns None, and the state file is never written — a frozen looper grid
with nothing logged anywhere.

Hand-starting hid this (the HUD usually came up first). Giving the looper stack systemd
units, with ``After=mpe-sooperlooper``, made the engine start first and the race
deterministic in the losing direction. Verified on the appliance 2026-08-17: the file
went 47 s without a write while the engine happily answered ``/get tempo`` with 120.0.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _writer_with_stub_sl(cached_tempo):
    """A HudWriter whose OSC layer is a stub, without touching the network."""
    import sl_hud_monitor

    writer = sl_hud_monitor.HudWriter.__new__(sl_hud_monitor.HudWriter)
    sl = MagicMock()
    sl.client = MagicMock()
    sl.cached.return_value = cached_tempo
    writer._sl = sl
    writer._registered_at = 0.0
    return writer


class TempoSeedTests(unittest.TestCase):
    def test_seeds_tempo_when_no_auto_update_has_arrived(self) -> None:
        writer = _writer_with_stub_sl(cached_tempo=None)
        writer.register_auto_updates()
        writer._sl.get.assert_called_once_with("tempo", -1)

    def test_does_not_re_seed_once_tempo_is_known(self) -> None:
        """Re-registration runs every 15 s; it must not blocking-get every time."""
        writer = _writer_with_stub_sl(cached_tempo=120.0)
        writer.register_auto_updates()
        writer._sl.get.assert_not_called()

    def test_still_registers_the_auto_updates(self) -> None:
        """Seeding is additional to the subscription, not a replacement for it."""
        writer = _writer_with_stub_sl(cached_tempo=None)
        writer.register_auto_updates()
        paths = [c.args[0] for c in writer._sl.client.send_message.call_args_list]
        self.assertIn("/register_auto_update", paths, "global tempo subscription missing")
        self.assertTrue(
            any(p.startswith("/sl/") and p.endswith("/register_auto_update") for p in paths),
            "per-loop subscriptions missing",
        )

    def test_tempo_is_queried_as_a_global_control(self) -> None:
        """Loop -1 maps to the engine-wide key; loop 0 would query loop zero instead."""
        writer = _writer_with_stub_sl(cached_tempo=None)
        writer.register_auto_updates()
        _ctrl, loop = writer._sl.get.call_args.args
        self.assertLess(loop, 0, "tempo must be fetched as a global, not per-loop")


if __name__ == "__main__":
    unittest.main()
