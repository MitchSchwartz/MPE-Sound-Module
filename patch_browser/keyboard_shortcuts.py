"""Keyboard chords for the touch browser.

The appliance normally runs with no keyboard attached: the DSI panel and its
touchscreen are the whole interface. Everything here is therefore *inert by
default* — no keyboard, no KEYDOWN events, no dispatch — and that is the design
rather than a limitation.

It matters most for the terminal (#113). That issue was written around "there is
no physical keyboard", which made an on-screen keyboard driving a PTY the only
way in, and made stranding the user at an unreachable console the dominant risk.
Entering by chord inverts it: a keyboard is attached *by construction* whenever
the terminal can be reached, so the thing that rescues you is the thing that got
you in. Do not add a touch affordance that opens the terminal — that would
restore exactly the failure this removes.

Chords are deliberately Ctrl+Alt+<key>. Fullscreen KMSDRM owns the console, so
no desktop environment competes for them, and requiring two modifiers keeps a
stray key from a device that enumerates as a keyboard from firing anything.

No pygame import: the key and modifier values below are SDL's, which pygame
mirrors exactly. Keeping this module pure makes the chord table testable without
a display, and ``tests/test_keyboard_shortcuts.py`` asserts the constants still
match real pygame wherever pygame is actually installed.
"""

from __future__ import annotations

# SDL keycodes (ASCII for printable keys).
K_T = 116
K_R = 114

# SDL modifier bits.
KMOD_LCTRL = 0x0040
KMOD_RCTRL = 0x0080
KMOD_LALT = 0x0100
KMOD_RALT = 0x0200
KMOD_CTRL = KMOD_LCTRL | KMOD_RCTRL
KMOD_ALT = KMOD_LALT | KMOD_RALT

# Action names, not screens: a chord requests an intent and the app decides what
# to show. Keeps this module free of UI state.
ACTION_TERMINAL = "terminal"
ACTION_RESTART_BENCH = "restart_bench"

_CHORDS: dict[int, str] = {
    K_T: ACTION_TERMINAL,
    K_R: ACTION_RESTART_BENCH,
}

_KEY_NAMES: dict[int, str] = {K_T: "T", K_R: "R"}


def chord_label(action: str) -> str:
    """Human-readable chord for the settings/help text, or ``""``."""
    for key, name in _CHORDS.items():
        if name == action:
            return f"Ctrl+Alt+{_KEY_NAMES.get(key, '?')}"
    return ""


def match_chord(key: int, mods: int) -> str | None:
    """Return the action for a KEYDOWN, or ``None``.

    Requires Ctrl AND Alt. Extra modifiers (shift, caps lock, a held meta key)
    are tolerated: rejecting them would make the chord fail intermittently for
    reasons the user cannot see, which is worse than firing one action too
    readily behind a confirm screen.
    """
    if not (mods & KMOD_CTRL) or not (mods & KMOD_ALT):
        return None
    return _CHORDS.get(key)
