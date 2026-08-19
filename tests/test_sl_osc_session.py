"""SlOscSession — one port, one cache (criterion 41)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from sl_osc_session import SlOscSession, _cache_key  # noqa: E402


class SlOscSessionTests(unittest.TestCase):
    def test_cache_key_global_tempo(self) -> None:
        self.assertEqual(_cache_key(-1, "tempo"), "-2:tempo")

    def test_bench_and_hud_share_cache(self) -> None:
        session = SlOscSession()
        session._on_bench_state("/sl/bench/state", 3, "state", 4.0)
        self.assertEqual(session.cached("state", 3), 4.0)
        session._on_hud_reply("/r", 3, "loop_pos", 1.5)
        self.assertEqual(session.cached("loop_pos", 3), 1.5)

    def test_seed_tempo_queries_when_missing(self) -> None:
        session = SlOscSession()
        session._client = MagicMock()
        session.last = {}
        session.get = MagicMock(return_value=120.0)
        session.seed_tempo()
        session.get.assert_called_once_with("tempo", -1)

    def test_seed_tempo_skips_when_cached(self) -> None:
        session = SlOscSession()
        session.last = {"-2:tempo": 120.0}
        session.get = MagicMock()
        session.seed_tempo()
        session.get.assert_not_called()

    def test_start_refuses_held_port(self) -> None:
        session = SlOscSession()
        body = Path(SCRIPTS / "sl_osc_session.py").read_text(encoding="utf-8")
        self.assertIn("Refusing to run blind", body)
        fake = MagicMock()
        fake.osc_server.ThreadingOSCUDPServer.side_effect = OSError(98, "Address already in use")
        fake.dispatcher.Dispatcher.return_value = MagicMock()
        with patch.dict(
            sys.modules,
            {
                "pythonosc": fake,
                "pythonosc.dispatcher": fake.dispatcher,
                "pythonosc.osc_server": fake.osc_server,
                "pythonosc.udp_client": fake.udp_client,
            },
        ):
            with self.assertRaises(SystemExit) as ctx:
                session.start()
        msg = ctx.exception.args[0] if ctx.exception.args else ""
        self.assertIn("Refusing to run blind", str(msg))


if __name__ == "__main__":
    unittest.main()
