"""Effect picker: which effects the FX chain plays, chosen on the play screen.

A tap on the Effect fader's label opens it. A small up caret drawn right of the
label's text (`_effect_caret_rect`, inside the tap target) says it can be tapped. Each effect is a checkbox row,
listed in the order the chain runs them (`fx_host_protocol.CHAIN_ORDER`). A
tap on a row's checkbox or name switches that effect on or off and the panel stays open, so
several can be set in one visit; Done or a tap on the backdrop closes it, as
the other modals close.

Seven rows at 800x480 (2026-10-13, the rotary and the auto-wah): one column,
in run order, each row 44 px (the minimum touch target) with 6 px between, and
Done in the header row at the right of the title rather than under the list.
Five 52 px rows, a title and a Done row under them were 418 px; seven of them
would be 556, past the screen. One column keeps the run order readable top to
bottom and every slider ~390 px long; two columns would halve the sliders, and
a scrolling list would hide rows.

Each row is two controls side by side (`split_effect_row`): the checkbox and
name on the left, a tap target that toggles the effect, and a horizontal level
slider on the right, 0-100 %, with its value shown after the track. The
slider's touch target is the full row height. Dragging it (or pressing it: the
level jumps to the finger, as the brightness slider does) sets that effect's
level for the loaded patch, live: the route file is rewritten while the finger
moves, throttled as an Effect fader drag is, so the remapper sends each new
level to the FX host as you drag. An unticked effect's slider is greyed out
(the play screen's locked-fader look: dim track, hollow handle, muted text),
keeps its value, and takes no touch.

The panel only states intent. `FxPolicy.toggle_effect` sets the loaded patch's
enable mask and `FxPolicy.set_level` one effect's level (nothing global), and
both are written to `fx-route.json`; the remapper is still the one process
that sends to the FX host. With MPE_FX_HOST off there is no Effect fader, so there is no label to
tap, and opening is refused anyway.

This module also chooses the two labels that name the chain, because both are
a fit against pixels on this screen: the Effect fader's (`_effect_fader_label`)
and the mod-knob switch's (`_mod_knob_switch_label`). Each takes the names when
they fit its budget, else the count (`fx_policy.chain_label`). Text is measured
in one place, `_text_px`.

Names come from `fx_host_protocol.EFFECT_NAMES`; nothing here lists effects of
its own.
"""

from __future__ import annotations

import pygame

from patch_browser import fx_host_protocol
from patch_browser.geometry import Rect
from patch_browser.mixer import FADER_NAME_DY
from patch_browser.touch_ui_constants import SETTINGS_ROW_GAP, TAP_MOVE_THRESHOLD_PX
from patch_browser.touch_ui_enums import Screen

