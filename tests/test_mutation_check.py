"""
Script: tests/test_mutation_check.py
What: Tests for tests/mutation_check.py, the diff-scoped mutation run.
Doing: Stubs the cosmic-ray processes and work database, which only a mutation run installs; everything else is real.
Why: The diff parsing, the annotation spans, the bash mutants of the git guard hook, the YAML mutants of the workflow and action wiring, the report, the committed ledger and the test files chosen per module decide what the check can see, and a check that silently sees less reads as evidence.
Goal: Pin each of those, and join the module list to the --cov paths test.yml measures.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import mutation_check
from mutation_check import (
    HOOK,
    HOOK_TESTS,
    MEASURED,
    WIRING_FILES,
    annotation_spans,
    bash_mutants,
    case_alternatives,
    changed_lines,
    code_columns,
    config_text,
    importing_tests,
    imports_target,
    kept_operator,
    load_ledger,
    names_target,
    naming_pattern,
    outcomes,
    report,
    survivors,
    worth_running,
    yaml_mutants,
    yaml_tests,
)

ROOT = Path(__file__).resolve().parents[1]


def tracked(*patterns: str) -> list[str]:
    return subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", *patterns], capture_output=True, text=True, check=True
    ).stdout.splitlines()


def mutation(operator: str, start: tuple[int, int], end: tuple[int, int]) -> SimpleNamespace:
    return SimpleNamespace(operator_name=operator, start_pos=start, end_pos=end)


def dump_row(
    operator: str, row: int, outcome: str | None, worker: str = "normal", module: str = "m.py"
) -> str:
    item = {
        "job_id": "j",
        "mutations": [{"module_path": module, "operator_name": operator, "start_pos": [row, 0]}],
    }
    result = (
        None
        if outcome is None and worker == "normal"
        else {"worker_outcome": worker, "test_outcome": outcome}
    )
    return json.dumps([item, result])


class KeptOperatorTests(unittest.TestCase):
    def test_keeps_one_swap_per_binary_operator(self) -> None:
        self.assertTrue(kept_operator("core/ReplaceBinaryOperator_Add_Sub"))
        self.assertFalse(kept_operator("core/ReplaceBinaryOperator_Add_Mul"))
        self.assertTrue(kept_operator("core/ReplaceBinaryOperator_BitOr_BitAnd"))
        self.assertFalse(kept_operator("core/ReplaceBinaryOperator_BitOr_BitXor"))

    def test_keeps_the_boundary_and_negation_swaps_per_comparison(self) -> None:
        for kept in ("Eq_NotEq", "Lt_LtE", "Lt_GtE", "In_NotIn", "Is_IsNot"):
            self.assertTrue(kept_operator(f"core/ReplaceComparisonOperator_{kept}"), kept)
        for dropped in ("Eq_Lt", "Eq_Is", "Lt_Eq", "In_Eq"):
            self.assertFalse(kept_operator(f"core/ReplaceComparisonOperator_{dropped}"), dropped)

    def test_keeps_every_operator_outside_the_two_swap_families(self) -> None:
        for name in (
            "core/NumberReplacer",
            "core/AddNot",
            "core/ZeroIterationForLoop",
            "core/ReplaceUnaryOperator_USub_UAdd",
        ):
            self.assertTrue(kept_operator(name), name)

    def test_every_kept_swap_names_two_different_operators(self) -> None:
        for family, swaps in mutation_check.KEPT_SWAPS.items():
            for swap in swaps:
                before, after = swap.split("_")
                self.assertNotEqual(before, after, f"{family}_{swap}")
            # One kept swap per source operator for binary operators.
            if family == "ReplaceBinaryOperator":
                sources = [swap.split("_")[0] for swap in swaps]
                self.assertEqual(len(sources), len(set(sources)), sorted(sources))


class ChangedLinesTests(unittest.TestCase):
    def test_reads_every_line_of_each_hunk(self) -> None:
        diff = "--- a/m.py\n+++ b/m.py\n@@ -3,0 +4,2 @@ def f():\n+a\n+b\n@@ -10 +12 @@\n-x\n+y\n"
        self.assertEqual(changed_lines(diff), {4, 5, 12})

    def test_a_pure_deletion_changes_no_line_that_can_be_mutated(self) -> None:
        self.assertEqual(changed_lines("@@ -7,3 +6,0 @@\n-a\n-b\n-c\n"), set())

    def test_a_removed_line_that_looks_like_a_hunk_header_is_not_one(self) -> None:
        self.assertEqual(changed_lines("@@ -1 +1 @@\n-@@ -5 +9 @@\n+x\n"), {1})

    def test_no_diff_changes_nothing(self) -> None:
        self.assertEqual(changed_lines(""), set())


class AnnotationSpanTests(unittest.TestCase):
    SOURCE = (
        "from __future__ import annotations\n"
        "def f(a: int | None, *rest: str, key: bool = True, **extra: list[int]) -> str | None:\n"
        "    total: int | float = 0\n"
        "    plain = a | 1\n"
        "    return None\n"
        "async def g(b) -> dict[str, int]:\n"
        "    return {}\n"
    )

    def text(self, span) -> str:
        (row, col), (end_row, end_col) = span
        self.assertEqual(row, end_row)
        return self.SOURCE.splitlines()[row - 1][col:end_col]

    def test_finds_return_argument_and_variable_annotations(self) -> None:
        self.assertEqual(
            sorted(self.text(span) for span in annotation_spans(self.SOURCE)),
            sorted(
                [
                    "int | None",
                    "str",
                    "bool",
                    "list[int]",
                    "str | None",
                    "int | float",
                    "dict[str, int]",
                ]
            ),
        )

    def test_an_unannotated_function_has_no_span(self) -> None:
        self.assertEqual(annotation_spans("def f(a, b):\n    return a | b\n"), [])


class WorthRunningTests(unittest.TestCase):
    SPANS = (((2, 10), (2, 20)),)

    def test_runs_a_kept_operator_on_a_changed_line(self) -> None:
        self.assertTrue(worth_running(mutation("core/AddNot", (5, 4), (5, 9)), {5}, self.SPANS))

    def test_a_mutation_spanning_onto_a_changed_line_runs(self) -> None:
        self.assertTrue(worth_running(mutation("core/AddNot", (4, 4), (6, 1)), {6}, self.SPANS))

    def test_skips_an_unchanged_line(self) -> None:
        self.assertFalse(
            worth_running(mutation("core/AddNot", (5, 4), (5, 9)), {4, 6}, self.SPANS)
        )

    def test_skips_a_dropped_swap(self) -> None:
        dropped = mutation("core/ReplaceBinaryOperator_Add_Mul", (5, 4), (5, 5))
        self.assertFalse(worth_running(dropped, {5}, self.SPANS))

    def test_skips_a_mutation_inside_an_annotation(self) -> None:
        inside = mutation("core/ReplaceBinaryOperator_BitOr_BitAnd", (2, 14), (2, 15))
        self.assertFalse(worth_running(inside, {2}, self.SPANS))
        # Touching the span's edges is still inside it.
        self.assertFalse(worth_running(mutation("core/AddNot", (2, 10), (2, 20)), {2}, self.SPANS))

    def test_a_mutation_outside_the_annotation_on_the_same_line_runs(self) -> None:
        after = mutation("core/ReplaceBinaryOperator_BitOr_BitAnd", (2, 24), (2, 25))
        self.assertTrue(worth_running(after, {2}, self.SPANS))
        self.assertTrue(worth_running(mutation("core/AddNot", (2, 9), (2, 15)), {2}, self.SPANS))


class SkipUnwantedTests(unittest.TestCase):
    def fake_cosmic_ray(self, items):
        recorded = []

        class FakeDB:
            pending_work_items = items

            def set_multiple_results(self, job_ids, result):
                recorded.append((job_ids, result))

        @contextlib.contextmanager
        def use_db(path, mode):
            recorded.append(("open", path, mode))
            yield FakeDB()

        work_db = types.ModuleType("cosmic_ray.work_db")
        work_db.WorkDB = SimpleNamespace(Mode=SimpleNamespace(open="open-mode"))
        work_db.use_db = use_db
        work_item = types.ModuleType("cosmic_ray.work_item")
        work_item.WorkerOutcome = SimpleNamespace(SKIPPED="skipped")
        work_item.WorkResult = lambda output, worker_outcome: (output, worker_outcome)
        modules = {
            "cosmic_ray": types.ModuleType("cosmic_ray"),
            "cosmic_ray.work_db": work_db,
            "cosmic_ray.work_item": work_item,
        }
        return mock.patch.dict(sys.modules, modules), recorded

    def test_marks_only_the_mutants_not_worth_running_as_skipped(self) -> None:
        items = [
            SimpleNamespace(job_id="keep", mutations=[mutation("core/AddNot", (3, 0), (3, 4))]),
            SimpleNamespace(
                job_id="unchanged", mutations=[mutation("core/AddNot", (9, 0), (9, 4))]
            ),
            SimpleNamespace(
                job_id="annotation", mutations=[mutation("core/AddNot", (3, 10), (3, 12))]
            ),
        ]
        patch, recorded = self.fake_cosmic_ray(items)
        with patch:
            mutation_check.skip_unwanted(Path("s.sqlite"), {3}, [((3, 8), (3, 20))])
        self.assertEqual(recorded[0], ("open", "s.sqlite", "open-mode"))
        self.assertEqual(
            recorded[1], (["unchanged", "annotation"], ("not worth running", "skipped"))
        )
        self.assertEqual(len(recorded), 2)

    def test_writes_nothing_when_every_mutant_is_worth_running(self) -> None:
        keep = SimpleNamespace(job_id="keep", mutations=[mutation("core/AddNot", (3, 0), (3, 4))])
        patch, recorded = self.fake_cosmic_ray([keep])
        with patch:
            mutation_check.skip_unwanted(Path("s.sqlite"), {3}, [])
        self.assertEqual(recorded, [("open", "s.sqlite", "open-mode")])


class ImportsTargetTests(unittest.TestCase):
    def imports(self, source: str, target: str) -> bool:
        return imports_target(ast.parse(source), target)

    def test_every_spelling_of_a_package_import_counts(self) -> None:
        for source in (
            "import ci_tools.common\n",
            "import ci_tools.common as common\n",
            "from ci_tools.common import CiToolError\n",
            "from ci_tools import common\n",
            "from ci_tools import cli, common\n",
            "def f():\n    from ci_tools.common import run_cmd\n",
        ):
            with self.subTest(source=source):
                self.assertTrue(self.imports(source, "ci_tools/common.py"))

    def test_a_sibling_or_a_prefix_does_not_count(self) -> None:
        for source in (
            "import ci_tools\n",
            "from ci_tools import cli\n",
            "from ci_tools.common_extra import x\n",
            "import ci_tools.common_extra\n",
            "from shared import common\n",
            "from . import common\n",
            "COMMON = 'ci_tools/common.py'\n",
        ):
            with self.subTest(source=source):
                self.assertFalse(self.imports(source, "ci_tools/common.py"))

    def test_any_import_of_a_package_runs_its_init(self) -> None:
        for source in (
            "import ci_tools\n",
            "import ci_tools as tools\n",
            "from ci_tools import cli\n",
            "import ci_tools.common\n",
            "from ci_tools.common import CiToolError\n",
        ):
            with self.subTest(source=source):
                self.assertTrue(self.imports(source, "ci_tools/__init__.py"))
        for source in (
            "import shared\n",
            "from shared import oci_layout\n",
            "from shared.oci_layout import unpack_layer_tarballs\n",
            "import ci_tools_extra\n",
            "from ci_tools_extra.common import x\n",
        ):
            with self.subTest(source=source):
                self.assertFalse(self.imports(source, "ci_tools/__init__.py"))

    def test_a_script_loaded_by_its_file_name_counts(self) -> None:
        source = (
            "import importlib.util\n"
            "PATH = ROOT / 'files' / 'scripts' / 'configure_signing_policy.py'\n"
            "spec = importlib.util.spec_from_file_location('m', PATH)\n"
        )
        self.assertTrue(self.imports(source, "files/scripts/configure_signing_policy.py"))
        self.assertFalse(self.imports(source, "files/scripts/other.py"))

    def test_naming_a_script_without_loading_it_does_not_count(self) -> None:
        source = "DOC = 'see configure_signing_policy.py'\nNAME = 'configure_signing_policy.py'\n"
        self.assertFalse(self.imports(source, "files/scripts/configure_signing_policy.py"))


class ImportingTestsTests(unittest.TestCase):
    def test_finds_the_test_file_named_for_each_kind_of_module(self) -> None:
        for target, test in (
            ("ci_tools/common.py", "tests/test_common.py"),
            ("shared/oci_layout.py", "tests/test_oci_layout.py"),
            (
                "containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py",
                "tests/test_install_zfs_from_akmods_cache.py",
            ),
            (
                "files/scripts/configure_signing_policy.py",
                "tests/test_configure_signing_policy.py",
            ),
            ("ci_tools/cli.py", "tests/e2e/test_cli_end_to_end.py"),
        ):
            with self.subTest(target=target):
                found = importing_tests(target)
                self.assertIn(test, found)
                self.assertEqual(list(found), sorted(set(found)))

    def test_every_measured_module_with_code_has_a_test_file(self) -> None:
        # A module no test imports is reported as a finding on every change
        # to it, so today's tree should hold none outside the empty __init__.
        for target in tracked(*(f"{directory}/*.py" for directory in MEASURED)):
            if Path(target).name == "__init__.py":
                continue
            with self.subTest(target=target):
                self.assertTrue(importing_tests(target))


def code_text(line: str) -> str:
    return "".join(ch for ch, code in zip(line, code_columns(line)) if code)


class CodeColumnsTests(unittest.TestCase):
    def test_quotes_are_not_code(self) -> None:
        self.assertEqual(code_text("""x == 'a == b' "c == d" $'e == f' y"""), "x ==    y")

    def test_a_comment_is_not_code_but_a_hash_inside_a_word_is(self) -> None:
        self.assertEqual(code_text("x=0 # y=1"), "x=0 ")
        self.assertEqual(code_text("((${#a[@]} == 0))"), "((${#a[@]} == 0))")

    def test_a_substitution_inside_double_quotes_is_code_again(self) -> None:
        self.assertEqual(code_text('"a $(f (x) "b" == c) d" == e'), "f (x)  == c == e")

    def test_an_escaped_character_is_not_code(self) -> None:
        self.assertEqual(code_text(r"a \' == b"), "a  == b")
        self.assertEqual(code_text(r'"a \" == b" c'), " c")


class CaseAlternativesTests(unittest.TestCase):
    def words(self, line: str):
        spans = case_alternatives(line)
        return None if spans is None else [line[a:b] for a, b in spans]

    def test_reads_each_alternative_of_a_pattern(self) -> None:
        self.assertEqual(
            self.words("    --namespace | --super-prefix | --attr-source)"),
            ["--namespace", "--super-prefix", "--attr-source"],
        )
        self.assertEqual(self.words("  (a|b) x=1 ;;"), ["a", "b"])
        self.assertEqual(self.words("  *) refuse x ;;"), ["*"])

    def test_a_quoted_alternative_is_one_word(self) -> None:
        self.assertEqual(self.words(r"""  '|' | "'" | $'\t')"""), ["'|'", '"\'"', r"$'\t'"])

    def test_a_pattern_that_carries_on_to_the_next_line_counts(self) -> None:
        self.assertEqual(self.words("    -c* | -C* | \\"), ["-c*", "-C*"])

    def test_a_line_that_is_not_a_pattern_is_none(self) -> None:
        for line in (
            "x=$(a | b)",
            "echo a | grep b)",
            "a | b",
            "a >x | b)",
            "  ;;",
            "  'unclosed | b)",
            "",
        ):
            with self.subTest(line=line):
                self.assertIsNone(case_alternatives(line))


class BashMutantsTests(unittest.TestCase):
    def mutants(self, source: str):
        lines = set(range(1, source.count("\n") + 2))
        return [(op, line) for _, op, line in bash_mutants(source, lines)]

    def test_drops_each_alternative_of_a_case_pattern(self) -> None:
        self.assertEqual(
            self.mutants("    --namespace | --super-prefix | --attr-source)"),
            [
                ("bash/DropCaseAlternative_--namespace", "    --super-prefix | --attr-source)"),
                ("bash/DropCaseAlternative_--super-prefix", "    --namespace | --attr-source)"),
                ("bash/DropCaseAlternative_--attr-source", "    --namespace | --super-prefix)"),
            ],
        )

    def test_a_single_alternative_is_not_dropped(self) -> None:
        self.assertEqual(self.mutants("  -) x=1 ;;"), [("bash/FlipFlag_1_0", "  -) x=0 ;;")])

    def test_turns_each_refuse_call_into_a_no_op(self) -> None:
        self.assertEqual(
            self.mutants('((x)) && refuse "${MSG}"; refuse y'),
            [
                ("bash/RefuseToNoop", '((x)) && : "${MSG}"; refuse y'),
                ("bash/RefuseToNoop", '((x)) && refuse "${MSG}"; : y'),
            ],
        )

    def test_the_refuse_definition_and_other_names_are_not_mutated(self) -> None:
        self.assertEqual(self.mutants("refuse() {\nrefuse_all x\nover-refuse x"), [])

    def test_swaps_each_test_operator(self) -> None:
        line = '[[ "$a" == x || "$b" != y ]] && [ 1 -eq 2 ] || [ 1 -ne 2 ]'
        self.assertEqual(
            self.mutants(line),
            [
                ("bash/SwapTest_==_!=", line.replace("==", "!=")),
                ("bash/SwapTest_!=_==", line.replace("!=", "==")),
                ("bash/SwapTest_-eq_-ne", line.replace("-eq", "-ne")),
                ("bash/SwapTest_-ne_-eq", line.replace("-ne", "-eq")),
            ],
        )

    def test_an_operator_glued_to_a_word_is_not_swapped(self) -> None:
        self.assertEqual(self.mutants("x==y a!=b --foo-eq"), [])

    def test_flips_each_zero_or_one_assignment(self) -> None:
        self.assertEqual(
            self.mutants("local a=1; b=0 c=1)"),
            [
                ("bash/FlipFlag_1_0", "local a=0; b=0 c=1)"),
                ("bash/FlipFlag_0_1", "local a=1; b=1 c=1)"),
                ("bash/FlipFlag_1_0", "local a=1; b=0 c=0)"),
            ],
        )

    def test_an_option_value_or_a_longer_number_is_not_flipped(self) -> None:
        self.assertEqual(self.mutants("git --foo=1 x=10 x=1a y=$x"), [])

    def test_nothing_in_a_string_or_a_comment_is_mutated(self) -> None:
        self.assertEqual(
            self.mutants("""m='a == b refuse x=1' "c != d" # x=1 refuse\n  # a | b)"""), []
        )

    def test_only_the_changed_lines_are_mutated(self) -> None:
        self.assertEqual(
            [(n, line) for n, _, line in bash_mutants("a=1\nb=1\nc=1\n", {2, 9})], [(2, "b=0")]
        )

    def test_the_issue_329_instance_is_one_of_the_hook_mutants(self) -> None:
        # Dropping --attr-source from this arm let `git --attr-source HEAD
        # diff /dev/null /etc/shadow` through, and no test noticed (#329).
        source = (ROOT / HOOK).read_text(encoding="utf-8")
        arm = source.splitlines().index("    --namespace | --super-prefix | --attr-source)") + 1
        self.assertIn(
            ("bash/DropCaseAlternative_--attr-source", "    --namespace | --super-prefix)"),
            [(op, line) for _, op, line in bash_mutants(source, {arm})],
        )


