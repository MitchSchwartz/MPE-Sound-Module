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


class MissingFileIsSilent(unittest.TestCase):
    """A file that does not exist is the normal state for the request and
    handoff files this helper reads, and the docstring already promises
    {} for missing. Warning on it put ~419 journald writes per second on
    the remapper's hot path (measured 2026-08-28).
    """

    def test_absent_file_returns_empty_without_printing(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "not-there.json"
            with redirect_stdout(buf):
                result = read_json_dict(missing, label="governor-fade-request")
        self.assertEqual(result, {})
        self.assertEqual(buf.getvalue(), "", "absent file must not log")

    def test_corrupt_file_still_warns(self):
        """Silencing ENOENT must not silence real problems."""
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json")
            with redirect_stdout(buf):
                result = read_json_dict(bad, label="bad file")
        self.assertEqual(result, {})
        self.assertIn("bad file", buf.getvalue())
