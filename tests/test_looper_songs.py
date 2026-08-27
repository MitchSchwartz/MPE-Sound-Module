"""Tests for looper song save/load."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests import conftest  # noqa: F401

from scripts.sooperlooper.looper_songs import (
    SlotEntry,
    SongResult,
    _fsync_dir,
    _fsync_file,
    TrackEntry,
    build_manifest_v2,
    list_songs,
    load_song,
    manifest_path,
    save_song,
    session_has_content,
    parse_manifest,
    slugify,
    verify_slot_files,
    wav_path,
    wav_path_v2,
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
    @patch("scripts.sooperlooper.looper_songs.MIN_LOOP_WAV_BYTES", 10)
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
        self.assertTrue(wav_path_v2(slug, 0, 0, songs_dir=self.root).exists(),
                        "v2 layout: the filename carries (track, slot)")
        data = json.loads(manifest_path(slug, songs_dir=self.root).read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "My Song")
        self.assertEqual(data["version"], 2)
        self.assertEqual(len(data["tracks"]), 1)
        entry = data["tracks"][0]
        self.assertEqual(entry["track"], 0)
        self.assertEqual(entry["active_slot"], 0, "no matrix given — slot 0")
        self.assertEqual(len(entry["slots"]), 8)
        self.assertEqual(entry["slots"][0]["file"], wav_path_v2(slug, 0, 0).name)
        self.assertTrue(all(s is None for s in entry["slots"][1:]))

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


class ManifestV2Tests(unittest.TestCase):
    """Manifest v2 parse / build / verify — multi-clip-per-track-spec rev 3."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _wav(self, name: str, *, size: int = 4096) -> Path:
        path = self.root / name
        path.write_bytes(b"\x00" * size)
        return path

    # --- v1 compat -----------------------------------------------------
    def test_v1_manifest_becomes_slot_zero_of_each_track(self) -> None:
        """Gate A: v1 songs load forever. Nothing rewrites them in place."""
        song = parse_manifest(
            {
                "version": 1,
                "name": "Old",
                "bpm": 120.0,
                "grid_active": True,
                "loops": [
                    {"i": 0, "file": "old_00.wav", "len_s": 2.0, "sl_state": 4, "wet": 0.8},
                    {"i": 3, "file": "old_03.wav", "len_s": 4.0, "sl_state": 10},
                ],
            },
            slug="old",
        )
        self.assertIsNotNone(song)
        self.assertEqual(song.version, 1)
        self.assertEqual([t.track for t in song.tracks], [0, 3])
        first = song.track(0)
        self.assertEqual(first.active_slot, 0, "v1 had nothing else it could be")
        self.assertEqual(first.slots[0].file, "old_00.wav")
        self.assertTrue(all(s is None for s in first.slots[1:]))
        self.assertAlmostEqual(first.wet, 0.8)
        self.assertEqual(len(first.slots), 8)

    def test_v1_and_v2_parse_to_the_same_shape(self) -> None:
        """One shape is the point — load_song must not branch on version, or
        the v1 path rots the first time the v2 path changes."""
        v1 = parse_manifest(
            {"version": 1, "bpm": 120.0, "loops": [{"i": 2, "file": "x.wav"}]},
            slug="s",
        )
        v2 = parse_manifest(
            {
                "version": 2,
                "bpm": 120.0,
                "tracks": [
                    {"track": 2, "active_slot": 0,
                     "slots": [{"file": "x.wav"}] + [None] * 7}
                ],
            },
            slug="s",
        )
        self.assertEqual(v1.tracks, v2.tracks)

    # --- v2 parsing ----------------------------------------------------
    def test_v2_round_trips_through_build_and_parse(self) -> None:
        tracks = [
            TrackEntry(
                track=5,
                slots=tuple([None, None, SlotEntry("a.wav", 4.0, 4)] + [None] * 5),
                active_slot=2,
                wet=0.5,
            )
        ]
        payload = build_manifest_v2(
            name="Jam", slug="jam", bpm=120.0, grid_active=True,
            tracks=tracks, saved_at="2026-08-26T23:00:00-04:00",
        )
        self.assertEqual(payload["version"], 2)
        back = parse_manifest(json.loads(json.dumps(payload)), slug="jam")
        self.assertEqual(back.tracks, tuple(tracks))
        self.assertEqual(back.name, "Jam")

    def test_multiple_slots_on_one_track_survive(self) -> None:
        song = parse_manifest(
            {
                "version": 2,
                "bpm": 120.0,
                "tracks": [
                    {"track": 0, "active_slot": 3,
                     "slots": [{"file": "s0.wav"}, None, None, {"file": "s3.wav"}]
                              + [None] * 4}
                ],
            },
            slug="m",
        )
        self.assertEqual(song.track(0).occupied(), [0, 3])
        self.assertEqual(song.track(0).active_slot, 3)

    def test_short_slots_array_is_padded_to_eight(self) -> None:
        song = parse_manifest(
            {"version": 2, "tracks": [{"track": 0, "slots": [{"file": "a.wav"}]}]},
            slug="m",
        )
        self.assertEqual(len(song.track(0).slots), 8)

    def test_active_slot_pointing_at_an_empty_cell_is_dropped(self) -> None:
        """A dangling pointer must not become a load of nothing."""
        song = parse_manifest(
            {"version": 2, "tracks": [
                {"track": 0, "active_slot": 4, "slots": [{"file": "a.wav"}] + [None] * 7}
            ]},
            slug="m",
        )
        self.assertIsNone(song.track(0).active_slot)

    def test_one_bad_cell_does_not_lose_the_song(self) -> None:
        song = parse_manifest(
            {"version": 2, "tracks": [
                {"track": 0, "slots": [{"file": "a.wav"}, {"nofile": 1}, "junk"]
                          + [None] * 5}
            ]},
            slug="m",
        )
        self.assertEqual(song.track(0).occupied(), [0])

    def test_out_of_range_track_is_dropped(self) -> None:
        song = parse_manifest(
            {"version": 2, "tracks": [
                {"track": 99, "slots": [{"file": "a.wav"}] + [None] * 7},
                {"track": 1, "slots": [{"file": "b.wav"}] + [None] * 7},
            ]},
            slug="m",
        )
        self.assertEqual([t.track for t in song.tracks], [1])

    def test_empty_and_junk_manifests_return_none(self) -> None:
        for raw in ({}, {"version": 2, "tracks": []}, {"version": 1, "loops": []},
                    {"version": 2, "tracks": [{"track": 0, "slots": [None] * 8}]}):
            self.assertIsNone(parse_manifest(raw, slug="m"), raw)

    def test_grid_inactive_without_a_tempo(self) -> None:
        song = parse_manifest(
            {"version": 2, "bpm": 0.0, "grid_active": True,
             "tracks": [{"track": 0, "slots": [{"file": "a.wav"}] + [None] * 7}]},
            slug="m",
        )
        self.assertFalse(song.grid_active, "grid_active needs a real tempo")

    # --- paths ---------------------------------------------------------
    def test_v2_wav_path_encodes_track_and_slot(self) -> None:
        self.assertEqual(
            wav_path_v2("jam", 3, 5, songs_dir=self.root).name, "jam_t03_s5.wav"
        )

    def test_v1_wav_path_is_unchanged(self) -> None:
        """The v1 reader depends on this exact name."""
        self.assertEqual(wav_path("jam", 3, songs_dir=self.root).name, "jam_03.wav")

    # --- verification (save-path step 3) -------------------------------
    def test_verify_passes_when_every_file_is_present(self) -> None:
        self._wav("a.wav")
        song = parse_manifest(
            {"version": 2, "tracks": [
                {"track": 0, "active_slot": 0, "slots": [{"file": "a.wav"}] + [None] * 7}
            ]}, slug="m")
        self.assertEqual(verify_slot_files(song, songs_dir=self.root), [])

    def test_verify_names_a_missing_file(self) -> None:
        song = parse_manifest(
            {"version": 2, "tracks": [
                {"track": 4, "slots": [None, {"file": "gone.wav"}] + [None] * 6}
            ]}, slug="m")
        problems = verify_slot_files(song, songs_dir=self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn("track 4 slot 1", problems[0])
        self.assertIn("gone.wav", problems[0])

    def test_verify_rejects_a_truncated_file(self) -> None:
        """A stub WAV is the shape a missed swap-flush leaves behind."""
        self._wav("stub.wav", size=8)
        song = parse_manifest(
            {"version": 2, "tracks": [
                {"track": 0, "slots": [{"file": "stub.wav"}] + [None] * 7}
            ]}, slug="m")
        problems = verify_slot_files(song, songs_dir=self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn("under", problems[0])

    def test_verify_checks_inactive_slots_too(self) -> None:
        """Inactive slots are exactly the ones save does NOT write — they rely
        on an earlier flush, which is the thing that can silently not happen."""
        self._wav("live.wav")
        song = parse_manifest(
            {"version": 2, "tracks": [
                {"track": 0, "active_slot": 0,
                 "slots": [{"file": "live.wav"}, {"file": "never-flushed.wav"}]
                          + [None] * 6}
            ]}, slug="m")
        problems = verify_slot_files(song, songs_dir=self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn("never-flushed.wav", problems[0])


class V1UpgradeTests(unittest.TestCase):
    """A v1 song on disk must keep loading, and Overwrite-Save upgrades it."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.probe = MagicMock()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_v1_song(self, slug: str = "oldie") -> None:
        wav_path(slug, 0, songs_dir=self.root).write_bytes(b"\x00" * 4096)
        manifest_path(slug, songs_dir=self.root).write_text(
            json.dumps({
                "version": 1,
                "name": "Oldie",
                "slug": slug,
                "bpm": 120.0,
                "grid_active": True,
                "loops": [{"i": 0, "file": f"{slug}_00.wav", "len_s": 2.0,
                           "sl_state": SL_STATE_PLAYING, "wet": 1.0}],
            }),
            encoding="utf-8",
        )

    @patch("scripts.sooperlooper.looper_songs.time.sleep", return_value=None)
    def test_a_v1_song_still_loads(self, _sleep) -> None:
        self._write_v1_song()
        self.probe.get.return_value = 0
        result = load_song(self.probe, "oldie", songs_dir=self.root)
        self.assertTrue(result.ok, result.message)
        self.assertIn("Oldie", result.message)
        loads = [c.args for c in self.probe.send.call_args_list
                 if c.args[0] == "/sl/0/load_loop"]
        self.assertEqual(len(loads), 1, "the v1 clip is loaded as slot 0")
        self.assertIn("oldie_00.wav", loads[0][1][0])

    @patch("scripts.sooperlooper.looper_songs.time.sleep", return_value=None)
    def test_load_only_touches_the_active_slot(self, _sleep) -> None:
        """Lazy load: inactive occupied slots stay on disk (Gate A)."""
        for name in ("m_t00_s0.wav", "m_t00_s3.wav"):
            (self.root / name).write_bytes(b"\x00" * 4096)
        manifest_path("m", songs_dir=self.root).write_text(
            json.dumps({
                "version": 2, "name": "M", "slug": "m", "bpm": 120.0,
                "grid_active": True,
                "tracks": [{
                    "track": 0, "wet": 1.0, "active_slot": 3,
                    "slots": [{"file": "m_t00_s0.wav", "len_s": 2.0, "sl_state": 4},
                              None, None,
                              {"file": "m_t00_s3.wav", "len_s": 2.0, "sl_state": 4}]
                             + [None] * 4,
                }],
            }),
            encoding="utf-8",
        )
        self.probe.get.return_value = 0
        result = load_song(self.probe, "m", songs_dir=self.root)
        self.assertTrue(result.ok, result.message)
        loads = [c.args for c in self.probe.send.call_args_list
                 if c.args[0] == "/sl/0/load_loop"]
        self.assertEqual(len(loads), 1, "one buffer per track — one load")
        self.assertIn("m_t00_s3.wav", loads[0][1][0],
                      "the ACTIVE slot, not slot 0")

    @patch("scripts.sooperlooper.looper_songs._save_loop_blocking")
    @patch("scripts.sooperlooper.looper_songs.time.sleep", return_value=None)
    def test_overwrite_save_upgrades_v1_and_prunes_the_old_wav(
        self, _sleep, _save
    ) -> None:
        self._write_v1_song()

        def write(_send, _loop, path):
            path.write_bytes(b"\x00" * 4096)
            return True

        _save.side_effect = write
        self.probe.get.side_effect = lambda ctrl, loop=None: {
            "state": SL_STATE_PLAYING, "loop_len": 2.0, "wet": 1.0, "tempo": 120.0
        }.get(ctrl, 0) if loop != 1 else SL_STATE_OFF

        def get(ctrl, loop=None):
            if ctrl == "tempo":
                return 120.0
            if loop == 0:
                return {"state": SL_STATE_PLAYING, "loop_len": 2.0, "wet": 1.0}[ctrl]
            return SL_STATE_OFF if ctrl == "state" else 0.0

        self.probe.get.side_effect = get
        result = save_song(self.probe, "Oldie", overwrite=True, songs_dir=self.root)
        self.assertTrue(result.ok, result.message)

        data = json.loads(
            manifest_path("oldie", songs_dir=self.root).read_text(encoding="utf-8")
        )
        self.assertEqual(data["version"], 2)
        self.assertTrue(wav_path_v2("oldie", 0, 0, songs_dir=self.root).exists())
        self.assertFalse(wav_path("oldie", 0, songs_dir=self.root).exists(),
                         "stale v1 WAV pruned on overwrite")

    @patch("scripts.sooperlooper.looper_songs._save_loop_blocking")
    @patch("scripts.sooperlooper.looper_songs.time.sleep", return_value=None)
    def test_save_aborts_rather_than_write_a_manifest_it_cannot_reload(
        self, _sleep, _save
    ) -> None:
        """Step 3. save_loop 'succeeded' but left nothing usable — without the
        verify, the manifest would point at a stub and the save would look
        exactly like one that worked."""
        _save.side_effect = lambda _s, _l, path: (path.write_bytes(b"\x00" * 8), True)[1]

        def get(ctrl, loop=None):
            if ctrl == "tempo":
                return 120.0
            if loop == 0:
                return {"state": SL_STATE_PLAYING, "loop_len": 2.0, "wet": 1.0}[ctrl]
            return SL_STATE_OFF if ctrl == "state" else 0.0

        self.probe.get.side_effect = get
        result = save_song(self.probe, "Bad", songs_dir=self.root)
        self.assertFalse(result.ok)
        self.assertFalse(manifest_path("bad", songs_dir=self.root).exists(),
                         "no manifest may exist for a song that cannot reload")


class DurabilityTests(unittest.TestCase):
    """D1: a "Saved" toast must mean the song survives a power cut.

    save_loop hit ~700 MB/s in SP1, which is the page cache, not the SD card.
    These tests pin the flushes; without them a save looks identical whether
    the bytes reached the card or not — the reading that cannot fail.
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.probe = MagicMock()

        def get(ctrl, loop=None):
            if ctrl == "tempo":
                return 120.0
            if loop == 0:
                return {"state": SL_STATE_PLAYING, "loop_len": 2.0, "wet": 1.0}[ctrl]
            return SL_STATE_OFF if ctrl == "state" else 0.0

        self.probe.get.side_effect = get

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _save(self, **kw):
        with patch(
            "scripts.sooperlooper.looper_songs._save_loop_blocking",
            side_effect=lambda _s, _l, path: (
                path.write_bytes(b"\x00" * 4096), True
            )[1],
        ), patch("scripts.sooperlooper.looper_songs.time.sleep", return_value=None):
            return save_song(self.probe, "Durable", songs_dir=self.root, **kw)

    def test_save_syncs_every_wav_and_the_directory(self) -> None:
        synced_files: list[Path] = []
        synced_dirs: list[Path] = []
        with patch("scripts.sooperlooper.looper_songs._fsync_file",
                   side_effect=synced_files.append), \
             patch("scripts.sooperlooper.looper_songs._fsync_dir",
                   side_effect=synced_dirs.append):
            result = self._save()
        self.assertTrue(result.ok, result.message)
        names = [p.name for p in synced_files]
        self.assertIn(wav_path_v2("durable", 0, 0, songs_dir=self.root).name, names)
        self.assertTrue(any(n.endswith(".json.tmp") for n in names),
                        f"manifest flushed before the rename: {names}")
        self.assertIn(self.root, synced_dirs,
                      "directory flushed, or the rename itself can be lost")

    def test_wavs_are_synced_before_the_manifest_is_written(self) -> None:
        """Order matters. A manifest durable ahead of the audio it names is a
        song that survives the power cut pointing at files that did not."""
        order: list[str] = []
        with patch("scripts.sooperlooper.looper_songs._fsync_file",
                   side_effect=lambda p: order.append(p.suffix)), \
             patch("scripts.sooperlooper.looper_songs._fsync_dir",
                   side_effect=lambda p: order.append("dir")):
            self.assertTrue(self._save().ok)
        self.assertEqual(order[0], ".wav")
        self.assertLess(order.index(".wav"), order.index(".tmp"))
        self.assertLess(order.index(".tmp"), order.index("dir"))

    def test_fsync_helpers_survive_a_missing_path(self) -> None:
        """Best-effort by design — a vanished temp file must not turn a good
        save into a crash on the instrument."""
        _fsync_file(self.root / "nope.wav")
        _fsync_dir(self.root / "nope")

    def test_fsync_can_be_disabled_for_non_appliance_targets(self) -> None:
        with patch("scripts.sooperlooper.looper_songs.FSYNC_ENABLED", False), \
             patch("scripts.sooperlooper.looper_songs.os.open") as opened:
            _fsync_file(self.root)
            _fsync_dir(self.root)
        opened.assert_not_called()

    def test_fsync_file_really_flushes_a_real_file(self) -> None:
        """Positive control: the helper must actually reach the fd layer.
        Every other test here patches the helper out, so without this one they
        would all still pass with the bodies emptied."""
        target = self.root / "real.wav"
        target.write_bytes(b"\x00" * 64)
        with patch("scripts.sooperlooper.looper_songs.os.fsync") as fsync:
            _fsync_file(target)
        self.assertEqual(fsync.call_count, 1)
