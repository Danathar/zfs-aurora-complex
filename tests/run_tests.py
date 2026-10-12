"""
Script: tests/run_tests.py
What: Runs this repository's test suite, refusing any selection that would import Python from outside tests/.
Doing: Rejects the pytest options that move collection off this tree, import a module by name, write to a path they name or open the interactive debugger, resolves every positional selection inside tests/, requires every file Python or pytest could load from the checkout to be tracked by git, then execs pytest.
Why: `.claude/settings.json` has to allow *some* test command unattended, and an unrestricted one is unbounded local code execution -- pytest imports every module it collects, and an import is not a tool call, so nothing in the deny list is consulted.
Goal: Make the one command an agent may run without a prompt able to import only code that is already in the diff.

The problem this exists for, stated plainly. `.claude/settings.json` denies
`cosign sign`, `skopeo copy`, `podman push`, `gh release`, `gh pr merge` and
the rest because `docs/SECURITY-AI.md` says those operations are outward-facing
and irreversible. Those rules decide *commands*. A Python module that runs

    subprocess.run(["cosign", "sign", ...])

at import time is not a command the agent asked to run, so no rule sees it. A
test runner imports every module it collects. So an allow-listed
`python3 -m pytest <anything>` is a way to reach every denied operation with
one approved command, and the deny list is only as strong as the set of files
that runner is willing to import.

What this closes and what it does not, because the difference is the whole
point:

  * **Closed:** code from outside this repository. `pytest /tmp/x.py`,
    `--pyargs some.installed.module`, `-p some_plugin` and
    `--confcutdir=/` each import something that was never in a diff and that
    no reviewer will ever see. Those are refused outright, in every spelling
    pytest accepts -- including `-o addopts=...`, which splices whatever it
    is given into the command line after this script has looked at it.
  * **Closed:** the options that write. pytest can send a report, a log or a
    debug trace to a path the caller names, and `--basetemp` empties the
    directory it is given. An allow-listed command carrying one of those
    overwrites `cosign.pub`, `.claude/settings.json` or the git gate beside it,
    with no `Read(...)` deny row in the way -- those gate the Read tool -- and
    no prefix rule able to see a flag in the middle of an ordinary command.
    That is the write half of the primitive `.claude/hooks/gate-git-diff.sh`
    refuses for `git`; see `REFUSED_WRITE_OPTIONS`.
  * **Closed:** the interactive debugger. `--trace` stops in pdb at the
    start of every test and `--pdb` at the first failure, and pdb runs any
    Python statement it reads from standard input (`!stmt`). So
    `printf '!stmt\\nc\\n' | python3 tests/run_tests.py --trace tests`
    runs code that is in no file at all, which no untracked-file check below
    can see. See `REFUSED_DEBUGGER_OPTIONS`.
  * **Closed:** a file dropped into the checkout and not committed. Checking
    only the `*.py` under a selection was not enough, because pytest and
    Python load code from further afield: `python3 -m pytest` puts the
    repository root first on `sys.path`, so an untracked root `pytest.py` *is*
    pytest; pytest puts `tests/` first too, so an untracked `tests/ci_tools.py`
    shadows the real package for a test that names one file; `test*.txt` under
    `tests/` is collected as a doctest; and a `tests/pytest.ini` sets `addopts`
    exactly as the refused `-o` would. So every untracked `*.py`, `*.pyc` and
    `*.so` anywhere in the checkout, every untracked file under `tests/`, and
    every untracked pytest or coverage config file at the root is refused --
    see `untracked_loadable_files`. Staging it is a separate step that shows
    up in `git status` and in the pull request.
  * **Closed:** the options that import a module by name. `-W ignore::mod.W`
    imports `mod` to resolve the warning category, `--pdbcls=mod:Cls` imports
    `mod`, and `--cov-config` names an rc file whose `plugins =` imports and
    whose `data_file =` writes. Each is refused with the import options.
  * **Closed:** an argument file. pytest's parser is built with
    `fromfile_prefix_chars="@"`, so `@PATH` anywhere in argv is replaced by
    the lines of PATH *inside pytest*, after this script has looked at the
    one word it was given. A file holding `--junitxml=cosign.pub` or
    `/tmp/elsewhere_test.py` carried every refusal above past the checks
    below, and `@.env` printed the file's first line in pytest's
    "file or directory not found" error without writing anything. Any
    argument starting with `@` is refused; see `ARGUMENT_FILE_PREFIX`.
  * **Not closed:** a hand-built `__pycache__/*.pyc` whose header matches a
    tracked source's size and mtime. Python prefers it over the source, but
    making one takes a binary write timed to the source, and a byte-code cache
    is not something this script can tell from the one the last run left.
  * **Not closed:** a *tracked* file under `tests/`. Agents are invited to add
    tests here -- `docs/SECURITY-AI.md` lists editing tests as something an
    agent may do unattended -- so a committed test module runs by design. The
    control for that one is review, not this script, and
    `docs/SECURITY-AI.md` says so rather than implying otherwise.

A committed manifest of test files was the alternative, matching
`build_files/brew-payload.manifest`. It was not taken: it would be a second
hand-maintained copy of the `tests/` file list, churned by every pull request
that adds a test, and it would gate a file the same agent may edit in the same
commit. Asking git which files are tracked gets the drop-in property for free
and cannot drift from the tree.

Standard library only, like everything else that runs before pytest is
installed. When pytest is absent this falls back to
`python3 -m unittest discover`, which CONTRIBUTING documents as the
no-network path -- with the caveat that `unittest discover` does not recurse
into `tests/e2e/` the way pytest does, so the fallback runs fewer tests and
says so.

Run it the way the permission rule spells it, from the repository root:

    python3 tests/run_tests.py                 # the whole suite
    python3 tests/run_tests.py tests/test_cli.py -v

Every check above runs after this file's own imports, so those imports have to
be safe on their own. Python puts the script's directory first on `sys.path`,
so an untracked `tests/argparse.py` would *be* `argparse` here and run before
`untracked_loadable_files` looks. Everything this file imports is standard
library, so the first thing it does is take `tests/` off the path, as
tests/check_coverage.py does. That is also why there is no
`from __future__ import annotations`: `__future__` is an ordinary import at run
time, it would have to come before this, and a `tests/__future__.py` shadows it.
"""

