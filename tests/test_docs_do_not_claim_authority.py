"""No document in this repo may read as a description of current behaviour.

Mitch, 2026-09-07, after being handed a prose contract, a data mirror and a
test suite:

    "So now we have three documents that could still come out of sync. One of
    them is a long description. One of them is tests, and one of them is the
    code... what we need is the code to be the documentation."

The mirror and the contract were deleted. This file holds the rest of the repo
to the same rule, because the 23 spec files and the decision log were what
confused the last several rounds of work: an audit on 2026-09-07 found eleven
places where two documents state opposite things about the same gesture, and
four statements presented as current fact that the code had contradicted for
weeks.

Correcting those by hand fixes the ones we found. This test is for the ones we
did not, and for the next file somebody adds.
"""

from tests import conftest  # noqa: F401

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SPECS = REPO / "Documents" / "specs"
AUDIT = REPO / "Documents" / "looper-audit-2026-09-07.md"

STAMP = "HISTORY — this file does not describe the instrument."


class SpecsAreStampedTests(unittest.TestCase):
    """A spec is a statement of intent. It must not be read as a report."""

    def test_there_are_specs_to_check(self) -> None:
        """Guard against this whole file passing because the glob broke."""
        self.assertGreater(len(list(SPECS.glob("*.md"))), 15)

    def test_every_spec_says_it_is_history(self) -> None:
        missing = [
            p.name for p in sorted(SPECS.glob("*.md"))
            if STAMP not in p.read_text()
        ]
        self.assertEqual(missing, [], (
            "these files read as current behaviour:\n  "
            + "\n  ".join(missing)
            + "\n\nAdd the HISTORY stamp used by the others, or delete the "
              "file. A spec with no stamp is what the last audit had to "
              "untangle."
        ))

    def test_the_stamp_points_somewhere_a_reader_can_go(self) -> None:
        """'Read the code' is useless without saying which part."""
        for p in sorted(SPECS.glob("*.md")):
            with self.subTest(spec=p.name):
                head = p.read_text()[:1200]
                self.assertIn("looper-audit-2026-09-07.md", head)


class TheLogSaysItIsALogTests(unittest.TestCase):
    def test_decisions_warns_it_is_not_current_behaviour(self) -> None:
        s = (REPO / "Documents" / "DECISIONS.md").read_text()
        self.assertIn("This is a log, not a description of the instrument.", s)
        self.assertIn("absence of a correction is not", s,
                      "a reader must not take an unmarked row as current")

    def test_direction_does_not_promise_currency(self) -> None:
        s = (REPO / "Documents" / "DIRECTION.md").read_text()
        self.assertNotIn("read before looper work", s.split("\n")[0])
        self.assertIn("not a description of the instrument", s)


class TheAuditDescribesNothingTests(unittest.TestCase):
    """The one document this work added, held to its own rule."""

    def test_it_says_so_in_its_first_lines(self) -> None:
        head = AUDIT.read_text()[:600]
        self.assertIn("describes no behaviour", head)
        self.assertIn("will not be updated", head)

    def test_it_maps_the_modules_that_do_describe_behaviour(self) -> None:
        s = AUDIT.read_text()
        for module in ("looper_timing.py", "binding_table.py",
                       "sl_grid_state.py", "tail_phase.py"):
            with self.subTest(module=module):
                self.assertIn(module, s)


class AgentsPointsAtCodeFirstTests(unittest.TestCase):
    """What an agent reads before touching the looper.

    The old line sent it to DIRECTION.md and DECISIONS.md — one weeks stale,
    the other holding a false statement about `eighth_per_cycle` that had
    stood since 2026-08-30.
    """

    def test_it_names_the_two_authorities(self) -> None:
        s = (REPO / "AGENTS.md").read_text()
        self.assertIn("looper_timing.py", s)
        self.assertIn("binding_table.py", s)

    def test_it_says_the_documents_are_history(self) -> None:
        s = (REPO / "AGENTS.md").read_text()
        self.assertIn("the code is the documentation", s.lower())


if __name__ == "__main__":
    unittest.main()


class TheLoopCountHasOneHomeTests(unittest.TestCase):
    """The number a reader acts on has to match the number the code uses.

    `sl_limits.MAX_USABLE_LOOPS` has said 15 since 2026-08-22. Four entry
    documents said 16 for the sixteen days after that, and the 2026-08-30
    ownership review recorded the disagreement in a table — where nothing could
    fail on it, so it survived another eight days. A stamp saying "this file
    may be stale" does not help: the count is what gets copied into a script.
    """

    #: Files a new dev or agent reads before touching the looper.
    ENTRY_DOCS = (
        "AGENTS.md",
        "README.md",
        "docs/CODE-MAP.md",
        "scripts/sooperlooper/README.md",
        "config/mpe-sooperlooper.service",
    )

    #: "16 loops", "16-track", "sixteen tracks", "all 16", "8 visible of 16".
    CLAIM = re.compile(
        r"\b(?:16|sixteen)[\s-]*(?:loops?|tracks?|fixture)"
        r"|(?:all|of|onto)\s+(?:16|sixteen)\b",
        re.IGNORECASE,
    )
    #: Real filenames, not claims about how many loops there are.
    NOT_A_CLAIM = re.compile(r"smoke-16-loops\.sh|diagnose-16loop")

    def test_no_entry_document_states_a_loop_count_the_code_denies(self) -> None:
        from sl_limits import MAX_USABLE_LOOPS

        self.assertEqual(MAX_USABLE_LOOPS, 15, "update this test with the code")
        offences = []
        for name in self.ENTRY_DOCS:
            path = REPO / name
            if not path.exists():
                continue
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if self.NOT_A_CLAIM.search(line):
                    continue
                if self.CLAIM.search(line):
                    offences.append(f"{name}:{n}: {line.strip()[:90]}")
        self.assertEqual(offences, [], "\n".join(
            [f"documents claiming a loop count MAX_USABLE_LOOPS "
             f"({MAX_USABLE_LOOPS}) denies:"] + offences))
