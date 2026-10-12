"""Effect picker: which effect the FX host plays, chosen on the play screen.

A tap on the Effect fader's label opens it; the label is the active effect's
name. Tapping an effect selects it and closes the panel; Cancel or a tap on the
backdrop closes it unchanged, as the other modals do.

The panel only states intent. `FxPolicy.select_effect` sets the loaded
patch's effect (and the default for patches that never chose one), restores
that effect's depth for the patch, and writes both to `fx-route.json`; the
remapper is still the one process that sends to the FX host. With MPE_FX_HOST off there is no Effect fader, so
there is no label to tap, and opening is refused anyway.

Names come from `fx_host_protocol.EFFECT_NAMES`, in id order; nothing here
lists effects of its own.
"""

from __future__ import annotations

from patch_browser import fx_host_protocol
from patch_browser.fx_policy import effect_name
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

    def _open_fx_effect_modal(self) -> None:
        if not self.fx_policy.enabled:
            return
        self._clear_modal_pointer()
        self.screen_state = Screen.FX_EFFECT_MODAL

    def _close_fx_effect_modal(self) -> None:
        self._clear_modal_pointer()
        self.screen_state = Screen.BROWSER

    def _select_fx_effect(self, effect_id: int) -> None:
        self.fx_policy.select_effect(effect_id)
        self._close_fx_effect_modal()
        self._toast(f"Effect: {self.fx_policy.effect_name}", 1.2)

    def _fx_effect_modal_layout(self) -> tuple[Rect, list[tuple[Rect, int]], Rect]:
        """(panel, [(option rect, effect id)], cancel rect), shared by draw and hit test."""
        ids = fx_host_protocol.EFFECT_IDS
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
        cancel = Rect(inner_x, panel.bottom - pad - SETTINGS_ROW_H, inner_w, SETTINGS_ROW_H)
        return panel, options, cancel

    def _draw_fx_effect_modal(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=150)
        panel, options, cancel = self._fx_effect_modal_layout()
        self._draw_modal_shell(panel, border_radius=16)
        self.screen.blit(
            self.font_md.render("Effect", True, self.theme.text),
            (panel.x + FX_EFFECT_MODAL_PAD, panel.y + FX_EFFECT_MODAL_PAD),
        )
        active = self.fx_policy.effect_id
        for rect, effect_id in options:
            self._draw_theme_choice(
                rect,
                effect_name(effect_id),
                selected=effect_id == active,
                pressed=self._pressed(f"effect:{effect_id}"),
            )
        self._draw_button(cancel, "Cancel", pressed=self._pressed("cancel"))

    def _fx_effect_modal_hit_at(self, pos: tuple[int, int]) -> str | None:
        _panel, options, cancel = self._fx_effect_modal_layout()
        if cancel.contains(*pos):
            return "cancel"
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
        if hit == "cancel":
            self._close_fx_effect_modal()
        elif hit.startswith("effect:"):
            self._select_fx_effect(int(hit.split(":", 1)[1]))
