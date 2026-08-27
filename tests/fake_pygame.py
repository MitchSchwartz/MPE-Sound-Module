"""Shared pygame stub for touch-browser unit tests (no display)."""

from __future__ import annotations

import sys
import types
from unittest import mock


def install_fake_pygame(*, minimal: bool = False) -> None:
    """Install a process-wide fake pygame module if one is not already complete."""
    existing = sys.modules.get("pygame")
    if minimal:
        if isinstance(existing, types.ModuleType):
            if not hasattr(existing, "error"):
                existing.error = type("error", (Exception,), {})
            return
        fake = types.ModuleType("pygame")
        fake.error = type("error", (Exception,), {})
        sys.modules["pygame"] = fake
        return

    if (
        isinstance(existing, types.ModuleType)
        and hasattr(existing, "display")
        and hasattr(existing, "Rect")
        and hasattr(existing, "font")
        and hasattr(existing, "draw")
    ):
        if not hasattr(existing, "error"):
            existing.error = type("error", (Exception,), {})
        return

    fake = types.ModuleType("pygame")

    fake.QUIT = 12
    # SDL key/modifier values (#113 keyboard shortcuts). Real values, so a
    # test that reasons about chords reasons about the same numbers the
    # appliance will see.
    fake.KEYDOWN = 768
    fake.K_ESCAPE = 27
    fake.K_t = 116
    fake.K_r = 114
    fake.KMOD_LCTRL = 0x0040
    fake.KMOD_RCTRL = 0x0080
    fake.KMOD_CTRL = 0x00C0
    fake.KMOD_LALT = 0x0100
    fake.KMOD_RALT = 0x0200
    fake.KMOD_ALT = 0x0300
    fake.MOUSEBUTTONDOWN = 1025
    fake.MOUSEBUTTONUP = 1026
    fake.MOUSEMOTION = 1024
    fake.FINGERDOWN = 1792
    fake.FINGERUP = 1793
    fake.FINGERMOTION = 1794
    fake.SRCALPHA = 65536
    fake.FULLSCREEN = 1
    fake.error = type("error", (Exception,), {})

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

        def pygame_rect(self):
            return self

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
    fake.time = types.SimpleNamespace(
        Clock=mock.Mock(return_value=mock.Mock(get_time=mock.Mock(return_value=16)))
    )
    fake.font = types.SimpleNamespace(
        Font=_FakeFont,
        match_font=mock.Mock(return_value="/tmp/fake-font.ttf"),
    )
    fake.draw = types.SimpleNamespace(
        rect=mock.Mock(),
        line=mock.Mock(),
        lines=mock.Mock(),
        polygon=mock.Mock(),
        circle=mock.Mock(),
    )

    sys.modules["pygame"] = fake
