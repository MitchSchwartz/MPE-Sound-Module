"""JACK transport state helpers."""

import unittest

from scripts.sooperlooper.jack_transport_util import (
    transport_label,
    transport_rolling,
    transport_stopped,
)


class JackTransportUtilTests(unittest.TestCase):
    def test_rolling(self) -> None:
        self.assertTrue(transport_rolling("jack.ROLLING"))
        self.assertTrue(transport_rolling("jack.STARTING"))
        self.assertFalse(transport_rolling("jack.STOPPED"))

    def test_stopped(self) -> None:
        self.assertTrue(transport_stopped("jack.STOPPED"))
        self.assertFalse(transport_stopped("jack.ROLLING"))

    def test_label(self) -> None:
        self.assertEqual(transport_label("jack.ROLLING"), "ROLLING")


if __name__ == "__main__":
    unittest.main()