class HookTestsTests(unittest.TestCase):
    def test_each_hook_test_file_is_tracked_and_runs_the_hook(self) -> None:
        # By its path, or as the PreToolUse hook .claude/settings.json registers.
        names = set(tracked("tests/test_*.py"))
        for test in HOOK_TESTS:
            with self.subTest(test=test):
                self.assertIn(test, names)
                text = (ROOT / test).read_text(encoding="utf-8")
                self.assertRegex(text, r'gate-git-diff\.sh|\["PreToolUse"\]')


class CheckHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.hook = self.root / HOOK
        self.hook.parent.mkdir(parents=True)
        self.original = b"a=1\r\nb=1\n[[ x == y ]]\n"
        self.hook.write_bytes(self.original)

    def run_check(self, run_mutant):
        git = mock.Mock(return_value="@@ -1,2 +1,2 @@\n@@ -3 +3 @@\n")
        baseline = mock.Mock()
        with mock.patch.multiple(
            mutation_check, ROOT=self.root, git=git, run_mutant=run_mutant, baseline=baseline
        ):
            try:
                return mutation_check.check_hook("origin/main")
            finally:
                git.assert_called_once_with("diff", "-U0", "origin/main", "--", HOOK)
                baseline.assert_called_once_with(HOOK_TESTS)

    def test_runs_the_hook_tests_against_each_mutant_written_in_place(self) -> None:
        seen = []

        def run_mutant(tests):
            seen.append(self.hook.read_bytes())
            self.assertEqual(tests, HOOK_TESTS)
            return ["killed", "survived", "timeout"][len(seen) - 1]

        counts, found = self.run_check(run_mutant)
        self.assertEqual(
            seen,
            [
                b"a=0\r\nb=1\n[[ x == y ]]\n",
                b"a=1\r\nb=0\n[[ x == y ]]\n",
                b"a=1\r\nb=1\n[[ x != y ]]\n",
            ],
        )
        self.assertEqual(counts, {"killed": 1, "survived": 1, "timeout": 1})
        self.assertEqual(
            found,
            [{"module": HOOK, "line_number": 2, "operator": "bash/FlipFlag_1_0", "line": "b=1"}],
        )
        self.assertEqual(self.hook.read_bytes(), self.original)

    def test_restores_the_original_bytes_when_a_run_is_interrupted(self) -> None:
        with self.assertRaises(KeyboardInterrupt):
            self.run_check(mock.Mock(side_effect=KeyboardInterrupt))
        self.assertEqual(self.hook.read_bytes(), self.original)

    def test_restores_the_original_bytes_when_the_run_is_terminated(self) -> None:
        # A real process and a real signal: a mock cannot show that a
        # `finally` runs, because SIGTERM's default action skips it.
        started = self.root / "started"
        child = textwrap.dedent(
            f"""
            import sys, time
            from pathlib import Path
            from unittest import mock
            sys.path.insert(0, {str(ROOT / "tests")!r})
            import mutation_check

            def run_mutant(tests):
                Path({str(started)!r}).touch()
                time.sleep(60)

            with mock.patch.multiple(
                mutation_check,
                ROOT=Path({str(self.root)!r}),
                git=mock.Mock(return_value="@@ -1 +1 @@\\n"),
                baseline=mock.Mock(),
                run_mutant=run_mutant,
            ):
                mutation_check.check_hook("origin/main")
            """
        )
        for number in mutation_check.STOP_SIGNALS:
            with self.subTest(signal=signal.Signals(number).name):
                started.unlink(missing_ok=True)
                self.hook.write_bytes(self.original)
                process = subprocess.Popen([sys.executable, "-c", child])
                self.addCleanup(process.kill)
                deadline = time.monotonic() + 30
                while not started.exists():
                    self.assertIsNone(process.poll(), "the child ended before mutating")
                    self.assertLess(time.monotonic(), deadline, "no mutant was written")
                    time.sleep(0.05)
                self.assertEqual(self.hook.read_bytes(), b"a=0\r\nb=1\n[[ x == y ]]\n")
                process.send_signal(number)
                self.assertEqual(process.wait(timeout=30), 128 + number)
                self.assertEqual(self.hook.read_bytes(), self.original)

    def test_puts_the_previous_signal_handlers_back(self) -> None:
        before = {number: signal.getsignal(number) for number in mutation_check.STOP_SIGNALS}
        self.run_check(mock.Mock(return_value="killed"))
        after = {number: signal.getsignal(number) for number in mutation_check.STOP_SIGNALS}
        self.assertEqual(after, before)

    def test_a_failing_baseline_stops_before_any_mutant_is_written(self) -> None:
        error = subprocess.CalledProcessError(1, ["pytest"], output="1 failed\n")
        run_mutant = mock.Mock()
        with mock.patch.multiple(
            mutation_check,
            ROOT=self.root,
            baseline=mock.Mock(side_effect=error),
            run_mutant=run_mutant,
        ), self.assertRaises(subprocess.CalledProcessError):
            mutation_check.check_hook("b")
        run_mutant.assert_not_called()
        self.assertEqual(self.hook.read_bytes(), self.original)

    def test_a_mutant_is_killed_when_the_tests_fail_and_survives_when_they_pass(self) -> None:
        passing = self.root / "test_passes.py"
        passing.write_text("def test_passes():\n    assert True\n")
        failing = self.root / "test_fails.py"
        failing.write_text("def test_fails():\n    assert False\n")
        self.assertEqual(mutation_check.run_mutant((str(passing),)), "survived")
        self.assertEqual(mutation_check.run_mutant((str(failing),)), "killed")

    def test_a_mutant_that_outruns_the_timeout_is_a_timeout(self) -> None:
        expired = subprocess.TimeoutExpired(["pytest"], mutation_check.TIMEOUT_SECONDS)
        with mock.patch.object(subprocess, "run", side_effect=expired) as run:
            self.assertEqual(mutation_check.run_mutant(("tests/t.py",)), "timeout")
        self.assertEqual(run.call_args.args[0], mutation_check.test_command(("tests/t.py",)))
        self.assertEqual(run.call_args.kwargs["timeout"], mutation_check.TIMEOUT_SECONDS)

