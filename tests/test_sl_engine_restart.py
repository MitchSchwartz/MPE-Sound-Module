"""Grid configuration must survive an engine restart.

`apply_grid_sync` used to run exactly once, at bench startup. After
`mpe looper sl-restart` the engine came back with SooperLooper's defaults —
`smart_eighths` back ON — while the bench carried on believing its configuration
was in force. Phase 3M detects restart via smart_eighths drift (criterion 40).
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from scripts.sooperlooper.sl_bench_listener import (
    GLOBAL_CONFIG_PROBE,
    SlBenchStateListener,
)
from scripts.sooperlooper.sl_grid_sync import (
    ENGINE_CONFIG_PROBE,
    expected_engine_config,
)


class EngineConfigProbeTests(unittest.TestCase):
    def test_probe_is_a_value_the_engine_never_picks_after_we_configure(self) -> None:
        self.assertEqual(expected_engine_config(), 0.0)
        self.assertEqual(ENGINE_CONFIG_PROBE, "smart_eighths")

    def test_the_listener_and_the_grid_module_agree_on_which_control(self) -> None:
        self.assertEqual(GLOBAL_CONFIG_PROBE, ENGINE_CONFIG_PROBE)


class GlobalUpdateRoutingTests(unittest.TestCase):
    def test_global_updates_do_not_get_mistaken_for_a_loop(self) -> None:
        fs = MagicMock()
        seen = []
        listener = SlBenchStateListener({0: fs}, on_global=lambda c, v: seen.append((c, v)))

        listener.on_global_update("/sl/bench/global", -2, "smart_eighths", 1.0)
        self.assertEqual(seen, [("smart_eighths", 1.0)])
        fs.sync_from_sl.assert_not_called()

    def test_a_bench_with_no_handler_does_not_explode(self) -> None:
        listener = SlBenchStateListener({})
        listener.on_global_update("/sl/bench/global", -2, "smart_eighths", 1.0)

    def test_registration_subscribes_to_the_config_probe_globally(self) -> None:
        client = MagicMock()
        listener = SlBenchStateListener({})
        listener.register(client, num_loops=2)

        paths = [c.args[0] for c in client.send_message.call_args_list]
        self.assertIn("/register_auto_update", paths)
        globals_sent = [
            c.args[1]
            for c in client.send_message.call_args_list
            if c.args[0] == "/register_auto_update"
        ]
        self.assertEqual(len(globals_sent), 1)
        self.assertEqual(globals_sent[0][0], GLOBAL_CONFIG_PROBE)
        self.assertEqual(globals_sent[0][3], "/sl/bench/global")

    def test_reregistration_renews_the_global_subscription_too(self) -> None:
        client = MagicMock()
        listener = SlBenchStateListener({})
        listener.register(client, num_loops=1)
        listener._last_register = 0.0
        client.reset_mock()
        listener.maybe_reregister()

        paths = [c.args[0] for c in client.send_message.call_args_list]
        self.assertIn("/register_auto_update", paths)


if __name__ == "__main__":
    unittest.main()
