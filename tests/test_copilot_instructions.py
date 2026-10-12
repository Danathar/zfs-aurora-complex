"""
Script: tests/test_copilot_instructions.py
What: Joins `.github/copilot-instructions.md` to the things it restates.
Doing: Resolves its safety-critical file list, its check command and its claims about the tree.
Why: It is a hand-maintained copy of rules enforced elsewhere, loaded automatically by Copilot, and until now no test opened it.
Goal: Make a moved safety-critical file, a changed CI command or a new dependency fail here instead of silently making the instructions wrong.

`.github/copilot-instructions.md` says "standard library only", "the version is
pinned in test.yml" and "`.github/scripts/README.md` maps step to command to
module". A suggestion built on one of those sentences after it stopped being true
is exactly the plausible-but-wrong class the agent docs are written against.

The same file also says per-module coverage floors live in
`.coverage-thresholds.json`; that file arrives with the contributor-prerequisites
change, so the join to it is not made here.

No PyYAML and no third-party parser: the one workflow this file reads is
`.github/workflows/test.yml` -- the file CI's install line lives in -- and a
test of what CI installs must not skip the day that line changes.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COPILOT = REPO_ROOT / ".github" / "copilot-instructions.md"
AGENTS = REPO_ROOT / "AGENTS.md"
TEST_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test.yml"
BUILD_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build.yml"
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"


def workflow_paths() -> list[Path]:
    """Every workflow GitHub runs: each `.yml` and `.yaml` file in WORKFLOW_DIR.

    A glob of one extension would exempt every workflow spelled the other way.
    """
    return sorted([*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")])


SCRIPTS_README = REPO_ROOT / ".github" / "scripts" / "README.md"

BACKTICKED_RE = re.compile(r"`([^`]+)`")
WHITESPACE_RE = re.compile(r"\s+")

# Section 0 rule 2's opening line, in the numbered rendering both AGENTS.md and
# CLAUDE.md use.
RULE_2_MARKER = "Treat the build, promotion, and signing path as safety-critical"
NEXT_ITEM_RE = re.compile(r"^\s*(?:\d+\.|[-*])\s+\*\*")

# Trees the "Python 3, standard library only" claim covers: everything that
# runs as a module from a workflow step or inside the image build.
SHIPPED_TREES = ("ci_tools", "shared", "files/scripts", "containerfiles/zfs-akmods")

# Imports that are neither stdlib nor a module of this repository.
LOCAL_ROOTS = {"ci_tools", "shared", "tests", "check_coverage", "run_tests"}

# `ci_tools.cli` commands `.github/scripts/README.md` does not name today. Frozen rather than
# ignored: a new command that goes undocumented grows this set and fails, and documenting one
# of these shrinks it and fails until it is dropped from here, which is the only way a
# "documented" list stays honest without this test editing docs.
UNDOCUMENTED_COMMANDS = {"write-akmods-badge", "write-last-good-build-badge"}


def collapse(text: str) -> str:
    """Whitespace-insensitive form, so a wrapped sentence compares as one line."""

    return WHITESPACE_RE.sub(" ", text).strip()


def tracked_files() -> list[str]:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.split()


def rule_2_paths() -> list[str]:
    """The backticked paths AGENTS.md section 0 rule 2 names."""

    lines = AGENTS.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if RULE_2_MARKER in line)
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if NEXT_ITEM_RE.match(line):
            break
        block.append(line)
    found = BACKTICKED_RE.findall("\n".join(block))
    return [token for token in found if "/" in token or token.endswith((".py", ".yml"))]


def path_list(text: str) -> list[str]:
    """Path-looking tokens from a plain prose list, backticked or not.

    The template writes its file list as running prose inside an HTML comment
    (`build.yml, .github/actions/publish-native-image, ... or
    files/scripts/configure_signing_policy.py.`), so the backtick-based reader
    the agent docs allow would return nothing at all here -- and an empty list
    compares equal to nothing, which is how this kind of check passes while
    reading the wrong thing.
    """

    stripped = text.replace("`", "")
    tokens = re.findall(r"[A-Za-z0-9_./-]+", stripped)
    out = []
    for token in tokens:
        token = token.rstrip(".") if token.endswith((".", ",")) else token
        if token.endswith((".py", ".yml")) or "/" in token:
            out.append(token)
    return out


def module_imports(path: Path) -> list[tuple[str, int, bool]]:
    """(top-level module, line, guarded) for every import in one file.

    `guarded` means the import sits inside a `try:` whose handlers name
    ImportError -- the optional-PyYAML pattern the workflow tests use.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"))
    guarded_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if not any(
            isinstance(handler.type, ast.Name) and handler.type.id == "ImportError"
            for handler in node.handlers
        ):
            continue
        for child in ast.walk(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                guarded_lines.add(child.lineno)
    out: list[tuple[str, int, bool]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name.split(".")[0], node.lineno, node.lineno in guarded_lines))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.append((node.module.split(".")[0], node.lineno, node.lineno in guarded_lines))
    return out


def third_party(path: Path) -> list[tuple[str, int, bool]]:
    return [
        entry
        for entry in module_imports(path)
        if entry[0] not in sys.stdlib_module_names and entry[0] not in LOCAL_ROOTS
    ]


