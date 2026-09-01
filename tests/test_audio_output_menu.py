"""The output menu: what is offered, and what is deliberately not.

Spec: Documents/specs/audio-output-selection-spec.md sections 3 and 4.

Mitch, 2026-09-01, on the first draft: "why is a not-available device shown?"
That reframed the feature. The menu lists PRESENT devices only, which turns
"chosen but absent" from a menu problem into a startup problem. The single
exception is inert: a saved-but-absent device is shown, marked, and cannot be
picked — otherwise the stored preference is invisible.
"""

from __future__ import annotations

import unittest
from unittest import mock

from patch_browser import audio_output as ao

KA1 = ao.OutputDevice(index="0", card_id="KA1", key="usb:2972:0051",
                      speed="12", product="FiiO KA1")
SCARLETT = ao.OutputDevice(index="1", card_id="USB", key="usb:1235:8212:F4N2X0Z",
                           speed="480", product="Scarlett 4i4 USB")
NO_IDENTITY = ao.OutputDevice(index="2", card_id="Odd", key="",
                              speed="", product="Nameless DAC")


def keys(rows):
    return [r.key for r in rows]


class AlwaysOfferedTests(unittest.TestCase):
    def test_automatic_is_first_and_always_present(self):
        """Tier detection is right most of the time, and a device chosen once at
        a rehearsal must not become a trap six months later."""
        rows = ao.menu_rows((), selection=ao.AUTO)
        self.assertEqual(rows[0].key, ao.AUTO)

    def test_silent_is_offered_so_the_idle_sink_is_a_stated_intent(self):
        """Today it is only ever reached by accident, and that is the state the
        appliance reports as state=ok."""
        rows = ao.menu_rows((), selection=ao.AUTO)
        self.assertIn(ao.SILENT, keys(rows))
        silent = [r for r in rows if r.key == ao.SILENT][0]
        self.assertIn("nothing will be audible", silent.subtitle.lower())


class PresentDevicesTests(unittest.TestCase):
    def test_connected_devices_are_offered(self):
        rows = ao.menu_rows((KA1, SCARLETT), selection=ao.AUTO)
        self.assertIn(KA1.key, keys(rows))
        self.assertIn(SCARLETT.key, keys(rows))

    def test_the_row_shows_usb_speed(self):
        """Speed predicts the smallest usable period and is otherwise invisible."""
        rows = ao.menu_rows((KA1, SCARLETT), selection=ao.AUTO)
        by_key = {r.key: r for r in rows}
        self.assertIn("full speed", by_key[KA1.key].subtitle)
        self.assertIn("high speed", by_key[SCARLETT.key].subtitle)

    def test_the_label_is_the_product_string(self):
        rows = ao.menu_rows((SCARLETT,), selection=ao.AUTO)
        self.assertEqual([r for r in rows if r.key == SCARLETT.key][0].title,
                         "Scarlett 4i4 USB")

    def test_a_device_with_no_usb_identity_is_not_offered(self):
        """There is nothing stable to store, so it cannot be a selection --
        it can still be bound automatically."""
        rows = ao.menu_rows((NO_IDENTITY,), selection=ao.AUTO)
        self.assertEqual(keys(rows), [ao.AUTO, ao.SILENT])

    def test_the_current_selection_is_marked(self):
        rows = ao.menu_rows((KA1, SCARLETT), selection=SCARLETT.key)
        selected = [r.key for r in rows if r.selected]
        self.assertEqual(selected, [SCARLETT.key])


class AbsentDeviceTests(unittest.TestCase):
    def test_an_absent_device_is_never_offered_as_a_choice(self):
        """The rule Mitch set: a row you cannot pick is not a choice."""
        rows = ao.menu_rows((KA1,), selection=ao.AUTO)
        self.assertNotIn(SCARLETT.key, keys(rows))

    def test_a_saved_absent_device_is_shown_but_inert(self):
        rows = ao.menu_rows((KA1,), selection=SCARLETT.key, label="Scarlett 4i4 USB")
        row = [r for r in rows if r.key == SCARLETT.key][0]
        self.assertFalse(row.enabled, "a device that is not here must not be pickable")
        self.assertIn("not connected", row.subtitle)

    def test_the_saved_absent_row_is_named_not_keyed(self):
        rows = ao.menu_rows((KA1,), selection=SCARLETT.key, label="Scarlett 4i4 USB")
        row = [r for r in rows if r.key == SCARLETT.key][0]
        self.assertEqual(row.title, "Scarlett 4i4 USB")

    def test_without_a_stored_label_the_key_is_shown_rather_than_nothing(self):
        rows = ao.menu_rows((KA1,), selection=SCARLETT.key, label="")
        row = [r for r in rows if r.key == SCARLETT.key][0]
        self.assertEqual(row.title, SCARLETT.key)

    def test_a_present_saved_device_is_a_normal_enabled_row(self):
        """Negative control: the inert row must appear only when ABSENT."""
        rows = ao.menu_rows((KA1, SCARLETT), selection=SCARLETT.key)
        row = [r for r in rows if r.key == SCARLETT.key][0]
        self.assertTrue(row.enabled)
        self.assertNotIn("not connected", row.subtitle)

    def test_there_is_exactly_one_row_per_device(self):
        """The inert row must not duplicate a device that is present."""
        rows = ao.menu_rows((KA1, SCARLETT), selection=SCARLETT.key)
        self.assertEqual(len(keys(rows)), len(set(keys(rows))))


class SettingsLabelTests(unittest.TestCase):
    def test_absent_selection_says_so_on_the_settings_row(self):
        """The settings row is what you read at soundcheck without opening
        anything. It must not claim a device that is not there."""
        with mock.patch.object(ao, "list_outputs", return_value=(KA1,)), \
             mock.patch.object(ao, "current_selection",
                                        return_value=(SCARLETT.key, "Scarlett 4i4 USB")):
            self.assertEqual(ao.output_settings_label(),
                             "Audio device — Scarlett 4i4 USB (not connected)")

    def test_present_selection_names_the_device(self):
        with mock.patch.object(ao, "list_outputs", return_value=(KA1,)), \
             mock.patch.object(ao, "current_selection",
                                        return_value=(KA1.key, "FiiO KA1")):
            self.assertEqual(ao.output_settings_label(), "Audio device — FiiO KA1")

    def test_automatic(self):
        with mock.patch.object(ao, "current_selection", return_value=(ao.AUTO, "")):
            self.assertEqual(ao.output_settings_label(), "Audio device — Automatic")


class EnumerationIsNotReimplementedTests(unittest.TestCase):
    """Python must not grow its own idea of what a selectable device is.

    Four divergent buffer lists is the precedent; that one made 96 and 192
    runnable on the appliance and unreachable from every user interface.
    """

    def test_python_shells_out_to_the_same_enumerator_the_graph_uses(self):
        import inspect
        src = inspect.getsource(ao.list_outputs)
        self.assertIn("LIST_OUTPUTS_SCRIPT", src)
        for invented in ("/proc/asound", "idVendor", "pcm0p", "Loopback", "Dummy"):
            self.assertNotIn(invented, src,
                             f"list_outputs() reimplements enumeration ({invented})")


if __name__ == "__main__":
    unittest.main()
