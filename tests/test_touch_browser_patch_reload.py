"""Unit tests for touch browser patch reload / Surge liveness (no display)."""

from __future__ import annotations

import sys
import time
import types
import unittest
from unittest import mock


def _install_fake_pygame() -> None:
    if isinstance(sys.modules.get("pygame"), types.ModuleType):
        return
    fake = types.ModuleType("pygame")
    fake.error = type("error", (Exception,), {})
    sys.modules["pygame"] = fake


_install_fake_pygame()

from patch_browser.touch_browser_patches import TouchBrowserPatchesMixin  # noqa: E402


class _PatchHost(TouchBrowserPatchesMixin):
    def __init__(self) -> None:
        self.loader = mock.Mock()
        self.loader.osc_enabled = True
        self.surge_monitor = mock.Mock()
        self.scanner = mock.Mock()
        self.categories: list[str] = []
        self.browse_folder_index = 0
        self.loaded_folder_index = 0
        self.left_nav_mode = mock.Mock()
        self.detail_patch = None
        self.loaded_patch_info = None
        self.volume_level = 1.0
        self._pending_last_patch = None
        self._pending_load_next = 0.0
        self._last_known_surge_pid = None
        self._surge_was_healthy = False
        self._surge_liveness_initialized = False

    def _refresh_lists(self, *, scroll_to_selection: bool = False) -> None:
        return None

    def _apply_volume(self, level: float, *, persist: bool = True) -> None:
        return None

    def _toast(self, message: str, seconds: float = 2.0) -> None:
        return None

    def _layout(self) -> None:
        return None

    def _sync_pressure_live(self, floor: float | None = None) -> None:
        return None


class SurgeReadyForPatchLoadTests(unittest.TestCase):
    def test_false_when_osc_disabled(self) -> None:
        host = _PatchHost()
        host.loader.osc_enabled = False
        self.assertFalse(host._surge_ready_for_patch_load())

    def test_false_when_osc_port_closed(self) -> None:
        host = _PatchHost()
        host.surge_monitor.check_health.return_value = (True, None)
        host.surge_monitor._is_osc_port_in_use.return_value = False
        self.assertFalse(host._surge_ready_for_patch_load())

    def test_true_when_healthy_and_port_open(self) -> None:
        host = _PatchHost()
        host.surge_monitor.check_health.return_value = (True, None)
        host.surge_monitor._is_osc_port_in_use.return_value = True
        self.assertTrue(host._surge_ready_for_patch_load())


class BootstrapPatchesTests(unittest.TestCase):
    def test_queues_pending_when_surge_not_ready(self) -> None:
        host = _PatchHost()
        host.scanner.patches = {}
        host.scanner.load_last_patch.return_value = {
            "category": "Bass",
            "patch_path": "/tmp/test.fxp",
        }
        host.scanner.quick_scan_category.return_value = [{"name": "test", "path": "/tmp/test.fxp"}]
        host.surge_monitor.check_health.return_value = (False, "down")
        host.surge_monitor._is_osc_port_in_use.return_value = False

        host._bootstrap_patches()

        self.assertIsNotNone(host._pending_last_patch)
        self.assertIsNone(host.loaded_patch_info)
        self.assertGreater(host._pending_load_next, time.time())


class SurgeRequeueTests(unittest.TestCase):
    def test_pid_change_requeues_loaded_patch(self) -> None:
        host = _PatchHost()
        host._surge_liveness_initialized = True
        host._surge_was_healthy = True
        host._last_known_surge_pid = 100
        host.loaded_patch_info = {"name": "A", "category": "B", "path": "/p/a.fxp"}
        host.surge_monitor.check_health.return_value = (True, None)
        host.surge_monitor.surge_pid = 200

        host._maybe_requeue_patch_after_surge_change()

        self.assertIsNotNone(host._pending_last_patch)
        self.assertEqual(host._pending_last_patch["name"], "A")

    def test_first_call_initializes_without_requeue(self) -> None:
        host = _PatchHost()
        host.loaded_patch_info = {"name": "A", "category": "B", "path": "/p/a.fxp"}
        host.surge_monitor.check_health.return_value = (True, None)
        host.surge_monitor.surge_pid = 42

        host._maybe_requeue_patch_after_surge_change()

        self.assertIsNone(host._pending_last_patch)
        self.assertTrue(host._surge_liveness_initialized)
        self.assertEqual(host._last_known_surge_pid, 42)


class ProfileSwitchReloadTests(unittest.TestCase):
    def test_skips_liveness_requeue_while_profile_switch_active(self) -> None:
        host = _PatchHost()
        host._surge_liveness_initialized = True
        host._last_known_surge_pid = 1
        host._surge_was_healthy = True
        host.loaded_patch_info = {"name": "A", "category": "B", "path": "/p/a.fxp"}
        host._profile_switch_reload_active = True
        host.surge_monitor.check_health.return_value = (True, None)
        host.surge_monitor.surge_pid = 99

        host._maybe_requeue_patch_after_surge_change()

        self.assertIsNone(host._pending_last_patch)

    def test_profile_switch_sends_load_twice(self) -> None:
        host = _PatchHost()
        patch = {"name": "Lead", "category": "Synth", "path": "/p/lead.fxp"}
        host._pending_last_patch = dict(patch)
        host._pending_load_next = 0.0
        host._profile_switch_reload_active = True
        host._profile_switch_sent_once = False
        host.surge_monitor.check_health.return_value = (True, None)
        host.surge_monitor._is_osc_port_in_use.return_value = True
        host.loader.load_patch.return_value = True

        host._retry_pending_load()
        self.assertTrue(host._profile_switch_sent_once)
        self.assertIsNotNone(host._pending_last_patch)
        self.assertGreater(host._pending_load_next, time.time())

        host._pending_load_next = 0.0
        host._retry_pending_load()
        self.assertFalse(host._profile_switch_reload_active)
        self.assertIsNone(host._pending_last_patch)
        self.assertEqual(host.loader.load_patch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
