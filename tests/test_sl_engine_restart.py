"""Grid configuration must survive an engine restart.

`apply_grid_sync` used to run exactly once, at bench startup. After
`mpe looper sl-restart` the engine came back with SooperLooper's defaults —
`smart_eighths` back ON, no internal sync source — while the bench carried on
believing its configuration was in force. The next take recorded in the wrong
mode and nothing said so.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from scripts.sooperlooper.sl_bench_listener import (
    GLOBAL_SENTINEL,
    SlBenchStateListener,
)
from scripts.sooperlooper.sl_grid_sync import (
    RESTART_SENTINEL,
    SYNC_SOURCE_INTERNAL,
    expected_sentinel,
)


class SentinelTests(unittest.TestCase):
    def test_the_sentinel_is_a_value_the_engine_never_picks_itself(self) -> None:
        """A fresh engine must not accidentally look configured."""
        self.assertEqual(expected_sentinel(), float(SYNC_SOURCE_INTERNAL))
        self.assertNotEqual(expected_sentinel(), 0.0, "0 is SL's own default")

    def test_the_listener_and_the_grid_module_agree_on_which_control(self) -> None:
        """Two modules naming the same control separately is how drift starts."""
        self.assertEqual(GLOBAL_SENTINEL, RESTART_SENTINEL)


class GlobalUpdateRoutingTests(unittest.TestCase):
    def test_global_updates_do_not_get_mistaken_for_a_loop(self) -> None:
        """SL reports loop index -2 for engine-wide controls.

        Routing that through the per-loop path would look up footswitch -2,
        find nothing, and silently drop the only signal we have that the engine
        restarted.
        """
        fs = MagicMock()
        seen = []
        listener = SlBenchStateListener({0: fs}, on_global=lambda c, v: seen.append((c, v)))

        listener.on_global_update("/sl/bench/global", -2, "sync_source", 0.0)
        self.assertEqual(seen, [("sync_source", 0.0)])
        fs.sync_from_sl.assert_not_called()

    def test_a_bench_with_no_handler_does_not_explode(self) -> None:
        listener = SlBenchStateListener({})
        listener.on_global_update("/sl/bench/global", -2, "sync_source", 0.0)

    def test_registration_subscribes_to_the_sentinel_globally(self) -> None:
        """Global path has no /sl/N prefix — verified against control_osc.cpp."""
        client = MagicMock()
        listener = SlBenchStateListener({})
        listener.register(client, num_loops=2)

        paths = [c.args[0] for c in client.send_message.call_args_list]
        self.assertIn("/register_auto_update", paths)
        globals_sent = [c.args[1] for c in client.send_message.call_args_list
                        if c.args[0] == "/register_auto_update"]
        self.assertEqual(len(globals_sent), 1)
        self.assertEqual(globals_sent[0][0], GLOBAL_SENTINEL)
        self.assertEqual(globals_sent[0][3], "/sl/bench/global",
                         "must not share the per-loop return path")

    def test_reregistration_renews_the_global_subscription_too(self) -> None:
        """The engine forgets subscriptions when it restarts, this one included."""
        client = MagicMock()
        listener = SlBenchStateListener({})
        listener.register(client, num_loops=1)
        listener._last_register = 0.0  # force the cadence
        client.reset_mock()
        listener.maybe_reregister()

        paths = [c.args[0] for c in client.send_message.call_args_list]
        self.assertIn("/register_auto_update", paths)


if __name__ == "__main__":
    unittest.main()
