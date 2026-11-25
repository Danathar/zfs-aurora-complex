"""
Script: tests/mutation_check.py
What: Mutation-tests the lines a change touches in the measured modules, the git guard hook and the workflow and action YAML, and reports the survivors.
Doing: Runs cosmic-ray over only the changed lines of each changed module, runs only the test files that import that module, does the same for the changed lines of .claude/hooks/gate-git-diff.sh with a few bash operators of its own and for the changed `${{ }}` wiring lines of .github/workflows/ and .github/actions/ with two YAML operators, and prints a Markdown report of every surviving mutant .mutation-survivors.json has not accepted.
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

The git guard hook, .claude/hooks/gate-git-diff.sh, is bash, which cosmic-ray
cannot read, so its changed lines get a small fixed set of bash mutants
instead (see bash_mutants): drop one alternative from a `case` pattern, turn
a `refuse` call into `:`, swap `-eq`/`-ne` and `==`/`!=`, and flip a `x=1` or
`x=0` assignment. Each runs against HOOK_TESTS, the test files that run the
hook. Their survivors go in the same report and the same ledger
(Danathar/akmods#192 for the ruling, #329 here).

A workflow under .github/workflows/ or a composite action under
.github/actions/ passes values between steps, jobs and actions in one-line
`${{ }}` hand-offs that no unit test executes. Each changed line of the form
`<key>: ${{ <expr> }}` inside a `with:`, `env:` or `outputs:` block, or an
output's `value: ${{ <expr> }}`, gets two mutants (see yaml_mutants): the line
deleted, and its value replaced with "". Each runs against the test files
whose text names the YAML file's repository path, for a workflow its directory
and its file name apart, or for an action its directory (see names_target). A changed wiring line in a file no test names is
reported the way a module no test imports is. Their survivors go in the same
report and the same ledger (#349).

It mutates the files in place, one mutant at a time, and restores each file
afterwards: cosmic-ray for a Python module, a try/finally that rewrites the
original bytes for the hook and the YAML. Run it in a checkout you are not editing.

Exit status: 0 when no unaccepted mutant survives, 1 when one does or when a
changed module has no test file that imports it (or a changed wiring line is
in a file no test file names), 2 when the unmodified tests
fail or cosmic-ray itself fails.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / ".mutation-survivors.json"

# The --cov paths in .github/workflows/test.yml. tests/test_mutation_check.py
# fails when the two lists disagree.
MEASURED = ("ci_tools", "shared", "containerfiles/zfs-akmods", "files/scripts")

# The bash hook that re-gates the allow-listed git commands, and the test
# files that run it. Line coverage leaves the hook out by decision
# (tests/check_coverage.py); mutation testing does not.
HOOK = ".claude/hooks/gate-git-diff.sh"
HOOK_TESTS = (
    "tests/test_git_diff_gate.py",
    "tests/test_gate_refusal_corpus.py",
    "tests/test_claude_settings_git_env.py",
)

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

# The workflow and composite-action files whose `${{ }}` wiring lines get the
# YAML mutants, as git pathspecs.
WIRING_FILES = (
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    ".github/actions/*/action.yml",
    ".github/actions/*/action.yaml",
)

TIMEOUT_SECONDS = 120.0
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

# The bash mutants other than a dropped case alternative. Each pattern must
# start and end on code (see code_columns), never in a string or a comment.
TEST_SWAPS = {"-eq": "-ne", "-ne": "-eq", "==": "!=", "!=": "=="}
TEST_WORD = re.compile(r"(?<!\S)(-eq|-ne|==|!=)(?!\S)")
FLAG = re.compile(r"(?<![^\s;])[A-Za-z_]\w*=([01])(?![^\s;)])")
REFUSE = re.compile(r"(?<![^\s;&|(])refuse(?![^\s;)])")

# A YAML line whose whole value is one `${{ }}` expression, quoted or not,
# and a line that opens a mapping: a key with no value on the same line.
WIRING = re.compile(
    r"""^(?P<indent> *)(?P<key>[A-Za-z_][\w-]*): +(?P<q>["']?)\$\{\{.*\}\}(?P=q) *(?:#.*)?$"""
)
MAPPING_KEY = re.compile(r"^ *(?:- +)?([A-Za-z_][\w-]*): *(?:#.*)?$")
WIRING_BLOCKS = ("with", "env", "outputs")


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


def code_columns(line: str) -> list[bool]:
    """Which characters of one line of bash are code: outside quotes and comments.

    It reads one line on its own, so a quote it misreads cannot hide the rest
    of the file. A `$(...)` inside double quotes is code again.
    """
    code = [False] * len(line)
    state = ""  # "" for code, or the quote that is open: ', " or $'
    outer = []  # [state to return to, open parentheses] per `$(` in double quotes
    i = 0
    while i < len(line):
        ch = line[i]
        if state == "":
            if ch == "\\":
                i += 2
                continue
            if ch == "#" and (i == 0 or line[i - 1] in " \t"):
                break
            if line.startswith("$'", i):
                state, i = "$'", i + 2
                continue
            if ch in "'\"":
                state = ch
            elif ch == ")" and outer and not outer[-1][1]:
                state = outer.pop()[0]
            else:
                if outer and ch in "()":
                    outer[-1][1] += 1 if ch == "(" else -1
                code[i] = True
        elif ch == "\\" and state != "'":
            i += 2
            continue
        elif ch == state[-1]:
            state = ""
        elif state == '"' and line.startswith("$(", i):
            outer.append([state, 0])
            state, i = "", i + 2
            continue
        i += 1
    return code


def case_alternatives(line: str) -> list[tuple[int, int]] | None:
    """The spans of the alternatives of the `case` pattern one line holds, or None.

    The line is the pattern, optionally after `(`, ended by `)` or by `| \\`
    when the pattern carries on to the next line. A quoted alternative such
    as `'|'` is one word.
    """
    i = len(line) - len(line.lstrip()) + (line.lstrip().startswith("("))
    spans = []
    while True:
        start = i
        while i < len(line) and line[i] not in " \t|()":
            if line[i] in ";&<>`":
                return None
            if line[i] in "'\"":
                end = line.find(line[i], i + 1)
                if end < 0:
                    return None
                i = end
            i += 1
        if i == start:
            return None
        spans.append((start, i))
        rest = line[i:].lstrip(" \t")
        if rest.startswith(")"):
            return spans
        if not rest.startswith("|"):
            return None
        after = rest[1:].lstrip(" \t")
        if after.rstrip() == "\\":
            return spans
        i = len(line) - len(after)


def bash_mutants(source: str, lines: set[int]) -> list[tuple[int, str, str]]:
    """(line number, operator, mutated line) for each bash mutant on `lines` of `source`.

    The operators are the ones #329 names: drop one alternative from a `case`
    pattern of two or more, turn a `refuse` call into the no-op `:`, swap
    `-eq`/`-ne` and `==`/`!=`, and flip `x=1`/`x=0`. A comment line has none.
    """
    rows = source.splitlines()
    found = []
    for number in sorted(n for n in lines if 1 <= n <= len(rows)):
        line = rows[number - 1]
        if line.lstrip().startswith("#"):
            continue
        spans = case_alternatives(line)
        if spans and len(spans) > 1:
            words = [line[a:b] for a, b in spans]
            for index, word in enumerate(words):
                kept = " | ".join(words[:index] + words[index + 1 :])
                mutated = line[: spans[0][0]] + kept + line[spans[-1][1] :]
                found.append((number, f"bash/DropCaseAlternative_{word}", mutated))
        code = code_columns(line)
        for pattern in (REFUSE, TEST_WORD, FLAG):
            for match in pattern.finditer(line):
                if not (code[match.start()] and code[match.end() - 1]):
                    continue
                if pattern is REFUSE:
                    operator, start, end, new = "bash/RefuseToNoop", *match.span(), ":"
                elif pattern is TEST_WORD:
                    old = match.group(1)
                    new = TEST_SWAPS[old]
                    operator, start, end = f"bash/SwapTest_{old}_{new}", *match.span()
                else:
                    old = match.group(1)
                    new = "0" if old == "1" else "1"
                    operator, start, end = f"bash/FlipFlag_{old}_{new}", *match.span(1)
                found.append((number, operator, line[:start] + new + line[end:]))
    return found


def yaml_parents(rows: list[str], index: int) -> list[str | None]:
    """The keys of the mappings that hold line `index` of `rows`, innermost first.

    A parent is the nearest line above with less indentation, skipping blank
    lines and comments. One that does not open a mapping, such as a list item
    with a value or a `run: |` block, is None.
    """
    indent = len(rows[index]) - len(rows[index].lstrip(" "))
    keys = []
    for row in reversed(rows[:index]):
        stripped = row.strip()
        if not stripped or stripped.startswith("#"):
            continue
        level = len(row) - len(row.lstrip(" "))
        if level >= indent:
            continue
        match = MAPPING_KEY.match(row)
        keys.append(match.group(1) if match else None)
        indent = level
        if level == 0:
            break
    return keys


def yaml_mutants(source: str, lines: set[int]) -> list[tuple[int, str, str | None]]:
    """(line number, operator, mutated line or None to delete it) per YAML mutant.

    The operators are the ones #349 names, for a changed `<key>: ${{ <expr> }}`
    line directly inside a `with:`, `env:` or `outputs:` block, or a `value:`
    line of one output in an `outputs:` block: delete the line, and replace
    its value with "".
    """
    rows = source.splitlines()
    found = []
    for number in sorted(n for n in lines if 1 <= n <= len(rows)):
        match = WIRING.match(rows[number - 1])
        if not match:
            continue
        parents = yaml_parents(rows, number - 1)
        if not (
            (parents[:1] and parents[0] in WIRING_BLOCKS)
            or (match["key"] == "value" and parents[1:2] == ["outputs"])
        ):
            continue
        found.append((number, "yaml/DeleteLine", None))
        found.append((number, "yaml/EmptyValue", f'{match["indent"]}{match["key"]}: ""'))
    return found


def naming_pattern(target: str) -> re.Pattern:
    """A pattern for the text that names `target`, or for an action its directory.

    It matches the path written whole, `.github/workflows/build.yml`, or as
    quoted parts joined by `/`, `".github" / "workflows" / "build.yml"`.
    """
    parts = target.split("/")
    if parts[:2] == [".github", "actions"]:
        parts = parts[:3]
    joint = r"""(?:/|["'] */ *["'])"""
    return re.compile(joint.join(re.escape(part) for part in parts) + r"(?![\w.-])")


def names_target(text: str, target: str) -> bool:
    """Whether the text of a test file names the YAML file `target`.

    A workflow test may build the path from a constant for the directory,
    `WORKFLOW_DIR / "build.yml"`, so the directory and the quoted file name
    anywhere in the text also name a workflow.
    """
    if naming_pattern(target).search(text):
        return True
    directory, _, name = target.rpartition("/")
    return (
        directory == ".github/workflows"
        and re.search(rf"""["']{re.escape(name)}["']""", text) is not None
        and naming_pattern(directory).search(text) is not None
    )


def yaml_tests(target: str) -> tuple[str, ...]:
    """Every tracked test file whose text names the YAML file `target`."""
    names = git("ls-files", "tests/test_*.py", "tests/**/test_*.py").splitlines()
    return tuple(
        name
        for name in sorted(set(names))
        if names_target((ROOT / name).read_text(encoding="utf-8"), target)
    )


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

    A result of None is a changed module no test file imports, or a changed
    workflow or action no test file names: every mutant in it would survive,
    so it counts as one finding without running any.
    """
    out = ["## Mutation check", ""]
    if not results:
        out.append("No measured module changed.")
        return "\n".join(out) + "\n", 0
    new = []
    untested = []
    for target, result in results.items():
        if result is None:
            reader = "names" if target.startswith(".github/") else "imports"
            out.append(f"- `{target}`: no test file {reader} it")
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
                f"{len(untested)} changed file(s) have no test file that imports or names "
                "them, so no test can notice a change to them breaking."
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
            operator = s["operator"].replace("|", "\\|")
            out.append(f"| `{s['module']}:{s['line_number']}` | `{operator}` | `{line}` |")
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


def run_mutant(tests: tuple[str, ...]) -> str:
    """Run `tests` against the mutant on disk: killed, survived or timeout."""
    try:
        result = subprocess.run(
            test_command(tests),
            check=False,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return "timeout"
    return "survived" if result.returncode == 0 else "killed"


# The signals that end a run with no traceback, and so with no `finally`,
# unless a handler turns them into an exception. SIGINT already raises
# KeyboardInterrupt; SIGKILL cannot be caught.
STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


def stop_on_signal(number: int, _frame) -> None:
    """Exit as the signal would have, but through the `finally` blocks on the way."""
    raise SystemExit(128 + number)


def mutate_in_place(
    target: str, tests: tuple[str, ...], mutants: list[tuple[int, str, str | None]]
) -> tuple[dict[str, int], list[dict]]:
    """Write each mutant over `target` in turn and run `tests` against it.

    A mutated line of None deletes the line. The tests run the file they
    always run. The original bytes are written back however the run ends.
    """
    path = ROOT / target
    original = path.read_bytes()
    rows = original.decode("utf-8").splitlines(keepends=True)
    counts, found = {}, []
    # SIGTERM and SIGHUP end Python without running a `finally`, which would
    # leave a mutant in place, for the hook as the live PreToolUse hook: a
    # timeout(1), a closed terminal or a cancelled tool call sends one.
    # Raising instead lets the `finally` below write the original back (#332).
    previous = {number: signal.signal(number, stop_on_signal) for number in STOP_SIGNALS}
    try:
        for number, operator, mutated in mutants:
            row = rows[number - 1]
            ending = row[len(row.rstrip("\r\n")) :]
            replacement = [] if mutated is None else [mutated + ending]
            text = "".join([*rows[: number - 1], *replacement, *rows[number:]])
            path.write_bytes(text.encode("utf-8"))
            outcome = run_mutant(tests)
            counts[outcome] = counts.get(outcome, 0) + 1
            if outcome == "survived":
                found.append(
                    {
                        "module": target,
                        "line_number": number,
                        "operator": operator,
                        "line": row.strip(),
                    }
                )
    finally:
        path.write_bytes(original)
        for number, handler in previous.items():
            signal.signal(number, handler)
    return counts, found


def check_hook(base: str) -> tuple[dict[str, int], list[dict]]:
    """Mutate the lines of HOOK that differ from `base`; return the outcome."""
    baseline(HOOK_TESTS)
    source = (ROOT / HOOK).read_bytes().decode("utf-8")
    lines = changed_lines(git("diff", "-U0", base, "--", HOOK))
    return mutate_in_place(HOOK, HOOK_TESTS, bash_mutants(source, lines))


def check_yaml(target: str, base: str) -> tuple[dict[str, int], list[dict]] | None:
    """Mutate the wiring lines of the YAML file `target` that differ from `base`.

    None when there is a mutant but no test file names `target`: every
    mutant would survive, so the report counts it as one finding.
    """
    source = (ROOT / target).read_bytes().decode("utf-8")
    mutants = yaml_mutants(source, changed_lines(git("diff", "-U0", base, "--", target)))
    if not mutants:
        return {}, []
    tests = yaml_tests(target)
    if not tests:
        return None
    baseline(tests)
    return mutate_in_place(target, tests, mutants)


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
        *(f"{directory}/*.py" for directory in MEASURED), HOOK, *WIRING_FILES,
    ).splitlines()
    results = {}
    os.chdir(ROOT)
    try:
        with tempfile.TemporaryDirectory() as workdir:
            for target in sorted(changed):
                if target == HOOK:
                    results[target] = check_hook(args.base)
                    continue
                if target.startswith(".github/"):
                    results[target] = check_yaml(target, args.base)
                    continue
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
