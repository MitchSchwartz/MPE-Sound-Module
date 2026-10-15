"""The committed patch metadata baseline must not carry machine-specific paths.

The baseline ships in a public repo. Rows identify patches by ``stable_key``;
the ``path`` field is a root-keyed pointer (``factory:Basses/X.fxp``), never an
absolute path from whichever machine generated the file.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from patch_browser.patch_identity import make_patch_entry
from patch_browser.patch_metadata import build_baseline_document

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = REPO_ROOT / "data" / "patch_metadata_baseline.json"

# Absolute POSIX paths, home shorthands, and Windows drive paths.
_ABSOLUTE = re.compile(r'"(/|~|[A-Za-z]:[\\/])')
_HOME_DIRS = re.compile(r"(/home/|/Users/|/root/|\\Users\\)")


class CommittedBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = BASELINE.read_text(encoding="utf-8")
        self.rows = json.loads(self.text)["patches"]

    def test_no_home_directory_paths_anywhere(self) -> None:
        hits = _HOME_DIRS.findall(self.text)
        self.assertEqual(len(hits), 0, f"{len(hits)} home-directory path(s) in baseline")

    def test_every_path_is_root_keyed(self) -> None:
        bad = []
        for key, row in self.rows.items():
            path = str(row.get("path", ""))
            root = key.split(":", 1)[0]
            if _ABSOLUTE.match(json.dumps(path)) or not path.startswith(f"{root}:"):
                bad.append(key)
        self.assertEqual(bad[:5], [], f"{len(bad)} row(s) with non-root-keyed path")


class GeneratorTests(unittest.TestCase):
    def test_build_baseline_writes_root_keyed_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "factory"
            path = root / "Piano" / "Grand.fxp"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"x")
            entry = make_patch_entry(
                name="Grand", path=path, patch_dir=root, root_label="factory"
            )
            doc = build_baseline_document({entry["stable_key"]: entry})
            row = doc["patches"][entry["stable_key"]]
            self.assertEqual(row["path"], "factory:Piano/Grand.fxp")
            self.assertNotIn(tmp, json.dumps(doc))

    def test_absolute_path_without_root_fields_is_reduced_to_name(self) -> None:
        doc = build_baseline_document(
            {"k": {"name": "X", "path": "/somewhere/deep/X.fxp", "category": "Bass"}}
        )
        self.assertEqual(doc["patches"]["k"]["path"], "X.fxp")


if __name__ == "__main__":
    unittest.main()