class SafetyCriticalListTests(unittest.TestCase):
    """The file list is maintained by hand in the instructions; join it to rule 2."""

    def setUp(self) -> None:
        self.rule_2 = rule_2_paths()
        self.tracked = set(tracked_files())

    def test_rule_2_still_parses(self) -> None:
        self.assertEqual(7, len(self.rule_2), f"rule 2 parsed as {self.rule_2}")

    def test_copilot_instructions_name_exactly_the_rule_2_files(self) -> None:
        text = COPILOT.read_text(encoding="utf-8")
        sentence = text.split("The safety-critical files are", 1)[1]
        sentence = sentence.split("A change to any of them", 1)[0]
        self.assertEqual(sorted(self.rule_2), sorted(path_list(sentence)))

    def test_every_named_file_exists(self) -> None:
        # A list that names a moved file is not a list: the reviewer prompt
        # points at nothing and the real file is unguarded.
        for path in self.rule_2:
            with self.subTest(path=path):
                self.assertTrue(
                    path in self.tracked
                    or any(name.startswith(path.rstrip("/") + "/") for name in self.tracked)
                    or any(name.endswith("/" + path) for name in self.tracked),
                    f"{path} is named as safety-critical but is not in the tracked tree",
                )


class CheckCommandTests(unittest.TestCase):
    """The command the instructions name is the command CI runs."""

    def setUp(self) -> None:
        self.workflow = TEST_WORKFLOW.read_text(encoding="utf-8")

    def test_copilot_instructions_name_the_ruff_invocation_ci_runs(self) -> None:
        ruff = "ruff check ci_tools/ shared/ tests/ files/ containerfiles/"
        self.assertRegex(self.workflow, rf"(?m)^\s+run: {re.escape(ruff)}\s*$")
        self.assertIn(ruff, collapse(COPILOT.read_text(encoding="utf-8")))

    def test_the_ruff_pin_really_lives_where_copilot_instructions_say(self) -> None:
        # The claim is specifically "with the version pinned in
        # .github/workflows/test.yml". A pin that moved elsewhere leaves the
        # sentence pointing at a file with no version in it.
        claim = collapse(COPILOT.read_text(encoding="utf-8"))
        self.assertIn("the version pinned in `.github/workflows/test.yml`", claim)
        self.assertRegex(self.workflow, r'"ruff==\d+\.\d+\.\d+"')


class CopilotClaimTests(unittest.TestCase):
    """The claims copilot-instructions.md makes about the tree, checked."""

    def setUp(self) -> None:
        self.text = collapse(COPILOT.read_text(encoding="utf-8"))

    def test_the_shipped_trees_import_only_the_standard_library(self) -> None:
        self.assertIn("Python 3, standard library only", self.text)
        offenders = []
        for tree in SHIPPED_TREES:
            root = REPO_ROOT / tree
            self.assertTrue(root.is_dir(), f"{tree} is named by the instructions but is missing")
            for path in sorted(root.rglob("*.py")):
                for module, line, _ in third_party(path):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{line} imports {module}")
        self.assertEqual(
            [],
            offenders,
            "copilot-instructions.md tells Copilot not to add a dependency, and "
            f"one is now imported: {offenders}",
        )

    def test_the_suite_still_runs_with_nothing_installed(self) -> None:
        # "`python3 -m unittest discover -s tests` works with nothing
        # installed" is only true while every third-party import under tests/
        # is optional. An unguarded `import yaml` or `import pytest` turns the
        # documented no-install command into a collection error, and CI would
        # not notice: its job installs pytest.
        self.assertIn("python3 -m unittest discover -s tests", self.text)
        unguarded = []
        for path in sorted((REPO_ROOT / "tests").rglob("*.py")):
            for module, line, guarded in third_party(path):
                if not guarded:
                    unguarded.append(f"{path.relative_to(REPO_ROOT)}:{line} imports {module}")
        self.assertEqual([], unguarded, f"unguarded third-party imports under tests/: {unguarded}")

    def test_the_import_reader_actually_reads_imports(self) -> None:
        # Guard the guard: the assertions above pass trivially if the reader
        # returns nothing at all.
        names = {module for module, _, _ in module_imports(Path(__file__))}
        self.assertLessEqual({"re", "unittest", "ast"}, names)

    def test_the_step_to_command_map_the_instructions_point_at_is_complete(self) -> None:
        self.assertIn("`.github/scripts/README.md` maps step to command to module", self.text)
        from ci_tools.cli import command_map

        documented = SCRIPTS_README.read_text(encoding="utf-8")
        absent = {name for name in command_map() if name not in documented}
        self.assertEqual(
            UNDOCUMENTED_COMMANDS,
            absent,
            "the set of ci_tools.cli commands missing from .github/scripts/README.md "
            "changed; the instructions send Copilot there for the step-to-module map",
        )


class BuildTriggerTests(unittest.TestCase):
    """Editing the instructions must not publish an image."""

    def setUp(self) -> None:
        text = BUILD_WORKFLOW.read_text(encoding="utf-8")
        block = text.split("paths-ignore:", 1)[1].split("\n  workflow_dispatch:", 1)[0]
        self.ignored = [
            line.strip().lstrip("- ").strip('"').strip("'")
            for line in block.splitlines()
            if line.strip().startswith("- ")
        ]

    def test_the_paths_ignore_list_parses(self) -> None:
        self.assertIn("docs/**", self.ignored)
        self.assertIn("**/*.md", self.ignored)

    def test_no_workflow_step_reads_the_instructions(self) -> None:
        # Being ignored by build.yml's paths-ignore (the markdown glob) is only
        # safe while nothing that builds reads the file. A step that started
        # reading it would make an ignored edit change what ships.
        for workflow in workflow_paths():
            with self.subTest(workflow=workflow.name):
                self.assertNotIn(
                    "copilot-instructions.md", workflow.read_text(encoding="utf-8")
                )


if __name__ == "__main__":
    unittest.main()
