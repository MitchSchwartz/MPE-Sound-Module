"""Surge's amp/volume is never set: the stage gain in mpe-fx-host is the one level.

Surge reads `/param/*/amp/volume` as a cube law clamped to 0..1, so a gain sent
there in dB was cut three times too deep and lost above unity. The level of the
trunk is decided in `patch_normalization.stage_db` and sent by `PatchLoader` as
a `gain` datagram. This refuses a second site: any product file (Python or
shell) that names Surge's volume parameter, except

- `PatchLoader`'s AUTHORED_VOLUME_PATHS, read only (`_query_osc_float`), to
  notice an edited patch. A read, not a send;
- the calibration script's AMP_VOLUME_PATHS, written only by
  `set_capture_volume`, which pads a clipped capture of the standalone Surge
  that calibration measures, outside the stage.

The pad is the one named exception to "nothing sends amp/volume" (spec D3 against
D9). It is enforced here: one binding, one writing function, one calling file,
and the write always puts the authored value back
(tests/test_calibration_stage.py).
"""

from __future__ import annotations

import ast
import re
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
AMP_VOLUME = re.compile(r"amp/volume")
# A string that is a path, not prose: no whitespace, naming the parameter. Catches
# "/param/a/amp/volume", f"/param/{scene}/amp/volume" and a bare "amp/volume" piece.
PATH_TOKEN = re.compile(r"^\S*amp/volume\S*$")

# (file, name the literal is bound to): the only places it may appear.
ALLOWED = {
    ("patch_browser/patch_loader.py", "AUTHORED_VOLUME_PATHS"),
    ("scripts/calibrate-patch-normalization.py", "AMP_VOLUME_PATHS"),
}


def product_files(root: Path) -> list[Path]:
    out = []
    for path in root.rglob("*"):
        rel = path.relative_to(root).parts
        if not path.is_file() or path.suffix not in (".py", ".sh"):
            continue
        if rel[0] in ("tests", ".git", "Documents", "docs") or "__pycache__" in rel:
            continue
        out.append(path)
    return out


def _bound_name(tree: ast.AST, node: ast.Constant) -> str | None:
    for parent in ast.walk(tree):
        if isinstance(parent, ast.Assign) and any(
            sub is node for sub in ast.walk(parent.value)
        ):
            targets = [t.id for t in parent.targets if isinstance(t, ast.Name)]
            return targets[0] if len(targets) == 1 else None
    return None


def amp_volume_sites(root: Path) -> list[tuple[str, str | None]]:
    """(file, bound name) for every product mention of Surge's volume parameter."""
    sites = []
    for path in product_files(root):
        rel = str(path.relative_to(root))
        text = path.read_text(encoding="utf-8", errors="replace")
        if not AMP_VOLUME.search(text):
            continue
        if path.suffix != ".py":
            sites.append((rel, None))
            continue
        tree = ast.parse(text)
        inside_fstring = {
            id(part) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)
            for part in node.values
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                value = "".join(
                    p.value if isinstance(p, ast.Constant) else "{}" for p in node.values
                )
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in inside_fstring:
                    continue
                value = node.value
            else:
                continue
            if PATH_TOKEN.match(value):
                sites.append((rel, _bound_name(tree, node)))
    return sites


