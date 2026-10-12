"""Play-screen FX controls and the Settings default, at the 800x480 DSI size."""

from __future__ import annotations

import itertools
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.fake_pygame import install_fake_pygame

install_fake_pygame()

from patch_browser.fx_policy import (  # noqa: E402
    CC1_TO_FX,
    CC1_TO_SURGE,
    MOD_WHEEL_EFFECT,
    MOD_WHEEL_PATCH_FIRST,
    FxPolicy,
    chain_count,
    chain_labels,
    chain_names,
    mod_knob_label,
    mod_knob_labels,
)
from patch_browser import fx_host_protocol  # noqa: E402
from patch_browser.geometry import Rect  # noqa: E402
from patch_browser.touch_browser_app import TouchPatchBrowser  # noqa: E402
from patch_browser.touch_ui_constants import (  # noqa: E402
    TAP_MOVE_THRESHOLD_PX,
    COMPACT_TOGGLE_W,
    SETTINGS_TOGGLE_W,
    BROWSE_FILTER_W,
    BROWSE_OFFSET_HOME,
    BROWSE_PATCH_W,
    BROWSE_TRACK_W,
    LEFT_NAV_WIDTH,
)
from patch_browser.touch_ui_enums import Screen  # noqa: E402

SCREEN_W, SCREEN_H = 800, 480

#: Pixel widths of every label the switch can show (`mod_knob_labels`) in the
#: UI font (DejaVu Sans, which the appliance loads first), measured with
#: pygame-ce 2.5.8 on 2026-10-11. The fake pygame cannot measure glyphs, so the
#: fit tests measure through these (`_measure`), and a label missing from the
#: table is a KeyError. Change a label and
#: `test_measured_widths_cover_every_label` fails until re-measured.
LABEL_PX = {
    22: {
        "Mod knob \u2192 Patch mod": 232,
        "Mod knob \u2192 No FX": 184,
        "Mod knob \u2192 Chorus": 196,
        "Mod knob \u2192 1 FX": 170,
        "Mod knob \u2192 Drive": 179,
        "Mod knob \u2192 Drive+Chorus": 266,
        "Mod knob \u2192 2 FX": 170,
        "Mod knob \u2192 Phaser": 194,
        "Mod knob \u2192 Phaser+Chorus": 280,
        "Mod knob \u2192 Drive+Phaser": 264,
        "Mod knob \u2192 Drive+Phaser+Chorus": 350,
        "Mod knob \u2192 3 FX": 170,
        "Mod knob \u2192 Tremolo": 207,
        "Mod knob \u2192 Chorus+Tremolo": 294,
        "Mod knob \u2192 Drive+Tremolo": 277,
        "Mod knob \u2192 Drive+Chorus+Tremolo": 364,
        "Mod knob \u2192 Phaser+Tremolo": 291,
        "Mod knob \u2192 Phaser+Chorus+Tremolo": 378,
        "Mod knob \u2192 Drive+Phaser+Tremolo": 361,
        "Mod knob \u2192 Drive+Phaser+Chorus+Tremolo": 448,
        "Mod knob \u2192 4 FX": 170,
        "Mod knob \u2192 Flanger": 202,
        "Mod knob \u2192 Flanger+Chorus": 288,
        "Mod knob \u2192 Drive+Flanger": 272,
        "Mod knob \u2192 Drive+Flanger+Chorus": 358,
        "Mod knob \u2192 Phaser+Flanger": 286,
        "Mod knob \u2192 Phaser+Flanger+Chorus": 372,
        "Mod knob \u2192 Drive+Phaser+Flanger": 356,
        "Mod knob \u2192 Drive+Phaser+Flanger+Chorus": 442,
        "Mod knob \u2192 Flanger+Tremolo": 299,
        "Mod knob \u2192 Flanger+Chorus+Tremolo": 386,
        "Mod knob \u2192 Drive+Flanger+Tremolo": 369,
        "Mod knob \u2192 Drive+Flanger+Chorus+Tremolo": 456,
        "Mod knob \u2192 Phaser+Flanger+Tremolo": 383,
        "Mod knob \u2192 Phaser+Flanger+Chorus+Tremolo": 470,
        "Mod knob \u2192 Drive+Phaser+Flanger+Tremolo": 453,
        "Mod knob \u2192 Drive+Phaser+Flanger+Chorus+Tremolo": 540,
        "Mod knob \u2192 5 FX": 170,
    },
    18: {
        "Mod knob \u2192 Patch mod": 188,
        "Mod knob \u2192 No FX": 149,
        "Mod knob \u2192 Chorus": 158,
        "Mod knob \u2192 1 FX": 137,
        "Mod knob \u2192 Drive": 146,
        "Mod knob \u2192 Drive+Chorus": 216,
        "Mod knob \u2192 2 FX": 137,
        "Mod knob \u2192 Phaser": 157,
        "Mod knob \u2192 Phaser+Chorus": 227,
        "Mod knob \u2192 Drive+Phaser": 215,
        "Mod knob \u2192 Drive+Phaser+Chorus": 285,
        "Mod knob \u2192 3 FX": 137,
        "Mod knob \u2192 Tremolo": 170,
        "Mod knob \u2192 Chorus+Tremolo": 240,
        "Mod knob \u2192 Drive+Tremolo": 228,
        "Mod knob \u2192 Drive+Chorus+Tremolo": 298,
        "Mod knob \u2192 Phaser+Tremolo": 239,
        "Mod knob \u2192 Phaser+Chorus+Tremolo": 309,
        "Mod knob \u2192 Drive+Phaser+Tremolo": 297,
        "Mod knob \u2192 Drive+Phaser+Chorus+Tremolo": 367,
        "Mod knob \u2192 4 FX": 137,
        "Mod knob \u2192 Flanger": 163,
        "Mod knob \u2192 Flanger+Chorus": 233,
        "Mod knob \u2192 Drive+Flanger": 221,
        "Mod knob \u2192 Drive+Flanger+Chorus": 291,
        "Mod knob \u2192 Phaser+Flanger": 232,
        "Mod knob \u2192 Phaser+Flanger+Chorus": 302,
        "Mod knob \u2192 Drive+Phaser+Flanger": 290,
        "Mod knob \u2192 Drive+Phaser+Flanger+Chorus": 360,
        "Mod knob \u2192 Flanger+Tremolo": 245,
        "Mod knob \u2192 Flanger+Chorus+Tremolo": 315,
        "Mod knob \u2192 Drive+Flanger+Tremolo": 303,
        "Mod knob \u2192 Drive+Flanger+Chorus+Tremolo": 373,
        "Mod knob \u2192 Phaser+Flanger+Tremolo": 314,
        "Mod knob \u2192 Phaser+Flanger+Chorus+Tremolo": 384,
        "Mod knob \u2192 Drive+Phaser+Flanger+Tremolo": 372,
        "Mod knob \u2192 Drive+Phaser+Flanger+Chorus+Tremolo": 442,
        "Mod knob \u2192 5 FX": 137,
    },
}