import sys

# Only when run as a script. Imported by tests/test_run_tests.py, the entry at
# index 0 is not this file's directory, and under `python3 -P` (or
# PYTHONSAFEPATH) nothing was prepended at all -- index 0 is then the stdlib.
if __name__ == "__main__" and not sys.flags.safe_path:
    del sys.path[0]

# Every import below must stay after the trim, and stay standard library.
import argparse
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"

# Options that move collection, conftest discovery or plugin loading off this
# tree. Each is refused rather than sanitised: an option that can name an
# arbitrary importable thing has no safe value that is worth the code to
# recognise, and refusing is legible in a way a rewritten argv is not.
#
# `--pyargs` turns positional arguments into module names, which would bypass
# the path check below entirely. `-p` loads a plugin module by name from
# sys.path. `--confcutdir` is the upward bound on conftest.py collection, so a
# value above the repository root pulls in a conftest nobody here wrote.
# `--rootdir` and `-c`/`--config-file` relocate what pytest considers the
# project, which moves both of those in turn. `-o`/`--override-ini` sets any
# ini option for the run, and one of those is `addopts`, whose value pytest
# splices into the command line before it parses options -- so
# `-o addopts=--pyargs x` is `--pyargs x` with an allowed option wrapped
# around it. Refused whole: there is no ini key worth telling apart.
# `-W`/`--pythonwarnings` imports the module of a dotted warning category
# (`-W ignore::mod.W` runs `mod`), and `--pdbcls=mod:Cls` imports `mod` for the
# debugger. `--cov-config` names a coverage rc file, whose `plugins =` imports
# modules by name and whose `data_file =` writes wherever it says.
REFUSED_IMPORT_OPTIONS = (
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
)

