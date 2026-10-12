"""Effect picker: which effects the FX chain plays, chosen on the play screen.

A tap on the Effect fader's label opens it. Each effect is a checkbox row,
listed in the order the chain runs them (`fx_host_protocol.CHAIN_ORDER`). A
tap on a row switches that effect on or off and the panel stays open, so
several can be set in one visit; Done or a tap on the backdrop closes it, as
the other modals close.

The panel only states intent. `FxPolicy.toggle_effect` sets the loaded patch's
enable mask (and nothing global), and writes it to
`fx-route.json`; the remapper is still the one process that sends to the FX
host. With MPE_FX_HOST off there is no Effect fader, so there is no label to
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
from patch_browser.touch_ui_constants import SETTINGS_ROW_GAP, SETTINGS_ROW_H, TAP_MOVE_THRESHOLD_PX
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
#: The checkbox square in each picker row, and the space either side of it.
FX_EFFECT_CHECK_PX = 24
FX_EFFECT_CHECK_PAD = 12
FX_EFFECT_MODAL_W = 420
FX_EFFECT_MODAL_PAD = 24
FX_EFFECT_OPTION_H = SETTINGS_ROW_H
#: Width kept at the right end of each row for a per-effect settings button
#: (parameter pages, later). 0 today: no button, the whole row is the choice.
FX_EFFECT_ROW_SETTINGS_W = 0


def split_effect_row(row: Rect, settings_w: int | None = None) -> tuple[Rect, Rect | None]:
    """(choice, settings button or None) for one picker row.

    The one place a row is divided, so a settings button is a width here and
    a hit/draw branch, not a new layout.
    """
    width = FX_EFFECT_ROW_SETTINGS_W if settings_w is None else settings_w
    if width <= 0:
        return row, None
    choice = Rect(row.x, row.y, row.w - width - SETTINGS_ROW_GAP, row.h)
    return choice, Rect(choice.right + SETTINGS_ROW_GAP, row.y, width, row.h)


class TouchBrowserFxEffectModalMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _effect_label_rect(self) -> Rect:
        """The Effect fader's label, the picker's tap target. Empty when no fader.

        The strip under the track (value and name lines), the column's width.
        """
        for channel in getattr(self, "mixer_channels", ()):
            if channel.channel_id == EFFECT_CHANNEL_ID:
                column, track = channel.column_rect, channel.track_rect
                top = track.bottom + EFFECT_LABEL_TRACK_CLEARANCE
                return Rect(column.x, top, column.w, EFFECT_LABEL_TARGET_H)
        return Rect(0, 0, 0, 0)

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
        the label to its left (Patch mod, wider than its column) and the pane's
        right edge."""
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
            return left >= left_limit and left + width <= self.main_rect.right

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
        self.screen_state = Screen.FX_EFFECT_MODAL

    def _close_fx_effect_modal(self) -> None:
        self._clear_modal_pointer()
        self.screen_state = Screen.BROWSER

    def _toggle_fx_effect(self, effect_id: int) -> None:
        """A row tap: that effect on or off. The panel stays open."""
        self.fx_policy.toggle_effect(effect_id)

    def _fx_effect_modal_layout(self) -> tuple[Rect, list[tuple[Rect, int]], Rect]:
        """(panel, [(row rect, effect id)] in run order, Done rect), shared by
        draw and hit test."""
        ids = fx_host_protocol.CHAIN_ORDER
        pad = FX_EFFECT_MODAL_PAD
        panel_w = min(FX_EFFECT_MODAL_W, self.width - 48)
        title_h = self.font_md.get_height() + 12
        list_h = len(ids) * FX_EFFECT_OPTION_H + max(0, len(ids) - 1) * SETTINGS_ROW_GAP
        panel_h = pad + title_h + list_h + 16 + SETTINGS_ROW_H + pad
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        inner_x = panel.x + pad
        inner_w = panel.w - pad * 2
        y = panel.y + pad + title_h
        options = [
            (
                split_effect_row(
                    Rect(inner_x, y + i * (FX_EFFECT_OPTION_H + SETTINGS_ROW_GAP), inner_w, FX_EFFECT_OPTION_H)
                )[0],
                effect_id,
            )
            for i, effect_id in enumerate(ids)
        ]
        done = Rect(inner_x, panel.bottom - pad - SETTINGS_ROW_H, inner_w, SETTINGS_ROW_H)
        return panel, options, done

    def _draw_fx_effect_row(self, rect: Rect, label: str, *, checked: bool, pressed: bool = False) -> None:
        """One checkbox row: the box, then the effect's name."""
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
            (panel.x + FX_EFFECT_MODAL_PAD, panel.y + FX_EFFECT_MODAL_PAD),
        )
        mask = self.fx_policy.mask
        for rect, effect_id in options:
            self._draw_fx_effect_row(
                rect,
                fx_host_protocol.EFFECT_NAMES[effect_id],
                checked=fx_host_protocol.is_enabled(mask, effect_id),
                pressed=self._pressed(f"effect:{effect_id}"),
            )
        self._draw_button(done, "Done", pressed=self._pressed("done"))

    def _fx_effect_modal_hit_at(self, pos: tuple[int, int]) -> str | None:
        _panel, options, done = self._fx_effect_modal_layout()
        if done.contains(*pos):
            return "done"
        for rect, effect_id in options:
            if rect.contains(*pos):
                return f"effect:{effect_id}"
        return None

    def _handle_fx_effect_modal_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_modal_pointer()
        # The panel rect is what a backdrop tap is measured against; set it
        # here too, so a tap before the first frame is judged correctly.
        self._modal_panel_rect = self._fx_effect_modal_layout()[0]
        self._modal_press_hit(pos, self._fx_effect_modal_hit_at(pos))

    def _handle_fx_effect_modal_pointer_up(self, pos: tuple[int, int]) -> None:
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
