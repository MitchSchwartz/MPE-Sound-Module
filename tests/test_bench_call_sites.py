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
it parses the bench and binds every call it can resolve against the real
`inspect.signature` — both `var.method(...)` and the constructor call
`SomeClass(...)` itself. A wrong keyword, a missing required argument or too
many positionals fails here instead of on the Pi.

The class set is DERIVED FROM THE BENCH'S OWN IMPORTS, never hand-listed. The
first version of this file carried a hand-maintained tuple of five classes and
a completeness critic broke it in one move: inserting `stale_lamp_note=None`
into the `TransportButtonLeds(...)` call — the exact kwarg this branch removed,
the exact failure mode as `force=` — left the suite byte-identically green,
because that class was not on the list and because constructor arguments were
never bound at all. A guard with a hand-maintained list of what to guard decays
into a guard that watches nothing, which is the same shape as the bug.

WHAT THIS DOES NOT DO — say it plainly, because an instrument that overstates
its reach is worse than none. It proves nothing about behaviour, ordering,
threading or timing. It only proves the calls are *callable*. `run_bench`
remains unexecuted by any test.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "sooperlooper"))

BENCH = REPO / "scripts" / "sooperlooper-apc-bench.py"
TREE = ast.parse(BENCH.read_text(), filename=str(BENCH))


def _imported_classes(tree: ast.AST) -> dict[str, type]:
    """Every class the bench imports by name, from its own import statements.

    Derived, not declared. Add an import to the bench and it is covered here
    with no edit to this file.
    """
    found: dict[str, type] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        try:
            mod = importlib.import_module(node.module)
        except Exception:  # a module needing rtmidi/OSC is simply not covered
            continue
        for alias in node.names:
            obj = getattr(mod, alias.name, None)
            if inspect.isclass(obj):
                found[alias.asname or alias.name] = obj
    return found


def _locals_by_class(tree: ast.AST, classes: dict[str, type]):
    """Map local variable name -> (class, lineno), for `name = SomeClass(...)`.

    This resolver has no flow sensitivity, so it trusts a name only when the
    module leaves no room for doubt: exactly ONE assignment from a class we
    resolved, and every OTHER assignment to that name a non-call (a sentinel).

    Both halves are load-bearing, and each is here because of a real line in
    the bench:

      * `slot_surface` is `None` at :392 and `SlotSurface(...)` at :411. A rule
        that rejected every twice-assigned name would drop it — and dropping it
        is exactly how the `repaint_scenes(force=True)` crashloop would slip
        through this guard a second time. Sentinels must not cost coverage.
      * `midi_out` is `rtmidi.MidiOut()` first and `PacedMidiOut(...)` after. A
        rule that accepted any name with one resolvable class would report the
        wrapper's signature for calls made against the raw port and invent a
        failure at :164. A guard that cries wolf gets muted, and a muted guard
        is worth nothing.
    """
    class_assigns: dict[str, list] = {}
    other_call_assign: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        func = value.func if isinstance(value, ast.Call) else None
        cls_name = func.id if isinstance(func, ast.Name) else None
        resolved = classes.get(cls_name) if cls_name else None
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if resolved is not None:
                class_assigns.setdefault(target.id, []).append((resolved, node.lineno))
            elif isinstance(value, ast.Call):
                other_call_assign.add(target.id)

    return {
        name: entries[0]
        for name, entries in class_assigns.items()
        if len(entries) == 1 and name not in other_call_assign
    }


def _bindable(call: ast.Call) -> bool:
    """`*args`/`**kwargs` at a call site cannot be bound statically."""
    return not any(isinstance(a, ast.Starred) for a in call.args) and \
        not any(kw.arg is None for kw in call.keywords)


def _resolved_calls(tree, classes, locals_):
    """Yield (label, callable_or_None, ast.Call) for everything resolvable.

    Two shapes: the constructor `SomeClass(...)`, and the method call
    `var.method(...)` on a local assigned from one.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _bindable(node):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in classes:
            cls = classes[func.id]
            yield f"{func.id}()", cls.__init__, node, cls, "__init__"
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
                and func.value.id in locals_:
            cls, assigned_at = locals_[func.value.id]
            if node.lineno <= assigned_at:
                continue  # the name does not hold this class yet
            yield (f"{func.value.id}.{func.attr}", getattr(cls, func.attr, None),
                   node, cls, func.attr)


class BenchCallSitesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.classes = _imported_classes(TREE)
        self.locals_ = _locals_by_class(TREE, self.classes)
        self.calls = list(_resolved_calls(TREE, self.classes, self.locals_))

    def test_the_resolver_actually_resolved_something(self) -> None:
        """The positive control.

        A resolver that silently resolves nothing passes forever. Without this,
        renaming a class or changing the bench's assignment style turns the
        whole file into a test that checks zero call sites and still reports
        green — the exact failure this file exists to prevent, reproduced
        inside the guard.
        """
        self.assertGreaterEqual(
            len(self.classes), 10,
            f"only {len(self.classes)} classes resolved from the bench's imports",
        )
        ctors = [c for c in self.calls if c[4] == "__init__"]
        methods = [c for c in self.calls if c[4] != "__init__"]
        self.assertGreaterEqual(len(ctors), 8, "constructor call sites went unseen")
        self.assertGreaterEqual(len(methods), 5, "method call sites went unseen")

    def test_every_resolved_call_binds_against_the_real_signature(self) -> None:
        for label, target, call, cls, attr in self.calls:
            with self.subTest(call=label, line=call.lineno):
                self.assertIsNotNone(
                    target,
                    f"{BENCH.name}:{call.lineno} calls {cls.__name__}.{attr}(), "
                    f"which does not exist",
                )
                if not callable(target):
                    continue
                try:
                    sig = inspect.signature(target)
                except (ValueError, TypeError):
                    continue
                # `self` is bound by the attribute access / by instantiation.
                args = ["<self>"] + ["<pos>"] * len(call.args)
                kwargs = {kw.arg: "<kw>" for kw in call.keywords}
                try:
                    sig.bind(*args, **kwargs)
                except TypeError as exc:
                    self.fail(
                        f"{BENCH.name}:{call.lineno} — "
                        f"{cls.__name__}.{attr}{sig} rejects this call: {exc}"
                    )


if __name__ == "__main__":
    unittest.main()
