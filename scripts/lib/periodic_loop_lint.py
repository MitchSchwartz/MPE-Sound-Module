"""AST lint: no subprocess/JACK-client forks in periodic loop hot paths.

`Documents/DECISIONS.md` — no forks in periodic loops (~400 ms/fork on the Pi).
Used by `tests/test_periodic_loop_lint.py` (T3a).

**What it actually caught, measured 2026-08-30.** Twelve evasions of its own
stated purpose were run against it: **three** were caught. The three were the
valuable ones — a direct call, one function deep, and a method on `self` — so
the interprocedural core works. The rest is now partly closed and the gaps that
remain are written down rather than left to be rediscovered:

  * **Loop shape.** `while not self._stop.wait(interval)` — the idiomatic
    periodic poll, being the sleep and the stop-check in one — was not
    recognised. That was the MAIN LOOP of four of the nine modules the lint was
    pointed at, and they contain no `while True` at all, so it walked them and
    found nothing to check. **Fixed.**
  * **Scope.** The nine modules were a hand-maintained tuple and had drifted:
    eleven other modules with periodic loops were missing, including
    `scripts/sooperlooper-apc-bench.py`, the busiest loop in the system at a
    measured ~485 Hz. **Fixed** — the scope is discovered (39 modules), and
    `KNOWN_PERIODIC_MODULES` is kept only so discovery can be checked against
    something known.
  * **Call shape.** `os.system("jack_lsp")` forks exactly as hard as
    `subprocess.run` and escaped entirely. **Fixed** via `SPAWNING_CALLS`.

**Still escaping, deliberately recorded (6 of 12 caught):** an aliased import
(`import subprocess as sp`), a from-import (`from subprocess import run`), argv
built in a variable or an f-string, and the loop shapes `while self.alive:` and
`for x in items:`. The first two are closable with per-module import tracking;
the argv ones need dataflow.

**Rejected, with evidence: flagging every `subprocess.*` call in a periodic
loop.** It is closer to the doctrine's words and it is wrong here — it produces
four findings and all four are legitimate: `sl-watchdog.py:226` (`systemctl
restart`) and `:530` (`wire-jack-graph.sh connect`) are REPAIR actions on a
fault branch, not per-tick work, and `calibrate-patch-normalization.py:700` is
an `ffmpeg` capture in an offline calibration tool. Naming specific expensive
commands rather than all forks is therefore a deliberate choice, not a lax one.
`_reachable_from_loop` cannot see branch conditions, so "reachable from a loop"
is not "runs every tick".

**Zero findings across all 39 discovered modules** as of 2026-08-30 — the tree
is clean, and it was clean before the widening too. The defect fixed here was
never a fork; it was a guard that read the same whether it was guarding or not.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Roots searched for periodic loops. The scope used to be a hand-maintained
#: tuple of nine paths, which had drifted: eleven other modules containing
#: `while True` were not in it, including `scripts/sooperlooper-apc-bench.py`,
#: the busiest loop in the system at a measured ~485 Hz. A list of what to
#: guard, maintained separately from the code being guarded, decays silently —
#: so the scope is discovered instead.
SEARCH_ROOTS = ("scripts", "patch_browser")

#: Kept only so the discovery can be checked against something known. These are
#: the nine the hand-maintained tuple named; the lint no longer depends on it.
KNOWN_PERIODIC_MODULES = (
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


def discover_periodic_modules() -> tuple[str, ...]:
    """Every module under SEARCH_ROOTS that contains a periodic loop."""
    out: list[str] = []
    for root in SEARCH_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            if "test" in path.name:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError):
                continue
            if _collect_loops(tree):
                out.append(path.relative_to(REPO_ROOT).as_posix())
    return tuple(out)


#: Backwards-compatible name. Discovered, not declared.
def PERIODIC_LOOP_MODULES() -> tuple[str, ...]:  # noqa: N802
    return discover_periodic_modules()


# Direct calls to these from a periodic loop body are always errors.
FORBIDDEN_CALLEES = frozenset({"jack_graph", "journalctl", "jack_lsp", "jack_cpu_load", "pgrep"})

#: `os.system("jack_lsp")` forks exactly as hard as `subprocess.run`. These
#: escaped entirely: `_call_name` returned "os.system", which is neither in
#: FORBIDDEN_CALLEES nor prefixed "subprocess.".
SPAWNING_CALLS = frozenset({
    "os.system", "os.popen", "os.fork", "os.forkpty",
    "os.spawnl", "os.spawnv", "os.spawnlp", "os.spawnvp", "os.execv",
})

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
                # `.wait(interval)` is the idiomatic periodic poll — the sleep
                # and the stop-check in one call — and matching only `.is_set`
                # made this lint blind to the MAIN LOOP of four of the nine
                # modules it was pointed at (surge_cpu_monitor:101,
                # engine_state_monitor:44, looper_clock_monitor:53,
                # surge_peak_monitor:98). They contain no `while True` at all,
                # so the lint walked them and found nothing to check, and
                # reported the same clean result it would have if they were
                # forking on every tick.
                if isinstance(func, ast.Attribute) and func.attr in {"is_set", "wait"}:
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
        if isinstance(func.value, ast.Name) and func.value.id in ("subprocess", "os"):
            return f"{func.value.id}.{func.attr}"
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
        if name in FORBIDDEN_CALLEES or name in SPAWNING_CALLS:
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


def _build_function_map(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = node
    return funcs


def _callees_from_node(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _reachable_from_loop(
    loop: ast.For | ast.While,
    func_map: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
) -> list[ast.AST]:
    """Loop body plus bodies of module functions reachable from it (T3a inter-procedural)."""
    bodies: list[ast.AST] = [loop]
    queue = list(_callees_from_node(loop))
    visited: set[str] = set()
    while queue:
        name = queue.pop()
        if name in visited:
            continue
        visited.add(name)
        fn = func_map.get(name)
        if fn is None:
            continue
        bodies.append(fn)
        queue.extend(_callees_from_node(fn))
    return bodies


def lint_source(source: str, *, path: str = "<string>") -> list[LintFinding]:
    tree = ast.parse(source, filename=path)
    func_map = _build_function_map(tree)
    findings: list[LintFinding] = []
    for loop in _collect_loops(tree):
        for body in _reachable_from_loop(loop, func_map):
            for lineno, detail in _subprocess_in_node(body):
                findings.append(LintFinding(path, lineno, "fork-in-periodic-loop", detail))
    return findings


def _lint_source_loop_body_only(source: str, *, path: str = "<string>") -> list[LintFinding]:
    """Legacy: loop literal bodies only (for regression tests)."""
    tree = ast.parse(source, filename=path)
    findings: list[LintFinding] = []
    for loop in _collect_loops(tree):
        for lineno, detail in _subprocess_in_node(loop):
            findings.append(LintFinding(path, lineno, "fork-in-periodic-loop", detail))
    return findings


def lint_file(path: Path) -> list[LintFinding]:
    rel = str(path.relative_to(REPO_ROOT)) if path.is_absolute() else str(path)
    return lint_source(path.read_text(encoding="utf-8"), path=rel)


def lint_modules(modules: Iterable[str] | None = None) -> list[LintFinding]:
    """Lint every module with a periodic loop. Pass `modules` to narrow it."""
    out: list[LintFinding] = []
    for rel in (discover_periodic_modules() if modules is None else modules):
        out.extend(lint_file(REPO_ROOT / rel))
    return out
