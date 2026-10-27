"""
Script: tests/mutation_check.py
What: Mutation-tests the lines a change touches in the measured modules, and reports the survivors.
Doing: Runs cosmic-ray over only the changed lines of each changed module, runs only the test files that import that module, and prints a Markdown report of every surviving mutant .mutation-survivors.json has not accepted.
Why: Line coverage says a rule ran; it does not say a test would notice the rule breaking. Run by hand against #210, 27 of the 62 mutants on its changed lines survived the tests (#243).
Goal: Report, on the pull request that adds it, a change whose tests would not notice it breaking.

A mutant is a copy of a module with one small deliberate break: a comparison
flipped, a constant changed, a loop that never iterates. When the tests still
pass against it, the rule it broke is unprotected. This is a copy of
the sister project zfs-kinoite-complex's tests/mutation_check.py, itself a port
of atomic-image-builder's, with the same operator filter, ledger shape and
report, so the repositories read the same output. Two things differ from
atomic-image-builder's:

  * The modules it mutates are the ones test.yml measures for coverage (see
    MEASURED), not a hand-kept table.
  * The test files run against a module are derived rather than listed: every
    tests/**/test_*.py that imports it, or, for a script outside a package,
    loads it by file name with spec_from_file_location. Run under pytest,
    because tests import one another as `tests.<module>`. A test that only
    reaches a module through another one, or through a subprocess (tests/e2e/),
    is not run, so a mutant only such a test kills is reported as a survivor.

Before mutating a module it runs that module's test files once against the
unmodified code. A test that already fails there would kill every mutant, and
the report would read as clean, so a failing baseline stops the run instead.

It mutates the files in place, one mutant at a time, and cosmic-ray restores
each file afterwards. Run it in a checkout you are not editing.

Exit status: 0 when no unaccepted mutant survives, 1 when one does or when a
changed module has no test file that imports it, 2 when the unmodified tests
fail or cosmic-ray itself fails.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / ".mutation-survivors.json"

# The --cov paths in .github/workflows/test.yml. tests/test_mutation_check.py
# fails when the two lists disagree.
MEASURED = ("ci_tools", "shared", "containerfiles/zfs-akmods", "files/scripts")

# cosmic-ray's core operators replace each binary operator with every other
# one and each comparison with every other one. Most of those replacements
# raise on the first call and say nothing a single swap would not, while
# multiplying the run time by ten. Keep one swap per binary operator and the
# boundary and negation swaps per comparison; skip every other one.
KEPT_SWAPS = {
    "ReplaceBinaryOperator": {
        "Add_Sub", "Sub_Add", "Mul_Div", "Div_Mul", "FloorDiv_Div", "Mod_FloorDiv",
        "Pow_Mul", "LShift_RShift", "RShift_LShift", "BitOr_BitAnd", "BitAnd_BitOr",
        "BitXor_BitOr",
    },
    "ReplaceComparisonOperator": {
        "Eq_NotEq", "NotEq_Eq", "Lt_LtE", "Lt_GtE", "LtE_Lt", "LtE_Gt", "Gt_GtE",
        "Gt_LtE", "GtE_Gt", "GtE_Lt", "Is_IsNot", "IsNot_Is", "In_NotIn", "NotIn_In",
    },
}

TIMEOUT_SECONDS = 120.0
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def kept_operator(name: str) -> bool:
    """Whether a mutation by the cosmic-ray operator `name` is worth running."""
    family, _, swap = name.removeprefix("core/").partition("_")
    return family not in KEPT_SWAPS or swap in KEPT_SWAPS[family]


def changed_lines(diff: str) -> set[int]:
    """The new-side line numbers a `git diff -U0` of one file adds or changes."""
    lines = set()
    for line in diff.splitlines():
        match = HUNK.match(line)
        if match:
            start = int(match.group(1))
            count = 1 if match.group(2) is None else int(match.group(2))
            lines.update(range(start, start + count))
    return lines


def annotation_spans(source: str) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """The (start, end) positions of every type annotation in `source`.

    A mutant inside an annotation is equivalent under `from __future__ import
    annotations`, which every measured module with code in it uses: the
    annotation is never evaluated. Without that import it fails at import
    time, which every test run notices, so skipping it there loses nothing.
    """
    spans = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotations = [node.returns]
        elif isinstance(node, (ast.arg, ast.AnnAssign)):
            annotations = [node.annotation]
        else:
            continue
        spans.extend(
            ((a.lineno, a.col_offset), (a.end_lineno, a.end_col_offset))
            for a in annotations
            if a is not None
        )
    return spans


def worth_running(mutation, lines: set[int], spans) -> bool:
    """Whether one cosmic-ray mutation touches a changed line and can matter."""
    start, end = tuple(mutation.start_pos), tuple(mutation.end_pos)
    return (
        kept_operator(mutation.operator_name)
        and not lines.isdisjoint(range(start[0], end[0] + 1))
        and not any(low <= start and end <= high for low, high in spans)
    )


def skip_unwanted(session: Path, lines: set[int], spans) -> None:
    """Mark every pending mutant that is not worth running as skipped."""
    from cosmic_ray.work_db import WorkDB, use_db
    from cosmic_ray.work_item import WorkerOutcome, WorkResult

    with use_db(str(session), WorkDB.Mode.open) as work_db:
        skipped = [
            item.job_id
            for item in work_db.pending_work_items
            if not all(worth_running(m, lines, spans) for m in item.mutations)
        ]
        if skipped:
            work_db.set_multiple_results(
                skipped,
                WorkResult(output="not worth running", worker_outcome=WorkerOutcome.SKIPPED),
            )


def imports_target(tree: ast.Module, target: str) -> bool:
    """Whether a parsed test file imports `target`, by module name or by file name.

    A module in a package is imported by its dotted name. A package's
    __init__.py runs on any import of the package or of a module in it, so
    each of those counts. A script outside one is loaded with
    spec_from_file_location from a path that names its file.
    """
    dotted = target.removesuffix(".py").replace("/", ".")
    is_init = dotted.endswith(".__init__")
    if is_init:
        dotted = dotted.removesuffix(".__init__")
    package, _, module = dotted.rpartition(".")

    def names_target(name: str) -> bool:
        return name == dotted or (is_init and name.startswith(dotted + "."))

    calls, constants = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(names_target(alias.name) for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if names_target(node.module):
                return True
            if node.module == package and any(alias.name == module for alias in node.names):
                return True
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            calls.add(node.func.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            constants.add(node.value)
    return "spec_from_file_location" in calls and Path(target).name in constants


def importing_tests(target: str) -> tuple[str, ...]:
    """Every tracked test file that imports `target`, as a path from the root."""
    names = git("ls-files", "tests/test_*.py", "tests/**/test_*.py").splitlines()
    return tuple(
        name
        for name in sorted(set(names))
        if imports_target(ast.parse((ROOT / name).read_text(encoding="utf-8")), target)
    )


def test_command(tests: tuple[str, ...]) -> list[str]:
    """The pytest command that runs `tests`, against a mutant or the unmodified code."""
    # -x stops at the first failure: one is enough to kill the mutant. The
    # cache plugin is off so a run leaves no .pytest_cache behind.
    return [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *tests]


def config_text(target: str, tests: tuple[str, ...]) -> str:
    """A cosmic-ray config that mutates `target` and runs `tests` against it."""
    command = test_command(tests)
    return "\n".join(
        [
            "[cosmic-ray]",
            f"module-path = {json.dumps(target)}",
            f"timeout = {TIMEOUT_SECONDS}",
            "excluded-modules = []",
            f"test-command = {json.dumps(' '.join(command))}",
            "",
            "[cosmic-ray.distributor]",
            'name = "local"',
            "",
        ]
    )


def load_ledger(path: Path) -> set[tuple[str, str, str]]:
    """The (module, operator, stripped source line) keys a maintainer accepted."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {(entry["module"], entry["operator"], entry["line"]) for entry in data["accepted"]}