EFFECT_CHANNEL_ID = "effect"
#: The label's tap target starts this far below the track, so a press on the
#: track's bottom edge (pulling the effect to 0) is still the fader...
EFFECT_LABEL_TRACK_CLEARANCE = 4
#: ...and is this tall, the minimum touch target, reaching into the gap above
#: the bottom row by the clearance.
EFFECT_LABEL_TARGET_H = 44
#: The Effect fader's label starts at least this far right of its neighbour's
#: (Patch mod, wider than its own column): they may not touch. The glyphs' side
#: bearings add a few pixels of visible space on top. Tremolo, the longest single
#: name, has exactly this with the nav open, so a larger gap would turn it into
#: "1 FX".
EFFECT_LABEL_GAP_PX = 1
#: The up caret right of the label's text: this far after the text, this wide
#: (odd, so the point is one pixel), this tall. Drawn with pygame primitives, not
#: a glyph, so it does not depend on the font. The label's text stays centred on
#: its column like every fader label; the caret hangs to its right, toward the
#: pane edge, so it never narrows the room the names have on the Patch mod side.
EFFECT_CARET_GAP_PX = 4
EFFECT_CARET_W = 9
EFFECT_CARET_H = 5
#: The checkbox square in each picker row, and the space either side of it.
FX_EFFECT_CHECK_PX = 24
FX_EFFECT_CHECK_PAD = 12
#: The effect names' column, right of the checkbox: the longest name (the
#: auto-wah's, 95 px in DejaVu Sans 22 with pygame-ce 2.5.8; the tremolo's 82)
#: and some air.
FX_EFFECT_NAME_W = 104
#: The toggle's width: pad, checkbox, pad, name column.
FX_EFFECT_CHOICE_W = FX_EFFECT_CHECK_PAD * 2 + FX_EFFECT_CHECK_PX + FX_EFFECT_NAME_W
#: Panel width. 420 held a checkbox list; the level sliders need room for a
#: track a finger can place to a few percent (~320 px at 600), so the panel is
#: wider. Rows are the 44 px minimum touch target (seven fit; see the module
#: docstring): the slider's touch target is the whole row height.
FX_EFFECT_MODAL_W = 600
#: Side padding, and the (smaller) padding above the header and below the list.
FX_EFFECT_MODAL_PAD = 24
FX_EFFECT_MODAL_PAD_V = 16
FX_EFFECT_OPTION_H = 44
FX_EFFECT_ROW_GAP = 6
#: The header row: the title at the left, Done at the right, one row tall,
#: and the space between it and the first effect.
FX_EFFECT_HEADER_H = 44
FX_EFFECT_HEADER_GAP = 10
FX_EFFECT_DONE_W = 120
#: The level slider: the space between the toggle and the slider, the value
#: text's column at the slider's right end ("100%", 45 px in DejaVu Sans 18),
#: the handle, and the drawn track's thickness. The track is inset by half a
#: handle at each end so the handle never leaves the slider.
FX_LEVEL_GAP = 8
FX_LEVEL_VALUE_W = 56
FX_LEVEL_HANDLE_W = 20
FX_LEVEL_HANDLE_H = 32
#: Taller while held, so the finger's slider shows it has it.
FX_LEVEL_HANDLE_H_DRAG = 40
FX_LEVEL_TRACK_H = 8
#: Width kept at the right end of each row for a per-effect settings button
#: (parameter pages, later). 0 today: no button, the whole row is the choice.
FX_EFFECT_ROW_SETTINGS_W = 0


def split_effect_row(row: Rect, settings_w: int | None = None) -> tuple[Rect, Rect, Rect | None]:
    """(choice, level slider, settings button or None) for one picker row.

    The one place a row is divided, so a settings button is a width here and
    a hit/draw branch, not a new layout. The choice (checkbox and name) is a
    fixed width at the left; the level slider takes the rest of the row, at
    the row's full height.
    """
    width = FX_EFFECT_ROW_SETTINGS_W if settings_w is None else settings_w
    body = row if width <= 0 else Rect(row.x, row.y, row.w - width - SETTINGS_ROW_GAP, row.h)
    choice = Rect(body.x, body.y, FX_EFFECT_CHOICE_W, body.h)
    level = Rect(choice.right + FX_LEVEL_GAP, body.y, body.right - choice.right - FX_LEVEL_GAP, body.h)
    settings = None if width <= 0 else Rect(body.right + SETTINGS_ROW_GAP, row.y, width, row.h)
    return choice, level, settings


