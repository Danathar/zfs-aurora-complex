"""
Script: tests/test_sibling_delta_doc.py
What: Joins docs/sibling-delta.md's module table to the `ci_tools/*.py` modules in the tree.
Doing: Reads the table's rows, set-compares their module names to the files in `ci_tools/`, and checks each status is one of the forms the page defines.
Why: Most of `ci_tools/` is shared with the sister repository, and fixes made to one copy have repeatedly not reached the other. The page is the only record of which differences are meant, and a record that misses a module records nothing about it.
Goal: Make a new module with no row, a row for a removed module, or a `port pending` row that names no issue fail here.

Whether a row is *true* -- whether the two copies really match, or a
difference really is deliberate -- is a review question; this file does not
fetch the sister repository. It checks only that every module has been given
an answer in one of the allowed shapes.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER = REPO_ROOT / "docs" / "sibling-delta.md"
CI_TOOLS = REPO_ROOT / "ci_tools"

ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([^|]*?)\s*\|")
# The issue a `port pending` row names, in this repository (`#N`). A port the
# sister repository owes is tracked from that issue, not named here.
ISSUE_RE = re.compile(r"^#\d+$")
SISTER_COMMIT_RE = re.compile(r"^Last sister commit reviewed: `([0-9a-f]{40})`$", re.MULTILINE)
# The prefixes a status may take; every form but `same` must give a reason.
REASON_PREFIXES = ("differs on purpose: ", "only here: ")
PORT_PREFIX = "port pending: "


def module_rows() -> dict[str, str]:
    """{module: status} for each row of the table under `## Modules`."""

    section = LEDGER.read_text(encoding="utf-8").split("\n## Modules\n", 1)[1].split("\n## ", 1)[0]
    rows: dict[str, str] = {}
    for line in section.splitlines():
        match = ROW_RE.match(line)
        if match:
            module, status = match.groups()
            assert module not in rows, f"{module} has two rows"
            rows[module] = status
    return rows


def status_problem(status: str) -> str | None:
    """None when `status` is an allowed form, else what is wrong with it."""

    if status == "same":
        return None
    for prefix in REASON_PREFIXES:
        if status.startswith(prefix):
            return None if status[len(prefix) :].strip() else "gives no reason"
    if status.startswith(PORT_PREFIX):
        issues = [part.strip() for part in status[len(PORT_PREFIX) :].split(",")]
        if not issues or not all(ISSUE_RE.match(issue) for issue in issues):
            return "does not name the tracking issue as #N"
        return None
    return "is not `same`, `differs on purpose: ...`, `port pending: ...` or `only here: ...`"


class SiblingDeltaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = module_rows()

    def test_the_table_parses_into_something_plausible(self) -> None:
        for expected in ("__init__.py", "common.py", "promote_stable.py"):
            self.assertIn(expected, self.rows)

    def test_every_module_has_a_row_and_every_row_a_module(self) -> None:
        modules = {path.name for path in CI_TOOLS.glob("*.py")}
        self.assertEqual(
            sorted(modules - set(self.rows)),
            [],
            "ci_tools modules docs/sibling-delta.md has no row for",
        )
        self.assertEqual(
            sorted(set(self.rows) - modules),
            [],
            "docs/sibling-delta.md rows for modules ci_tools no longer has",
        )

    def test_every_status_is_an_allowed_form(self) -> None:
        for module, status in sorted(self.rows.items()):
            with self.subTest(module=module):
                problem = status_problem(status)
                self.assertIsNone(problem, f"{module}: status {status!r} {problem}")

    def test_the_last_sister_commit_reviewed_is_recorded(self) -> None:
        self.assertRegex(LEDGER.read_text(encoding="utf-8"), SISTER_COMMIT_RE)


class StatusFormTests(unittest.TestCase):
    """The status reader accepts the documented forms and nothing else."""

    def test_allowed_forms(self) -> None:
        for status in (
            "same",
            "differs on purpose: the User-Agent names the repository",
            "only here: the sister has no copy",
            "port pending: #12",
            "port pending: #7, #12",
        ):
            with self.subTest(status=status):
                self.assertIsNone(status_problem(status))

    def test_rejected_forms(self) -> None:
        for status in (
            "",
            "Same",
            "differs",
            "differs on purpose: ",
            "only here: ",
            "port pending: ",
            "port pending: soon",
            "port pending: #12, later",
            "port pending: other/repo#3",
            "port pending: Danathar/zfs-kinoite-complex#7",
        ):
            with self.subTest(status=status):
                self.assertIsNotNone(status_problem(status))


if __name__ == "__main__":
    unittest.main()