WIRING = textwrap.dedent(
    """\
    on:
      workflow_call:
        outputs:
          held:
            description: >-
              Held.
            value: ${{ jobs.a.outputs.held }}
    env:
      TOP: ${{ vars.TOP }}
    jobs:
      a:
        if: ${{ github.event_name == 'push' }}
        outputs:
          held: ${{ steps.prepare.outputs.held }}
        steps:
          - uses: ./x
            with:
              # held: ${{ a comment }}
              held: ${{ needs.b.outputs.held }}
              plain: value
              repo: ghcr.io/${{ inputs.org }}
              quoted: "${{ inputs.q }}"  # why
          - with:
              first: ${{ inputs.first }}
            run: |
              echo "${{ inputs.x }}"
              x: ${{ inputs.y }}
    """
)


class YamlMutantsTests(unittest.TestCase):
    def mutants(self, source: str, lines=None):
        lines = set(range(1, source.count("\n") + 2)) if lines is None else lines
        return yaml_mutants(source, lines)

    def test_deletes_and_empties_each_wiring_line_in_with_env_and_outputs(self) -> None:
        self.assertEqual(
            self.mutants(WIRING),
            [
                (7, "yaml/DeleteLine", None),
                (7, "yaml/EmptyValue", '        value: ""'),
                (9, "yaml/DeleteLine", None),
                (9, "yaml/EmptyValue", '  TOP: ""'),
                (14, "yaml/DeleteLine", None),
                (14, "yaml/EmptyValue", '      held: ""'),
                (19, "yaml/DeleteLine", None),
                (19, "yaml/EmptyValue", '          held: ""'),
                (22, "yaml/DeleteLine", None),
                (22, "yaml/EmptyValue", '          quoted: ""'),
                (24, "yaml/DeleteLine", None),
                (24, "yaml/EmptyValue", '          first: ""'),
            ],
        )

    def test_only_the_changed_lines_are_mutated(self) -> None:
        self.assertEqual(
            [(n, op) for n, op, _ in self.mutants(WIRING, {12, 14, 20, 99})],
            [(14, "yaml/DeleteLine"), (14, "yaml/EmptyValue")],
        )

    def test_a_line_outside_the_wiring_blocks_is_not_mutated(self) -> None:
        # An `if:`, a comment, a plain value, an expression inside a longer
        # value, and a line of a `run: |` block are not hand-offs.
        self.assertEqual(self.mutants(WIRING, {12, 18, 20, 21, 26, 27}), [])

    def test_a_value_line_outside_an_outputs_block_is_not_mutated(self) -> None:
        source = "inputs:\n  v:\n    value: ${{ inputs.w }}\nwith:\n  v:\n    value: ${{ a }}\n"
        self.assertEqual(self.mutants(source), [])

    def test_each_kind_of_hand_off_in_the_real_tree_gets_both_mutants(self) -> None:
        # yaml_tests picks this file for both targets, so naming one exact line
        # here would kill that line's mutants whatever the wiring tests check.
        # Every line of each kind is read instead, and one is enough.
        for target, pattern in (
            (
                ".github/actions/prepare-main-akmods/action.yml",
                r" +value: \$\{\{ steps\.resolve\.outputs\.\w+ \}\}",
            ),
            (".github/workflows/build.yml", r" +\w+: \$\{\{ steps\.prepare\.outputs\.\w+ \}\}"),
            (
                ".github/workflows/build.yml",
                r" +\w+: \$\{\{ needs\.build-zfs-akmods\.outputs\.\w+ \}\}",
            ),
        ):
            with self.subTest(target=target, pattern=pattern):
                source = (ROOT / target).read_text(encoding="utf-8")
                rows = source.splitlines()
                numbers = [n for n, row in enumerate(rows, 1) if re.fullmatch(pattern, row)]
                self.assertGreater(len(numbers), 1)
                for number in numbers:
                    self.assertEqual(
                        [op for _, op, _ in yaml_mutants(source, {number})],
                        ["yaml/DeleteLine", "yaml/EmptyValue"],
                    )


