"""
Script: tests/check_coverage.py
What: Enforces the per-module coverage floors recorded in .coverage-thresholds.json.
Doing: Reads the coverage.json produced by the test run and compares each module's covered-statement count against its floor.
Why: A repository-wide percentage is the wrong gate here -- CONTRIBUTING says the number moving is not a finding, and one well-tested module can hide another that stopped being exercised at all.
Goal: Make a test that stops reaching a module fail on the pull request that stops it, without ever blocking a change on a percentage.

Deliberately not a --cov-fail-under gate. Three reasons, in order:

1. A percentage moves when code is added, so it punishes growth. A covered-line
   count only falls when a test stops reaching lines that used to run.
2. A single repository-wide number lets one module's coverage mask another's.
   The floors are per module so a well-tested helper cannot hide a regression
   that stops a shipped module from being exercised.
3. CONTRIBUTING is explicit that the percentage itself is not a finding. This
   does not change that. Nothing here reads or asserts a percentage; the
   percentages printed alongside each result are context for a reader, not the
   thing being gated.

Floors are raise-only by convention, and raising is never automatic. Raising a
floor is evidenced by an absence being closed -- the suite demonstrably reached
those lines. Lowering one is evidenced by an absence, and a deleted test and a
deliberately removed code path look identical from the count alone. So lowering
a floor stays a decision a person makes in a diff, with a reason in the commit.

The floors only ever cover what `--cov` measures, which left a hole: a file that
ships and executes but sits outside those paths had no entry anywhere, so it was
neither measured nor recorded as deliberately unmeasured. `Containerfile` and
`build_files/build-image.sh` are exactly that -- they run only inside an image
build, so nothing on the host reaches them.

So the manifest has a second section. Every tracked `*.py`, `*.sh` and
`Containerfile` outside `tests/` must appear in `floors` (measured, with a
number) or in `unmeasured` (with a reason). Adding a shipped file and nothing
else turns the gate red, which is the point: the choice gets made once, in the
open, instead of drifting. The gate asserts a decision, not a number.

The two sections are mutually exclusive and both are checked in both directions:
a file in `unmeasured` that coverage *did* measure is an error, because it
should carry a floor instead.

Run by .github/workflows/test.yml. Also runnable by hand:

    python3 -m pytest tests/ --cov=ci_tools --cov=shared \\
      --cov=containerfiles/zfs-akmods --cov=files/scripts \\
      --cov-branch --cov-report=json
    python3 tests/check_coverage.py

An agent permission config may allow `python3 tests/check_coverage.py` without
a prompt, so this file holds itself to the rule an allow-listed Python command
needs: nothing git does not track gets imported.
Python puts the script's own directory first on `sys.path`, so an untracked
`tests/json.py` would *be* `json` here, and the stdlib imports below would run
it. Everything this file imports is standard library, so the first thing it
does is take `tests/` off the path. That is also why there is no
`from __future__ import annotations`: `__future__` is an ordinary import at run
time, it would have to come before this, and a `tests/__future__.py` shadows it.
"""

import sys

# Only when run as a script. Imported by tests/test_check_coverage.py, the
# entry at index 0 is not this file's directory, and under `python3 -P` (or
# PYTHONSAFEPATH) nothing was prepended at all -- index 0 is then the stdlib.
if __name__ == "__main__" and not sys.flags.safe_path:
    del sys.path[0]

# Every import below must stay after the trim, and stay standard library.
import argparse
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
THRESHOLDS_PATH = REPO_ROOT / ".coverage-thresholds.json"
DEFAULT_COVERAGE_REPORT = REPO_ROOT / "coverage.json"

# What counts as a shipped executable, for the completeness check. Workflow and
# action YAML is deliberately not here: it is configuration rather than a file
# with statements, twenty-odd entries of "runs only in CI" would be noise, and
# the properties of it worth pinning are already pinned by
# tests/test_workflow_build_container.py.
SHIPPED_SUFFIXES = (".py", ".sh")
SHIPPED_NAMES = ("Containerfile",)
EXCLUDED_PREFIXES = ("tests/",)


class CoverageGateError(RuntimeError):
    """Raised when the gate cannot run at all, as distinct from a floor failing."""


