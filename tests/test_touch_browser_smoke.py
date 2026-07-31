"""Smoke tests for TouchPatchBrowser mixin wiring (mocked pygame, no display)."""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock


def _install_fake_pygame() -> None:
    if isinstance(sys.modules.get("pygame"), types.ModuleType):
        return

    fake = types.ModuleType("pygame")

    fake.QUIT = 12
    fake.MOUSEBUTTONDOWN = 1025
    fake.MOUSEBUTTONUP = 1026
    fake.MOUSEMOTION = 1024
    fake.FINGERDOWN = 1792
    fake.FINGERUP = 1793
    fake.FINGERMOTION = 1794
    fake.SRCALPHA = 65536
    fake.FULLSCREEN = 1

    class _FakeRect:
        def __init__(self, *args, **kwargs):
            if len(args) == 4:
                self.x, self.y, self.w, self.h = args
            elif len(args) == 1 and isinstance(args[0], tuple):
                self.x, self.y, self.w, self.h = args[0]
            else:
                self.x = self.y = self.w = self.h = 0
            self.centery = self.y + self.h // 2

        @property
        def right(self) -> int:
            return self.x + self.w

        @property
        def bottom(self) -> int:
            return self.y + self.h

    class _FakeSurface:
        def __init__(self, size, flags=0):
            self.size = size

        def fill(self, *_args, **_kwargs) -> None:
            return None

        def blit(self, *_args, **_kwargs) -> None:
            return None

        def get_size(self):
            return self.size

        def get_clip(self):
            return _FakeRect(0, 0, 800, 480)

        def set_clip(self, _rect) -> None:
            return None

        def get_width(self) -> int:
            return self.size[0]

        def get_height(self) -> int:
            return self.size[1]

    class _FakeFont:
        def __init__(self, *_args, **_kwargs):
            pass

        def size(self, text):
            return (max(8, len(text) * 8), 16)

        def render(self, text, _antialias, _color):
            surf = _FakeSurface((max(8, len(text) * 8), 16))
            surf.get_width = lambda: max(8, len(text) * 8)
            surf.get_height = lambda: 16
            return surf

        def get_linesize(self) -> int:
            return 18

    class _FakeEvent:
        def __init__(self, kind, attrs=None):
            self.type = kind
            for key, value in (attrs or {}).items():
                setattr(self, key, value)

    fake.Rect = _FakeRect
    fake.Surface = _FakeSurface
    fake.event = types.SimpleNamespace(Event=_FakeEvent)
    fake.init = mock.Mock()
    fake.quit = mock.Mock()
    fake.display = types.SimpleNamespace(
        set_caption=mock.Mock(),
        set_mode=mock.Mock(return_value=_FakeSurface((800, 480))),
        flip=mock.Mock(),
    )
    fake.mouse = types.SimpleNamespace(set_visible=mock.Mock())
    fake.time = types.SimpleNamespace(Clock=mock.Mock(return_value=mock.Mock(get_time=mock.Mock(return_value=16))))
    fake.font = types.SimpleNamespace(
        Font=_FakeFont,
        match_font=mock.Mock(return_value="/tmp/fake-font.ttf"),
    )
    fake.draw = types.SimpleNamespace(
        rect=mock.Mock(),
        line=mock.Mock(),
        lines=mock.Mock(),
    )

    sys.modules["pygame"] = fake


_install_fake_pygame()

from patch_browser.touch_browser_app import TouchPatchBrowser  # noqa: E402
from patch_browser.touch_ui_enums import Screen  # noqa: E402


def _default_surge_status() -> dict:
    return {
        "status": "Running",
        "details": "ok",
        "can_restart": False,
    }


class TouchBrowserSmokeTests(unittest.TestCase):
    def _make_browser(self) -> TouchPatchBrowser:
        scanner = mock.Mock()
        scanner.load_last_patch.return_value = None
        scanner.get_categories.return_value = []
        scanner.scan_complete.is_set.return_value = True
        scanner.wait_for_scan.return_value = True
        scanner.patches = {}

        loader = mock.Mock()
        loader.osc_enabled = False
        loader.normalization.is_globally_enabled.return_value = True
        loader.normalization.is_enabled.return_value = True
        loader.normalization.get_entry.return_value = None
        loader.normalization.get_raw_gain_db.return_value = None
        loader.normalization.list_missing.return_value = []

        surge_monitor = mock.Mock()
        surge_monitor.get_status_summary.return_value = _default_surge_status()

        cpu_monitor = mock.Mock()
        cpu_monitor.snapshot.return_value = {"online": True, "percent": 12.5}

        backlight = mock.Mock()
        backlight.get_percent.return_value = 100
        backlight.restore_saved.return_value = None

        patches = [
            mock.patch("patch_browser.touch_browser_app.PatchScanner", return_value=scanner),
            mock.patch("patch_browser.touch_browser_app.PatchLoader", return_value=loader),
            mock.patch("patch_browser.touch_browser_app.SurgeMonitor", return_value=surge_monitor),
            mock.patch("patch_browser.touch_browser_app.SurgeCpuMonitor", return_value=cpu_monitor),
            mock.patch("patch_browser.touch_browser_app.BacklightController", return_value=backlight),
            mock.patch("patch_browser.touch_browser_app.evdev_bridge_enabled", return_value=False),
            mock.patch.object(TouchPatchBrowser, "_start_background_scan"),
            mock.patch.object(TouchPatchBrowser, "_wait_for_initial_scan"),
            mock.patch.object(TouchPatchBrowser, "_complete_boot_splash"),
            mock.patch.object(TouchPatchBrowser, "_start_evdev_touch_bridge"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        return TouchPatchBrowser()

    def test_handle_event_quit_stops_run_loop(self) -> None:
        browser = self._make_browser()
        import pygame

        browser._handle_event(pygame.event.Event(pygame.QUIT))
        self.assertFalse(browser._running)

    def test_handle_event_mousebuttonup_and_draw_do_not_raise(self) -> None:
        browser = self._make_browser()
        import pygame

        browser.screen_state = Screen.BROWSER
        browser.left_nav_collapsed = True
        browser.nav_list.items = []
        browser.nav_list.take_tap_index = mock.Mock(return_value=None)

        event = pygame.event.Event(
            pygame.MOUSEBUTTONUP,
            {"pos": (400, 240), "button": 1},
        )
        browser._handle_event(event)
        browser._draw()


if __name__ == "__main__":
    unittest.main()