class YamlTestsTests(unittest.TestCase):
    def test_a_workflow_is_named_by_its_path_whole_or_in_quoted_parts(self) -> None:
        pattern = naming_pattern(".github/workflows/build.yml")
        for text in (
            'read(".github/workflows/build.yml")',
            'ROOT / ".github" / "workflows" / "build.yml"',
            "ROOT / '.github'/'workflows'/'build.yml'",
        ):
            with self.subTest(text=text):
                self.assertTrue(pattern.search(text))
        for text in (
            '".github/workflows/build.yml.bak"',
            '".github/workflows/build.yaml"',
            '"build.yml"',
            '".github/workflows/build-pr.yml"',
        ):
            with self.subTest(text=text):
                self.assertFalse(pattern.search(text))

    def test_an_action_is_named_by_its_directory(self) -> None:
        pattern = naming_pattern(".github/actions/prepare-main-akmods/action.yml")
        for text in (
            '"./.github/actions/prepare-main-akmods"',
            'ROOT / ".github" / "actions" / "prepare-main-akmods" / "action.yml"',
        ):
            with self.subTest(text=text):
                self.assertTrue(pattern.search(text))
        self.assertFalse(pattern.search('".github/actions/prepare-main-akmods-x"'))
        self.assertFalse(pattern.search('".github/actions/prepare"'))

    def test_a_workflow_is_named_by_its_directory_and_file_name_apart(self) -> None:
        directory = 'WORKFLOW_DIR = ROOT / ".github" / "workflows"\n'
        target = ".github/workflows/build.yml"
        self.assertTrue(names_target(directory + 'read(WORKFLOW_DIR / "build.yml")', target))
        self.assertFalse(names_target(directory + 'read(WORKFLOW_DIR / "build-pr.yml")', target))
        self.assertFalse(names_target(directory + "# build.yml in a comment", target))
        self.assertFalse(names_target('read("build.yml")', target))
        # An action is named by its own directory, not by "action.yml".
        self.assertFalse(
            names_target(
                'ROOT / ".github" / "actions" / "other" / "action.yml"',
                ".github/actions/prepare-main-akmods/action.yml",
            )
        )

    def test_finds_the_test_files_that_read_a_workflow_and_an_action(self) -> None:
        for target, test in (
            (".github/workflows/build.yml", "tests/test_workflow_build_container.py"),
            (
                ".github/actions/prepare-main-akmods/action.yml",
                "tests/test_workflow_build_container.py",
            ),
            (
                ".github/actions/prepare-main-akmods/action.yml",
                "tests/test_action_prepare_main_akmods.py",
            ),
        ):
            with self.subTest(target=target, test=test):
                found = yaml_tests(target)
                self.assertIn(test, found)
                self.assertEqual(list(found), sorted(set(found)))

    def test_the_pathspecs_cover_every_workflow_and_action(self) -> None:
        listed = set(tracked(*WIRING_FILES))
        self.assertIn(".github/workflows/build.yml", listed)
        self.assertIn(".github/actions/prepare-main-akmods/action.yml", listed)


