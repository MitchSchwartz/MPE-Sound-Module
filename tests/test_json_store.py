"""Tests for atomic JSON store helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from patch_browser.json_store import atomic_write_json, read_json_dict


class JsonStoreTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            payload = {"alpha": {"gain_db": 1.5}, "beta": {"enabled": True}}
            atomic_write_json(path, payload)
            loaded = read_json_dict(path)
            self.assertEqual(loaded, payload)

    def test_read_missing_returns_empty_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            self.assertEqual(read_json_dict(path), {})

    def test_read_invalid_json_returns_empty_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(read_json_dict(path), {})


if __name__ == "__main__":
    unittest.main()