# Options whose job is to write a file at a path the caller names. These have
# nothing to do with what gets imported, which is why they were not on the list
# above; they are here because the command this runner stands in front of is
# allow-listed and unprompted, so the flag in the middle of it writes with no
# rule in its way. `.claude/settings.json` denies the *Read tool* this
# repository's secret-shaped paths and has no Write rule at all, and a prefix
# rule cannot see a flag in the middle of an otherwise ordinary command --
# which is the same pair of sentences `_note_git_diff_gate` makes about
# `git --output=FILE`.
#
# `--junitxml` (and its `--junit-xml` alias) overwrites the path with the XML
# report. `--log-file` truncates it. `--debug` replaces it with pytest's trace
# log, and takes no value at all in its bare form. `--basetemp` is worse than a
# write: pytest empties that directory before using it, so
# `--basetemp=.claude` deletes the settings file and the hook beside it.
#
# `--report-log` and `--cov-report` belong to plugins -- pytest-reportlog is not
# installed here and pytest-cov is installed only in CI, which runs
# `python3 -m pytest` directly rather than this wrapper. The strings are refused
# unconditionally anyway, so installing a plugin later does not quietly reopen
# this. There is no `--cache-dir`: pytest spells that one as the `cache_dir`
# ini key, reachable only through `-o`, which the list above already refuses.
#
# Option abbreviation is not a further spelling to cover: pytest's parser
# rejects `--junitx=`, `--basete=` and `--log-fil=`. `--junit-xml` is a real
# alias and needs its own entry.
REFUSED_WRITE_OPTIONS = (
    "--basetemp",
    "--junitxml",
    "--junit-xml",
    "--log-file",
    "--debug",
    "--report-log",
    "--cov-report",
)

# Options that open pdb, the interactive debugger. `--trace` stops at the start
# of every test and `--pdb` at the first failure or error, and at that prompt
# pdb runs any Python statement it reads (`!stmt`, or any line that is not a
# pdb command). The command this runner stands in front of is allow-listed,
# and its standard input is whatever the caller pipes into it, so
# `printf '!stmt\nc\n' | python3 tests/run_tests.py --trace tests` runs
# `stmt` with no prompt -- code that was never in a file, so the
# untracked-file refusal below has nothing to find. `--pdbcls` is on the import
# list above, because its value names a module; these two take no value and
# need no module, so they are their own list. Neither has a short spelling,
# and `--trace-config`, which only prints the conftest files pytest loads,
# opens no prompt and is not here.
REFUSED_DEBUGGER_OPTIONS = (
    "--pdb",
    "--trace",
)

REFUSED_OPTIONS = REFUSED_IMPORT_OPTIONS + REFUSED_WRITE_OPTIONS + REFUSED_DEBUGGER_OPTIONS

# pytest builds its argparse parser with `fromfile_prefix_chars="@"`, so an
# argument starting with this character is not an argument at all: argparse
# replaces it with the lines of the file it names before any option is parsed,
# and in a value position as well as a bare one (`-k @x` expands too). The
# checks below see only the `@PATH` word and never the lines that replace it,
# so the whole argument is refused rather than read -- reading it here would be
# a second parser for pytest's argument-file format, and the file could change
# between that read and pytest's.
ARGUMENT_FILE_PREFIX = "@"

# pytest's short options that take no value, from `pytest -h` (9.1.1). In a
# single-dash argument these may precede the option that matters: `-vo
# addopts=x` is `-v -o addopts=x` and `-xp name` is `-x -p name`. Any other
# letter ends the cluster, either as the option itself or as one whose
# value is the rest of the argument (`-kfoo`, `-Werror`, `-rp`). A letter a
# plugin adds is not here and is read as value-taking -- test.yml installs
# no plugin, and a wrong guess there costs one cluster spelling that can be
# written as separate arguments, each of which is checked on its own.
SHORT_FLAGS = frozenset("hlqsvVx")


def refused_option(argument: str) -> str | None:
    """Return the refused option `argument` spells, or None.

    Every spelling pytest accepts is checked, not only the bare one:
    `--rootdir x` and `--rootdir=x`; a short option with its value attached
    (`-poutside.evil`, `-oaddopts=...`); and a short option closing a cluster
    of flags (`-vo addopts=...`). Matching the bare and `=` forms alone let
    the attached and clustered spellings reach pytest.
    """
    for option in REFUSED_OPTIONS:
        if argument == option or argument.startswith(f"{option}="):
            return option
    if argument.startswith("-") and not argument.startswith("--"):
        for letter in argument[1:]:
            if letter in SHORT_FLAGS:
                continue
            if f"-{letter}" in REFUSED_OPTIONS:
                return f"-{letter}"
            break
    return None


