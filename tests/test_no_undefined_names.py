"""Catch names that exist nowhere in a file — the runtime NameError class.

2026-08-27: `sl_grid_sync.main()` called `resolve_num_loops()` that was never
imported. The full suite passed — 1209 tests — because nothing exercises
`main()`, so the appliance deployed with grid sync dead: every loop left
unquantized, and the only sign was one WARN line in a deploy log.

Tests cover behaviour that is called. This covers the rest of the file. It is
deliberately the crudest possible check — a name is a fault only if it is bound
*nowhere* in the file at any scope and is not a builtin — so it has almost no
false positives and needs no dependency (this Pi has no pyflakes or ruff).
It cannot find shadowing or scope errors; it finds "you forgot the import",
which is the one that shipped.
"""

from __future__ import annotations

import ast
import builtins
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCAN_DIRS = [REPO / "scripts", REPO / "patch_browser"]
BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__",
                                 "__package__", "__loader__", "__builtins__"}


def _bound_names(tree: ast.AST) -> set[str]:
    """Every name bound anywhere in the file, at any scope."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.MatchAs) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchStar) and node.name:
            names.add(node.name)
    return names


def _has_star_import(tree: ast.AST) -> bool:
    return any(
        isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names)
        for n in ast.walk(tree)
    )


def undefined_names(path: Path) -> list[tuple[str, int]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    if _has_star_import(tree):
        return []  # a star import can supply anything; not analysable here
    known = _bound_names(tree) | BUILTINS
    bad: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in known:
                bad.append((node.id, node.lineno))
    return bad


def python_files() -> list[Path]:
    out: list[Path] = []
    for root in SCAN_DIRS:
        if root.exists():
            out.extend(sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts))
    return out


class NoUndefinedNamesTests(unittest.TestCase):
    def test_every_script_resolves_its_names(self) -> None:
        problems: list[str] = []
        for path in python_files():
            for name, line in undefined_names(path):
                problems.append(f"{path.relative_to(REPO)}:{line} undefined name {name!r}")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_the_checker_catches_a_missing_import(self) -> None:
        """Positive control. The regression that shipped, in miniature —
        without this the checker could return [] forever and the suite above
        would stay green exactly as it did on 2026-08-27."""
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sample.py"
            p.write_text("def main():\n    return resolve_num_loops()\n")
            self.assertEqual(undefined_names(p), [("resolve_num_loops", 2)])

    def test_the_checker_accepts_a_present_import(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sample.py"
            p.write_text("from x import resolve_num_loops\n\n"
                         "def main():\n    return resolve_num_loops()\n")
            self.assertEqual(undefined_names(p), [])

    def test_it_actually_scanned_something(self) -> None:
        """A path typo would make every scan vacuously pass."""
        files = python_files()
        self.assertGreater(len(files), 30, "the scan found suspiciously few files")
