"""
Script: tests/test_sibling_delta_doc.py
What: Joins docs/sibling-delta.md's tables to the `ci_tools/*.py` modules, the `.github/actions/*/action.yml` build steps, the `tests/*.py` test tools and the image build files in the tree.
Doing: Reads each table's rows, set-compares their names to the files on disk, and checks each status is one of the forms the page defines.
Why: Most of `ci_tools/`, every build step in `.github/actions/`, the test tools in `tests/` and the code that runs inside the image build are shared with the sister repository, and fixes made to one copy have repeatedly not reached the other. The page is the only record of which differences are meant, and a record that misses a file records nothing about it.
Goal: Make a new module, build step, test tool or image build file with no row, a row for a removed one, or a `port pending` row that names no issue fail here.

Whether a difference really is deliberate is a review question. Whether a
`same` row is true is not: given a checkout of the sister repository in
SIBLING_DELTA_SISTER_CHECKOUT, SameRowsMatchSisterTests compares every `same`
row byte for byte. Without that variable it is skipped, so the suite still
runs offline; this file never fetches the sister itself.
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER = REPO_ROOT / "docs" / "sibling-delta.md"
CI_TOOLS = REPO_ROOT / "ci_tools"
ACTIONS = REPO_ROOT / ".github" / "actions"
TESTS = REPO_ROOT / "tests"
# The code that runs inside the image build. Data files under files/usr/ are
# left out on purpose; docs/sibling-delta.md says why.
IMAGE_BUILD_GLOBS = (
    "Containerfile",
    "build_files/*",
    "containerfiles/*/*.py",
    "files/scripts/*.py",
    "shared/*.py",
)

ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([^|]*?)\s*\|")
# The issue a `port pending` row names, in this repository (`#N`). A port the
# sister repository owes is tracked from that issue, not named here.
ISSUE_RE = re.compile(r"^#\d+$")
SISTER_COMMIT_RE = re.compile(r"^Last sister commit reviewed: `([0-9a-f]{40})`$", re.MULTILINE)
# The prefixes a status may take; every form but `same` must give a reason.
REASON_PREFIXES = ("differs on purpose: ", "only here: ")
PORT_PREFIX = "port pending: "


def section_rows(heading: str) -> dict[str, str]:
    """{name: status} for each row of the table under `## <heading>`."""

    section = (
        LEDGER.read_text(encoding="utf-8").split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]
    )
    rows: dict[str, str] = {}
    for line in section.splitlines():
        match = ROW_RE.match(line)
        if match:
            name, status = match.groups()
            assert name not in rows, f"{name} has two rows"
            rows[name] = status
    return rows


def module_rows() -> dict[str, str]:
    """{module: status} for each row of the table under `## Modules`."""

    return section_rows("Modules")


def build_step_rows() -> dict[str, str]:
    """{action directory: status} for each row of the table under `## Shared build steps`."""

    return section_rows("Shared build steps")


def tool_rows() -> dict[str, str]:
    """{file name: status} for each row of the table under `## Test tools`."""

    return section_rows("Test tools")


def image_build_rows() -> dict[str, str]:
    """{repository-relative path: status} for each row under `## Image build files`."""

    return section_rows("Image build files")


def image_build_files() -> set[str]:
    """Repository-relative paths of the image build files in the tree."""

    return {
        path.relative_to(REPO_ROOT).as_posix()
        for pattern in IMAGE_BUILD_GLOBS
        for path in REPO_ROOT.glob(pattern)
        if path.is_file()
    }


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


# How each table's row name maps to a repository-relative path.
SECTION_PATHS = {
    "Modules": "ci_tools/{}",
    "Shared build steps": ".github/actions/{}/action.yml",
    "Test tools": "tests/{}",
    "Image build files": "{}",
}
SISTER_ENV = "SIBLING_DELTA_SISTER_CHECKOUT"


def same_row_paths() -> list[str]:
    """Repository-relative paths of every row whose status is `same`."""

    return sorted(
        template.format(name)
        for heading, template in SECTION_PATHS.items()
        for name, status in section_rows(heading).items()
        if status == "same"
    )


def same_rows_that_differ(paths: list[str], here: Path, sister: Path) -> list[str]:
    """The paths among `paths` whose bytes differ between `here` and `sister`, or that the sister lacks."""

    return [
        path
        for path in paths
        if not (sister / path).is_file()
        or (here / path).read_bytes() != (sister / path).read_bytes()
    ]


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

    def test_every_build_step_has_a_row_and_every_row_a_build_step(self) -> None:
        steps = {path.parent.name for path in ACTIONS.glob("*/action.yml")}
        rows = build_step_rows()
        self.assertIn("publish-native-image", steps)
        self.assertEqual(
            sorted(steps - set(rows)),
            [],
            ".github/actions build steps docs/sibling-delta.md has no row for",
        )
        self.assertEqual(
            sorted(set(rows) - steps),
            [],
            "docs/sibling-delta.md rows for build steps .github/actions no longer has",
        )

    def test_every_build_step_status_is_an_allowed_form(self) -> None:
        for step, status in sorted(build_step_rows().items()):
            with self.subTest(step=step):
                problem = status_problem(status)
                self.assertIsNone(problem, f"{step}: status {status!r} {problem}")

    def test_every_test_tool_has_a_row_and_every_row_a_test_tool(self) -> None:
        tools = {path.name for path in TESTS.glob("*.py") if not path.name.startswith("test_")}
        rows = tool_rows()
        self.assertIn("run_tests.py", tools)
        self.assertEqual(
            sorted(tools - set(rows)),
            [],
            "tests/ test tools docs/sibling-delta.md has no row for",
        )
        self.assertEqual(
            sorted(set(rows) - tools),
            [],
            "docs/sibling-delta.md rows for test tools tests/ no longer has",
        )

    def test_every_test_tool_status_is_an_allowed_form(self) -> None:
        for tool, status in sorted(tool_rows().items()):
            with self.subTest(tool=tool):
                problem = status_problem(status)
                self.assertIsNone(problem, f"{tool}: status {status!r} {problem}")

    def test_every_image_build_file_has_a_row_and_every_row_a_file(self) -> None:
        files = image_build_files()
        rows = image_build_rows()
        self.assertIn("build_files/build-image.sh", files)
        self.assertEqual(
            sorted(files - set(rows)),
            [],
            "image build files docs/sibling-delta.md has no row for",
        )
        self.assertEqual(
            sorted(set(rows) - files),
            [],
            "docs/sibling-delta.md rows for image build files the tree no longer has",
        )

    def test_every_image_build_file_status_is_an_allowed_form(self) -> None:
        for path, status in sorted(image_build_rows().items()):
            with self.subTest(path=path):
                problem = status_problem(status)
                self.assertIsNone(problem, f"{path}: status {status!r} {problem}")

    def test_the_last_sister_commit_reviewed_is_recorded(self) -> None:
        self.assertRegex(LEDGER.read_text(encoding="utf-8"), SISTER_COMMIT_RE)


@unittest.skipUnless(os.environ.get(SISTER_ENV), f"{SISTER_ENV} names no sister checkout")
class SameRowsMatchSisterTests(unittest.TestCase):
    def test_every_same_row_is_byte_identical_in_the_sister(self) -> None:
        sister = Path(os.environ[SISTER_ENV])
        self.assertTrue((sister / "ci_tools").is_dir(), f"{sister} is not a sister checkout")
        self.assertEqual(
            same_rows_that_differ(same_row_paths(), REPO_ROOT, sister),
            [],
            "docs/sibling-delta.md says `same` but the sister's copy differs",
        )


class SameRowCompareTests(unittest.TestCase):
    def test_every_section_contributes_same_rows(self) -> None:
        paths = same_row_paths()
        for prefix in ("ci_tools/", ".github/actions/", "shared/"):
            with self.subTest(prefix=prefix):
                self.assertTrue(any(path.startswith(prefix) for path in paths))
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue((REPO_ROOT / path).is_file(), path)

    def test_a_changed_or_missing_sister_copy_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as here, tempfile.TemporaryDirectory() as sister:
            here_root, sister_root = Path(here), Path(sister)
            for name, mine, theirs in (
                ("same.py", b"a", b"a"),
                ("changed.py", b"a", b"b"),
                ("missing.py", b"a", None),
            ):
                (here_root / name).write_bytes(mine)
                if theirs is not None:
                    (sister_root / name).write_bytes(theirs)
            self.assertEqual(
                same_rows_that_differ(
                    ["changed.py", "missing.py", "same.py"], here_root, sister_root
                ),
                ["changed.py", "missing.py"],
            )


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
