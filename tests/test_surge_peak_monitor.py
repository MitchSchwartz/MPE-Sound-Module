"""Tests for live peak meter math and offline monitor behavior."""

from __future__ import annotations

import math
import sys
import unittest
from unittest.mock import MagicMock, patch

from patch_browser.peak_meter_math import (
    PEAK_METER_CLIP_DBFS,
    PEAK_METER_FLOOR_DBFS,
    PEAK_METER_ORANGE_DBFS,
    PEAK_METER_RED_DBFS,
    PEAK_METER_YELLOW_DBFS,
    dbfs_to_meter_ratio,
    linear_peak_to_dbfs,
    peak_meter_color_dbfs,
)
from patch_browser.surge_peak_monitor import (
    PEAK_METER_ENV,
    SurgePeakMonitor,
    _buffer_peak,
    peak_meter_enabled,
)


class PeakMeterMathTests(unittest.TestCase):
    def test_silence_returns_none(self) -> None:
        self.assertIsNone(linear_peak_to_dbfs(0.0))

    def test_unity_peak_is_zero_dbfs(self) -> None:
        db = linear_peak_to_dbfs(1.0)
        assert db is not None
        self.assertAlmostEqual(db, 0.0, places=6)

    def test_half_peak_is_minus_six_db(self) -> None:
        db = linear_peak_to_dbfs(0.5)
        assert db is not None
        self.assertAlmostEqual(db, -6.0206, places=3)

    def test_floor_maps_to_zero_ratio(self) -> None:
        self.assertEqual(dbfs_to_meter_ratio(PEAK_METER_FLOOR_DBFS), 0.0)

    def test_clip_dbfs_maps_to_full_bar(self) -> None:
        self.assertEqual(dbfs_to_meter_ratio(PEAK_METER_CLIP_DBFS), 1.0)

    def test_color_buckets(self) -> None:
        self.assertEqual(peak_meter_color_dbfs(PEAK_METER_YELLOW_DBFS - 0.1), "ok")
        self.assertEqual(peak_meter_color_dbfs(PEAK_METER_YELLOW_DBFS), "warn")
        self.assertEqual(peak_meter_color_dbfs(PEAK_METER_ORANGE_DBFS - 0.1), "warn")
        self.assertEqual(peak_meter_color_dbfs(PEAK_METER_ORANGE_DBFS), "orange")
        self.assertEqual(peak_meter_color_dbfs(PEAK_METER_RED_DBFS - 0.1), "orange")
        self.assertEqual(peak_meter_color_dbfs(PEAK_METER_RED_DBFS), "hot")
        self.assertEqual(peak_meter_color_dbfs(PEAK_METER_CLIP_DBFS), "hot")


class BufferPeakTests(unittest.TestCase):
    def test_empty_buffer(self) -> None:
        self.assertEqual(_buffer_peak(b"", 0), 0.0)

    def test_finds_max_abs_sample(self) -> None:
        import struct

        samples = struct.pack("<fff", 0.1, -0.8, 0.3)
        self.assertAlmostEqual(_buffer_peak(samples, 3), 0.8)


def _healthy_surge() -> MagicMock:
    surge = MagicMock()
    surge.check_health.return_value = (True, None)
    return surge


class _FakeJackClient:
    """Minimal stand-in for jack.Client that records its callbacks."""

    activate_error: Exception | None = None

    def __init__(self, *_args, **_kwargs):
        self.inports = MagicMock()
        self.inports.register.side_effect = lambda _name: MagicMock()
        self.shutdown_cb = None
        self.closed = False

    def set_process_callback(self, _cb):
        return None

    def set_shutdown_callback(self, cb):
        self.shutdown_cb = cb

    def activate(self):
        if self.activate_error is not None:
            raise self.activate_error

    def deactivate(self):
        return None

    def close(self):
        self.closed = True


def _jack_module(client_cls) -> type:
    return type("JackMod", (), {"Client": client_cls})


class PeakMeterEnableTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop(PEAK_METER_ENV, None)
            self.assertFalse(peak_meter_enabled())

    def test_enabled_by_env(self) -> None:
        with patch.dict("os.environ", {PEAK_METER_ENV: "1"}):
            self.assertTrue(peak_meter_enabled())

    def test_no_jack_client_registered_when_disabled(self) -> None:
        """The tap must not join the graph unless opted in — jackd blocks on it."""
        monitor = SurgePeakMonitor(_healthy_surge())
        monitor._jack_available = None
        with patch.dict("os.environ", {PEAK_METER_ENV: "0"}):
            with patch.dict(sys.modules, {"jack": _jack_module(_FakeJackClient)}):
                monitor._poll_once()
        self.assertIsNone(monitor._client)
        self.assertFalse(monitor.snapshot()["online"])


class PeakMeterLifecycleTests(unittest.TestCase):
    """jackd restarts on every buffer/rate change — the client must not outlive it."""

    def _build_online_monitor(self) -> tuple[SurgePeakMonitor, _FakeJackClient]:
        monitor = SurgePeakMonitor(_healthy_surge())
        monitor._jack_available = None
        created: list[_FakeJackClient] = []

        class Recording(_FakeJackClient):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                created.append(self)

        with patch.dict("os.environ", {PEAK_METER_ENV: "1"}):
            with patch.dict(sys.modules, {"jack": _jack_module(Recording), "numpy": MagicMock()}):
                monitor._poll_once()
        self.assertIsNotNone(monitor._client)
        return monitor, created[0]

    def test_registers_a_shutdown_callback(self) -> None:
        _monitor, client = self._build_online_monitor()
        self.assertIsNotNone(
            client.shutdown_cb,
            "no shutdown callback: a jackd restart would strand this client forever",
        )

    def test_rebuilds_client_after_server_shutdown(self) -> None:
        monitor, client = self._build_online_monitor()
        first = monitor._client

        client.shutdown_cb("shutdown", "server died")  # JACK notification thread

        with patch.dict("os.environ", {PEAK_METER_ENV: "1"}):
            with patch.dict(
                sys.modules, {"jack": _jack_module(_FakeJackClient), "numpy": MagicMock()}
            ):
                monitor._poll_once()

        self.assertTrue(client.closed, "stale client was never closed")
        self.assertIsNotNone(monitor._client)
        self.assertIsNot(monitor._client, first, "monitor reused the dead client")

    def test_broken_client_reads_offline_not_a_plausible_number(self) -> None:
        """Fail closed: a dead meter must show '—', never a decaying needle."""
        monitor, _client = self._build_online_monitor()
        for port in monitor._inports:
            port.is_connected_to.side_effect = RuntimeError("client is dead")

        self.assertFalse(monitor._surge_outputs_connected())
        self.assertTrue(monitor._server_gone.is_set())


class SurgePeakMonitorOfflineTests(unittest.TestCase):
    def test_offline_when_surge_unhealthy(self) -> None:
        surge = MagicMock()
        surge.check_health.return_value = (False, "down")
        monitor = SurgePeakMonitor(surge)
        monitor._poll_once()
        snap = monitor.snapshot()
        self.assertFalse(snap["online"])
        self.assertIsNone(snap["dbfs"])
        self.assertEqual(snap["source"], "none")

    def test_jack_activate_failure_retries(self) -> None:
        surge = MagicMock()
        surge.check_health.return_value = (True, None)

        class FakeJackModule:
            class Client:
                inports = MagicMock()

                def __init__(self, *_args, **_kwargs):
                    self.inports.register.side_effect = [MagicMock(), MagicMock()]

                def set_process_callback(self, _cb):
                    return None

                def set_shutdown_callback(self, _cb):
                    return None

                def activate(self):
                    raise RuntimeError("jack not ready")

                def deactivate(self):
                    return None

                def close(self):
                    return None

        monitor = SurgePeakMonitor(surge)
        monitor._jack_available = None

        with patch.dict("os.environ", {PEAK_METER_ENV: "1"}):
            with patch.dict(sys.modules, {"jack": FakeJackModule, "numpy": MagicMock()}):
                monitor._poll_once()
            self.assertIsNone(monitor._client)
            self.assertIsNone(monitor._jack_available)

            class OkClient(FakeJackModule.Client):
                def activate(self):
                    return None

            with patch.dict(
                sys.modules,
                {"jack": type("JackMod", (), {"Client": OkClient}), "numpy": MagicMock()},
            ):
                monitor._poll_once()
        self.assertIsNotNone(monitor._client)
        self.assertTrue(monitor._jack_available)


if __name__ == "__main__":
    unittest.main()
