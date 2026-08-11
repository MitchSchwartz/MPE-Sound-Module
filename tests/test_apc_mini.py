"""Tests for control surface registry and APC mini mk1/mk2 maps."""

from __future__ import annotations

import unittest

from patch_browser.apc_mini import (
    APC_MIDI_CHANNEL,
    ApcVariant,
    default_apc_variant,
    get_apc_map,
    grid_note,
    grid_position,
    is_apc_port_name,
    looper_transport_action,
    looper_transport_from_message,
    parse_channel_message,
    resolve_apc_map,
    scene_launch_index,
    scene_launch_notes,
    session_mode_sysex,
    shift_note,
    variant_from_usb_id,
)
from patch_browser.control_surfaces import (
    APC_MAP_MK1,
    APC_MAP_MK2,
    APC_MAPS,
    CONTROL_SURFACE_MAPS,
    ControlSurfaceMap,
    LooperTransportAction,
)


class ControlSurfaceRegistryTests(unittest.TestCase):
    def test_global_registry_lists_both_apc_maps(self) -> None:
        self.assertIn("apc-mini-mk1", CONTROL_SURFACE_MAPS)
        self.assertIn("apc-mini-mk2", CONTROL_SURFACE_MAPS)
        self.assertEqual(len(APC_MAPS), 2)

    def test_maps_are_frozen_dataclasses(self) -> None:
        self.assertIsInstance(APC_MAP_MK1, ControlSurfaceMap)
        self.assertNotEqual(APC_MAP_MK1.scene_launch_notes, APC_MAP_MK2.scene_launch_notes)


class ApcMiniMapTests(unittest.TestCase):
    def test_default_variant_is_mk1(self) -> None:
        self.assertEqual(default_apc_variant(), ApcVariant.MK1)
        self.assertEqual(get_apc_map().map_id, "apc-mini-mk1")

    def test_resolve_prefers_env_over_usb(self) -> None:
        self.assertEqual(resolve_apc_map(variant=ApcVariant.MK2).map_id, "apc-mini-mk2")

    def test_usb_id_mk2(self) -> None:
        self.assertEqual(variant_from_usb_id("09e8:0028"), ApcVariant.MK2)

    def test_scene_launch_notes_differ_by_variant(self) -> None:
        self.assertEqual(scene_launch_notes(ApcVariant.MK1)[0], 82)
        self.assertEqual(scene_launch_notes(ApcVariant.MK2)[0], 112)

    def test_shift_note_differs(self) -> None:
        self.assertEqual(shift_note(ApcVariant.MK1), 98)
        self.assertEqual(shift_note(ApcVariant.MK2), 122)

    def test_mk1_no_session_sysex(self) -> None:
        self.assertIsNone(session_mode_sysex(ApcVariant.MK1))
        self.assertIsNotNone(session_mode_sysex(ApcVariant.MK2))

    def test_port_name_match(self) -> None:
        self.assertTrue(is_apc_port_name("APC MINI:APC MINI MIDI 1 40:0"))
        self.assertTrue(APC_MAP_MK1.matches_port("APC MINI:APC MINI MIDI 1 40:0"))

    def test_grid_shared_across_variants(self) -> None:
        self.assertEqual(grid_note(0, 5), 5)
        self.assertEqual(grid_position(26), (3, 2))
        self.assertEqual(APC_MAP_MK2.grid_position(34), (4, 2))

    def test_mk1_scene_launch_index(self) -> None:
        self.assertEqual(scene_launch_index(82, variant=ApcVariant.MK1), 0)
        self.assertEqual(APC_MAP_MK1.scene_launch_index(89), 7)

    def test_both_maps_share_transport_slot_pattern(self) -> None:
        for variant in ApcVariant:
            surface = get_apc_map(variant)
            self.assertEqual(len(surface.looper_transport), 4)
            self.assertEqual(
                surface.looper_transport_action(surface.scene_launch_notes[0]),
                LooperTransportAction.RECORD,
            )
            self.assertEqual(
                surface.looper_transport_action(surface.scene_launch_notes[4]).value,
                "play_stop",
            )

    def test_parse_note_on_off_apc_quirk(self) -> None:
        on = parse_channel_message([0x90, 28, 127])
        assert on is not None
        self.assertTrue(on[3])

    def test_mk1_transport_from_grid_is_none(self) -> None:
        self.assertIsNone(
            looper_transport_from_message([0x90, 34, 127], variant=ApcVariant.MK1)
        )

    def test_mk1_transport_from_scene_launch(self) -> None:
        self.assertEqual(
            looper_transport_action(82, variant=ApcVariant.MK1),
            LooperTransportAction.RECORD,
        )
        self.assertEqual(
            looper_transport_from_message([0x90, 82, 127], variant=ApcVariant.MK1),
            LooperTransportAction.RECORD,
        )

    def test_mk2_transport_uses_high_scene_notes(self) -> None:
        self.assertEqual(
            looper_transport_from_message([0x90, 112, 127], variant=ApcVariant.MK2),
            LooperTransportAction.RECORD,
        )


if __name__ == "__main__":
    unittest.main()