def level_track(level: Rect) -> Rect:
    """The drawn track inside a level slider: inset by half a handle at the
    left, and by the value column plus half a handle at the right. A level is
    a position on this track; the slider's touch target is all of `level`."""
    x = level.x + FX_LEVEL_HANDLE_W // 2
    right = level.right - FX_LEVEL_VALUE_W - FX_LEVEL_HANDLE_W // 2
    return Rect(x, level.y + (level.h - FX_LEVEL_TRACK_H) // 2, max(1, right - x), FX_LEVEL_TRACK_H)


def level_at_x(track: Rect, x: int) -> float:
    """The level a finger at `x` sets: its place along the track, clamped to
    0..1 and rounded to the whole percent the slider shows."""
    frac = (x - track.x) / track.w if track.w > 0 else 0.0
    return round(max(0.0, min(1.0, frac)) * 100) / 100


def level_percent(level: float) -> str:
    return f"{int(round(level * 100))}%"


class TouchBrowserFxEffectModalMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _effect_channel(self):
        for channel in getattr(self, "mixer_channels", ()):
            if channel.channel_id == EFFECT_CHANNEL_ID:
                return channel
        return None

    def _effect_label_rect(self) -> Rect:
        """The Effect fader's label, the picker's tap target. Empty when no fader.

        The strip under the track (value and name lines), the column's width,
        widened to the right when the caret reaches past the column.
        """
        channel = self._effect_channel()
        if channel is None:
            return Rect(0, 0, 0, 0)
        column, track = channel.column_rect, channel.track_rect
        top = track.bottom + EFFECT_LABEL_TRACK_CLEARANCE
        right = max(column.right, self._effect_caret_rect(channel).right)
        return Rect(column.x, top, right - column.x, EFFECT_LABEL_TARGET_H)

    def _effect_caret_rect(self, channel) -> Rect:
        """Where the caret goes: after the label's text (centred on the column,
        as `_draw_vertical_fader` draws it), centred on the name line."""
        column = channel.column_rect
        text_w = self._text_px(self.font_sm, self._mixer_channel_label(channel))
        x = column.x + (column.w - text_w) // 2 + text_w + EFFECT_CARET_GAP_PX
        mid_y = channel.track_rect.bottom + FADER_NAME_DY + self.font_sm.get_linesize() // 2
        return Rect(x, mid_y - EFFECT_CARET_H // 2, EFFECT_CARET_W, EFFECT_CARET_H)

    def _draw_effect_caret(self, channel, color) -> None:
        """The up caret after the Effect fader's label, in the label's colour.
        Every other fader draws nothing here."""
        if channel.channel_id != EFFECT_CHANNEL_ID:
            return
        caret = self._effect_caret_rect(channel)
        pygame.draw.polygon(
            self.screen,
            color,
            [(caret.x, caret.bottom - 1), (caret.x + caret.w // 2, caret.y), (caret.right - 1, caret.bottom - 1)],
        )

    def _effect_label_hit(self, pos: tuple[int, int]) -> bool:
        policy = getattr(self, "fx_policy", None)
        if policy is None or not policy.enabled or not self.detail_patch:
            return False
        rect = self._effect_label_rect()
        return rect.w > 0 and rect.h > 0 and rect.contains(*pos)

    def _effect_label_pointer_down(self, pos: tuple[int, int]) -> bool:
        """A press on the label: remember it; the release decides. True if taken."""
        if not self._effect_label_hit(pos):
            self._effect_label_down_pos = None
            return False
        self._effect_label_down_pos = pos
        return True

    def _effect_label_tapped(self, pos: tuple[int, int]) -> bool:
        """The release of a tap that began on the label and stayed there."""
        start = getattr(self, "_effect_label_down_pos", None)
        self._effect_label_down_pos = None
        return (
            start is not None
            and self._effect_label_hit(pos)
            and self._pointer_move_distance(start, pos) <= TAP_MOVE_THRESHOLD_PX
        )

    # -- the labels that name the chain --------------------------------------

    def _text_px(self, font, text: str) -> int:
        """Rendered width of `text`: the one measurement the labels fit by."""
        return font.size(text)[0]

    def _effect_fader_label(self) -> str:
        """The enabled effects' names if they fit under the Effect fader, else
        the count. Centred on its column like every fader label, it must clear
        the label to its left (Patch mod, wider than its column), and its caret
        the pane's right edge."""
        channels = list(getattr(self, "mixer_channels", ()))
        index = next((i for i, c in enumerate(channels) if c.channel_id == EFFECT_CHANNEL_ID), None)
        if index is None:
            return self.fx_policy.chain_label()
        column = channels[index].column_rect
        left_limit = self.main_rect.x
        if index > 0:
            neighbour = channels[index - 1]
            n_col = neighbour.column_rect
            n_w = self._text_px(self.font_sm, self._mixer_channel_label(neighbour))
            left_limit = n_col.x + (n_col.w + n_w) // 2 + EFFECT_LABEL_GAP_PX

        def fits(text: str) -> bool:
            width = self._text_px(self.font_sm, text)
            left = column.x + (column.w - width) // 2
            caret_right = left + width + EFFECT_CARET_GAP_PX + EFFECT_CARET_W
            return left >= left_limit and caret_right <= self.main_rect.right

        return self.fx_policy.chain_label(fits)

    def _mod_knob_switch_label(self) -> str:
        """Where the knob goes, naming the chain only if it fits the switch's
        label budget (`_toggle_label_budget`, what the draw wraps to)."""
        rect = getattr(self, "mod_wheel_switch_rect", None)
        if rect is None or rect.w <= 0:
            return self.fx_policy.knob_label()
        font, budget = self._toggle_label_budget(rect)
        return self.fx_policy.knob_label(lambda text: self._text_px(font, text) <= budget)

    # -- the panel -------------------------------------------------------------

    def _open_fx_effect_modal(self) -> None:
        if not self.fx_policy.enabled:
            return
        self._clear_modal_pointer()
        self._fx_level_dragging = None
        self.screen_state = Screen.FX_EFFECT_MODAL

    def _close_fx_effect_modal(self) -> None:
        self._clear_modal_pointer()
        if getattr(self, "_fx_level_dragging", None) is not None:
            self._fx_level_dragging = None
            self.fx_policy.finish_level()
        self.screen_state = Screen.BROWSER

    def _toggle_fx_effect(self, effect_id: int) -> None:
        """A row tap: that effect on or off. The panel stays open."""
        self.fx_policy.toggle_effect(effect_id)

    def _fx_effect_modal_layout(self) -> tuple[Rect, list[tuple[Rect, int]], Rect]:
        """(panel, [(row rect, effect id)] in run order, Done rect), shared by
        draw and hit test."""
        ids = fx_host_protocol.CHAIN_ORDER
        pad, pad_v = FX_EFFECT_MODAL_PAD, FX_EFFECT_MODAL_PAD_V
        panel_w = min(FX_EFFECT_MODAL_W, self.width - 48)
        list_h = len(ids) * FX_EFFECT_OPTION_H + max(0, len(ids) - 1) * FX_EFFECT_ROW_GAP
        panel_h = pad_v + FX_EFFECT_HEADER_H + FX_EFFECT_HEADER_GAP + list_h + pad_v
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        inner_x = panel.x + pad
        inner_w = panel.w - pad * 2
        y = panel.y + pad_v + FX_EFFECT_HEADER_H + FX_EFFECT_HEADER_GAP
        options = [
            (split_effect_row(row)[0], effect_id) for row, effect_id in self._fx_effect_rows(inner_x, y, inner_w)
        ]
        done = Rect(inner_x + inner_w - FX_EFFECT_DONE_W, panel.y + pad_v, FX_EFFECT_DONE_W, FX_EFFECT_HEADER_H)
        return panel, options, done

    def _fx_effect_rows(self, inner_x: int, y: int, inner_w: int) -> list[tuple[Rect, int]]:
        return [
            (Rect(inner_x, y + i * (FX_EFFECT_OPTION_H + FX_EFFECT_ROW_GAP), inner_w, FX_EFFECT_OPTION_H), effect_id)
            for i, effect_id in enumerate(fx_host_protocol.CHAIN_ORDER)
        ]

    def _fx_effect_level_layout(self) -> list[tuple[Rect, Rect, int]]:
        """[(slider touch target, drawn track, effect id)] in run order: the
        right-hand part of each row of `_fx_effect_modal_layout`."""
        panel, options, _done = self._fx_effect_modal_layout()
        pad = FX_EFFECT_MODAL_PAD
        rows = self._fx_effect_rows(panel.x + pad, options[0][0].y if options else panel.y, panel.w - pad * 2)
        out = []
        for row, effect_id in rows:
            level = split_effect_row(row)[1]
            out.append((level, level_track(level), effect_id))
        return out

    def _draw_fx_effect_row(self, rect: Rect, label: str, *, checked: bool, pressed: bool = False) -> None:
        """One row's toggle: the box, then the effect's name."""
        theme = self.theme
        pygame.draw.rect(self.screen, theme.accent if pressed else theme.surface_alt, rect.pygame_rect, border_radius=10)
        box = Rect(
            rect.x + FX_EFFECT_CHECK_PAD,
            rect.y + (rect.h - FX_EFFECT_CHECK_PX) // 2,
            FX_EFFECT_CHECK_PX,
            FX_EFFECT_CHECK_PX,
        )
        if checked:
            pygame.draw.rect(self.screen, theme.bg if pressed else theme.accent, box.pygame_rect, border_radius=5)
            tick = theme.accent if pressed else theme.bg
            points = [
                (box.x + 5, box.y + 12),
                (box.x + 10, box.y + 17),
                (box.x + 19, box.y + 7),
            ]
            pygame.draw.lines(self.screen, tick, False, points, 3)
        else:
            pygame.draw.rect(
                self.screen, theme.bg if pressed else theme.muted, box.pygame_rect, width=2, border_radius=5
            )
        text_color = theme.bg if pressed else theme.text
        self.screen.blit(
            self.font_md.render(label, True, text_color),
            (box.right + FX_EFFECT_CHECK_PAD, rect.y + (rect.h - self.font_md.get_height()) // 2),
        )

    def _draw_fx_effect_modal(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=150)
        panel, options, done = self._fx_effect_modal_layout()
        self._draw_modal_shell(panel, border_radius=16)
        self.screen.blit(
            self.font_md.render("Effects", True, self.theme.text),
            (panel.x + FX_EFFECT_MODAL_PAD, done.y + (done.h - self.font_md.get_height()) // 2),
        )
        mask = self.fx_policy.mask
        sliders = {effect_id: (level, track) for level, track, effect_id in self._fx_effect_level_layout()}
        dragging = getattr(self, "_fx_level_dragging", None)
        for rect, effect_id in options:
            level, track = sliders[effect_id]
            row = Rect(rect.x, rect.y, level.right - rect.x, rect.h)
            pygame.draw.rect(self.screen, self.theme.surface_alt, row.pygame_rect, border_radius=10)
            checked = fx_host_protocol.is_enabled(mask, effect_id)
            self._draw_fx_effect_row(
                rect,
                fx_host_protocol.EFFECT_NAMES[effect_id],
                checked=checked,
                pressed=self._pressed(f"effect:{effect_id}"),
            )
            self._draw_fx_effect_level(
                level,
                track,
                self.fx_policy.level(effect_id),
                enabled=checked,
                dragging=dragging == effect_id,
            )
        self._draw_button(done, "Done", pressed=self._pressed("done"))

    def _draw_fx_effect_level(
        self, level: Rect, track: Rect, value: float, *, enabled: bool, dragging: bool = False
    ) -> None:
        """One level slider: track, fill up to the handle, handle, and the
        value. Greyed out as the play screen's locked fader is: a dim track,
        no fill, a hollow handle and muted text, at the value it keeps."""
        theme = self.theme
        value = max(0.0, min(1.0, float(value)))
        pygame.draw.rect(self.screen, theme.bg if enabled else theme.surface, track.pygame_rect, border_radius=4)
        handle_cx = track.x + int(round(track.w * value))
        if enabled and handle_cx > track.x:
            fill = pygame.Rect(track.x, track.y, handle_cx - track.x, track.h)
            pygame.draw.rect(self.screen, theme.accent, fill, border_radius=4)
        handle_h = FX_LEVEL_HANDLE_H_DRAG if dragging else FX_LEVEL_HANDLE_H
        handle = pygame.Rect(
            handle_cx - FX_LEVEL_HANDLE_W // 2,
            level.y + (level.h - handle_h) // 2,
            FX_LEVEL_HANDLE_W,
            handle_h,
        )
        pygame.draw.rect(self.screen, theme.accent if enabled else theme.surface, handle, border_radius=6)
        pygame.draw.rect(
            self.screen,
            theme.text if enabled else theme.muted,
            handle,
            width=2 if dragging else 1,
            border_radius=6,
        )
        text = self.font_sm.render(level_percent(value), True, theme.text if enabled else theme.muted)
        self.screen.blit(
            text,
            (level.right - text.get_width(), level.y + (level.h - self.font_sm.get_height()) // 2),
        )

    def _fx_effect_modal_hit_at(self, pos: tuple[int, int]) -> str | None:
        """"done", "effect:<id>" (a row's toggle), "level:<id>" (a ticked
        effect's slider) or None. A greyed slider is None: it takes no touch."""
        _panel, options, done = self._fx_effect_modal_layout()
        if done.contains(*pos):
            return "done"
        for rect, effect_id in options:
            if rect.contains(*pos):
                return f"effect:{effect_id}"
        for level, _track, effect_id in self._fx_effect_level_layout():
            if level.contains(*pos):
                if fx_host_protocol.is_enabled(self.fx_policy.mask, effect_id):
                    return f"level:{effect_id}"
                return None
        return None

    def _apply_fx_level_x(self, effect_id: int, x: int, *, final: bool) -> None:
        track = next(t for _l, t, eid in self._fx_effect_level_layout() if eid == effect_id)
        self.fx_policy.set_level(effect_id, level_at_x(track, x), final=final)

    def _handle_fx_effect_modal_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_modal_pointer()
        self._fx_level_dragging = None
        # The panel rect is what a backdrop tap is measured against; set it
        # here too, so a tap before the first frame is judged correctly.
        self._modal_panel_rect = self._fx_effect_modal_layout()[0]
        hit = self._fx_effect_modal_hit_at(pos)
        if hit is not None and hit.startswith("level:"):
            # A slider press is a drag from the first touch: the level jumps
            # to the finger and follows it. Not a pending tap, so a release
            # anywhere (even off the panel) only ends the drag.
            self._fx_level_dragging = int(hit.split(":", 1)[1])
            self._apply_fx_level_x(self._fx_level_dragging, pos[0], final=False)
            return
        self._modal_press_hit(pos, hit)

    def _handle_fx_effect_modal_pointer_move(self, pos: tuple[int, int]) -> None:
        effect_id = getattr(self, "_fx_level_dragging", None)
        if effect_id is not None:
            self._apply_fx_level_x(effect_id, pos[0], final=False)

    def _handle_fx_effect_modal_pointer_up(self, pos: tuple[int, int]) -> None:
        effect_id = getattr(self, "_fx_level_dragging", None)
        if effect_id is not None:
            self._fx_level_dragging = None
            self._apply_fx_level_x(effect_id, pos[0], final=True)
            self.fx_policy.finish_level()
            self._clear_modal_pointer()
            return
        if (
            self._modal_pending_key is None
            or self._pointer_move_distance(self._modal_pointer_down_pos, pos) > TAP_MOVE_THRESHOLD_PX
        ):
            self._clear_modal_pointer()
            return
        hit = self._modal_pending_key
        self._clear_modal_pointer()
        if hit == "done":
            self._close_fx_effect_modal()
        elif hit.startswith("effect:"):
            self._toggle_fx_effect(int(hit.split(":", 1)[1]))