def survivors(dump: str, root: Path) -> list[dict]:
    """Each surviving mutant in `cosmic-ray dump` output, keyed for the ledger."""
    found = []
    sources = {}
    for row in dump.splitlines():
        item, result = json.loads(row)
        if not result or result["test_outcome"] != "survived":
            continue
        for mutation in item["mutations"]:
            module = mutation["module_path"]
            if module not in sources:
                sources[module] = (root / module).read_text(encoding="utf-8").splitlines()
            line_number = mutation["start_pos"][0]
            found.append(
                {
                    "module": module,
                    "line_number": line_number,
                    "operator": mutation["operator_name"],
                    "line": sources[module][line_number - 1].strip(),
                }
            )
    return found


def outcomes(dump: str) -> dict[str, int]:
    """How many mutants were killed, survived, timed out, or were skipped."""
    counts = {}
    for row in dump.splitlines():
        _, result = json.loads(row)
        if result is None:
            outcome = "not run"
        elif result["worker_outcome"] == "normal":
            outcome = result["test_outcome"]
        else:
            outcome = result["worker_outcome"]
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def report(
    results: dict[str, tuple[dict[str, int], list[dict]] | None], accepted
) -> tuple[str, int]:
    """Markdown for every changed module, and how many findings are new.

    A result of None is a changed module no test file imports: every mutant in
    it would survive, so it counts as one finding without running any.
    """
    out = ["## Mutation check", ""]
    if not results:
        out.append("No measured module changed.")
        return "\n".join(out) + "\n", 0
    new = []
    untested = []
    for target, result in results.items():
        if result is None:
            out.append(f"- `{target}`: no test file imports it")
            untested.append(target)
            continue
        counts, found = result
        summary = ", ".join(f"{counts[k]} {k}" for k in sorted(counts) if k != "skipped")
        out.append(f"- `{target}`: {summary or 'no mutant on a changed line'}")
        new.extend(s for s in found if (s["module"], s["operator"], s["line"]) not in accepted)
    out.append("")
    if untested:
        out += [
            (
                f"{len(untested)} changed module(s) have no test file that imports them, "
                "so no test can notice a change to them breaking."
            ),
            "",
        ]
    if new:
        out += [
            (
                f"{len(new)} surviving mutant(s) are not in `.mutation-survivors.json`. "
                "Add a test that fails against each one, "
                "or add it to the ledger with the reason it cannot matter."
            ),
            "",
            "| Where | Operator | Line |",
            "|---|---|---|",
        ]
        for s in new:
            line = s["line"].replace("|", "\\|")
            out.append(f"| `{s['module']}:{s['line_number']}` | `{s['operator']}` | `{line}` |")
    elif not untested:
        out.append("No surviving mutant outside the ledger.")
    return "\n".join(out).rstrip("\n") + "\n", len(untested) + len(new)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True
    ).stdout