class CheckYamlTests(unittest.TestCase):
    TARGET = ".github/workflows/w.yml"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / self.TARGET
        self.path.parent.mkdir(parents=True)
        self.original = b"jobs:\r\n  a:\n    outputs:\n      x: ${{ steps.s.outputs.x }}\n    y: 1\n"
        self.path.write_bytes(self.original)

    def run_check(self, run_mutant, *, diff="@@ -4 +4 @@\n", tests=("tests/test_w.py",)):
        git = mock.Mock(return_value=diff)
        baseline = mock.Mock()
        found_tests = mock.Mock(return_value=tests)
        with mock.patch.multiple(
            mutation_check,
            ROOT=self.root,
            git=git,
            run_mutant=run_mutant,
            baseline=baseline,
            yaml_tests=found_tests,
        ):
            result = mutation_check.check_yaml(self.TARGET, "origin/main")
        git.assert_called_once_with("diff", "-U0", "origin/main", "--", self.TARGET)
        return result, baseline, found_tests

    def test_runs_the_naming_tests_against_each_mutant_written_in_place(self) -> None:
        seen = []

        def run_mutant(tests):
            seen.append(self.path.read_bytes())
            self.assertEqual(tests, ("tests/test_w.py",))
            return ["killed", "survived"][len(seen) - 1]

        (counts, found), baseline, found_tests = self.run_check(run_mutant)
        self.assertEqual(
            seen,
            [
                b"jobs:\r\n  a:\n    outputs:\n    y: 1\n",
                b'jobs:\r\n  a:\n    outputs:\n      x: ""\n    y: 1\n',
            ],
        )
        baseline.assert_called_once_with(("tests/test_w.py",))
        found_tests.assert_called_once_with(self.TARGET)
        self.assertEqual(counts, {"killed": 1, "survived": 1})
        self.assertEqual(
            found,
            [
                {
                    "module": self.TARGET,
                    "line_number": 4,
                    "operator": "yaml/EmptyValue",
                    "line": "x: ${{ steps.s.outputs.x }}",
                }
            ],
        )
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_restores_the_original_bytes_when_a_run_is_interrupted(self) -> None:
        with self.assertRaises(KeyboardInterrupt):
            self.run_check(mock.Mock(side_effect=KeyboardInterrupt))
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_a_change_with_no_wiring_line_runs_nothing(self) -> None:
        run_mutant = mock.Mock()
        result, baseline, found_tests = self.run_check(run_mutant, diff="@@ -5 +5 @@\n")
        self.assertEqual(result, ({}, []))
        run_mutant.assert_not_called()
        baseline.assert_not_called()
        found_tests.assert_not_called()

    def test_a_wiring_line_in_a_file_no_test_names_is_untested(self) -> None:
        run_mutant = mock.Mock()
        result, baseline, _ = self.run_check(run_mutant, tests=())
        self.assertIsNone(result)
        run_mutant.assert_not_called()
        baseline.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.original)


