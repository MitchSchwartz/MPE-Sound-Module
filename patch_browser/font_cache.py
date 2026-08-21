"""Memoising wrapper around ``pygame.font.Font``.

The touch UI redraws the whole screen every frame at 60 Hz with no damage tracking,
so every visible glyph run was re-rasterised 60 times a second — for text that changes
on touch, not per frame. Measured on the appliance 2026-08-17: the UI process held a
full core (101 jiffies/s) while idle, and because a JACK client lives in that same
process, jackd's realtime graph cycle waits on a GIL held by that draw loop.

Glyph rasterisation is pure: the same (text, antialias, colour) always yields the same
pixels, so caching is safe *provided callers treat the surface as read-only*. Nothing
in ``patch_browser`` mutates a rendered surface — every call site blits it directly and
drops it — so this wrapper is a drop-in for the three shared fonts.

If a caller ever needs to mutate a rendered surface (``set_alpha``, ``fill``), it must
take its own ``.copy()`` first, or the mutation will be visible to every later frame
that hits the same cache entry.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

# Enough for every string on screen across all views, with room for the changing ones
# (patch names, dB readouts). Far past this and we would be caching per-frame text,
# which is a sign the caller should be formatting less often instead.
DEFAULT_MAX_ENTRIES = 768


def _colour_key(colour: Any) -> Any:
    """Hashable key for a pygame colour, which may be a tuple, int, str or Color."""
    if colour is None:
        return None
    if isinstance(colour, (int, str)):
        return colour
    try:
        return (colour[0], colour[1], colour[2], colour[3])
    except (TypeError, IndexError, KeyError):
        pass
    try:
        return tuple(colour)
    except TypeError:
        return repr(colour)


class CachedFont:
    """``pygame.font.Font`` with memoised ``render()`` and ``size()``.

    Unknown attributes pass through to the wrapped font, so this stands in wherever a
    real Font is expected (``get_height``, ``get_linesize``, metrics, and so on).
    """

    def __init__(self, font: Any, *, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self._font = font
        self._max_entries = max_entries
        self._render_cache: OrderedDict[Any, Any] = OrderedDict()
        self._size_cache: OrderedDict[Any, Any] = OrderedDict()
        self.render_hits = 0
        self.render_misses = 0

    @property
    def font(self) -> Any:
        """The wrapped pygame Font — for callers that genuinely need a fresh surface."""
        return self._font

    def _store(self, cache: OrderedDict, key: Any, value: Any) -> Any:
        cache[key] = value
        if len(cache) > self._max_entries:
            cache.popitem(last=False)
        return value

    def render(
        self,
        text: str,
        antialias: bool = True,
        color: Any = None,
        background: Any = None,
    ) -> Any:
        """Cached glyph raster. **The returned surface is shared — do not mutate it.**"""
        key = (text, bool(antialias), _colour_key(color), _colour_key(background))
        cached = self._render_cache.get(key)
        if cached is not None:
            self._render_cache.move_to_end(key)
            self.render_hits += 1
            return cached
        self.render_misses += 1
        if background is None:
            surface = self._font.render(text, antialias, color)
        else:
            surface = self._font.render(text, antialias, color, background)
        return self._store(self._render_cache, key, surface)

    def size(self, text: str) -> Any:
        """Cached text metrics — ``wrap_text_lines`` calls this per word, per frame."""
        cached = self._size_cache.get(text)
        if cached is not None:
            self._size_cache.move_to_end(text)
            return cached
        return self._store(self._size_cache, text, self._font.size(text))

    def clear(self) -> None:
        """Drop cached rasters — call on theme change so recoloured text re-renders."""
        self._render_cache.clear()
        self._size_cache.clear()

    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes this wrapper does not define.
        return getattr(self._font, name)
