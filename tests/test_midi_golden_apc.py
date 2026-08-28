"""Golden-stream test: the translator against a real APC mini mk2 capture.

The fixture is a verbatim recording of the `APC mini mk2 Notes` port
(ALSA 32:1) taken 2026-08-28 while playing the pads by hand -- 187
messages, 6.7 seconds, notes 36-96, channel 1, velocity fixed at 127.
See docs/CLASSIC-MIDI-PLAN.md section 5 (OPEN-4).

Synthetic tests cover the translator's rules; this one covers the shape
of what the hardware actually emits, which is the part we cannot invent.
"""

import json
import pathlib
import unittest

import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from midi_translate import (  # noqa: E402
    MASTER_CHANNEL,
    MEMBER_CHANNELS,
    ClassicToMpe,
)

FIXTURE = (
    pathlib.Path(__file__).resolve().parent
    / "fixtures"
    / "apc-mini-mk2-notes-2026-08-28.jsonl"
)


def source_held(stream):
    """Notes left hanging by the capture itself."""
    held = []
    for msg in stream:
        if msg[0] & 0xF0 == 0x90 and msg[2] > 0:
            if msg[1] in held:
                held.remove(msg[1])
            held.append(msg[1])
        elif msg[1] in held:
            held.remove(msg[1])
    return set(held)


def load_stream():
    rows = [
        json.loads(line)
        for line in FIXTURE.read_text().splitlines()
        if line.strip()
    ]
    return [row["msg"] for row in rows]


class GoldenApcStream(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stream = load_stream()

    def setUp(self):
        self.translator = ClassicToMpe()
        self.out = []
        for msg in self.stream:
            self.out.extend(self.translator.translate(msg))

    def test_fixture_is_the_capture_we_recorded(self):
        """Guard the fixture itself -- a silently truncated file would
        make every assertion below pass vacuously."""
        self.assertEqual(len(self.stream), 187)
        notes = {m[1] for m in self.stream}
        self.assertEqual((min(notes), max(notes)), (36, 96))
        self.assertEqual({m[0] & 0xF0 for m in self.stream}, {0x80, 0x90})
        self.assertEqual({m[0] & 0x0F for m in self.stream}, {0})
        self.assertEqual({m[2] for m in self.stream if m[0] & 0xF0 == 0x90}, {127})

    def test_every_note_leaves_on_a_member_channel(self):
        for msg in self.out:
            if msg[0] & 0xF0 in (0x80, 0x90):
                self.assertIn(msg[0] & 0x0F, MEMBER_CHANNELS, msg)
                self.assertNotEqual(msg[0] & 0x0F, MASTER_CHANNEL, msg)

    def test_translator_state_mirrors_the_source_stream(self):
        """The capture window closed with note 62 still held, so the
        stream is deliberately unbalanced. The translator must hold
        exactly what the source holds -- no more, no fewer."""
        self.assertEqual(set(self.translator.active_notes), source_held(self.stream))

    def test_note_offs_never_orphaned(self):
        held = {}
        for msg in self.out:
            kind, chan = msg[0] & 0xF0, msg[0] & 0x0F
            if kind == 0x90 and msg[2] > 0:
                held.setdefault(chan, []).append(msg[1])
            else:
                self.assertIn(msg[1], held.get(chan, []), f"off with no on: {msg}")
                held[chan].remove(msg[1])
        residual = {n for notes in held.values() for n in notes}
        self.assertEqual(residual, source_held(self.stream))

    def test_one_note_per_member_channel_at_a_time(self):
        """The MPE invariant that makes per-note expression work at all."""
        held = {}
        for msg in self.out:
            kind, chan = msg[0] & 0xF0, msg[0] & 0x0F
            if kind == 0x90 and msg[2] > 0:
                self.assertNotIn(
                    chan, held, f"channel {chan} reused while holding {held.get(chan)}"
                )
                held[chan] = msg[1]
            elif kind == 0x80 or (kind == 0x90 and msg[2] == 0):
                held.pop(chan, None)

    def test_note_numbers_and_velocities_pass_through_unaltered(self):
        """Note number and velocity are never rewritten. The output is
        the input plus synthetic note_offs -- the APC double-strikes
        (note_on 59 twice with no note_off between), and MPE requires
        the held note be released before the channel retriggers."""
        src_on = [(m[1], m[2]) for m in self.stream if m[0] & 0xF0 == 0x90]
        dst_on = [(m[1], m[2]) for m in self.out if m[0] & 0xF0 == 0x90]
        self.assertEqual(src_on, dst_on)
        self.assertLessEqual({m[1] for m in self.out}, {m[1] for m in self.stream})

    def test_retriggered_note_is_released_before_it_sounds_again(self):
        """Guards the synthetic note_off the double-strike depends on."""
        held = set()
        inserted = 0
        src_i = 0
        for msg in self.out:
            kind = msg[0] & 0xF0
            if kind == 0x90 and msg[2] > 0:
                self.assertNotIn(msg[1], held, f"note {msg[1]} retriggered while held")
                held.add(msg[1])
            else:
                held.discard(msg[1])
        self.assertGreater(
            len(self.out), len(self.stream) - 1, "expected a synthetic release"
        )

    def test_translation_is_deterministic(self):
        second = []
        fresh = ClassicToMpe()
        for msg in self.stream:
            second.extend(fresh.translate(msg))
        self.assertEqual(self.out, second)


if __name__ == "__main__":
    unittest.main()