class ConfigTextTests(unittest.TestCase):
    def values(self, text: str) -> dict[str, object]:
        # Every value written is a JSON scalar or the empty list, so JSON reads it.
        return {
            key: json.loads(value)
            for key, value in re.findall(r"^([\w-]+) = (.+)$", text, flags=re.MULTILINE)
        }

    def test_mutates_the_target_with_its_own_tests_failing_fast(self) -> None:
        text = config_text("shared/oci_layout.py", ("tests/test_a.py", "tests/test_b.py"))
        values = self.values(text)
        self.assertEqual(values["module-path"], "shared/oci_layout.py")
        self.assertEqual(values["timeout"], mutation_check.TIMEOUT_SECONDS)
        self.assertEqual(values["excluded-modules"], [])
        self.assertEqual(values["name"], "local")
        self.assertEqual(
            values["test-command"].split(),
            [
                sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider",
                "tests/test_a.py", "tests/test_b.py",
            ],
        )
        self.assertLess(text.index("[cosmic-ray]"), text.index("module-path"))
        self.assertLess(text.index("test-command"), text.index("[cosmic-ray.distributor]"))
        self.assertLess(text.index("[cosmic-ray.distributor]"), text.index('name = "local"'))


class LedgerTests(unittest.TestCase):
    def test_reads_each_accepted_entry_as_a_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            entry = {"module": "m.py", "operator": "core/AddNot", "line": "if x:", "reason": "r"}
            path.write_text(json.dumps({"accepted": [entry]}))
            self.assertEqual(load_ledger(path), {("m.py", "core/AddNot", "if x:")})

    def test_the_committed_ledger_names_real_live_mutation_sites(self) -> None:
        # An entry whose line has since been edited would quietly accept
        # nothing, and one naming a skipped swap could never match; both
        # are rot a reader would trust.
        data = json.loads(mutation_check.LEDGER.read_text(encoding="utf-8"))
        self.assertTrue(data["description"])
        keys = [(entry["module"], entry["operator"], entry["line"]) for entry in data["accepted"]]
        self.assertEqual(len(keys), len(set(keys)), "a ledger entry is listed twice")
        self.assertEqual(load_ledger(mutation_check.LEDGER), set(keys))
        measured = set(
            tracked(*(f"{directory}/*.py" for directory in MEASURED), HOOK, *WIRING_FILES)
        )
        for entry in data["accepted"]:
            with self.subTest(entry=entry):
                self.assertEqual(set(entry), {"module", "operator", "line", "reason"})
                self.assertIn(entry["module"], measured)
                self.assertTrue(entry["reason"].strip())
                source = (ROOT / entry["module"]).read_text(encoding="utf-8")
                rows = [line.strip() for line in source.splitlines()]
                self.assertIn(entry["line"], rows)
                if entry["module"] == HOOK:
                    # The operator must still be one bash_mutants makes on that line.
                    lines = {n + 1 for n, row in enumerate(rows) if row == entry["line"]}
                    self.assertIn(
                        entry["operator"], {op for _, op, _ in bash_mutants(source, lines)}
                    )
                elif entry["module"].startswith(".github/"):
                    lines = {n + 1 for n, row in enumerate(rows) if row == entry["line"]}
                    self.assertIn(
                        entry["operator"], {op for _, op, _ in yaml_mutants(source, lines)}
                    )
                else:
                    self.assertTrue(kept_operator(entry["operator"]))


class DumpTests(unittest.TestCase):
    def test_keys_each_survivor_by_its_source_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "m.py").write_text("a = 1\n    if a == 1:\n")
            dump = "\n".join(
                [
                    dump_row("core/NumberReplacer", 1, "killed"),
                    dump_row("core/ReplaceComparisonOperator_Eq_NotEq", 2, "survived"),
                    dump_row("core/NumberReplacer", 1, "survived"),
                    dump_row("core/AddNot", 2, None),
                    dump_row("core/AddNot", 2, None, worker="skipped"),
                ]
            )
            self.assertEqual(
                survivors(dump, Path(tmp)),
                [
                    {
                        "module": "m.py",
                        "line_number": 2,
                        "operator": "core/ReplaceComparisonOperator_Eq_NotEq",
                        "line": "if a == 1:",
                    },
                    {
                        "module": "m.py",
                        "line_number": 1,
                        "operator": "core/NumberReplacer",
                        "line": "a = 1",
                    },
                ],
            )

    def test_counts_each_outcome(self) -> None:
        dump = "\n".join(
            [
                dump_row("core/AddNot", 1, "killed"),
                dump_row("core/AddNot", 1, "killed"),
                dump_row("core/AddNot", 1, "survived"),
                dump_row("core/AddNot", 1, None, worker="timeout"),
                dump_row("core/AddNot", 1, None, worker="skipped"),
                dump_row("core/AddNot", 1, None),
            ]
        )
        self.assertEqual(
            outcomes(dump),
            {"killed": 2, "survived": 1, "timeout": 1, "skipped": 1, "not run": 1},
        )


SURVIVOR = {"module": "m.py", "line_number": 4, "operator": "core/AddNot", "line": "x = a | b"}


class ReportTests(unittest.TestCase):

    def test_says_so_when_no_measured_module_changed(self) -> None:
        self.assertEqual(report({}, set()), ("## Mutation check\n\nNo measured module changed.\n", 0))

    def test_lists_each_target_without_its_skipped_count(self) -> None:
        text, new = report(
            {"m.py": ({"killed": 3, "skipped": 90}, []), "n.py": ({"skipped": 5}, [])}, set()
        )
        self.assertEqual(new, 0)
        self.assertIn("- `m.py`: 3 killed\n", text)
        self.assertIn("- `n.py`: no mutant on a changed line\n", text)
        self.assertNotIn("90", text)
        self.assertTrue(text.endswith("No surviving mutant outside the ledger.\n"))

    def test_an_accepted_survivor_is_not_reported(self) -> None:
        text, new = report(
            {"m.py": ({"survived": 1}, [SURVIVOR])}, {("m.py", "core/AddNot", "x = a | b")}
        )
        self.assertEqual(new, 0)
        self.assertIn("No surviving mutant outside the ledger.", text)

    def test_tables_each_new_survivor_and_escapes_its_pipes(self) -> None:
        other = dict(SURVIVOR, operator="core/NumberReplacer")
        text, new = report(
            {"m.py": ({"killed": 1, "survived": 2}, [SURVIVOR, other])},
            {("m.py", "core/NumberReplacer", "x = a | b")},
        )
        self.assertEqual(new, 1)
        self.assertIn("- `m.py`: 1 killed, 2 survived\n", text)
        self.assertIn("1 surviving mutant(s) are not in `.mutation-survivors.json`.", text)
        self.assertIn("| Where | Operator | Line |\n|---|---|---|\n", text)
        self.assertTrue(text.endswith("| `m.py:4` | `core/AddNot` | `x = a \\| b` |\n"))
        self.assertNotIn("NumberReplacer", text)

    def test_a_module_no_test_imports_is_a_finding_on_its_own(self) -> None:
        text, new = report({"m.py": None, "n.py": ({"killed": 1}, [])}, set())
        self.assertEqual(new, 1)
        self.assertIn("- `m.py`: no test file imports it\n", text)
        self.assertIn("- `n.py`: 1 killed\n", text)
        self.assertTrue(text.endswith("so no test can notice a change to them breaking.\n"))
        self.assertNotIn("No surviving mutant", text)

    def test_an_untested_module_and_a_survivor_both_count(self) -> None:
        text, new = report({"m.py": ({"survived": 1}, [SURVIVOR]), "n.py": None}, set())
        self.assertEqual(new, 2)
        self.assertLess(text.index("no test can notice"), text.index("| Where |"))