def name_uses(path: Path, name: str) -> list[str]:
    """The enclosing function (or call) of every use of a module-level name."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    uses = []
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Name) and node.id == name:
                    uses.append(fn.name)
    return uses


class NoSecondLevelSite(unittest.TestCase):
    def test_only_the_two_named_sites_mention_amp_volume(self) -> None:
        sites = amp_volume_sites(REPO)
        self.assertEqual(sorted(set(sites) - ALLOWED), [])
        # Both exceptions exist, so a rename cannot leave the list stale and silent.
        self.assertEqual(set(sites), ALLOWED)

    def test_the_loader_only_reads_it(self) -> None:
        path = REPO / "patch_browser/patch_loader.py"
        self.assertEqual(set(name_uses(path, "AUTHORED_VOLUME_PATHS")), {"_settle_patch_level"})
        text = path.read_text(encoding="utf-8")
        self.assertIn("self._query_osc_float(path))\n                for path in AUTHORED_VOLUME_PATHS)", text)
        self.assertNotRegex(text, r"send_message\([^)]*(AUTHORED_VOLUME_PATHS|\bpath\b)")

    def test_calibration_writes_it_in_one_function(self) -> None:
        path = REPO / "scripts/calibrate-patch-normalization.py"
        self.assertEqual(set(name_uses(path, "AMP_VOLUME_PATHS")), {"set_capture_volume"})

    def test_nothing_outside_calibration_calls_the_pad(self) -> None:
        callers = [
            str(p.relative_to(REPO)) for p in product_files(REPO)
            if p.suffix == ".py" and "set_capture_volume(" in p.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertEqual(callers, ["scripts/calibrate-patch-normalization.py"])


class WhoSendsTheStage(unittest.TestCase):
    """PatchLoader sends nothing unless handed a sender: the instrument's loaders
    are, the calibration's is not (it measures Surge alone)."""

    INSTRUMENT = ("patch_browser/touch_browser_app.py", "patch_browser_ui.py", "scripts/load-patch-osc.py")

    def _calls(self, rel: str) -> list[str]:
        text = (REPO / rel).read_text(encoding="utf-8")
        return re.findall(r"PatchLoader\([^)]*\)?[^)]*\)", text)

    def test_the_instruments_loaders_send(self) -> None:
        for rel in self.INSTRUMENT:
            calls = self._calls(rel)
            self.assertTrue(calls, rel)
            for call in calls:
                self.assertIn("stage_sender=UdpStageSender()", call, rel)

    def test_calibrations_loader_does_not(self) -> None:
        calls = self._calls("scripts/calibrate-patch-normalization.py")
        self.assertEqual(len(calls), 3)
        for call in calls:
            self.assertNotIn("stage_sender", call)

    def test_the_default_sends_nothing(self) -> None:
        from unittest import mock

        from patch_browser.patch_loader import PatchLoader

        with mock.patch("socket.socket.sendto") as sendto:
            loader = PatchLoader(normalization_store=mock.Mock(**{"stage_norm.return_value": (6.0, None)}))
            loader.set_volume(1.0)
            loader.refresh_patch_volume("X")
        sendto.assert_not_called()



# The stage gain's senders, by (file, enclosing function): every construction of
# UdpStageSender and every fx_host_protocol.encode_gain call in product code. A
# `gain` sent from anywhere else moves the live trunk behind PatchLoader's back
# (P0, 2026-10-19: the UI's keepalive re-applied its patch's boost to a patch a
# harness had loaded). Changing this set is a decision, not a refactor.
STAGE_SENDERS = {
    ("patch_browser/touch_browser_app.py", "UdpStageSender", "__init__"),
    ("patch_browser_ui.py", "UdpStageSender", "__init__"),
    ("patch_browser_ui.py", "UdpStageSender", "_handle_surge_error_dialog"),
    ("scripts/load-patch-osc.py", "UdpStageSender", "main"),
    ("scripts/calibrate-patch-normalization.py", "UdpStageSender", "run_controls"),
    ("patch_browser/patch_loader.py", "encode_gain", "_send_stage_db"),
    ("scripts/calibrate-patch-normalization.py", "encode_gain", "run_controls"),
}
# `gain <n>` written as a literal outside the encoder. The live monitor's own
# `gain` goes to its own port (scripts/sooperlooper/live_monitor.py), not to
# mpe-fx-host.
GAIN_LITERAL_ALLOWED = {
    "patch_browser/fx_host_protocol.py",
    "scripts/sooperlooper/live_monitor.py",
}
GAIN_LITERAL = re.compile(r"^gain [\d{]")
SHELL_GAIN_SEND = re.compile(r"\bgain\s+[\d.$]")
SHELL_UDP = re.compile(r"/dev/udp|\bnc\b|\bsocat\b|\boscsend\b|\b9958\b")