def refusal_message(option: str) -> str:
    """Why `option` is refused, in the words that apply to it.

    The three lists are refused for unrelated reasons, and one message
    covering all of them would have to be vague enough to explain none. A reader who sees
    `--basetemp` refused for letting pytest import outside code learns nothing
    they can act on.
    """
    tail = (
        "Run pytest directly if you mean it -- that command is not on the "
        "unattended allow list."
    )
    if option in REFUSED_DEBUGGER_OPTIONS:
        return (
            f"{option} is refused here: it opens pytest's interactive debugger, "
            "which runs any Python statement it reads from standard input, and "
            f"this command runs unattended. {tail}"
        )
    if option in REFUSED_WRITE_OPTIONS:
        return (
            f"{option} is refused here: it writes to a path this command names, "
            "and this command runs unattended, so the flag reaches any file "
            f"this uid can touch. {tail}"
        )
    return (
        f"{option} is refused here: it lets pytest import code from outside "
        "this repository, which is the thing this runner exists to prevent. "
        f"{tail}"
    )


def tracked_python_files() -> set[Path]:
    """Every tracked `*.py` path in the repository, resolved.

    `git ls-files` rather than a `.git` read: it is the same answer the
    reviewer gets, and it reports the index, so a file staged in this commit
    counts and a file merely written into the working tree does not.
    """
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--", "*.py"],
        capture_output=True,
        text=True,
        check=True,
    )
    return {
        (REPO_ROOT / name).resolve()
        for name in result.stdout.split("\0")
        if name
    }


def selection_path(argument: str) -> Path:
    """Resolve one positional selection to the file or directory it names.

    pytest node IDs carry `::` separators (`tests/test_x.py::TestY::test_z`).
    Only the part before the first `::` is a path.
    """
    return (REPO_ROOT / argument.split("::", 1)[0]).resolve()


def python_files_under(path: Path) -> list[Path]:
    """Every `*.py` a selection of `path` could cause to be imported.

    A directory selection is every `*.py` beneath it, not only `test_*.py`:
    package `__init__.py` files and `conftest.py` are imported too, and a
    module that is imported is a module that runs.
    """
    if path.is_dir():
        return sorted(p.resolve() for p in path.rglob("*.py"))
    return [path]


def is_selection(argument: str) -> bool:
    """Is `argument` something pytest could collect?

    An option and its value are not distinguished here on purpose. pytest has
    too many value-taking options (`-k expr`, `-m mark`, `--deselect id`) for a
    hand-kept list of them to stay right, and getting that list wrong in the
    generous direction would skip a real path. So the test is the one that
    matters for collection: a bare argument naming a path that exists is
    checked, and one that does not is passed through -- pytest cannot import
    what is not there, and an option value is not a path.
    """
    return not argument.startswith("-") and selection_path(argument).exists()


def check_selection(argument: str, tracked: set[Path]) -> str | None:
    """Return why `argument` is refused, or None if it is acceptable."""
    path = selection_path(argument)
    try:
        path.relative_to(TESTS_DIR)
    except ValueError:
        return (
            f"{argument!r} resolves to {path}, which is outside {TESTS_DIR}. "
            "This runner imports what it collects, so it collects only tests/."
        )
    untracked = [p for p in python_files_under(path) if p not in tracked]
    if untracked:
        listed = ", ".join(str(p.relative_to(REPO_ROOT)) for p in untracked)
        return (
            f"{argument!r} would import untracked Python: {listed}. "
            "Commit it first -- a module that runs unattended belongs in the diff."
        )
    return None


# Config files pytest or pytest-cov read from the directory they start in and
# its ancestors. Under `tests/` every untracked file is refused anyway, so only
# the repository root needs naming.
ROOT_CONFIG_NAMES = (
    "pytest.ini",
    ".pytest.ini",
    "pyproject.toml",
    "tox.ini",
    "setup.cfg",
    ".coveragerc",
)

# Caches the runs themselves write. Python reads a `__pycache__` entry only for
# a source file beside it, so an untracked one there imports nothing new.
CACHE_DIRECTORIES = frozenset({"__pycache__", ".pytest_cache"})


