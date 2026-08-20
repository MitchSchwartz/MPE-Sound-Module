"""AST lint: no subprocess/JACK-client forks in periodic loop hot paths.

Documents/DECISIONS.md — no forks in periodic loops. Used by tests/test_periodic_loop_lint.py
(T3a). Scans watchdog, session, HUD, and publisher Python modules.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]

# Modules in scope for T3a (relative to repo root).
PERIODIC_LOOP_MODULES = (
    "scripts/sooperlooper/sl-watchdog.py",
    "scripts/sooperlooper/sl_hud_monitor.py",
    "scripts/sooperlooper/looper_session.py",
    "scripts/session-snapshot-publisher.py",
    "patch_browser/surge_cpu_monitor.py",
    "patch_browser/surge_poly_governor.py",
    "patch_browser/engine_state_monitor.py",
    "patch_browser/looper_clock_monitor.py",
    "patch_browser/surge_peak_monitor.py",
)

# Direct calls to these from a periodic loop body are always errors.
FORBIDDEN_CALLEES = frozenset({"jack_graph", "journalctl", "jack_lsp", "jack_cpu_load"})

SUBPROCESS_ATTRS = frozenset({"run", "call", "check_output", "Popen", "check_call", "check_output"})


@dataclass(frozen=True)
class LintFinding:
    path: str
    line: int
    kind: str
    detail: str


def _is_periodic_loop(node: ast.For | ast.While) -> bool:
    if isinstance(node, ast.While):
        test = node.test
        if isinstance(test, ast.Constant) and test.value is True:
            return True
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            inner = test.operand
            if isinstance(inner, ast.Call):
                func = inner.func
                if isinstance(func, ast.Attribute) and func.attr == "is_set":
                    return True
        if isinstance(test, ast.Name) and test.id in {"running", "_running", "g_running"}:
            return True
        if isinstance(test, ast.Attribute) and test.attr in {"_running", "running"}:
            return True
    if isinstance(node, ast.For):
        if isinstance(node.iter, ast.Call):
            func = node.iter.func
            if isinstance(func, ast.Name) and func.id == "range":
                return True
    return False


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
            return f"subprocess.{func.attr}"
        return func.attr
    return None


def _subprocess_in_node(node: ast.AST) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        name = _call_name(child)
        if name is None:
            continue
        if name in FORBIDDEN_CALLEES:
            hits.append((child.lineno, name))
            continue
        if name.startswith("subprocess."):
            bad_argv = False
            for arg in ast.walk(child):
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    low = arg.value.lower()
                    if low in FORBIDDEN_CALLEES or any(
                        x in low for x in ("jack_lsp", "journalctl", "jack_cpu_load", "pgrep")
                    ):
                        bad_argv = True
                        break
            if bad_argv:
                hits.append((child.lineno, name))
    return hits


def _collect_loops(tree: ast.AST) -> list[ast.For | ast.While]:
    loops: list[ast.For | ast.While] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.While)) and _is_periodic_loop(node):
            loops.append(node)
    return loops


def lint_source(source: str, *, path: str = "<string>") -> list[LintFinding]:
    tree = ast.parse(source, filename=path)
    findings: list[LintFinding] = []
    for loop in _collect_loops(tree):
        for lineno, detail in _subprocess_in_node(loop):
            findings.append(LintFinding(path, lineno, "fork-in-periodic-loop", detail))
    return findings


def lint_file(path: Path) -> list[LintFinding]:
    rel = str(path.relative_to(REPO_ROOT)) if path.is_absolute() else str(path)
    return lint_source(path.read_text(encoding="utf-8"), path=rel)


def lint_modules(modules: Iterable[str] = PERIODIC_LOOP_MODULES) -> list[LintFinding]:
    out: list[LintFinding] = []
    for rel in modules:
        out.extend(lint_file(REPO_ROOT / rel))
    return out
