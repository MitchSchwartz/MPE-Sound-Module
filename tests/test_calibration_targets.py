"""Which patches a calibration run targets: one per stable key, capped after the filter.

Two patches that share a file name in different roots or folders are different
patches with different store entries (``factory:Keys/Church`` and
``thirdparty:Pack/Church``). Deduping by stem dropped one of them for good.
``--limit`` used to cap the scanned list before the missing-only filter, so a
pilot of 12 on a mostly calibrated library measured 3.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from patch_browser.patch_normalization import PatchNormalizationStore  # noqa: E402
from patch_browser.patch_sidecar_key import clear_path_caches  # noqa: E402
from test_calibration_integrity import load_cal_module  # noqa: E402


class _Library:
    """Temp factory + third-party roots with .fxp files at given relative paths."""

    def __init__(self, tmp: Path) -> None:
        self.factory = tmp / "patches_factory"
        self.thirdparty = tmp / "patches_3rdparty"
        self.factory.mkdir()
        self.thirdparty.mkdir()
        self.dirs = [self.factory, self.thirdparty]

    def add(self, root: Path, rel: str) -> Path:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return path


def _calibrated(gain_db: float = -3.0) -> dict:
    return {"gain_db": gain_db, "enabled": True}


class DedupeByStableKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = load_cal_module()
        clear_path_caches()
        self._tmp = tempfile.TemporaryDirectory()
        self.lib = _Library(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()
        clear_path_caches()

    def test_same_stem_in_different_roots_are_both_targets(self) -> None:
        a = self.lib.add(self.lib.factory, "Keys/Church.fxp")
        b = self.lib.add(self.lib.thirdparty, "Pack/Organs/Church.fxp")
        result = self.cal.dedupe_by_patch_key([a, b], self.lib.dirs)
        self.assertEqual(sorted(result), sorted([a, b]))

    def test_same_stem_in_different_folders_of_one_root_are_both_targets(self) -> None:
        a = self.lib.add(self.lib.factory, "Keys/Church.fxp")
        b = self.lib.add(self.lib.factory, "Pads/Church.fxp")
        self.assertEqual(len(self.cal.dedupe_by_patch_key([a, b], self.lib.dirs)), 2)

    def test_same_file_listed_twice_is_one_target(self) -> None:
        a = self.lib.add(self.lib.factory, "Keys/Church.fxp")
        result = self.cal.dedupe_by_patch_key([a, Path(str(a))], self.lib.dirs)
        self.assertEqual(result, [a])

    def test_collect_patch_paths_keeps_both_churches(self) -> None:
        a = self.lib.add(self.lib.factory, "Keys/Church.fxp")
        b = self.lib.add(self.lib.thirdparty, "Pack/Church.fxp")
        args = mock.Mock(favorites_only=False, folder=None, patch=None)
        with mock.patch.object(self.cal, "SURGE_PATCH_DIRS", self.lib.dirs):
            result = self.cal.collect_patch_paths(args)
        self.assertEqual(sorted(p.resolve() for p in result), sorted([a.resolve(), b.resolve()]))


class SelectTargetsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = load_cal_module()
        clear_path_caches()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.lib = _Library(self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()
        clear_path_caches()

    def _select(self, paths, store, **kwargs):
        return self.cal.select_targets(paths, store, patch_dirs=self.lib.dirs, **kwargs)

    def _store(self, data: dict) -> PatchNormalizationStore:
        path = self.tmp / "norm.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        store = PatchNormalizationStore(path)
        store.set_patch_dirs(self.lib.dirs)
        return store

    def test_calibrated_favourite_not_retargeted_but_same_stem_sibling_is(self) -> None:
        a = self.lib.add(self.lib.factory, "Keys/Church.fxp")
        b = self.lib.add(self.lib.thirdparty, "Pack/Church.fxp")
        store = self._store({"factory:Keys/Church": _calibrated()})
        targets = self._select(
            [a, b], store, verify_only=False, force=False, limit=0
        )
        self.assertEqual(targets, [b])

    def test_two_same_stem_targets_write_separate_stable_keyed_entries(self) -> None:
        a = self.lib.add(self.lib.factory, "Keys/Church.fxp")
        b = self.lib.add(self.lib.thirdparty, "Pack/Church.fxp")
        store = self._store({})
        paths = self.cal.dedupe_by_patch_key([a, b], self.lib.dirs)
        targets = self._select(
            paths, store, verify_only=False, force=False, limit=0
        )
        self.assertEqual(len(targets), 2)
        for gain, path in zip((-1.0, -7.0), targets):
            store.set_calibration(path.stem, gain, -20.0, patch_path=str(path))
        store.save()
        saved = json.loads(store.path.read_text())
        self.assertIn("factory:Keys/Church", saved)
        self.assertIn("thirdparty:Pack/Church", saved)
        self.assertNotIn("Church", saved)
        self.assertNotEqual(
            saved["factory:Keys/Church"]["gain_db"], saved["thirdparty:Pack/Church"]["gain_db"]
        )
        self.assertEqual(
            self._select(paths, store, verify_only=False, force=False, limit=0), []
        )

    def test_limit_counts_targets_after_the_missing_filter(self) -> None:
        paths = [self.lib.add(self.lib.factory, f"Lead/P{i:02d}.fxp") for i in range(16)]
        # The first 13 in sort order are calibrated; the old cap-before-filter gave 3.
        store = self._store({f"factory:Lead/P{i:02d}": _calibrated() for i in range(13)})
        more = [self.lib.add(self.lib.factory, f"Pad/Q{i:02d}.fxp") for i in range(10)]
        all_paths = self.cal.dedupe_by_patch_key(paths + more, self.lib.dirs)
        targets = self._select(
            all_paths, store, verify_only=False, force=False, limit=12
        )
        self.assertEqual(len(targets), 12)
        self.assertTrue(all(store.get_calibrated_gain_db(p.stem, patch_path=str(p)) is None for p in targets))

    def test_limit_applies_to_verify_only_targets(self) -> None:
        paths = [self.lib.add(self.lib.factory, f"Lead/P{i:02d}.fxp") for i in range(10)]
        store = self._store({f"factory:Lead/P{i:02d}": _calibrated() for i in range(5, 10)})
        targets = self._select(
            paths, store, verify_only=True, force=False, limit=3
        )
        self.assertEqual([p.stem for p in targets], ["P05", "P06", "P07"])

    def test_force_targets_everything_then_limits(self) -> None:
        paths = [self.lib.add(self.lib.factory, f"Lead/P{i:02d}.fxp") for i in range(6)]
        store = self._store({f"factory:Lead/P{i:02d}": _calibrated() for i in range(6)})
        self.assertEqual(
            len(self._select(paths, store, verify_only=False, force=True, limit=4)), 4
        )


class DryRunStatusTests(unittest.TestCase):
    """The dry-run line reads the entry by the patch's stable key, not its stem."""

    def setUp(self) -> None:
        self.cal = load_cal_module()
        clear_path_caches()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.lib = _Library(self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()
        clear_path_caches()

    def _dry_line(self, path: Path, store: PatchNormalizationStore) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cal.calibrate_patch(
                path, None, store, audio_device="x", mock_lufs=None, dry_run=True
            )
        return out.getvalue()

    def test_stable_keyed_calibrated_patch_reports_skip(self) -> None:
        a = self.lib.add(self.lib.factory, "Keys/Church.fxp")
        b = self.lib.add(self.lib.thirdparty, "Pack/Church.fxp")
        norm = self.tmp / "norm.json"
        norm.write_text(json.dumps({"factory:Keys/Church": _calibrated()}), encoding="utf-8")
        store = PatchNormalizationStore(norm)
        store.set_patch_dirs(self.lib.dirs)
        self.assertIn("[skip (has entry)] Church", self._dry_line(a, store))
        self.assertIn("[would calibrate] Church", self._dry_line(b, store))

    def test_main_dry_run_force_with_limit(self) -> None:
        a = self.lib.add(self.lib.factory, "Keys/Church.fxp")
        b = self.lib.add(self.lib.thirdparty, "Pack/Church.fxp")
        self.lib.add(self.lib.factory, "Keys/Zither.fxp")
        norm = self.tmp / "norm.json"
        norm.write_text(json.dumps({"factory:Keys/Church": _calibrated()}), encoding="utf-8")
        argv = [
            "calibrate", "--dry-run", "--output", str(norm),
            "--pressure-output", str(self.tmp / "pressure.json"),
            "--no-use-loopback", "--audio-device", "hw:9,0",
        ]
        out = io.StringIO()
        with mock.patch.object(self.cal, "SURGE_PATCH_DIRS", self.lib.dirs), \
                mock.patch.object(sys, "argv", argv + ["--force", "--limit", "2"]), \
                contextlib.redirect_stdout(out):
            self.assertEqual(self.cal.main(), 0)
        text = out.getvalue()
        self.assertIn("Targets: 2 patch(es) (3 in scope, 2 missing entries, force overwrite, limit 2)", text)
        self.assertIn("[skip (has entry)] Church", text)
        self.assertIn("[would calibrate] Church", text)
        self.assertNotIn("Zither", text)
        del a, b

        out = io.StringIO()
        with mock.patch.object(self.cal, "SURGE_PATCH_DIRS", self.lib.dirs), \
                mock.patch.object(sys, "argv", argv + ["--limit", "1"]), \
                contextlib.redirect_stdout(out):
            self.assertEqual(self.cal.main(), 0)
        text = out.getvalue()
        self.assertIn("Targets: 1 patch(es) (3 in scope, 2 missing entries, limit 1)", text)
        self.assertIn("[would calibrate] Church", text)
        self.assertNotIn("skip (has entry)", text)


if __name__ == "__main__":
    unittest.main()