#: Fader labels in the 18 px font (`font_sm`), measured the same way: Patch mod
#: and every label the Effect fader can show (`chain_labels`).
FADER_LABEL_PX_18 = {
    "Patch mod": 86,
    "No FX": 47,
    "Chorus": 56,
    "1 FX": 35,
    "Drive": 44,
    "Drive+Chorus": 114,
    "2 FX": 35,
    "Phaser": 55,
    "Phaser+Chorus": 125,
    "Drive+Phaser": 113,
    "Drive+Phaser+Chorus": 183,
    "3 FX": 35,
    "Tremolo": 69,
    "Chorus+Tremolo": 138,
    "Drive+Tremolo": 126,
    "Drive+Chorus+Tremolo": 196,
    "Phaser+Tremolo": 137,
    "Phaser+Chorus+Tremolo": 207,
    "Drive+Phaser+Tremolo": 195,
    "Drive+Phaser+Chorus+Tremolo": 265,
    "4 FX": 35,
    "Flanger": 61,
    "Flanger+Chorus": 131,
    "Drive+Flanger": 119,
    "Drive+Flanger+Chorus": 189,
    "Phaser+Flanger": 130,
    "Phaser+Flanger+Chorus": 200,
    "Drive+Phaser+Flanger": 188,
    "Drive+Phaser+Flanger+Chorus": 258,
    "Flanger+Tremolo": 143,
    "Flanger+Chorus+Tremolo": 213,
    "Drive+Flanger+Tremolo": 201,
    "Drive+Flanger+Chorus+Tremolo": 271,
    "Phaser+Flanger+Tremolo": 212,
    "Phaser+Flanger+Chorus+Tremolo": 282,
    "Drive+Phaser+Flanger+Tremolo": 270,
    "Drive+Phaser+Flanger+Chorus+Tremolo": 340,
    "5 FX": 35,
}
#: The smallest touch target the picker's controls may have.
MIN_TARGET_PX = 44

#: (switch setting, patch routes the mod wheel) -> what the label says.
LABEL_TRUTH = [
    (MOD_WHEEL_PATCH_FIRST, True, "Mod knob \u2192 Patch mod"),
    (MOD_WHEEL_PATCH_FIRST, None, "Mod knob \u2192 Patch mod"),  # unreadable patch: Surge
    (MOD_WHEEL_PATCH_FIRST, False, "Mod knob \u2192 No FX"),  # factory default: no effect
    (MOD_WHEEL_EFFECT, True, "Mod knob \u2192 No FX"),
    (MOD_WHEEL_EFFECT, False, "Mod knob \u2192 No FX"),
    (MOD_WHEEL_EFFECT, None, "Mod knob \u2192 No FX"),
]


ALL_MASKS = tuple(range(fx_host_protocol.MASK_ALL + 1))
C, D, P, T = (1 << fx_host_protocol.EFFECT_CHORUS), (1 << fx_host_protocol.EFFECT_TUBE_DRIVE), (
    1 << fx_host_protocol.EFFECT_PHASER
), (1 << fx_host_protocol.EFFECT_TREMOLO)


def _overlaps(a: Rect, b: Rect) -> bool:
    return a.x < b.right and b.x < a.right and a.y < b.bottom and b.y < a.bottom


def _center(rect: Rect) -> tuple[int, int]:
    return rect.centerx, rect.centery


def _inside(inner: Rect, outer: Rect) -> bool:
    return (
        inner.x >= outer.x
        and inner.y >= outer.y
        and inner.right <= outer.right
        and inner.bottom <= outer.bottom
    )


