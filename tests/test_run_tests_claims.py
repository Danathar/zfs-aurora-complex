"""
Script: tests/test_run_tests_claims.py
What: Joins the prose copy of what tests/run_tests.py refuses to the runner's own lists, and the runner's argument-file refusal to the prefix the installed pytest actually expands.
Doing: Reads `_note_test_runners` from `.claude/settings.json`, pulls the backticked options, suffixes and prefix out of it, and compares them with `REFUSED_OPTIONS`, the loadable suffixes in `untracked_loadable_files`, and `ARGUMENT_FILE_PREFIX`.
Why: `tests/test_run_tests.py` holds the refusals themselves and never opens the note. The settings note is what an agent reads next to the allow row, and a note that falls behind the runner promises a boundary that is not there.
Goal: A refusal added to or dropped from tests/run_tests.py fails here until the note says so, and a pytest that grows a second argument-file prefix fails here before it is a way past the runner.

The runner's refusals have landed one fix at a time (`-o`, the write options, the
import-by-name options and every untracked loadable, the `@` argument file), and each
fix has to update the note. So the claim is checked in both directions:

  * every alias group in `REFUSED_OPTIONS` is named by at least one spelling
    in the settings note, which lists the options exhaustively;
  * every option the note names is one the runner refuses, so a note that
    promises a refusal the runner dropped is red too;
  * the note names `ARGUMENT_FILE_PREFIX` and each suffix the runner refuses
    untracked, read out of `untracked_loadable_files`' pathspecs by AST.

The alias groups are written out here and held against the installed pytest's
option table in a separate test, so the note checks run without pytest while a
renamed alias still fails wherever pytest is installed.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import run_tests

REPO_ROOT = Path(__file__).resolve().parents[1]
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
RUNNER = REPO_ROOT / "tests" / "run_tests.py"

# Spellings pytest accepts for one option. Every other refused option is its
# own group. Held against pytest's table by
# `test_the_alias_groups_match_pytests_option_table`.
ALIAS_GROUPS = (
    frozenset({"-c", "--config-file"}),
    frozenset({"-o", "--override-ini"}),
    frozenset({"-W", "--pythonwarnings"}),
    frozenset({"--junitxml", "--junit-xml"}),
)

# `--report-log`, `--cov-report` and `--cov-config` belong to plugins, which
# `get_config()` does not load, so they are absent from the table read below;
# the runner refuses them whether or not the plugin is installed, see its
# comment on `REFUSED_WRITE_OPTIONS`.
PLUGIN_OPTIONS = frozenset({"--report-log", "--cov-report", "--cov-config"})

BACKTICKED = re.compile(r"`([^`]+)`")


def option_groups(options: tuple[str, ...]) -> list[frozenset[str]]:
    groups: list[frozenset[str]] = []
    for option in options:
        group = next((g for g in ALIAS_GROUPS if option in g), frozenset({option}))
        if group not in groups:
            groups.append(group)
    return groups


def backticked(text: str) -> list[str]:
    return BACKTICKED.findall(text)


def named_options(text: str) -> set[str]:
    """Each backticked word that is an option, with any `=value` cut off.

    A token such as `--junitxml=cosign.pub` names `--junitxml`. A token that
    does not start with `-` (`python3 -m pytest`, `git --output=FILE`) names a
    command, not an option the runner is said to refuse.
    """
    return {token.split("=", 1)[0] for token in backticked(text) if token.startswith("-")}


def loadable_suffixes() -> set[str]:
    """The suffixes `untracked_loadable_files` asks git for, as `.py` etc."""
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "untracked_loadable_files"
    )
    suffixes = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            match = re.fullmatch(r":\(glob\)\*\*/\*(\.\w+)", node.value)
            if match:
                suffixes.add(match.group(1))
    return suffixes


def settings_note() -> str:
    return json.loads(SETTINGS.read_text(encoding="utf-8"))["_note_test_runners"]


class ExtractionTests(unittest.TestCase):
    """The helpers below read what the copies are compared against."""

    def test_the_suffixes_are_read_out_of_the_runner(self) -> None:
        # Three today. An empty set would make the suffix checks vacuous.
        self.assertEqual(loadable_suffixes(), {".py", ".pyc", ".so"})

    def test_every_alias_group_holds_refused_options_only(self) -> None:
        # A group naming an option the runner does not refuse would let a copy
        # satisfy the check by naming that option instead.
        for group in ALIAS_GROUPS:
            with self.subTest(group=sorted(group)):
                self.assertLessEqual(group, set(run_tests.REFUSED_OPTIONS))

    def test_the_groups_partition_the_refused_options(self) -> None:
        groups = option_groups(run_tests.REFUSED_OPTIONS)
        self.assertEqual(set().union(*groups), set(run_tests.REFUSED_OPTIONS))
        self.assertEqual(sum(len(g) for g in groups), len(set(run_tests.REFUSED_OPTIONS)))


class SettingsNoteTests(unittest.TestCase):
    """`_note_test_runners` is the copy an agent reads beside the allow row."""

    def setUp(self) -> None:
        self.note = settings_note()

    def test_the_note_names_every_refused_option(self) -> None:
        named = named_options(self.note)
        for group in option_groups(run_tests.REFUSED_OPTIONS):
            with self.subTest(option=sorted(group)):
                self.assertTrue(group & named, f"_note_test_runners names none of {sorted(group)}")

    def test_the_note_names_only_refused_options(self) -> None:
        self.assertLessEqual(named_options(self.note), set(run_tests.REFUSED_OPTIONS))

    def test_the_note_names_the_argument_file_prefix(self) -> None:
        self.assertIn(run_tests.ARGUMENT_FILE_PREFIX, backticked(self.note))

    def test_the_note_names_every_untracked_suffix_the_runner_refuses(self) -> None:
        tokens = set(backticked(self.note))
        for suffix in sorted(loadable_suffixes()):
            with self.subTest(suffix=suffix):
                self.assertIn(suffix, tokens)


def pytest_table() -> tuple[list[list[str]], str] | None:
    """The installed pytest's option spellings and argument-file prefixes."""
    program = (
        "import json\n"
        "from _pytest.config import get_config\n"
        "parser = get_config()._parser\n"
        "parser.parse_known_args([])\n"
        "names = [argument.names() for group in [*parser._groups, parser._anonymous]\n"
        "         for argument in group.options]\n"
        "print(json.dumps([names, parser.optparser.fromfile_prefix_chars or '']))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        return None
    names, prefixes = json.loads(completed.stdout)
    return names, prefixes


class InstalledPytestTests(unittest.TestCase):
    """Claims about pytest itself, held against the pytest that is installed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.table = pytest_table()

    def setUp(self) -> None:
        if self.table is None:
            self.skipTest("pytest is not installed; the claims are held against 9.1.1")

    def test_every_argument_file_prefix_pytest_expands_is_refused(self) -> None:
        # run_tests.py says pytest's parser is built with
        # `fromfile_prefix_chars="@"`. If pytest ever expands a second
        # character, an argument starting with it carries a file of options
        # past every check the runner makes. The untracked-file check is
        # stubbed so that it cannot be the refusal that returns 2 here.
        _, prefixes = self.table
        self.assertIn(run_tests.ARGUMENT_FILE_PREFIX, prefixes)
        for prefix in prefixes:
            with (
                self.subTest(prefix=prefix),
                patch.object(run_tests, "untracked_loadable_files", return_value=[]),
                patch.object(run_tests, "run_pytest") as run_pytest,
            ):
                self.assertEqual(run_tests.main([f"{prefix}arguments", "tests"]), 2)
                run_pytest.assert_not_called()

    def test_the_alias_groups_match_pytests_option_table(self) -> None:
        # A refused option pytest spells two ways must be in one group here,
        # or a copy could name the spelling the runner forgot.
        names, _ = self.table
        refused = set(run_tests.REFUSED_OPTIONS)
        for spellings in names:
            group = frozenset(spellings) & refused
            if group:
                with self.subTest(option=sorted(spellings)):
                    self.assertEqual(group, frozenset(spellings), "a spelling is not refused")
                    self.assertIn(group, option_groups(run_tests.REFUSED_OPTIONS))
        registered = {name for spellings in names for name in spellings}
        self.assertLessEqual(refused - registered, set(PLUGIN_OPTIONS))


if __name__ == "__main__":
    unittest.main()