def untracked_loadable_files() -> list[str]:
    """Every untracked file in the checkout that Python or pytest could load.

    Ignored files are included on purpose -- a `.gitignore` entry says a file
    is not reviewed, which is the opposite of permission to run it. Three
    shapes, each a way code outside the diff reached the allowed command:

      * `*.py`, `*.pyc` and `*.so` anywhere. The repository root is first on
        `sys.path` under `python3 -m pytest`, so a root `pytest.py` replaces
        pytest itself, and every directory is importable as a package.
      * anything under `tests/`. pytest puts that directory first on
        `sys.path` too, collects `test*.txt` there as doctests, and reads a
        `pytest.ini` there before the root.
      * a pytest or coverage config file at the root, which can set `addopts`
        or load a plugin without a flag on the command line.
    """
    pathspecs = [
        ":(glob)**/*.py",
        ":(glob)**/*.pyc",
        ":(glob)**/*.so",
        ":(top)tests",
        *(f":(top){name}" for name in ROOT_CONFIG_NAMES),
    ]
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--others", "--", *pathspecs],
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(
        name
        for name in result.stdout.split("\0")
        if name and not CACHE_DIRECTORIES.intersection(Path(name).parts[:-1])
    )


def check_untracked_loadables() -> str | None:
    """Refuse the run while any file `untracked_loadable_files` names exists."""
    untracked = untracked_loadable_files()
    if untracked:
        return (
            f"untracked files Python or pytest could load: {', '.join(untracked)}. "
            "Commit them first -- code that runs unattended belongs in the diff."
        )
    return None


def run_pytest(arguments: list[str]) -> int:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *arguments], cwd=REPO_ROOT, check=False
    ).returncode


def run_unittest(arguments: list[str], selections: list[str]) -> int:
    """The no-network fallback CONTRIBUTING documents, for directory selections.

    Deliberately not a silent substitute, in two ways. `unittest discover` does
    not recurse into `tests/e2e/`, which pytest collects, so this runs strictly
    fewer tests and says so. And it understands none of pytest's options, so an
    invocation carrying any of them is refused rather than run with them
    dropped -- a `-k` that silently selected nothing would report a pass over
    tests that never ran.
    """
    directories = [s for s in selections if selection_path(s).is_dir()]
    if len(directories) != len(selections) or arguments != selections:
        print(
            "pytest is not installed, and the unittest fallback understands no "
            "options and only whole directories. Install pytest (see "
            "CONTRIBUTING.md) to use this invocation.",
            file=sys.stderr,
        )
        return 2
    print(
        "pytest is not installed; falling back to unittest discover. This does "
        "not recurse into tests/e2e/, so fewer tests run than CI runs.",
        file=sys.stderr,
    )
    status = 0
    for directory in directories:
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", directory],
            cwd=REPO_ROOT,
            check=False,
        )
        status = status or completed.returncode
    return status


def pytest_is_available() -> bool:
    return (
        subprocess.run(
            [sys.executable, "-c", "import pytest"],
            capture_output=True,
            cwd=REPO_ROOT,
            check=False,
        ).returncode
        == 0
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 tests/run_tests.py",
        description=(
            "Run the test suite with collection confined to tests/. "
            "Unrecognised arguments are passed through to pytest."
        ),
    )
    arguments = list(argv if argv is not None else sys.argv[1:])
    if "-h" in arguments or "--help" in arguments:
        parser.print_help()
        return 0

    for argument in arguments:
        if argument.startswith(ARGUMENT_FILE_PREFIX):
            print(
                f"{argument!r} is refused here: pytest reads an argument starting "
                f"with {ARGUMENT_FILE_PREFIX!r} as a file of further arguments, "
                "which this runner never sees, so every refusal it makes would "
                "stop at that one word. Put the arguments on the command line.",
                file=sys.stderr,
            )
            return 2
        option = refused_option(argument)
        if option is not None:
            print(refusal_message(option), file=sys.stderr)
            return 2

    selections = [a for a in arguments if is_selection(a)]
    if not selections:
        selections = ["tests"]
        arguments = ["tests", *arguments]

    tracked = tracked_python_files()
    problems = [check_untracked_loadables()]
    problems.extend(check_selection(s, tracked) for s in selections)
    refusals = [p for p in problems if p is not None]
    if refusals:
        for refusal in refusals:
            print(f"Refusing to run: {refusal}", file=sys.stderr)
        return 2

    if pytest_is_available():
        return run_pytest(arguments)
    return run_unittest(arguments, selections)


if __name__ == "__main__":
    raise SystemExit(main())
