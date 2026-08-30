"""Every call the bench makes must match the signature it is calling.

WHY THIS EXISTS. On 2026-08-30 a deploy reported PASS, 1739 tests passed, and
the appliance crashlooped on arrival:

    TypeError: SlotSurface.repaint_scenes() got an unexpected keyword argument 'force'

The refactor that removed every `force=` flag (one cache, one owner, so no
caller needs to invalidate by hand) missed one call site in the bench. Nothing
caught it because **no test executes `run_bench`** — it needs `rtmidi` and a
physical APC. So the single most-executed function on the appliance is the one
function the suite has never run, and a plain typo in it is invisible until the
service is already failing to start.

This is the project's signature bug shape aimed at the suite itself: a green
test run that reads identically whether the bench can start or not.

WHAT THIS DOES. It cannot run the event loop, so it does the next honest thing:
it parses the bench, resolves each local variable that is assigned from a class
this repo owns, and binds every method call on that variable against the real
`inspect.signature`. A wrong keyword, a missing required argument or too many
positionals fails here instead of on the Pi.

WHAT THIS DOES NOT DO — say it plainly, because an instrument that overstates
its reach is worse than none. It proves nothing about behaviour, ordering,
threading or timing. It only proves the calls are *callable*. `run_bench`
remains unexecuted by any test.
"""

from __future__ import annotations

import ast
import inspect
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SL = REPO / "scripts" / "sooperlooper"
sys.path.insert(0, str(SL))

BENCH = REPO / "scripts" / "sooperlooper-apc-bench.py"

#: Classes the bench instantiates into a local and then calls methods on.
#: Keyed by the name the bench uses, since that is what appears at the call site.
import apc_grid  # noqa: E402
import led_compositor  # noqa: E402
import slot_runtime  # noqa: E402
import slot_surface  # noqa: E402
import sl_grid_state  # noqa: E402

OWNED_CLASSES = {
    "SlotSurface": slot_surface.SlotSurface,
    "SlotRuntime": slot_runtime.SlotRuntime,
    "LedCompositor": led_compositor.LedCompositor,
    "GridState": sl_grid_state.GridState,
    "GridView": apc_grid.GridView,
}


def _locals_by_class(tree: ast.AST) -> dict[str, type]:
    """Map local variable name -> class, for `name = SomeOwnedClass(...)`."""
    found: dict[str, type] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        cls_name = func.id if isinstance(func, ast.Name) else None
        if cls_name not in OWNED_CLASSES:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found[target.id] = OWNED_CLASSES[cls_name]
    return found


def _method_calls(tree: ast.AST, known: dict[str, type]):
    """Yield (var, method, ast.Call) for every `var.method(...)` we can resolve."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
            continue
        if func.value.id in known:
            yield func.value.id, func.attr, node


class BenchCallSitesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tree = ast.parse(BENCH.read_text(), filename=str(BENCH))
        self.known = _locals_by_class(self.tree)

    def test_the_parse_actually_found_something(self) -> None:
        """A resolver that silently resolves nothing would pass forever.

        This is the positive control. Without it, renaming a class or changing
        the bench's assignment style turns this whole file into a test that
        checks zero call sites and still reports green — which is the exact
        failure mode the file was written to prevent.
        """
        self.assertGreaterEqual(
            len(self.known), 3,
            f"resolved only {self.known!r}; the AST resolver has gone blind",
        )
        calls = list(_method_calls(self.tree, self.known))
        self.assertGreaterEqual(
            len(calls), 5, f"resolved only {len(calls)} call sites in the bench",
        )

    def test_every_resolved_call_binds_against_the_real_signature(self) -> None:
        for var, method, call in _method_calls(self.tree, self.known):
            cls = self.known[var]
            with self.subTest(call=f"{var}.{method}", line=call.lineno):
                target = getattr(cls, method, None)
                self.assertIsNotNone(
                    target,
                    f"{BENCH.name}:{call.lineno} calls {cls.__name__}.{method}(), "
                    f"which does not exist",
                )
                if not callable(target):
                    continue
                sig = inspect.signature(target)
                # `self` is bound at the call site by the attribute access.
                args = ["<self>"] + ["<pos>" for a in call.args
                                     if not isinstance(a, ast.Starred)]
                kwargs = {kw.arg: "<kw>" for kw in call.keywords if kw.arg}
                if any(isinstance(a, ast.Starred) for a in call.args) or \
                   any(kw.arg is None for kw in call.keywords):
                    continue  # *args/**kwargs — cannot bind statically, skip honestly
                try:
                    sig.bind(*args, **kwargs)
                except TypeError as exc:
                    self.fail(
                        f"{BENCH.name}:{call.lineno} — "
                        f"{cls.__name__}.{method}{sig} rejects this call: {exc}"
                    )


if __name__ == "__main__":
    unittest.main()