def load_manifest(path: Path) -> tuple[dict[str, int], dict[str, str]]:
    """
    Return `(floors, unmeasured)`, rejecting a file that cannot mean what it says.

    Every rejection here is a gate error rather than a failure: a manifest that
    cannot be read is not the same as a repository that failed its floors, and
    conflating them would let a malformed file read as "nothing to check".
    """

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CoverageGateError(f"No thresholds file at {path}") from exc
    except json.JSONDecodeError as exc:
        raise CoverageGateError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise CoverageGateError(f"{path} must be an object with 'floors' and 'unmeasured'")

    raw_floors = raw.get("floors")
    raw_unmeasured = raw.get("unmeasured", {})
    if not isinstance(raw_floors, dict) or not raw_floors:
        raise CoverageGateError(f"{path} must have a non-empty 'floors' object of path -> minimum")
    if not isinstance(raw_unmeasured, dict):
        raise CoverageGateError(f"{path}: 'unmeasured' must be an object of path -> reason")

    floors: dict[str, int] = {}
    for module_path, floor in raw_floors.items():
        # A float or a string here would compare in surprising ways rather than
        # failing, and "80" >= 80 is a TypeError only sometimes.
        if not isinstance(floor, int) or isinstance(floor, bool) or floor < 0:
            raise CoverageGateError(
                f"{path}: floor for {module_path} must be a non-negative integer, got {floor!r}"
            )
        floors[module_path] = floor

    unmeasured: dict[str, str] = {}
    for module_path, reason in raw_unmeasured.items():
        # An empty reason is the failure this section exists to prevent. Listing
        # a file as unmeasured with nothing said about why records no decision
        # at all -- it just silences the check.
        if not isinstance(reason, str) or not reason.strip():
            raise CoverageGateError(
                f"{path}: {module_path} is listed as unmeasured but states no reason"
            )
        unmeasured[module_path] = reason.strip()

    both = sorted(set(floors) & set(unmeasured))
    if both:
        raise CoverageGateError(
            f"{path}: {', '.join(both)} appears in both 'floors' and 'unmeasured'"
        )
    return floors, unmeasured


