"""
Script: tests/test_run_tests.py
What: Holds the refusals in tests/run_tests.py, the command `.claude/settings.json` allows unattended.
Doing: Builds a throwaway git repository, points the runner at it, and asserts what it refuses to import and what it hands to pytest.
Why: That script is the only thing standing between an allow-listed test command and arbitrary local code execution, and a refusal nobody exercises is a refusal that silently stops working.
Goal: Make a weakened check fail here, rather than the first time a module nobody read is imported by an approved command.

The claims being held are the ones `docs/SECURITY-AI.md` makes in its "run a
test suite unattended" row, in the order they matter:

  * a selection outside `tests/` is refused -- that is code from outside this
    repository, which no reviewer will ever see;
  * the options that relocate collection or load a plugin are refused, in both
    the `--opt value` and `--opt=value` spellings, because matching one and not
    the other would be a check that a space defeats;
  * the options that write to a path they name are refused too -- `--junitxml`,
    `--log-file`, `--debug`, `--basetemp` and the plugin reports -- because the
    command carrying them runs unattended and nothing else gates what it
    writes;
  * an argument starting with `@` is refused, because pytest replaces it with
    the lines of the file it names after this runner has looked, which would
    carry every refusal above past it inside a file;
  * an untracked `.py` anywhere under a selection is refused, so a module
    dropped into `tests/` cannot be collected until it is committed;
  * so is any other untracked file Python or pytest would load without being
    named: a module anywhere in the checkout (the root is first on
    `sys.path`, so a root `pytest.py` replaces pytest), any file under
    `tests/` (a `test*.txt` doctest, a `tests/pytest.ini`), and a pytest or
    coverage config file at the root;
  * a tracked selection *is* run, with the arguments passed through unchanged.
    A runner that refused everything would pass the three assertions above and
    be useless, so this one is asserted too.

The fixture is a real `git init` rather than a stubbed `git ls-files`. The
whole point of asking git is that it reports the index, and a stub would agree
with whatever this file assumed instead of with git.

Standard library only, like the rest of the suite: no pytest is invoked here,
only `run_pytest` is observed being called.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_tests

# A real TestCase, because the unittest fallback below runs what it is given
# and reports exit code 5 when nothing was collected.
CASE = """import unittest


class %sTests(unittest.TestCase):
    def test_it(self) -> None:
        self.assertTrue(True)
