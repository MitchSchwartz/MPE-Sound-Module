"""Tests for looper song save/load."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests import conftest  # noqa: F401

from scripts.sooperlooper.looper_songs import (
    SongResult,
    list_songs,
    load_song,
    manifest_path,
    save_song,
    session_has_content,
    slugify,
    wav_path,
)
from scripts.sooperlooper.sl_loop_states import SL_STATE_OFF, SL_STATE_PLAYING


class SlugifyTests(unittest.TestCase):
    def test_slugify_normalizes(self) -> None:
        self.assertEqual(slugify("Friday Jam!"), "friday-jam")


class ListSongsTests(unittest.TestCase):
    def test_list_reads_manifests_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "old.json"
            new = root / "new.json"
            old.write_text(json.dumps({"name": "Old", "saved_at": "1"}), encoding="utf-8")
            new.write_text(json.dumps({"name": "New", "saved_at": "2"}), encoding="utf-8")
            import os
            import time

            os.utime(old, (time.time() - 10, time.time() - 10))
            songs = list_songs(songs_dir=root)
            self.assertEqual([s.slug for s in songs], ["new", "old"])


class SaveLoadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.probe = MagicMock()
        self.probe.get.side_effect = self._get
        self.probe.send = MagicMock()
        self._state = {0: SL_STATE_PLAYING, 1: SL_STATE_OFF}
        self._len = {0: 2.0, 1: 0.0}
        self._wet = {0: 0.8}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _get(self, ctrl: str, loop: int = 0, timeout: float = 1.5):
        if ctrl == "tempo" and loop == -1:
            return 120.0
        if ctrl == "state":
            return self._state.get(loop, SL_STATE_OFF)
        if ctrl == "loop_len":
            return self._len.get(loop, 0.0)
        if ctrl == "wet":
            return self._wet.get(loop, 1.0)
        return None

    @patch("scripts.sooperlooper.looper_songs._save_loop_blocking", return_value=True)
    @patch("scripts.sooperlooper.looper_songs.MIN_TAIL_WAV_BYTES", 10)
    def test_save_writes_manifest_and_wav(self, _save) -> None:
        def _write(send, loop, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * 64)
            return True

        _save.side_effect = _write
        result = save_song(
            self.probe,
            "My Song",
            songs_dir=self.root,
            scratch_loop=14,
        )
        self.assertTrue(result.ok, result.message)
        slug = slugify("My Song")
        self.assertTrue(manifest_path(slug, songs_dir=self.root).exists())
        self.assertTrue(wav_path(slug, 0, songs_dir=self.root).exists())
        data = json.loads(manifest_path(slug, songs_dir=self.root).read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "My Song")
        self.assertEqual(len(data["loops"]), 1)

    def test_save_rejects_duplicate_without_overwrite(self) -> None:
        manifest_path("dup", songs_dir=self.root).write_text("{}", encoding="utf-8")
        result = save_song(self.probe, "dup", songs_dir=self.root, overwrite=False)
        self.assertFalse(result.ok)

    @patch("scripts.sooperlooper.looper_songs.time.sleep", return_value=None)
    def test_load_applies_manifest(self, _sleep) -> None:
        slug = "jam"
        wav = wav_path(slug, 0, songs_dir=self.root)
        wav.write_bytes(b"x" * 64)
        manifest_path(slug, songs_dir=self.root).write_text(
            json.dumps(
                {
                    "version": 1,
                    "name": "Jam",
                    "bpm": 120.0,
                    "grid_active": True,
                    "loops": [
                        {
                            "i": 0,
                            "file": wav.name,
                            "len_s": 2.0,
                            "sl_state": SL_STATE_PLAYING,
                            "wet": 0.75,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        result = load_song(self.probe, slug, songs_dir=self.root, scratch_loop=14)
        self.assertTrue(result.ok, result.message)
        paths = [c.args[0] for c in self.probe.send.call_args_list]
        self.assertIn("/sl/0/load_loop", paths)

    def test_session_has_content(self) -> None:
        self.assertTrue(session_has_content(self.probe, scratch_loop=14))
        self._state[0] = SL_STATE_OFF
        self.assertFalse(session_has_content(self.probe, scratch_loop=14))


if __name__ == "__main__":
    unittest.main()