class _BrowserCase(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.prefs = self.root / "ui.json"
        for target in ("patch_browser.ui_prefs.UI_STATE_FILE",):
            patcher = mock.patch(target, self.prefs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _make_browser(self, *, fx_enabled: bool) -> TouchPatchBrowser:
        scanner = mock.Mock()
        scanner.load_last_patch.return_value = None
        scanner.get_categories.return_value = []
        scanner.get_subfolders.return_value = []
        scanner.get_patches_in_folder.return_value = []
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
        # Real numbers for the existing faders so the whole strip can draw.
        loader.normalization.get_user_trim_db.return_value = 0.0
        loader.normalization.get_slider_default_gain_db.return_value = 0.0
        loader.hold.get_effective_hold_mult.return_value = 1.0
        loader.pressure.get_slider_default_floor.return_value = 0.0
        loader.pressure.get_user_touch_offset.return_value = 0.0

        surge_monitor = mock.Mock()
        surge_monitor.get_status_summary.return_value = {"status": "Running", "details": "ok", "can_restart": False}
        cpu_monitor = mock.Mock()
        cpu_monitor.snapshot.return_value = {"online": True, "percent": 12.5}
        backlight = mock.Mock()
        backlight.get_percent.return_value = 100
        fake_screen = mock.Mock()
        fake_screen.get_size.return_value = (SCREEN_W, SCREEN_H)
        self.policy = FxPolicy(
            enabled=fx_enabled,
            route_path=self.root / "fx-route.json",
            levels_path=self.root / "fx-levels.json",
        )
        patches = [
            mock.patch("patch_browser.touch_browser_app.PatchScanner", return_value=scanner),
            mock.patch("patch_browser.touch_browser_app.PatchLoader", return_value=loader),
            mock.patch("patch_browser.touch_browser_app.FxPolicy", return_value=self.policy),
            mock.patch("patch_browser.touch_browser_app.SurgeMonitor", return_value=surge_monitor),
            mock.patch("patch_browser.touch_browser_app.SurgeCpuMonitor", return_value=cpu_monitor),
            mock.patch("patch_browser.touch_browser_app.BacklightController", return_value=backlight),
            mock.patch("patch_browser.touch_browser_app.evdev_bridge_enabled", return_value=False),
            mock.patch("patch_browser.touch_browser_app.acquire_browser_display", return_value=fake_screen),
            mock.patch.object(TouchPatchBrowser, "_start_background_scan"),
            mock.patch.object(TouchPatchBrowser, "_wait_for_initial_scan"),
            mock.patch.object(TouchPatchBrowser, "_complete_boot_splash"),
            mock.patch.object(TouchPatchBrowser, "_paint_boot_splash_frame"),
            mock.patch.object(TouchPatchBrowser, "_start_evdev_touch_bridge"),
            mock.patch(
                "patch_browser.touch_browser_wifi_modal.wifi_settings_row_label",
                return_value="Wi‑Fi — Not connected",
            ),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        browser = TouchPatchBrowser()
        browser.detail_patch = {
            "name": "A Patch With A Fairly Long Name",
            "category": "Keys",
            "path": str(self.root / "p.fxp"),
        }
        browser._layout()
        return browser

    def _measure(self, browser) -> None:
        """Measure text as the appliance's font does (the tables above)."""

        def px(font, text: str) -> int:
            return (FADER_LABEL_PX_18 if font is browser.font_sm and text in FADER_LABEL_PX_18 else LABEL_PX[
                18 if font is browser.font_sm else 22
            ])[text]

        patcher = mock.patch.object(browser, "_text_px", side_effect=px)
        patcher.start()
        self.addCleanup(patcher.stop)


class PlayScreenLayoutTests(_BrowserCase):
    def test_carousel_widths_follow_the_narrower_nav(self) -> None:
        self.assertLess(LEFT_NAV_WIDTH, 268)
        self.assertEqual(BROWSE_FILTER_W + LEFT_NAV_WIDTH, SCREEN_W)
        self.assertEqual(BROWSE_PATCH_W + LEFT_NAV_WIDTH, SCREEN_W)
        self.assertEqual(BROWSE_TRACK_W, BROWSE_FILTER_W + LEFT_NAV_WIDTH + BROWSE_PATCH_W)
        self.assertEqual(BROWSE_OFFSET_HOME, -BROWSE_FILTER_W)

    def test_six_faders_and_the_switch_fit_without_overlap(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        ids = [c.channel_id for c in browser.mixer_channels]
        self.assertEqual(ids, ["volume", "tail", "touch", "norm", "patch_mod", "effect"])
        screen = Rect(0, 0, SCREEN_W, SCREEN_H)
        main = browser.main_rect
        self.assertTrue(_inside(main, screen), main)
        columns = [c.column_rect for c in browser.mixer_channels]
        for col in columns:
            self.assertTrue(_inside(col, main), col)
        for a, b in itertools.combinations(columns, 2):
            self.assertFalse(_overlaps(a, b), (a, b))
        row = [
            browser.mod_wheel_switch_rect,
            browser.normalize_btn,
            browser.favorites_btn,
        ]
        for rect in row:
            self.assertGreater(rect.w, 0)
            self.assertTrue(_inside(rect, main), rect)
            for col in columns:
                self.assertFalse(_overlaps(rect, col), (rect, col))
        for a, b in itertools.combinations(row, 2):
            self.assertFalse(_overlaps(a, b), (a, b))
        self.assertFalse(_overlaps(browser.left_panel_rect, main))

    def test_fader_labels_do_not_collide(self) -> None:
        """'Patch mod' is wider than its column; it must still clear its neighbours."""
        browser = self._make_browser(fx_enabled=True)
        channels = browser.mixer_channels
        width = browser.font_sm.size
        for left, right in zip(channels, channels[1:]):
            lw = width(left.label)[0]
            rw = width(right.label)[0]
            left_edge = left.column_rect.x + (left.column_rect.w + lw) // 2
            right_edge = right.column_rect.x + (right.column_rect.w - rw) // 2
            self.assertLess(left_edge, right_edge, (left.label, right.label))

    def test_nav_header_buttons_do_not_overlap(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        buttons = [
            browser.nav_back_btn,
            browser.nav_current_btn,
            browser.nav_all_btn,
            browser.browse_looper_open_btn,
            browser.browse_filter_open_btn,
            browser.nav_collapse_btn,
        ]
        buttons = [b for b in buttons if b.w > 0]
        self.assertGreaterEqual(len(buttons), 5)
        for a, b in itertools.combinations(buttons, 2):
            self.assertFalse(_overlaps(a, b), (a, b))
        for b in buttons:
            self.assertTrue(_inside(b, browser.nav_header_rect), b)

    def test_fx_off_hides_switch_and_effect_but_keeps_patch_mod(self) -> None:
        browser = self._make_browser(fx_enabled=False)
        ids = [c.channel_id for c in browser.mixer_channels]
        self.assertIn("patch_mod", ids)
        self.assertNotIn("effect", ids)
        self.assertEqual(browser.mod_wheel_switch_rect.w, 0)

    def test_switch_label_fits_on_one_line_in_both_states_and_both_navs(self) -> None:
        """At 800x480, nav open (250 px) and collapsed, every mask, both
        destinations: the label chosen fits, and it is the names whenever they fit."""
        for collapsed in (False, True):
            browser = self._make_browser(fx_enabled=True)
            self._measure(browser)
            browser.left_nav_collapsed = collapsed
            browser._layout()
            rect = browser.mod_wheel_switch_rect
            self.assertTrue(_inside(rect, browser.main_rect), rect)
            for other in [browser.normalize_btn, browser.favorites_btn] + [c.column_rect for c in browser.mixer_channels]:
                self.assertFalse(_overlaps(rect, other), (rect, other))
            # The same budget `_draw_normalize_toggle` gives its label.
            compact = rect.w < 180
            size = 18 if compact else 22
            toggle_w = COMPACT_TOGGLE_W if compact else SETTINGS_TOGGLE_W
            budget = rect.w - toggle_w - 28
            for mask, target in itertools.product(ALL_MASKS, (MOD_WHEEL_PATCH_FIRST, MOD_WHEEL_EFFECT)):
                with self.subTest(collapsed=collapsed, mask=mask, target=target):
                    self.policy.uses_mod_wheel = True
                    self.policy.target = target
                    self.policy.set_mask(mask)
                    label = browser._mod_knob_switch_label()
                    self.assertLessEqual(LABEL_PX[size][label], budget, (rect, size, label))
                    if target == MOD_WHEEL_PATCH_FIRST:
                        self.assertEqual(label, "Mod knob \u2192 Patch mod")
                        continue
                    names = "Mod knob \u2192 " + chain_names(mask)
                    want = names if LABEL_PX[size][names] <= budget else "Mod knob \u2192 " + chain_count(mask)
                    self.assertEqual(label, want)

    def test_switch_names_one_effect_and_counts_two_or_more(self) -> None:
        """What the measured budget comes to on the 800x480 screen today."""
        browser = self._make_browser(fx_enabled=True)
        self._measure(browser)
        self.policy.target = MOD_WHEEL_EFFECT
        for mask, want in ((0, "No FX"), (T, "Tremolo"), (C | D, "2 FX"), (C | D | P | T, "4 FX")):
            with self.subTest(mask=mask):
                self.policy.set_mask(mask)
                self.assertEqual(browser._mod_knob_switch_label(), "Mod knob \u2192 " + want)

    def test_measured_widths_cover_every_label(self) -> None:
        for size, widths in LABEL_PX.items():
            self.assertEqual(set(widths), set(mod_knob_labels()), size)
        self.assertEqual(set(FADER_LABEL_PX_18), {"Patch mod"} | set(chain_labels()))

    def test_draw_does_not_raise(self) -> None:
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                browser = self._make_browser(fx_enabled=enabled)
                browser.screen_state = Screen.BROWSER
                # The fake pygame cannot draw the expanded nav's icons; the
                # collapsed nav still draws the detail pane and the FX row.
                browser.left_nav_collapsed = True
                browser._layout()
                browser._draw()


class PlayScreenSwitchTests(_BrowserCase):
    def _drawn_kwargs(self, browser) -> tuple[bool, dict]:
        calls = []
        with mock.patch.object(
            browser, "_draw_normalize_toggle", side_effect=lambda rect, on, **kw: calls.append((rect, on, kw))
        ):
            browser._draw_mod_wheel_row()
        self.assertEqual(len(calls), 1)
        rect, on, kw = calls[0]
        self.assertEqual(rect, browser.mod_wheel_switch_rect)
        return on, kw

    def _drawn_switch(self, browser) -> tuple[bool, str]:
        on, kw = self._drawn_kwargs(browser)
        return on, kw["label"]

    def test_label_states_where_the_knob_goes_now(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        for target, uses, label in LABEL_TRUTH:
            with self.subTest(target=target, uses=uses):
                self.policy.uses_mod_wheel = uses
                self.policy.target = target
                on, drawn = self._drawn_switch(browser)
                self.assertEqual(drawn, label)
                # The switch is the setting, except locked, where it shows Effect.
                self.assertEqual(on, uses is False or target == MOD_WHEEL_EFFECT)

    def test_label_names_the_faders_it_means(self) -> None:
        """The switch names the Patch mod fader, or the same chain the Effect
        fader names (in full, or counted where the switch has less room)."""
        browser = self._make_browser(fx_enabled=True)
        self._measure(browser)
        browser.left_nav_collapsed = True
        browser._layout()
        names = {c.channel_id: browser._mixer_channel_label(c) for c in browser.mixer_channels}
        self.assertTrue(mod_knob_label(CC1_TO_SURGE).endswith(names["patch_mod"]))
        self.policy.target = MOD_WHEEL_EFFECT
        for mask in ALL_MASKS:
            with self.subTest(mask=mask):
                self.policy.set_mask(mask)
                fader = browser._mixer_channel_label(next(c for c in browser.mixer_channels if c.channel_id == "effect"))
                self.assertIn(fader, (chain_names(mask), chain_count(mask)))
                tail = browser._mod_knob_switch_label().removeprefix("Mod knob \u2192 ")
                self.assertIn(tail, (chain_names(mask), chain_count(mask)))

    def test_tap_relabels_the_switch(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        self.policy.uses_mod_wheel = True
        self.assertEqual(self._drawn_switch(browser)[1], "Mod knob \u2192 Patch mod")
        browser._handle_browser_tap(_center(browser.mod_wheel_switch_rect))
        self.assertEqual(self._drawn_switch(browser)[1], "Mod knob \u2192 No FX")

    def test_no_explanation_text_on_the_play_screen(self) -> None:
        """The readout under the faders is gone, with FX on or off."""
        import patch_browser.touch_browser_draw as draw_mod

        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                browser = self._make_browser(fx_enabled=enabled)
                browser.screen_state = Screen.BROWSER
                browser.left_nav_collapsed = True
                browser._layout()
                drawn: list[str] = []
                real = draw_mod.blit_text_block

                def record(screen, font, lines, *a, **kw):
                    drawn.extend(lines)
                    return real(screen, font, lines, *a, **kw)

                with mock.patch.object(draw_mod, "blit_text_block", side_effect=record):
                    browser._draw()
                self.assertTrue(drawn)  # the instrument sees the title block
                for line in drawn:
                    self.assertNotIn("wheel", line.lower(), line)

    def test_tap_flips_the_current_target_not_the_default(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        self.assertEqual(self.policy.target, MOD_WHEEL_PATCH_FIRST)
        browser._handle_browser_tap(_center(browser.mod_wheel_switch_rect))
        self.assertEqual(self.policy.target, MOD_WHEEL_EFFECT)
        self.assertEqual(self.policy.global_target, MOD_WHEEL_PATCH_FIRST)
        self.assertFalse(self.prefs.exists() and "mod_wheel_target" in json.loads(self.prefs.read_text()))
        browser._handle_browser_tap(_center(browser.mod_wheel_switch_rect))
        self.assertEqual(self.policy.target, MOD_WHEEL_PATCH_FIRST)

    def test_switch_is_greyed_out_only_when_the_patch_has_no_mod_wheel_routing(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        for target, uses, label in LABEL_TRUTH:
            with self.subTest(target=target, uses=uses):
                self.policy.uses_mod_wheel = uses
                self.policy.target = target
                on, kw = self._drawn_kwargs(browser)
                # Unknown (unreadable patch) stays live, like the fail-safe.
                self.assertIs(kw.get("disabled", False), uses is False)
                self.assertEqual(kw["label"], label)
                # Locked shows the Effect position; otherwise the setting.
                self.assertEqual(on, uses is False or target == MOD_WHEEL_EFFECT)

    def _tap_changes_nothing(self, browser, tap) -> None:
        route = self.root / "fx-route.json"
        before = route.read_bytes() if route.exists() else None
        target = self.policy.target
        with mock.patch.object(browser, "_toast") as toast:
            tap()
        self.assertEqual(self.policy.target, target)
        self.assertEqual(route.read_bytes() if route.exists() else None, before)
        toast.assert_not_called()
        self.assertEqual(self._drawn_switch(browser)[1], "Mod knob \u2192 No FX")

    def test_tap_on_a_greyed_out_switch_changes_and_writes_nothing(self) -> None:
        for target in (MOD_WHEEL_PATCH_FIRST, MOD_WHEEL_EFFECT):
            with self.subTest(target=target):
                browser = self._make_browser(fx_enabled=True)
                self.policy.target = target
                self.policy.uses_mod_wheel = False
                pos = _center(browser.mod_wheel_switch_rect)
                self._tap_changes_nothing(browser, lambda: browser._handle_browser_tap(pos))

                def evdev() -> None:
                    browser._handle_evdev_browser_touch("down", pos)
                    browser._handle_evdev_browser_touch("up", pos)

                self._tap_changes_nothing(browser, evdev)

    def test_unknown_patch_keeps_the_switch_live(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        self.policy.uses_mod_wheel = None
        browser._handle_browser_tap(_center(browser.mod_wheel_switch_rect))
        self.assertEqual(self.policy.target, MOD_WHEEL_EFFECT)
        self.assertEqual(json.loads((self.root / "fx-route.json").read_text())["target"], MOD_WHEEL_EFFECT)

    def test_locked_switch_draws_on_without_touching_the_target(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        plain, wheel = self.root / "plain.fxp", self.root / "wheel.fxp"
        plain.write_bytes(b"hdr<patch><modrouting source=\"2\"/></patch>")
        wheel.write_bytes(b"hdr<patch><modrouting source=\"6\"/></patch>")
        self.assertEqual(self.policy.global_target, MOD_WHEEL_PATCH_FIRST)
        self.policy.on_patch_loaded(plain)
        route = (self.root / "fx-route.json").read_bytes()
        on, kw = self._drawn_kwargs(browser)
        self.assertTrue(on)
        self.assertTrue(kw["disabled"])
        self.assertEqual(self.policy.target, MOD_WHEEL_PATCH_FIRST)
        self.assertEqual((self.root / "fx-route.json").read_bytes(), route)
        self.policy.on_patch_loaded(wheel)
        on, kw = self._drawn_kwargs(browser)
        self.assertFalse(on)  # the real setting again
        self.assertFalse(kw["disabled"])

    def test_loading_a_patch_that_uses_the_wheel_brings_the_switch_back(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        plain, wheel = self.root / "plain.fxp", self.root / "wheel.fxp"
        plain.write_bytes(b"hdr<patch><modrouting source=\"2\"/></patch>")
        wheel.write_bytes(b"hdr<patch><modrouting source=\"6\"/></patch>")
        self.policy.on_patch_loaded(plain)
        self.assertTrue(self._drawn_kwargs(browser)[1]["disabled"])
        self.policy.on_patch_loaded(wheel)
        self.assertFalse(self._drawn_kwargs(browser)[1]["disabled"])
        self.assertEqual(self.policy.target, self.policy.global_target)
        browser._handle_browser_tap(_center(browser.mod_wheel_switch_rect))
        self.assertEqual(self.policy.target, MOD_WHEEL_EFFECT)

    def test_evdev_tap_flips_it_too(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        pos = _center(browser.mod_wheel_switch_rect)
        browser._handle_evdev_browser_touch("down", pos)
        browser._handle_evdev_browser_touch("up", pos)
        self.assertEqual(self.policy.target, MOD_WHEEL_EFFECT)


class EffectFaderTests(_BrowserCase):
    def _effect_channel(self, browser):
        return next(c for c in browser.mixer_channels if c.channel_id == "effect")

    def test_drag_writes_a_request_and_release_lands_the_last_position(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        channel = self._effect_channel(browser)
        top = channel.track_rect.y
        x = channel.column_rect.centerx
        browser._handle_mixer_down((x, channel.track_rect.bottom))
        self.assertTrue(self.policy.dragging_depth)
        browser._handle_mixer_motion((x, top + channel.track_rect.h // 2))
        browser._handle_mixer_motion((x, top))
        browser._persist_mixer_drag()
        self.assertFalse(self.policy.dragging_depth)
        route = json.loads((self.root / "fx-route.json").read_text())
        self.assertEqual(route["depth_request"], 1.0)

    def test_fader_follows_the_knob(self) -> None:
        from patch_browser.mixer_controls import mixer_control_by_id

        browser = self._make_browser(fx_enabled=True)
        (self.root / "fx-levels.json").write_text(json.dumps({"depth": 0.6, "source": "knob", "request": None}))
        self.assertTrue(self.policy.poll_levels())
        self.assertAlmostEqual(mixer_control_by_id(browser, "effect").read(browser), 0.6)


class EffectPickerTests(_BrowserCase):
    """Tap the Effect fader's label, toggle effects; both input paths."""

    DRIVE = fx_host_protocol.EFFECT_TUBE_DRIVE

    def _browser(self, *, fx_enabled: bool = True, collapsed: bool = False):
        browser = self._make_browser(fx_enabled=fx_enabled)
        browser.screen_state = Screen.BROWSER
        browser.left_nav_collapsed = collapsed
        browser._layout()
        return browser

    def _effect_channel(self, browser):
        return next(c for c in browser.mixer_channels if c.channel_id == "effect")

    def _label_pos(self, browser) -> tuple[int, int]:
        return _center(browser._effect_label_rect())

    def _option_pos(self, browser, effect_id: int) -> tuple[int, int]:
        _panel, options, _done = browser._fx_effect_modal_layout()
        return _center(next(rect for rect, eid in options if eid == effect_id))

    # Two input paths. pygame: SDL mouse events into _handle_event. evdev: the
    # bridge's queue, drained as the main loop does (browser gestures go to the
    # evdev handler, modal ones are injected as pointer events).
    def _tap_pygame(self, browser, pos, end=None) -> None:
        import pygame

        browser._handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": pos, "button": 1}))
        if end is not None:
            browser._handle_event(
                pygame.event.Event(pygame.MOUSEMOTION, {"pos": end, "rel": (0, 0), "buttons": (1, 0, 0)})
            )
        browser._handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": end or pos, "button": 1}))

    def _tap_evdev(self, browser, pos, end=None) -> None:
        browser._evdev_touch_queue.put(("down", pos))
        browser._drain_evdev_touch_queue()
        if end is not None:
            browser._evdev_touch_queue.put(("motion", end))
            browser._drain_evdev_touch_queue()
        browser._evdev_touch_queue.put(("up", end or pos))
        browser._drain_evdev_touch_queue()

    PATHS = ("pygame", "evdev")

    def _tap(self, path, browser, pos, end=None) -> None:
        (self._tap_pygame if path == "pygame" else self._tap_evdev)(browser, pos, end)

    def _rendered_texts(self, browser) -> list[str]:
        texts: list[str] = []
        for font in (browser.font_sm, browser.font_md):
            real = font.render

            def record(text, *a, _real=real, **kw):
                texts.append(text)
                return _real(text, *a, **kw)

            patcher = mock.patch.object(font, "render", side_effect=record)
            patcher.start()
            self.addCleanup(patcher.stop)
        browser._draw()
        return texts

    def _effect_label(self, browser) -> str:
        return browser._mixer_channel_label(self._effect_channel(browser))

    def test_label_names_the_enabled_effects(self) -> None:
        browser = self._browser(collapsed=True)
        self._measure(browser)
        self.assertEqual(self._effect_label(browser), "No FX")  # factory default
        # Changed without a relayout: the next frame names the new chain.
        self.policy.set_mask(D | C)
        texts = self._rendered_texts(browser)
        self.assertIn("Drive+Chorus", texts)
        self.assertNotIn("No FX", texts)
        self.assertNotIn("Effect", texts)

    def test_fader_label_in_every_state_and_both_navs(self) -> None:
        """Names in run order where they fit under the fader, else the count,
        "No FX" with none: each clears Patch mod's label and the pane edge."""
        for collapsed in (False, True):
            browser = self._browser(collapsed=collapsed)
            self._measure(browser)
            effect = self._effect_channel(browser)
            patch_mod = next(c for c in browser.mixer_channels if c.channel_id == "patch_mod")
            pm_right = patch_mod.column_rect.x + (patch_mod.column_rect.w + FADER_LABEL_PX_18["Patch mod"]) // 2

            def clears(text: str) -> bool:
                w = FADER_LABEL_PX_18[text]
                left = effect.column_rect.x + (effect.column_rect.w - w) // 2
                return pm_right < left and left + w <= browser.main_rect.right

            for mask in ALL_MASKS:
                with self.subTest(collapsed=collapsed, mask=mask):
                    self.policy.set_mask(mask)
                    label = self._effect_label(browser)
                    self.assertTrue(clears(label), label)
                    self.assertEqual(label, chain_names(mask) if clears(chain_names(mask)) else chain_count(mask))

    def test_fader_label_respects_the_pane_edge(self) -> None:
        """Unreachable at 800x480 (the Patch mod side binds first), so pinned
        with a narrowed pane: a name that would cross the edge becomes the count."""
        browser = self._browser(collapsed=True)
        self._measure(browser)
        column = self._effect_channel(browser).column_rect
        m = browser.main_rect
        browser.main_rect = Rect(m.x, m.y, column.right + 2 - m.x, m.h)
        self.policy.set_mask(T)
        self.assertEqual(self._effect_label(browser), "1 FX")
        self.policy.set_mask(D)
        self.assertEqual(self._effect_label(browser), "Drive")

    def test_fader_label_on_the_screen_today(self) -> None:
        """What the measured budget comes to: one name with the nav open, two
        short ones with it collapsed."""
        cases = {
            False: {0: "No FX", T: "Tremolo", C | D: "2 FX", C | D | P | T: "4 FX"},
            True: {T: "Tremolo", C | D: "Drive+Chorus", D | P: "Drive+Phaser", C | T: "2 FX", C | D | P: "3 FX"},
        }
        for collapsed, table in cases.items():
            browser = self._browser(collapsed=collapsed)
            self._measure(browser)
            for mask, want in table.items():
                with self.subTest(collapsed=collapsed, mask=mask):
                    self.policy.set_mask(mask)
                    self.assertEqual(self._effect_label(browser), want)

    def test_rows_toggle_and_the_panel_stays_open_until_done(self) -> None:
        for path in self.PATHS:
            with self.subTest(path=path):
                self.prefs.unlink(missing_ok=True)
                browser = self._browser()
                self.policy.set_mask(C)
                self.policy.set_depth(0.4, final=True)
                self._tap(path, browser, self._label_pos(browser))
                self.assertEqual(browser.screen_state, Screen.FX_EFFECT_MODAL)
                self.assertEqual(self.policy.depth, 0.4)  # the label tap moved nothing
                for effect_id, want in ((self.DRIVE, C | D), (fx_host_protocol.EFFECT_TREMOLO, C | D | T),
                                        (fx_host_protocol.EFFECT_CHORUS, D | T)):
                    self._tap(path, browser, self._option_pos(browser, effect_id))
                    self.assertEqual(browser.screen_state, Screen.FX_EFFECT_MODAL)
                    self.assertEqual(self.policy.mask, want)
                    self.assertEqual(json.loads((self.root / "fx-route.json").read_text())["mask"], want)
                    # Per patch only: no global default is written (#142 round 5, F3).
                    prefs = json.loads(self.prefs.read_text()) if self.prefs.exists() else {}
                    self.assertNotIn("fx_mask", prefs)
                    self.assertEqual(self.policy.depth, 0.4)
                self._tap(path, browser, _center(browser._fx_effect_modal_layout()[2]))
                self.assertEqual(browser.screen_state, Screen.BROWSER)
                self.assertEqual(self.policy.mask, D | T)
                self._measure(browser)
                # Nav open: two names do not fit under the fader.
                self.assertEqual(self._effect_label(browser), "2 FX")

    def test_done_and_backdrop_close_it_unchanged(self) -> None:
        for path in self.PATHS:
            for how in ("done", "backdrop"):
                with self.subTest(path=path, how=how):
                    browser = self._browser()
                    self._tap(path, browser, self._label_pos(browser))
                    self.assertEqual(browser.screen_state, Screen.FX_EFFECT_MODAL)
                    route = (self.root / "fx-route.json")
                    before = route.read_bytes() if route.exists() else None
                    if how == "done":
                        pos = _center(browser._fx_effect_modal_layout()[2])
                    else:
                        panel = browser._fx_effect_modal_layout()[0]
                        pos = (panel.x // 2, panel.y // 2 + 1)
                        self.assertFalse(panel.contains(*pos))
                    self._tap(path, browser, pos)
                    self.assertEqual(browser.screen_state, Screen.BROWSER)
                    self.assertEqual(self.policy.mask, 0)
                    self.assertEqual(route.read_bytes() if route.exists() else None, before)

    def test_a_drag_across_a_row_toggles_nothing(self) -> None:
        for path in self.PATHS:
            with self.subTest(path=path):
                browser = self._browser()
                browser._open_fx_effect_modal()
                x, y = self._option_pos(browser, self.DRIVE)
                self._tap(path, browser, (x, y), end=(x + TAP_MOVE_THRESHOLD_PX + 20, y))
                self.assertEqual(self.policy.mask, 0)
                self.assertEqual(browser.screen_state, Screen.FX_EFFECT_MODAL)

    def test_a_drag_from_the_label_opens_nothing_and_moves_nothing(self) -> None:
        for path in self.PATHS:
            with self.subTest(path=path):
                browser = self._browser()
                self.policy.set_depth(0.4, final=True)
                x, y = self._label_pos(browser)
                self._tap(path, browser, (x, y), end=(x, y - 120))
                self.assertEqual(browser.screen_state, Screen.BROWSER)
                self.assertEqual(self.policy.depth, 0.4)
                # A slide that stays on the label is a slide, not a tap.
                label = browser._effect_label_rect()
                start, end = (label.x + 1, label.centery), (label.right - 2, label.centery)
                self.assertGreater(end[0] - start[0], TAP_MOVE_THRESHOLD_PX)
                self._tap(path, browser, start, end=end)
                self.assertEqual(browser.screen_state, Screen.BROWSER)
                self.assertEqual(self.policy.depth, 0.4)

    def test_the_track_is_still_a_fader(self) -> None:
        browser = self._browser()
        channel = self._effect_channel(browser)
        browser._handle_mixer_down((channel.column_rect.centerx, channel.track_rect.bottom - 1))
        self.assertTrue(self.policy.dragging_depth)
        browser._persist_mixer_drag()
        self.assertEqual(browser.screen_state, Screen.BROWSER)

    def test_other_labels_open_nothing(self) -> None:
        browser = self._browser()
        for channel in browser.mixer_channels:
            if channel.channel_id == "effect":
                continue
            with self.subTest(channel=channel.channel_id):
                pos = (channel.column_rect.centerx, channel.track_rect.bottom + 20)
                self._tap_pygame(browser, pos)
                self.assertEqual(browser.screen_state, Screen.BROWSER)

    def test_hidden_and_inert_when_fx_is_off(self) -> None:
        browser = self._browser(fx_enabled=False)
        self.assertEqual(browser._effect_label_rect().w, 0)
        for channel in browser.mixer_channels:
            for path in self.PATHS:
                self._tap(path, browser, (channel.column_rect.centerx, channel.track_rect.bottom + 20))
                self.assertEqual(browser.screen_state, Screen.BROWSER)
        browser._open_fx_effect_modal()
        self.assertEqual(browser.screen_state, Screen.BROWSER)
        self.assertFalse((self.root / "fx-route.json").exists())

    def test_panel_lists_every_effect_in_run_order_and_checks_the_enabled(self) -> None:
        browser = self._browser(collapsed=True)
        for mask in ALL_MASKS:
            with self.subTest(mask=mask):
                self.policy.set_mask(mask)
                browser._open_fx_effect_modal()
                drawn = []
                with mock.patch.object(
                    browser,
                    "_draw_fx_effect_row",
                    side_effect=lambda rect, label, **kw: drawn.append((label, kw["checked"])),
                ):
                    browser._draw()
                self.assertEqual(
                    drawn,
                    [
                        (fx_host_protocol.EFFECT_NAMES[i], bool(mask & (1 << i)))
                        for i in fx_host_protocol.CHAIN_ORDER
                    ],
                )

    def test_rows_draw_in_both_states(self) -> None:
        browser = self._browser(collapsed=True)
        self.policy.set_mask(C | P)
        browser._open_fx_effect_modal()
        browser._draw()  # the real row drawing, checked and unchecked

    def test_panel_and_label_fit_at_800x480_with_either_nav(self) -> None:
        screen = Rect(0, 0, SCREEN_W, SCREEN_H)
        for collapsed in (False, True):
            with self.subTest(collapsed=collapsed):
                browser = self._browser(collapsed=collapsed)
                if not collapsed:
                    self.assertEqual(browser.main_rect.x - browser.left_panel_rect.x, LEFT_NAV_WIDTH)
                label = browser._effect_label_rect()
                self.assertTrue(_inside(label, browser.main_rect), label)
                self.assertGreaterEqual(label.h, MIN_TARGET_PX)
                self.assertGreaterEqual(label.w, 54)
                effect = self._effect_channel(browser)
                self.assertFalse(_overlaps(label, effect.track_rect))
                for other in [browser.mod_wheel_switch_rect, browser.normalize_btn, browser.favorites_btn]:
                    self.assertFalse(_overlaps(label, other), (label, other))
                for channel in browser.mixer_channels:
                    if channel is not effect:
                        self.assertFalse(_overlaps(label, channel.column_rect), channel.channel_id)
                panel, options, done = browser._fx_effect_modal_layout()
                self.assertTrue(_inside(panel, screen), panel)
                for rect, _eid in options:
                    self.assertTrue(_inside(rect, panel), rect)
                    self.assertGreaterEqual(rect.h, MIN_TARGET_PX)
                self.assertTrue(_inside(done, panel))
                self.assertGreaterEqual(done.h, MIN_TARGET_PX)
                for a, b in itertools.combinations([r for r, _ in options] + [done], 2):
                    self.assertFalse(_overlaps(a, b), (a, b))
                self.assertEqual([eid for _r, eid in options], list(fx_host_protocol.CHAIN_ORDER))

    def test_five_rows_and_done_fit_at_800x480(self) -> None:
        """F4: Flanger is the fifth row, between Phaser and Chorus, and the
        panel still fits the screen with every row a full touch target. The
        title is measured at a real DejaVu Sans 22 px line height (26 px, as
        pygame-ce reports it), not the fake font's."""
        browser = self._browser(collapsed=False)
        with mock.patch.object(browser.font_md, "get_height", return_value=26):
            panel, options, done = browser._fx_effect_modal_layout()
        names = [fx_host_protocol.EFFECT_NAMES[eid] for _r, eid in options]
        self.assertEqual(names, ["Drive", "Phaser", "Flanger", "Chorus", "Tremolo"])
        self.assertTrue(_inside(panel, Rect(0, 0, SCREEN_W, SCREEN_H)), panel)
        self.assertTrue(all(r.h >= MIN_TARGET_PX for r, _ in options))
        self.assertGreaterEqual(done.h, MIN_TARGET_PX)
        self.assertGreater(done.y, options[-1][0].bottom)

    def test_modal_draw_does_not_raise(self) -> None:
        browser = self._browser(collapsed=True)
        browser._open_fx_effect_modal()
        browser._draw()
        self.assertEqual(browser._modal_panel_rect, browser._fx_effect_modal_layout()[0])


class PatchModFaderTests(_BrowserCase):
    """The Patch mod handle follows the knob while CC 1 goes to Surge."""

    def _browser(self):
        browser = self._make_browser(fx_enabled=True)
        # The real loader would build the OSC message through the same policy.
        browser.loader.send_patch_mod.side_effect = lambda value, final=True: self.policy.set_patch_mod(
            value, final=final
        )
        return browser

    def _channel(self, browser):
        return next(c for c in browser.mixer_channels if c.channel_id == "patch_mod")

    def _knob(self, value: int, at: float) -> None:
        (self.root / "fx-levels.json").write_text(json.dumps({"patch_mod": value, "patch_mod_at": at}))

    def _read(self, browser) -> float:
        from patch_browser.mixer_controls import mixer_control_by_id

        return mixer_control_by_id(browser, "patch_mod").read(browser)

    def test_handle_follows_the_knob(self) -> None:
        browser = self._browser()
        self._knob(90, at=1e9)
        self.assertTrue(self.policy.poll_levels(now=1e9))
        self.assertEqual(self._read(browser), 90)

    def test_drag_holds_the_handle_and_release_lets_go(self) -> None:
        browser = self._browser()
        channel = self._channel(browser)
        x = channel.column_rect.centerx
        browser._handle_mixer_down((x, channel.track_rect.y))
        self.assertTrue(self.policy.dragging_patch_mod)
        self.assertEqual(self._read(browser), 127)
        # The knob moves while the finger is on the fader: the handle stays put.
        self._knob(5, at=1e12)
        self.assertFalse(self.policy.poll_levels(now=1e12))
        self.assertEqual(self._read(browser), 127)
        browser._persist_mixer_drag()
        self.assertFalse(self.policy.dragging_patch_mod)
        # A later knob move takes it again.
        self._knob(20, at=2e12)
        self.assertTrue(self.policy.poll_levels(now=2e12))
        self.assertEqual(self._read(browser), 20)


class SettingsDefaultTests(_BrowserCase):
    def test_row_cycles_and_persists_the_default(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        browser._open_settings_panel()
        browser._settings_slide = 1.0
        self.assertGreater(browser.mod_wheel_row_rect.h, 0)
        browser._execute_settings_hit("mod_wheel", (0, 0))
        self.assertEqual(self.policy.global_target, MOD_WHEEL_EFFECT)
        self.assertEqual(self.policy.target, MOD_WHEEL_EFFECT)
        self.assertEqual(json.loads(self.prefs.read_text())["mod_wheel_target"], MOD_WHEEL_EFFECT)
        browser._execute_settings_hit("mod_wheel", (0, 0))
        self.assertEqual(json.loads(self.prefs.read_text())["mod_wheel_target"], MOD_WHEEL_PATCH_FIRST)

    def test_row_is_hit_testable(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        browser._open_settings_panel()
        browser._settings_slide = 1.0
        browser._sync_settings_scroll_viewport()
        screen_rect = browser._panel_local_to_screen(browser.mod_wheel_row_rect, scrolled=True)
        self.assertEqual(browser._settings_hit_at(_center(screen_rect)), "mod_wheel")

    def test_row_absent_when_fx_off(self) -> None:
        browser = self._make_browser(fx_enabled=False)
        browser._open_settings_panel()
        self.assertEqual(browser.mod_wheel_row_rect.h, 0)

    def test_settings_draw_does_not_raise(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        browser.left_nav_collapsed = True
        browser._layout()
        browser._open_settings_panel()
        browser._settings_slide = 1.0
        browser._draw()


if __name__ == "__main__":
    unittest.main()