def shipped_executables(repo_root: Path) -> set[str]:
    """
    Return every tracked file that ships and executes, outside `tests/`.

    `git ls-files` rather than a filesystem walk: the tree carries __pycache__
    and coverage output, and a walk would either pick those up or need a second
    ignore list that could drift from .gitignore.
    """

    try:
        listing = subprocess.run(
            # `-c core.fsmonitor=false`: git runs the program a repo names in
            # core.fsmonitor to enumerate files, and `git ls-files` invokes it,
            # so `--repo-root` pointed at a tree whose .git/config sets
            # core.fsmonitor to a script would run that script. This command may be
            # allow-listed for an agent (Bash(python3 tests/check_coverage.py:*)),
            # which would make that code execution with no prompt. A command-line
            # -c overrides the target repo's config and disables the exec; git
            # still lists the tracked files.
            ["git", "-c", "core.fsmonitor=false", "-C", str(repo_root), "ls-files"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise CoverageGateError(
            f"Could not list tracked files with git: {exc}. "
            "The completeness check needs a git checkout."
        ) from exc

    shipped = set()
    for line in listing.splitlines():
        tracked = line.strip()
        if not tracked or tracked.startswith(EXCLUDED_PREFIXES):
            continue
        name = tracked.rsplit("/", 1)[-1]
        if tracked.endswith(SHIPPED_SUFFIXES) or name in SHIPPED_NAMES:
            shipped.add(tracked)
    if not shipped:
        raise CoverageGateError("git listed no shipped executables; the check cannot be trusted")
    return shipped


def load_covered_counts(path: Path) -> dict[str, tuple[int, int]]:
    """
    Return `{module path: (covered statements, total statements)}` from coverage.json.

    Statement counts, not branch counts: statements are what a missing test
    stops reaching, and mixing the two into one number would make a failure
    ambiguous to read.
    """

    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CoverageGateError(
            f"No coverage report at {path}. Run pytest with --cov-report=json first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise CoverageGateError(f"{path} is not valid JSON: {exc}") from exc

    files = report.get("files")
    if not isinstance(files, dict) or not files:
        raise CoverageGateError(f"{path} reports no files; the coverage run measured nothing")

    counts: dict[str, tuple[int, int]] = {}
    for module_path, entry in files.items():
        summary = entry["summary"]
        counts[module_path] = (summary["covered_lines"], summary["num_statements"])
    return counts


def evaluate(
    floors: dict[str, int],
    counts: dict[str, tuple[int, int]],
    unmeasured: dict[str, str] | None = None,
    shipped: set[str] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """
    Compare floors against measured counts, and check the manifest is complete.

    Returns (failures, passes, raisable). Every direction is checked so the
    manifest cannot rot:

    * a module measured with no floor          -- new code, no decision recorded
    * a floor naming a module nothing measured -- stale after a rename or delete
    * a shipped file in neither section        -- the hole this exists to close
    * an `unmeasured` entry coverage did reach -- it should carry a floor
    * an `unmeasured` entry that no longer exists

    `unmeasured` and `shipped` default to empty so the floor comparison can be
    exercised on its own.
    """

    unmeasured = unmeasured or {}
    shipped = shipped or set()

    failures: list[str] = []
    passes: list[str] = []
    raisable: list[str] = []

    for module_path, floor in sorted(floors.items()):
        if module_path not in counts:
            failures.append(
                f"{module_path}: has a floor of {floor} but the coverage run did not measure it "
                f"-- remove the stale entry, or restore the module to a --cov path"
            )
            continue

        covered, total = counts[module_path]
        percent = round(covered * 100 / total) if total else 100
        if covered < floor:
            failures.append(
                f"{module_path}: {covered}/{total} statements ({percent}%), floor is {floor} "
                f"-- {floor - covered} fewer statements are reached than before"
            )
            continue

        passes.append(f"{module_path}: {covered}/{total} statements ({percent}%), floor {floor}")
        if covered > floor:
            raisable.append(f"{module_path}: floor {floor}, reached {covered}")

    for module_path in sorted(set(counts) - set(floors)):
        covered, total = counts[module_path]
        failures.append(
            f"{module_path}: measured ({covered}/{total} statements) but has no floor "
            f"-- add an entry to .coverage-thresholds.json recording what it reaches today"
        )

    for module_path, reason in sorted(unmeasured.items()):
        if module_path in counts:
            covered, total = counts[module_path]
            failures.append(
                f"{module_path}: listed as unmeasured, but the coverage run reached "
                f"{covered}/{total} statements -- move it to 'floors' with that number"
            )
        elif shipped and module_path not in shipped:
            failures.append(
                f"{module_path}: listed as unmeasured but is not a tracked shipped file "
                f"-- remove the stale entry"
            )
        else:
            passes.append(f"{module_path}: unmeasured by decision -- {reason}")

    # The completeness check, and the reason this section exists: a file that
    # ships and executes but appears in neither list has had no decision made
    # about it at all.
    for module_path in sorted(shipped - set(floors) - set(unmeasured)):
        failures.append(
            f"{module_path}: ships and executes but appears in neither 'floors' nor "
            f"'unmeasured' -- record a floor if the suite reaches it, or say in "
            f"'unmeasured' why it cannot be measured"
        )

    return failures, passes, raisable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 tests/check_coverage.py",
        description="Enforce the per-module coverage floors in .coverage-thresholds.json.",
    )
    parser.add_argument(
        "--coverage-report",
        type=Path,
        default=DEFAULT_COVERAGE_REPORT,
        help="Path to the coverage.json written by --cov-report=json.",
    )
    parser.add_argument(
        "--thresholds",
        type=Path,
        default=THRESHOLDS_PATH,
        help="Path to the recorded floors and unmeasured decisions.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Checkout to enumerate shipped files from.",
    )
    parser.add_argument(
        "--skip-completeness",
        action="store_true",
        help=(
            "Compare floors only, without checking that every shipped file has a "
            "decision. For use where there is no git checkout; CI never passes it."
        ),
    )
    args = parser.parse_args(argv)

    try:
        floors, unmeasured = load_manifest(args.thresholds)
        counts = load_covered_counts(args.coverage_report)
        shipped = set() if args.skip_completeness else shipped_executables(args.repo_root)
    except CoverageGateError as exc:
        print(f"coverage: {exc}", file=sys.stderr)
        return 2

    failures, passes, raisable = evaluate(floors, counts, unmeasured, shipped)

    for line in passes:
        print(f"coverage: PASS {line}")
    for line in raisable:
        # Reported, never applied. See the module docstring on why raising is a
        # decision someone makes in a diff.
        print(f"coverage: could raise {line}")

    if failures:
        for line in failures:
            print(f"coverage: FAIL {line}", file=sys.stderr)
        print(f"coverage: {len(failures)} per-file check(s) failed", file=sys.stderr)
        return 1

    print(f"coverage: all {len(passes)} per-file decisions hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