def _enclosing(tree: ast.AST) -> dict[int, str | None]:
    """Each node's innermost enclosing function name (None at module level)."""
    owner: dict[int, str | None] = {}

    def visit(node: ast.AST, name: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else name
            owner[id(child)] = inner
            visit(child, inner)

    visit(tree, None)
    return owner


def stage_sender_sites(root: Path) -> list[tuple[str, str, str | None]]:
    """(file, what, enclosing function) for every stage-gain send in product code."""
    sites = []
    for path in product_files(root):
        rel = str(path.relative_to(root))
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix == ".sh":
            for line in text.splitlines():
                if not line.lstrip().startswith("#") and SHELL_GAIN_SEND.search(line) and SHELL_UDP.search(line):
                    sites.append((rel, "shell gain", None))
            continue
        tree = ast.parse(text)
        owner = _enclosing(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name in ("UdpStageSender", "encode_gain"):
                    sites.append((rel, name, owner.get(id(node))))
            literal = None
            if isinstance(node, ast.JoinedStr):
                literal = "".join(p.value if isinstance(p, ast.Constant) else "{}" for p in node.values)
            elif isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
                literal = node.value.decode("latin-1") if isinstance(node.value, bytes) else node.value
            if literal is not None and GAIN_LITERAL.match(literal) and rel not in GAIN_LITERAL_ALLOWED:
                sites.append((rel, "gain literal", owner.get(id(node))))
    return sites


class WhoSendsAGain(unittest.TestCase):
    """Every site that sends mpe-fx-host a stage gain, as a frozen list
    (STAGE_SENDERS): the file, the form (UdpStageSender, encode_gain or a
    literal) and the enclosing function. It proves nothing about what a listed
    site sends, only that no new site appears unreviewed. Adding a sender means
    adding it here, in review, with the reason it is safe."""

    def test_only_the_named_senders(self) -> None:
        sites = set(stage_sender_sites(REPO))
        self.assertEqual(sorted(sites - STAGE_SENDERS), [], "a new stage-gain sender")
        self.assertEqual(sorted(STAGE_SENDERS - sites), [], "a named sender is gone: update the list")

    def test_the_guard_sees_each_form(self) -> None:
        files = {
            "patch_browser/a.py": "from patch_browser.patch_loader import UdpStageSender\n"
                                  "def f():\n    UdpStageSender()(b'')\n",
            "scripts/b.py": "from patch_browser import fx_host_protocol\n"
                            "def g():\n    s.sendto(fx_host_protocol.encode_gain(2.0), a)\n",
            "patch_browser/c.py": "def h(v):\n    s.sendto(f'gain {v:.8f}'.encode(), a)\n",
            "patch_browser/d.py": "X = b'gain 4.0'\n",
            "scripts/e.sh": "echo \"gain 4.0\" > /dev/udp/127.0.0.1/9958\n",
            "patch_browser/ok.py": "raise ValueError(f'gain must be finite, got {v}')\n",
            "tests/t.py": "X = b'gain 4.0'\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, text in files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text)
            sites = sorted(stage_sender_sites(root), key=str)
        self.assertEqual(sites, sorted([
            ("patch_browser/a.py", "UdpStageSender", "f"),
            ("scripts/b.py", "encode_gain", "g"),
            ("patch_browser/c.py", "gain literal", "h"),
            ("patch_browser/d.py", "gain literal", None),
            ("scripts/e.sh", "shell gain", None),
        ], key=str))

class GuardSees(unittest.TestCase):
    """The guard's own controls: an added send, in each form, is caught."""

    def _sites(self, files: dict[str, str]) -> list[tuple[str, str | None]]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, text in files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text)
            return amp_volume_sites(root)

    def test_a_python_send_is_a_site(self) -> None:
        sites = self._sites({"patch_browser/x.py": 'c.send_message("/param/a/amp/volume", 0.5)\n'})
        self.assertEqual(sites, [("patch_browser/x.py", None)])

    def test_a_scene_b_send_bound_to_a_name_is_a_site(self) -> None:
        sites = self._sites({"scripts/y.py": 'P = "/param/b/amp/volume"\n'})
        self.assertEqual(sites, [("scripts/y.py", "P")])

    def test_a_built_path_is_a_site(self) -> None:
        sites = self._sites({
            "patch_browser/f.py": 'c.send_message(f"/param/{scene}/amp/volume", v)\n',
            "patch_browser/g.py": 'c.send_message("/param/a/" + "amp/volume", v)\n',
        })
        self.assertEqual(sorted(sites), [("patch_browser/f.py", None), ("patch_browser/g.py", None)])

    def test_a_shell_send_is_a_site(self) -> None:
        sites = self._sites({"scripts/z.sh": "oscsend localhost 53280 /param/a/amp/volume f 1\n"})
        self.assertEqual(sites, [("scripts/z.sh", None)])

    def test_tests_and_prose_are_not(self) -> None:
        sites = self._sites({
            "tests/t.py": 'X = "/param/a/amp/volume"\n',
            "patch_browser/doc.py": '"""Surge\'s `/param/*/amp/volume` is never set."""\n',
        })
        self.assertEqual(sites, [])


if __name__ == "__main__":
    unittest.main()