def cosmic_ray(*args: str) -> str:
    return subprocess.run(
        [sys.executable, "-m", "cosmic_ray.cli", *args], check=True, capture_output=True, text=True
    ).stdout


def baseline(tests: tuple[str, ...]) -> None:
    """Run `tests` against the unmodified code; raise CalledProcessError when they fail.

    A test that fails without any mutant, at collection or because of the
    environment, fails against every mutant too, so each would count as killed.
    """
    subprocess.run(test_command(tests), check=True, capture_output=True, text=True)


def check(
    target: str, tests: tuple[str, ...], base: str, workdir: Path
) -> tuple[dict[str, int], list[dict]]:
    """Mutate the lines of `target` that differ from `base`; return the outcome."""
    baseline(tests)
    config = workdir / (target.replace("/", "_") + ".toml")
    session = config.with_suffix(".sqlite")
    config.write_text(config_text(target, tests), encoding="utf-8")
    cosmic_ray("init", str(config), str(session))
    lines = changed_lines(git("diff", "-U0", base, "--", target))
    skip_unwanted(session, lines, annotation_spans((ROOT / target).read_text(encoding="utf-8")))
    cosmic_ray("exec", str(config), str(session))
    dump = cosmic_ray("dump", str(session))
    return outcomes(dump), survivors(dump, ROOT)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--base", required=True, help="the git revision the change is compared against"
    )
    parser.add_argument(
        "--ledger", type=Path, default=LEDGER, help="the accepted-survivor ledger"
    )
    args = parser.parse_args(argv)

    accepted = load_ledger(args.ledger)
    # --diff-filter=d leaves out a module the change deleted or renamed away:
    # it no longer exists to mutate.
    changed = git(
        "diff", "--name-only", "--diff-filter=d", args.base, "--",
        *(f"{directory}/*.py" for directory in MEASURED),
    ).splitlines()
    results = {}
    os.chdir(ROOT)
    try:
        with tempfile.TemporaryDirectory() as workdir:
            for target in sorted(changed):
                tests = importing_tests(target)
                results[target] = check(target, tests, args.base, Path(workdir)) if tests else None
    except subprocess.CalledProcessError as error:
        # pytest writes its failures to stdout, cosmic-ray to stderr.
        output = "".join(part for part in (error.stdout, error.stderr) if part)
        print(f"mutation check failed: {' '.join(error.cmd)}\n{output}", file=sys.stderr)
        return 2
    text, new = report(results, accepted)
    print(text, end="")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