"""


def git(repo: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


class RunnerRefusalTests(unittest.TestCase):
    """Every refusal, exercised against a real repository."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve() / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "tests" / "test_ok.py").write_text(CASE % "Ok")
        self.outside = Path(self.tmp.name).resolve() / "outside"
        self.outside.mkdir()
        (self.outside / "test_elsewhere.py").write_text(CASE % "Elsewhere")

        git(self.repo, "init", "-q")
        git(self.repo, "add", "tests/test_ok.py")

        self.addCleanup(patch.stopall)
        patch.object(run_tests, "REPO_ROOT", self.repo).start()
        patch.object(run_tests, "TESTS_DIR", self.repo / "tests").start()
        # pytest's presence is a property of the machine running this suite, so
        # it is pinned rather than discovered. The fallback has its own test.
        patch.object(run_tests, "pytest_is_available", return_value=True).start()
        self.run_pytest = patch.object(run_tests, "run_pytest", return_value=0).start()

    def test_a_tracked_selection_runs_with_its_arguments_unchanged(self) -> None:
        # Guard the guard: without this, a runner that refused everything would
        # satisfy every other assertion in this class.
        self.assertEqual(run_tests.main(["tests", "-v"]), 0)
        self.run_pytest.assert_called_once_with(["tests", "-v"])

    def test_no_selection_means_the_tests_directory(self) -> None:
        self.assertEqual(run_tests.main([]), 0)
        self.run_pytest.assert_called_once_with(["tests"])

    def test_a_node_id_is_resolved_by_its_path_half(self) -> None:
        self.assertEqual(run_tests.main(["tests/test_ok.py::test_ok"]), 0)
        self.run_pytest.assert_called_once_with(["tests/test_ok.py::test_ok"])

    def test_an_option_value_that_is_not_a_path_is_passed_through(self) -> None:
        # `-k expr` puts a bare word in argv. It is not a path, so it is not a
        # selection, and the default selection is used instead of refusing.
        self.assertEqual(run_tests.main(["-k", "test_ok"]), 0)
        self.run_pytest.assert_called_once_with(["tests", "-k", "test_ok"])

    def test_a_selection_outside_the_tests_directory_is_refused(self) -> None:
        self.assertEqual(run_tests.main([str(self.outside / "test_elsewhere.py")]), 2)
        self.run_pytest.assert_not_called()

    def test_a_path_that_escapes_upward_is_refused(self) -> None:
        self.assertEqual(run_tests.main(["tests/../../outside"]), 2)
        self.run_pytest.assert_not_called()

    def test_an_untracked_module_under_the_selection_is_refused(self) -> None:
        (self.repo / "tests" / "test_dropped_in.py").write_text("import os\n")
        self.assertEqual(run_tests.main(["tests"]), 2)
        self.run_pytest.assert_not_called()

    def test_staging_the_module_is_what_makes_it_runnable(self) -> None:
        # The refusal above is about the index, not about the commit: a file
        # staged in the change under review is in the diff, which is the
        # property being required.
        (self.repo / "tests" / "test_dropped_in.py").write_text(CASE % "DroppedIn")
        self.assertEqual(run_tests.main(["tests"]), 2)
        git(self.repo, "add", "tests/test_dropped_in.py")
        self.assertEqual(run_tests.main(["tests"]), 0)

    def test_an_untracked_module_beside_a_named_file_is_refused(self) -> None:
        # Naming one file still puts its directory first on sys.path -- tests/
        # has no __init__.py, so pytest's prepend import mode inserts it -- and
        # an untracked `tests/ci_tools.py` then shadows the real package for
        # every `from ci_tools... import` in the named file. So a sibling is
        # refused even though it is never collected.
        (self.repo / "tests" / "ci_tools.py").write_text("import os\n")
        self.assertEqual(run_tests.main(["tests/test_ok.py"]), 2)
        self.run_pytest.assert_not_called()

    def test_an_untracked_module_outside_the_tests_directory_is_refused(self) -> None:
        # `python3 -m pytest` puts the working directory -- the repository root
        # -- first on sys.path, so a root `pytest.py` is imported in place of
        # pytest itself, and any other untracked module is importable by name.
        # A .gitignore entry does not make one safe to run.
        (self.repo / ".gitignore").write_text("ignored/\n")
        for dropped in ("pytest.py", "shared/helper.py", "ignored/module.py", "fast.so"):
            with self.subTest(dropped=dropped):
                path = self.repo / dropped
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("import os\n")
                self.assertEqual(run_tests.main(["tests/test_ok.py"]), 2)
                self.run_pytest.assert_not_called()
                path.unlink()

    def test_an_untracked_file_under_tests_is_refused_whatever_its_suffix(self) -> None:
        # pytest collects `test*.txt` as a doctest by default, and reads a
        # `pytest.ini` under tests/ before the root -- one whose `addopts`
        # carries `-p` does what the refused `-o addopts=-p` would.
        for dropped in ("test_notes.txt", "pytest.ini", "data/fixture.json"):
            with self.subTest(dropped=dropped):
                path = self.repo / "tests" / dropped
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(">>> import os\n")
                self.assertEqual(run_tests.main(["tests"]), 2)
                self.run_pytest.assert_not_called()
                path.unlink()

    def test_an_untracked_config_file_at_the_root_is_refused(self) -> None:
        for name in run_tests.ROOT_CONFIG_NAMES:
            with self.subTest(name=name):
                (self.repo / name).write_text("[pytest]\naddopts = -p os\n")
                self.assertEqual(run_tests.main(["tests"]), 2)
                self.run_pytest.assert_not_called()
                (self.repo / name).unlink()

    def test_the_caches_a_run_leaves_behind_are_not_refused(self) -> None:
        # Python reads a __pycache__ entry only for a source beside it, so the
        # byte-code a previous run wrote imports nothing new. Refusing it would
        # refuse every second run.
        for cache in ("tests/__pycache__/test_ok.cpython-313.pyc", "tests/.pytest_cache/v/x"):
            path = self.repo / cache
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")
        self.assertEqual(run_tests.main(["tests"]), 0)

    def test_an_untracked_file_is_runnable_once_staged(self) -> None:
        (self.repo / "tests" / "test_notes.txt").write_text(">>> 1\n1\n")
        self.assertEqual(run_tests.main(["tests"]), 2)
        git(self.repo, "add", "tests/test_notes.txt")
        self.assertEqual(run_tests.main(["tests"]), 0)

    def test_an_untracked_repository_root_conftest_is_refused(self) -> None:
        # Never named on the command line, imported before any test: with no
        # ini file pytest collects conftest.py from the rootdir down.
        (self.repo / "conftest.py").write_text("import os\n")
        self.assertEqual(run_tests.main(["tests"]), 2)
        self.run_pytest.assert_not_called()

    def test_every_refused_option_is_refused_in_both_spellings(self) -> None:
        for option in run_tests.REFUSED_OPTIONS:
            for argument in (option, f"{option}=x"):
                with self.subTest(argument=argument):
                    self.run_pytest.reset_mock()
                    self.assertEqual(run_tests.main([argument, "tests"]), 2)
                    self.run_pytest.assert_not_called()

    def test_an_ini_override_cannot_smuggle_a_refused_option(self) -> None:
        # `-o addopts=...` is spliced into the command line by pytest after
        # this runner has looked at it, so an allowed option carrying a
        # refused one inside its value is the refused one (#194). Every
        # spelling pytest accepts for `-o` is exercised, not only the bare
        # form: the `=` form, the value attached to the short option, and the
        # short option closing a cluster of flags.
        for arguments in (
            ["-o", "addopts=--pyargs outside.evil", "tests"],
            ["--override-ini", "addopts=--pyargs outside.evil", "tests"],
            ["--override-ini=addopts=-p outside.evil", "tests"],
            ["-oaddopts=--pyargs outside.evil", "tests"],
            ["-vo", "addopts=--pyargs outside.evil", "tests"],
        ):
            with self.subTest(arguments=arguments):
                self.run_pytest.reset_mock()
                self.assertEqual(run_tests.main(arguments), 2)
                self.run_pytest.assert_not_called()

    def test_a_short_option_is_refused_with_its_value_attached_or_in_a_cluster(self) -> None:
        # argparse accepts `-pname` for `-p name` and `-xp name` for
        # `-x -p name`. A refusal that matched only the bare `-p` was one a
        # missing space defeated.
        for arguments in (
            ["-poutside.evil", "tests"],
            ["-xp", "outside.evil", "tests"],
            ["-c/elsewhere/pytest.ini", "tests"],
            ["-svc", "/elsewhere/pytest.ini", "tests"],
        ):
            with self.subTest(arguments=arguments):
                self.run_pytest.reset_mock()
                self.assertEqual(run_tests.main(arguments), 2)
                self.run_pytest.assert_not_called()

    def test_a_cluster_of_flags_or_an_attached_value_of_another_option_passes(self) -> None:
        # The cluster walk must stop at the first value-taking option, or a
        # `-k`, `-m` or `-r` value that happens to contain a refused letter
        # would be refused for spelling an option it does not: `-rp` reports
        # passed tests, `-mslow` selects a marker, and `-vv` is two flags and
        # no option at all. (`-W` is refused itself: its category imports.)
        for arguments in (["-vv", "tests"], ["-rp", "tests"], ["-mslow", "tests"], ["-kfoo", "tests"]):
            with self.subTest(arguments=arguments):
                self.run_pytest.reset_mock()
                self.assertEqual(run_tests.main(arguments), 0)
                self.run_pytest.assert_called_once_with(arguments)

    def test_a_write_option_is_refused_in_both_spellings(self) -> None:
        # The write family has no short spelling, so the two forms argparse
        # accepts are the whole surface: `--junitxml path` and
        # `--junitxml=path`. `--debug` also has a bare form that writes
        # `pytestdebug.log` into the working directory, which the loop over
        # `option` alone covers.
        for arguments in (
            ["--junitxml=cosign.pub", "tests"],
            ["--junitxml", "cosign.pub", "tests"],
            ["--junit-xml=cosign.pub", "tests"],
            ["--log-file=.claude/settings.json", "tests"],
            ["--debug", "tests"],
            ["--basetemp=.claude", "tests"],
            ["--cov-report=xml:cosign.pub", "tests"],
        ):
            with self.subTest(arguments=arguments):
                self.run_pytest.reset_mock()
                self.assertEqual(run_tests.main(arguments), 2)
                self.run_pytest.assert_not_called()

    def test_an_argument_file_is_refused(self) -> None:
        # pytest's parser expands `@PATH` into the lines of PATH, in a value
        # position as well as a bare one, after this runner has checked argv.
        # The file here holds a write option and an outside test, and each
        # reached pytest before this refusal existed; `@.env` needed no file
        # at all -- pytest printed its first line as a path it could not find.
        argument_file = self.outside / "arguments"
        argument_file.write_text(f"--junitxml=cosign.pub\n{self.outside}\n")
        for arguments in (
            [f"@{argument_file}", "tests"],
            ["tests", f"@{argument_file}"],
            ["-k", f"@{argument_file}", "tests"],
            ["@.env"],
        ):
            with self.subTest(arguments=arguments):
                self.run_pytest.reset_mock()
                self.assertEqual(run_tests.main(arguments), 2)
                self.run_pytest.assert_not_called()

    def test_a_write_refusal_says_why_it_was_refused(self) -> None:
        # The two lists are refused for unrelated reasons. A `--basetemp`
        # refused for "lets pytest import code from outside this repository"
        # tells the reader something untrue about the option in their hand.
        self.assertIn("writes to a path", run_tests.refusal_message("--basetemp"))
        self.assertIn("import code from outside", run_tests.refusal_message("--pyargs"))
        self.assertIn("interactive debugger", run_tests.refusal_message("--trace"))

    def test_the_refused_list_covers_the_options_that_import_from_elsewhere(self) -> None:
        # Pinned by name so that dropping one is a failure rather than a
        # quietly shorter tuple. `--pyargs` turns positionals into module
        # names, `-p` loads a plugin, `-o` can set `addopts` to either, and
        # the other three move what pytest treats as the project, and with
        # it conftest collection. `-W` and `--pdbcls` import the module they
        # name, and a `--cov-config` rc file can name plugins to import.
        self.assertEqual(
            set(run_tests.REFUSED_IMPORT_OPTIONS),
            {
                "-p",
                "--pyargs",
                "--rootdir",
                "--confcutdir",
                "-c",
                "--config-file",
                "--import-mode",
                "-o",
                "--override-ini",
                "-W",
                "--pythonwarnings",
                "--pdbcls",
                "--cov-config",
            },
        )

    def test_the_refused_list_covers_the_options_that_write_a_file(self) -> None:
        # Pinned by name for the same reason as the list above, and separately
        # from it because the two are refused for unrelated reasons. Each of
        # these names a path and writes it: the report, log and debug options
        # overwrite, and `--basetemp` empties the directory first.
        self.assertEqual(
            set(run_tests.REFUSED_WRITE_OPTIONS),
            {
                "--basetemp",
                "--junitxml",
                "--junit-xml",
                "--log-file",
                "--debug",
                "--report-log",
                "--cov-report",
            },
        )

    def test_the_refused_list_covers_the_options_that_open_the_debugger(self) -> None:
        # pdb runs any statement it reads from standard input, and the caller
        # chooses that input: `printf '!stmt\nc\n' | python3
        # tests/run_tests.py --trace tests` ran `stmt` with nothing written to
        # the checkout. `--trace` stops before every test, `--pdb` at the
        # first failure.
        self.assertEqual(set(run_tests.REFUSED_DEBUGGER_OPTIONS), {"--pdb", "--trace"})

    def test_a_debugger_option_is_refused_wherever_it_stands(self) -> None:
        # `--trace-config` only prints the conftest files pytest loads, and
        # `--pdb` is a prefix of the refused `--pdbcls`, so the match has to
        # be the whole option, not its start.
        for arguments in (
            ["--trace", "tests"],
            ["tests", "--pdb"],
            ["-x", "--pdb", "tests/test_run_tests.py"],
        ):
            with self.subTest(arguments=arguments):
                self.run_pytest.reset_mock()
                self.assertEqual(run_tests.main(arguments), 2)
                self.run_pytest.assert_not_called()
        self.assertIsNone(run_tests.refused_option("--trace-config"))

    def test_every_refused_option_is_in_exactly_one_list(self) -> None:
        # `REFUSED_OPTIONS` is the concatenation, and `refusal_message` picks
        # its wording by membership. An option in two lists would get the
        # wording of one for the reason of another.
        lists = (
            set(run_tests.REFUSED_IMPORT_OPTIONS),
            set(run_tests.REFUSED_WRITE_OPTIONS),
            set(run_tests.REFUSED_DEBUGGER_OPTIONS),
        )
        for index, first in enumerate(lists):
            for second in lists[index + 1 :]:
                self.assertEqual(first & second, set())
        self.assertEqual(set(run_tests.REFUSED_OPTIONS), set().union(*lists))

    def test_the_write_options_are_pytests_own_and_take_a_path(self) -> None:
        # Held against the installed pytest's option table rather than memory,
        # for the reason the flag table below is: a renamed or dropped option
        # would leave a refusal here that protects nothing, and reads as
        # protection. The table, not `pytest -h`: the help text is a rendering
        # of it that drops alias spellings, and which ones it drops depends on
        # the interpreter's argparse -- Python 3.12 prints `--junit-xml=path`
        # alone where 3.13 prints `--junitxml, --junit-xml=path`, so a check
        # against the text passed here and failed in CI. The plugin options are
        # skipped -- `--report-log` and `--cov-report` are refused so that
        # installing the plugin later cannot reopen this, so their absence from
        # the table is the expected state.
        list_options = (
            "from _pytest.config import get_config\n"
            "parser = get_config()._parser\n"
            "for group in [*parser._groups, parser._anonymous]:\n"
            "    for argument in group.options:\n"
            "        print(*argument.names())\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", list_options],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            self.skipTest("pytest is not installed; the list is held against 9.1.1's options")
        registered = set(completed.stdout.split())
        plugin_options = {"--report-log", "--cov-report"}
        for option in run_tests.REFUSED_WRITE_OPTIONS:
            if option in plugin_options:
                continue
            with self.subTest(option=option):
                self.assertIn(option, registered)

    def test_the_flag_table_matches_pytest_when_it_is_installed(self) -> None:
        # SHORT_FLAGS is what lets the cluster walk tell `-vo` (a flag, then
        # `-o`) from `-rp` (`-r` with value `p`). A flag pytest adds that is
        # missing here reads as value-taking, which would let `-<flag>o
        # addopts=...` through, so hold the table against the installed
        # pytest's own help text rather than against memory.
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-h"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            self.skipTest("pytest is not installed; the table is held against 9.1.1's help")
        help_text = completed.stdout
        # A value-taking option is documented with a metavar right after the
        # option (`-k EXPRESSION`, `-r, --report-chars chars`); a flag has
        # nothing before the help column (`-s`, `-v, --verbose`).
        option_line = re.compile(r"^-(\w)(?:,\s--[\w-]+)?(?:\s(\S+))?(?:\s{2,}.*)?$")
        flags = set()
        for line in help_text.splitlines():
            match = option_line.match(line.strip())
            if match and match.group(2) is None:
                flags.add(match.group(1))
        self.assertEqual(flags, set(run_tests.SHORT_FLAGS))


class UnittestFallbackTests(unittest.TestCase):
    """The no-network path, which must not quietly run something else."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve() / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "tests" / "test_ok.py").write_text(CASE % "Ok")
        git(self.repo, "init", "-q")
        git(self.repo, "add", "tests/test_ok.py")

        self.addCleanup(patch.stopall)
        patch.object(run_tests, "REPO_ROOT", self.repo).start()
        patch.object(run_tests, "TESTS_DIR", self.repo / "tests").start()
        patch.object(run_tests, "pytest_is_available", return_value=False).start()

    def test_it_refuses_an_invocation_carrying_options(self) -> None:
        # `unittest` understands none of pytest's options. Running with them
        # dropped would report a pass over a selection that never happened --
        # a `-k` naming one test would silently run all of them.
        self.assertEqual(run_tests.main(["-k", "test_ok"]), 2)

    def test_it_refuses_a_file_selection(self) -> None:
        self.assertEqual(run_tests.main(["tests/test_ok.py"]), 2)

    def test_a_whole_directory_falls_back(self) -> None:
        self.assertEqual(run_tests.main(["tests"]), 0)


if __name__ == "__main__":
    unittest.main()