class CheckTests(unittest.TestCase):
    def test_runs_init_then_skips_then_exec_then_dump(self) -> None:
        calls = []

        def cosmic_ray(*args):
            calls.append(("cosmic-ray", args[0]))
            if args[0] == "dump":
                return dump_row("core/AddNot", 1, "survived", module="shared/oci_layout.py") + "\n"
            return ""

        def git(*args):
            calls.append(("git", args))
            return "@@ -1 +1,2 @@\n"

        def skip(session, lines, spans):
            calls.append(("skip", session.name, lines, bool(spans)))

        tests = ("tests/test_oci_layout.py",)
        def baseline(tests):
            calls.append(("baseline", tests))

        with tempfile.TemporaryDirectory() as tmp, mock.patch.multiple(
            mutation_check, cosmic_ray=cosmic_ray, git=git, skip_unwanted=skip, baseline=baseline
        ):
            counts, found = mutation_check.check(
                "shared/oci_layout.py", tests, "origin/main", Path(tmp)
            )
            config = (Path(tmp) / "shared_oci_layout.py.toml").read_text()
        self.assertEqual(config, config_text("shared/oci_layout.py", tests))
        self.assertEqual(
            calls,
            [
                ("baseline", tests),
                ("cosmic-ray", "init"),
                ("git", ("diff", "-U0", "origin/main", "--", "shared/oci_layout.py")),
                ("skip", "shared_oci_layout.py.sqlite", {1, 2}, True),
                ("cosmic-ray", "exec"),
                ("cosmic-ray", "dump"),
            ],
        )
        self.assertEqual(counts, {"survived": 1})
        self.assertEqual(found[0]["line"], '"""')

    def test_a_failing_baseline_stops_before_any_mutant_runs(self) -> None:
        error = subprocess.CalledProcessError(1, ["pytest"], output="1 failed\n")
        cosmic_ray = mock.Mock()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.multiple(
            mutation_check, cosmic_ray=cosmic_ray, baseline=mock.Mock(side_effect=error)
        ), self.assertRaises(subprocess.CalledProcessError):
            mutation_check.check("shared/oci_layout.py", ("tests/t.py",), "b", Path(tmp))
        cosmic_ray.assert_not_called()

    def test_the_baseline_runs_the_mutants_test_command_unmutated(self) -> None:
        tests = ("tests/test_a.py",)
        with mock.patch.object(subprocess, "run") as run:
            mutation_check.baseline(tests)
        run.assert_called_once_with(
            mutation_check.test_command(tests), check=True, capture_output=True, text=True
        )
        command = json.dumps(" ".join(mutation_check.test_command(tests)))
        self.assertIn(command, config_text("m.py", tests))

    def test_the_baseline_raises_only_when_the_tests_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            passing = Path(tmp) / "test_passes.py"
            passing.write_text("def test_passes():\n    assert True\n")
            failing = Path(tmp) / "test_fails.py"
            failing.write_text("def test_fails():\n    assert False\n")
            mutation_check.baseline((str(passing),))
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                mutation_check.baseline((str(failing),))
        self.assertIn("1 failed", raised.exception.stdout)

    def test_the_process_helpers_run_git_and_cosmic_ray_from_this_interpreter(self) -> None:
        with mock.patch.object(
            subprocess, "run", return_value=SimpleNamespace(stdout="out")
        ) as run:
            self.assertEqual(mutation_check.git("diff", "x"), "out")
            self.assertEqual(mutation_check.cosmic_ray("dump", "s"), "out")
        self.assertEqual(run.call_args_list[0].args[0], ["git", "-C", str(ROOT), "diff", "x"])
        self.assertEqual(
            run.call_args_list[1].args[0], [sys.executable, "-m", "cosmic_ray.cli", "dump", "s"]
        )
        for call in run.call_args_list:
            self.assertEqual(call.kwargs, {"check": True, "capture_output": True, "text": True})


class MainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_main(self, argv, *, changed="", check=None, tests=("tests/test_x.py",)):
        ledger = Path(self.tmp.name) / "ledger.json"
        entry = {"module": "ci_tools/a.py", "operator": "core/AddNot", "line": "ok", "reason": "r"}
        ledger.write_text(json.dumps({"accepted": [entry]}))
        stdout, stderr = io.StringIO(), io.StringIO()
        git = mock.Mock(return_value=changed)
        check = check or mock.Mock()
        with mock.patch.multiple(
            mutation_check, git=git, check=check, importing_tests=mock.Mock(return_value=tests)
        ), mock.patch.object(mutation_check.os, "chdir") as chdir, contextlib.redirect_stdout(
            stdout
        ), contextlib.redirect_stderr(stderr):
            code = mutation_check.main([*argv, "--ledger", str(ledger)])
        return code, stdout.getvalue(), stderr.getvalue(), git, check, chdir

    def test_requires_a_base(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            mutation_check.main([])

    def test_passes_when_no_measured_module_changed(self) -> None:
        code, out, _, git, check, chdir = self.run_main(["--base", "origin/main"])
        self.assertEqual(code, 0)
        self.assertIn("No measured module changed.", out)
        self.assertEqual(
            git.call_args.args,
            (
                "diff", "--name-only", "--diff-filter=d", "origin/main", "--",
                "ci_tools/*.py", "shared/*.py", "containerfiles/zfs-akmods/*.py",
                "files/scripts/*.py", ".claude/hooks/gate-git-diff.sh",
                ".github/workflows/*.yml", ".github/workflows/*.yaml",
                ".github/actions/*/action.yml", ".github/actions/*/action.yaml",
            ),
        )
        check.assert_not_called()
        chdir.assert_called_once_with(mutation_check.ROOT)

    def test_checks_each_changed_module_in_order_and_fails_on_a_new_survivor(self) -> None:
        found = {
            "ci_tools/a.py": [
                {"module": "ci_tools/a.py", "line_number": 1, "operator": "core/AddNot", "line": "ok"}
            ],
            "ci_tools/b.py": [
                {"module": "ci_tools/b.py", "line_number": 9, "operator": "core/AddNot", "line": "new"}
            ],
        }
        check = mock.Mock(side_effect=lambda target, tests, base, wd: ({"survived": 1}, found[target]))
        code, out, _, _, check, _ = self.run_main(
            ["--base", "b"], changed="ci_tools/b.py\nci_tools/a.py\n", check=check
        )
        self.assertEqual(code, 1)
        self.assertEqual(
            [c.args[:3] for c in check.call_args_list],
            [("ci_tools/a.py", ("tests/test_x.py",), "b"), ("ci_tools/b.py", ("tests/test_x.py",), "b")],
        )
        self.assertIn("`ci_tools/b.py:9`", out)
        self.assertNotIn("`ci_tools/a.py:1`", out)

    def test_passes_when_every_survivor_is_accepted(self) -> None:
        found = [{"module": "ci_tools/a.py", "line_number": 1, "operator": "core/AddNot", "line": "ok"}]
        code, out, _, _, _, _ = self.run_main(
            ["--base", "b"],
            changed="ci_tools/a.py\n",
            check=mock.Mock(return_value=({"survived": 1}, found)),
        )
        self.assertEqual(code, 0)
        self.assertIn("No surviving mutant outside the ledger.", out)

    def test_a_changed_hook_gets_the_bash_mutants_and_its_survivors_are_reported(self) -> None:
        found = [{"module": HOOK, "line_number": 7, "operator": "bash/RefuseToNoop", "line": "a|b"}]
        check_hook = mock.Mock(return_value=({"killed": 2, "survived": 1}, found))
        with mock.patch.object(mutation_check, "check_hook", check_hook):
            code, out, _, _, check, _ = self.run_main(
                ["--base", "b"],
                changed=f"ci_tools/a.py\n{HOOK}\n",
                check=mock.Mock(return_value=({"killed": 1}, [])),
            )
        self.assertEqual(code, 1)
        check_hook.assert_called_once_with("b")
        self.assertEqual([c.args[0] for c in check.call_args_list], ["ci_tools/a.py"])
        self.assertIn(f"- `{HOOK}`: 2 killed, 1 survived", out)
        self.assertIn(f"| `{HOOK}:7` | `bash/RefuseToNoop` | `a\\|b` |", out)

    def test_an_accepted_hook_survivor_is_not_reported(self) -> None:
        found = [{"module": HOOK, "line_number": 7, "operator": "bash/RefuseToNoop", "line": "ok"}]
        ledger = Path(self.tmp.name) / "hook-ledger.json"
        entry = {"module": HOOK, "operator": "bash/RefuseToNoop", "line": "ok", "reason": "r"}
        ledger.write_text(json.dumps({"accepted": [entry]}))
        stdout = io.StringIO()
        with mock.patch.multiple(
            mutation_check,
            git=mock.Mock(return_value=f"{HOOK}\n"),
            check_hook=mock.Mock(return_value=({"survived": 1}, found)),
        ), mock.patch.object(mutation_check.os, "chdir"), contextlib.redirect_stdout(stdout):
            code = mutation_check.main(["--base", "b", "--ledger", str(ledger)])
        self.assertEqual(code, 0)
        self.assertIn("No surviving mutant outside the ledger.", stdout.getvalue())

    def test_a_changed_workflow_gets_the_yaml_mutants_and_its_survivors_are_reported(
        self,
    ) -> None:
        workflow = ".github/workflows/build.yml"
        action = ".github/actions/prepare-main-akmods/action.yml"
        found = [
            {"module": workflow, "line_number": 9, "operator": "yaml/DeleteLine", "line": "a: b"}
        ]
        results = {workflow: ({"killed": 1, "survived": 1}, found), action: None}
        check_yaml = mock.Mock(side_effect=lambda target, base: results[target])
        with mock.patch.object(mutation_check, "check_yaml", check_yaml):
            code, out, _, _, check, _ = self.run_main(
                ["--base", "b"], changed=f"{workflow}\n{action}\n"
            )
        self.assertEqual(code, 1)
        self.assertEqual(
            [c.args for c in check_yaml.call_args_list], [(action, "b"), (workflow, "b")]
        )
        check.assert_not_called()
        self.assertIn(f"- `{workflow}`: 1 killed, 1 survived", out)
        self.assertIn(f"| `{workflow}:9` | `yaml/DeleteLine` | `a: b` |", out)
        self.assertIn(f"- `{action}`: no test file names it", out)

    def test_an_accepted_yaml_survivor_is_not_reported(self) -> None:
        workflow = ".github/workflows/build.yml"
        found = [
            {"module": workflow, "line_number": 9, "operator": "yaml/EmptyValue", "line": "ok"}
        ]
        ledger = Path(self.tmp.name) / "yaml-ledger.json"
        entry = {"module": workflow, "operator": "yaml/EmptyValue", "line": "ok", "reason": "r"}
        ledger.write_text(json.dumps({"accepted": [entry]}))
        stdout = io.StringIO()
        with mock.patch.multiple(
            mutation_check,
            git=mock.Mock(return_value=f"{workflow}\n"),
            check_yaml=mock.Mock(return_value=({"survived": 1}, found)),
        ), mock.patch.object(mutation_check.os, "chdir"), contextlib.redirect_stdout(stdout):
            code = mutation_check.main(["--base", "b", "--ledger", str(ledger)])
        self.assertEqual(code, 0)
        self.assertIn("No surviving mutant outside the ledger.", stdout.getvalue())

    def test_a_changed_module_no_test_imports_fails_without_running_cosmic_ray(self) -> None:
        code, out, _, _, check, _ = self.run_main(
            ["--base", "b"], changed="ci_tools/a.py\n", tests=()
        )
        self.assertEqual(code, 1)
        check.assert_not_called()
        self.assertIn("- `ci_tools/a.py`: no test file imports it", out)

    def test_a_failing_cosmic_ray_command_exits_2_with_its_stderr(self) -> None:
        error = subprocess.CalledProcessError(
            1, ["python", "-m", "cosmic_ray.cli", "exec"], stderr="boom\n"
        )
        code, out, err, _, _, _ = self.run_main(
            ["--base", "b"], changed="ci_tools/a.py\n", check=mock.Mock(side_effect=error)
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err, "mutation check failed: python -m cosmic_ray.cli exec\nboom\n\n")

    def test_a_failing_baseline_exits_2_with_the_pytest_output(self) -> None:
        error = subprocess.CalledProcessError(1, ["python", "-m", "pytest"], output="1 failed\n")
        code, out, err, _, _, _ = self.run_main(
            ["--base", "b"], changed="ci_tools/a.py\n", check=mock.Mock(side_effect=error)
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err, "mutation check failed: python -m pytest\n1 failed\n\n")

    def test_a_module_the_change_deleted_is_not_checked(self) -> None:
        with tempfile.TemporaryDirectory() as repo:

            def run(*args: str) -> None:
                subprocess.run(
                    ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", *args],
                    check=True,
                    capture_output=True,
                )

            run("init", "-q")
            (Path(repo) / "shared").mkdir()
            (Path(repo) / "shared" / "gone.py").write_text("x = 1\n")
            run("add", ".")
            run("commit", "-qm", "base")
            run("rm", "-q", "shared/gone.py")
            run("commit", "-qm", "delete the module")
            with mock.patch.object(mutation_check, "ROOT", Path(repo)):
                listed = mutation_check.git(
                    "diff", "--name-only", "--diff-filter=d", "HEAD~1", "--", "shared/*.py"
                )
                plain = mutation_check.git("diff", "--name-only", "HEAD~1", "--", "shared/*.py")
            self.assertEqual(plain, "shared/gone.py\n")
            self.assertEqual(listed, "")


class MeasuredTests(unittest.TestCase):
    def test_mutates_exactly_the_paths_test_yml_measures_for_coverage(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        self.assertEqual(MEASURED, tuple(re.findall(r"--cov=(\S+)", workflow)))

    def test_the_test_yml_job_runs_this_script_against_the_base_branch(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        self.assertIn("python3 tests/mutation_check.py --base \"origin/$BASE_REF\"", workflow)
        self.assertIn("BASE_REF: ${{ github.base_ref }}", workflow)


if __name__ == "__main__":
    unittest.main()
