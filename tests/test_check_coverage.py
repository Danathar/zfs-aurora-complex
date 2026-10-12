"""
Script: tests/test_check_coverage.py
What: Tests for the per-module coverage floor gate.
Doing: Drives load_manifest, load_covered_counts, shipped_executables, evaluate, and main over hand-built reports and manifests.
Why: A gate that silently passes is worse than no gate, because it reads as evidence.
Goal: Pin each way the gate is meant to fail, and the message it fails with.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from check_coverage import (
    CoverageGateError,
    evaluate,
    load_covered_counts,
    load_manifest,
    main,
    shipped_executables,
)


def _write(directory: Path, name: str, document: object) -> Path:
    path = directory / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _manifest(floors: dict[str, int], unmeasured: dict[str, str] | None = None) -> dict:
    document: dict = {"floors": floors}
    if unmeasured is not None:
        document["unmeasured"] = unmeasured
    return document


def _report(files: dict[str, tuple[int, int]]) -> dict[str, object]:
    return {
        "files": {
            path: {"summary": {"covered_lines": covered, "num_statements": total}}
            for path, (covered, total) in files.items()
        }
    }


class LoadManifestTests(unittest.TestCase):
    def test_reads_integer_floors_and_reasoned_unmeasured_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write(
                Path(temp_dir),
                "thresholds.json",
                _manifest({"a.py": 12, "b.py": 0}, {"Containerfile": "runs only in a build"}),
            )
            floors, unmeasured = load_manifest(path)
            self.assertEqual(floors, {"a.py": 12, "b.py": 0})
            self.assertEqual(unmeasured, {"Containerfile": "runs only in a build"})

    def test_unmeasured_is_optional(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write(Path(temp_dir), "thresholds.json", _manifest({"a.py": 1}))
            self.assertEqual(load_manifest(path)[1], {})

    def test_a_missing_file_is_a_gate_error_naming_the_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "absent.json"
            with self.assertRaises(CoverageGateError) as caught:
                load_manifest(missing)
            self.assertIn(str(missing), str(caught.exception))

    def test_an_empty_floors_object_is_rejected(self) -> None:
        # An empty manifest would otherwise pass every check while gating
        # nothing at all -- the failure mode this whole file exists to prevent.
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write(Path(temp_dir), "thresholds.json", _manifest({}))
            with self.assertRaises(CoverageGateError) as caught:
                load_manifest(path)
            self.assertIn("non-empty 'floors'", str(caught.exception))

    def test_a_non_integer_floor_is_rejected_rather_than_compared(self) -> None:
        for floor in ("80", 80.5, None, True):
            with self.subTest(floor=floor), tempfile.TemporaryDirectory() as temp_dir:
                path = _write(Path(temp_dir), "thresholds.json", _manifest({"a.py": floor}))
                with self.assertRaises(CoverageGateError) as caught:
                    load_manifest(path)
                self.assertIn("non-negative integer", str(caught.exception))

    def test_an_unmeasured_entry_with_no_reason_is_rejected(self) -> None:
        # Listing a file as unmeasured and saying nothing records no decision;
        # it only silences the check. This is the whole value of the section.
        for reason in ("", "   ", None, 5):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temp_dir:
                path = _write(
                    Path(temp_dir), "thresholds.json", _manifest({"a.py": 1}, {"x.sh": reason})
                )
                with self.assertRaises(CoverageGateError) as caught:
                    load_manifest(path)
                self.assertIn("states no reason", str(caught.exception))

    def test_a_file_in_both_sections_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write(
                Path(temp_dir), "thresholds.json", _manifest({"a.py": 1}, {"a.py": "because"})
            )
            with self.assertRaises(CoverageGateError) as caught:
                load_manifest(path)
            self.assertIn("appears in both", str(caught.exception))

    def test_invalid_json_is_reported_as_such(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "thresholds.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(CoverageGateError) as caught:
                load_manifest(path)
            self.assertIn("not valid JSON", str(caught.exception))


class LoadCoveredCountsTests(unittest.TestCase):
    def test_reads_covered_and_total_statements(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write(Path(temp_dir), "coverage.json", _report({"a.py": (5, 7)}))
            self.assertEqual(load_covered_counts(path), {"a.py": (5, 7)})

    def test_a_report_measuring_nothing_is_a_gate_error(self) -> None:
        # A coverage run that measured no files produces a report the gate would
        # otherwise read as "no floors to check", and pass.
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write(Path(temp_dir), "coverage.json", {"files": {}})
            with self.assertRaises(CoverageGateError) as caught:
                load_covered_counts(path)
            self.assertIn("measured nothing", str(caught.exception))

    def test_a_missing_report_says_how_to_produce_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(CoverageGateError) as caught:
                load_covered_counts(Path(temp_dir) / "coverage.json")
            self.assertIn("--cov-report=json", str(caught.exception))


class EvaluateTests(unittest.TestCase):
    def test_a_module_at_its_floor_passes(self) -> None:
        failures, passes, raisable = evaluate({"a.py": 5}, {"a.py": (5, 10)})
        self.assertEqual(failures, [])
        self.assertEqual(raisable, [])
        self.assertEqual(len(passes), 1)
        self.assertIn("5/10 statements (50%), floor 5", passes[0])

    def test_a_module_below_its_floor_fails_and_says_by_how_much(self) -> None:
        failures, passes, _ = evaluate({"a.py": 9}, {"a.py": (5, 10)})
        self.assertEqual(passes, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("floor is 9", failures[0])
        self.assertIn("4 fewer statements are reached", failures[0])

    def test_a_measured_module_with_no_floor_fails(self) -> None:
        # New code arriving without a recorded decision. This is the direction
        # that keeps the manifest complete as the repository grows.
        failures, _, _ = evaluate({"a.py": 1}, {"a.py": (1, 1), "b.py": (0, 4)})
        self.assertEqual(len(failures), 1)
        self.assertIn("b.py", failures[0])
        self.assertIn("has no floor", failures[0])

    def test_a_floor_for_an_unmeasured_module_fails(self) -> None:
        # The other direction: the manifest cannot rot after a rename or a
        # deletion, which would otherwise leave a floor nothing enforces.
        failures, _, _ = evaluate({"gone.py": 3}, {"a.py": (1, 1)})
        self.assertEqual(len(failures), 2)
        self.assertTrue(any("gone.py" in line and "did not measure it" in line for line in failures))

    def test_coverage_above_the_floor_is_reported_as_raisable_but_still_passes(self) -> None:
        failures, passes, raisable = evaluate({"a.py": 5}, {"a.py": (8, 10)})
        self.assertEqual(failures, [])
        self.assertEqual(len(passes), 1)
        self.assertEqual(raisable, ["a.py: floor 5, reached 8"])

    def test_a_shipped_file_in_neither_section_fails(self) -> None:
        # The hole this section exists to close: a file that ships and executes
        # but has had no decision recorded about it at all.
        failures, _, _ = evaluate(
            {"a.py": 1},
            {"a.py": (1, 1)},
            unmeasured={},
            shipped={"a.py", "build_files/build-image.sh"},
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("build_files/build-image.sh", failures[0])
        self.assertIn("neither 'floors' nor 'unmeasured'", failures[0])

    def test_an_unmeasured_entry_the_run_actually_measured_fails(self) -> None:
        # It should carry a floor instead. Left as-is it would silence a file
        # the suite genuinely reaches.
        failures, _, _ = evaluate(
            {"a.py": 1},
            {"a.py": (1, 1), "b.py": (4, 5)},
            unmeasured={"b.py": "cannot be measured"},
            shipped={"a.py", "b.py"},
        )
        self.assertTrue(any("move it to 'floors' with that number" in f for f in failures))

    def test_a_stale_unmeasured_entry_fails(self) -> None:
        failures, _, _ = evaluate(
            {"a.py": 1},
            {"a.py": (1, 1)},
            unmeasured={"deleted.sh": "was never measurable"},
            shipped={"a.py"},
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("not a tracked shipped file", failures[0])

    def test_a_reasoned_unmeasured_entry_passes_and_shows_the_reason(self) -> None:
        failures, passes, _ = evaluate(
            {"a.py": 1},
            {"a.py": (1, 1)},
            unmeasured={"Containerfile": "runs only inside an image build"},
            shipped={"a.py", "Containerfile"},
        )
        self.assertEqual(failures, [])
        self.assertTrue(
            any("unmeasured by decision -- runs only inside an image build" in p for p in passes)
        )

    def test_completeness_is_not_checked_when_no_shipped_set_is_given(self) -> None:
        # --skip-completeness, and the floor-only tests above, rely on this.
        failures, _, _ = evaluate({"a.py": 1}, {"a.py": (1, 1)})
        self.assertEqual(failures, [])

    def test_a_module_with_no_statements_is_reported_as_fully_covered(self) -> None:
        # An __init__.py has zero statements; a naive percentage would divide by
        # zero rather than report it.
        failures, passes, _ = evaluate({"pkg/__init__.py": 0}, {"pkg/__init__.py": (0, 0)})
        self.assertEqual(failures, [])
        self.assertIn("(100%)", passes[0])


class MainExitStatusTests(unittest.TestCase):
    def _paths(self, temp_dir: str, floors: dict[str, int], files: dict[str, tuple[int, int]]):
        directory = Path(temp_dir)
        return [
            "--thresholds",
            str(_write(directory, "thresholds.json", _manifest(floors))),
            "--coverage-report",
            str(_write(directory, "coverage.json", _report(files))),
            "--skip-completeness",
        ]

    def test_exits_zero_when_every_floor_holds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            argv = self._paths(temp_dir, {"a.py": 5}, {"a.py": (5, 10)})
            self.assertEqual(main(argv), 0)

    def test_exits_one_when_a_floor_fails(self) -> None:
        # 1, not 2: the gate ran and the repository failed it. The distinction
        # matters to whoever reads the job log.
        with tempfile.TemporaryDirectory() as temp_dir:
            argv = self._paths(temp_dir, {"a.py": 9}, {"a.py": (5, 10)})
            self.assertEqual(main(argv), 1)

    def test_exits_two_when_the_gate_cannot_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            argv = [
                "--thresholds",
                str(Path(temp_dir) / "absent.json"),
                "--coverage-report",
                str(Path(temp_dir) / "also-absent.json"),
            ]
            self.assertEqual(main(argv), 2)

    def test_an_untracked_module_beside_the_script_is_not_imported(self) -> None:
        # An agent permission config may run this file without a prompt. Python puts the
        # script's directory first on sys.path, so without the trim at the top of
        # check_coverage.py an untracked tests/json.py -- or tests/__future__.py,
        # which is why the file has no __future__ import -- runs as the stdlib.
        source = Path(__file__).resolve().parent / "check_coverage.py"
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            shutil.copy(source, directory / "check_coverage.py")
            marker = directory / "ran"
            for name in ("json", "argparse", "subprocess", "pathlib", "__future__"):
                (directory / f"{name}.py").write_text(
                    f"open({str(marker)!r}, 'a').write({name!r})\n", encoding="utf-8"
                )
            result = subprocess.run(
                [sys.executable, str(directory / "check_coverage.py"), "--help"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists(), marker.read_text() if marker.exists() else "")


class RecordedManifestTests(unittest.TestCase):
    """
    The checked-in manifest against the checked-in tree.

    These run in the ordinary suite, so a shipped file added without a decision
    fails here as well as in the CI gate step -- which matters because the gate
    step needs a coverage run and this does not.
    """

    REPO_ROOT = Path(__file__).resolve().parent.parent

    def test_the_manifest_parses_and_records_both_kinds_of_decision(self) -> None:
        floors, unmeasured = load_manifest(self.REPO_ROOT / ".coverage-thresholds.json")
        self.assertGreater(len(floors), 1)
        self.assertIn("ci_tools/common.py", floors)
        # The two files that ship and execute but cannot be measured from the
        # host. If this ever becomes empty, the section has stopped doing
        # anything and the completeness check below is the only thing left.
        self.assertIn("Containerfile", unmeasured)
        self.assertIn("build_files/build-image.sh", unmeasured)

    def test_every_shipped_file_in_this_tree_has_a_recorded_decision(self) -> None:
        floors, unmeasured = load_manifest(self.REPO_ROOT / ".coverage-thresholds.json")
        shipped = shipped_executables(self.REPO_ROOT)

        self.assertEqual(
            shipped - set(floors) - set(unmeasured),
            set(),
            "ships and executes but has no entry in .coverage-thresholds.json",
        )
        self.assertEqual(
            set(unmeasured) - shipped,
            set(),
            "listed as unmeasured but is not a tracked shipped file",
        )

    def test_shipped_executables_finds_the_files_it_should_and_skips_tests(self) -> None:
        shipped = shipped_executables(self.REPO_ROOT)

        self.assertIn("Containerfile", shipped)
        self.assertIn("build_files/build-image.sh", shipped)
        self.assertIn("ci_tools/cli.py", shipped)
        # tests/ is excluded: test files are not shipped, and this file would
        # otherwise demand a decision about itself.
        self.assertNotIn("tests/check_coverage.py", shipped)
        self.assertFalse({path for path in shipped if path.startswith("tests/")})
        # Workflow YAML is deliberately out of scope; see the note in
        # check_coverage.py on why.
        self.assertFalse({path for path in shipped if path.endswith((".yml", ".yaml"))})

    def test_repo_root_does_not_run_the_targets_fsmonitor_program(self) -> None:
        # check_coverage.py is allow-listed to run unattended, and its
        # --repo-root feeds `git ls-files`, which runs the program a repo names
        # in core.fsmonitor. A tree whose config sets core.fsmonitor to a script
        # must not make shipped_executables run that script -- that would be
        # code execution under the allow rule with no prompt.
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)

            def git(*args: str) -> None:
                subprocess.run(
                    ["git", "-C", str(repo), *args],
                    check=True,
                    capture_output=True,
                    text=True,
                )

            git("init", "-q")
            git("config", "user.email", "t@example.com")
            git("config", "user.name", "t")
            (repo / "ship.sh").write_text("echo hi\n", encoding="utf-8")
            git("add", "ship.sh")
            git("commit", "-qm", "seed")

            marker = repo / "FSMONITOR_RAN"
            helper = repo / "fsmonitor.sh"
            # A valid fsmonitor v2 reply (a NUL-terminated empty token) so git
            # is satisfied; the touch is the observable side effect.
            helper.write_text(
                f'#!/bin/sh\ntouch "{marker}"\nprintf "%s\\0" ""\n',
                encoding="utf-8",
            )
            helper.chmod(0o755)
            git("config", "core.fsmonitor", str(helper))

            shipped = shipped_executables(repo)

            self.assertIn("ship.sh", shipped)
            self.assertFalse(
                marker.exists(),
                "git ls-files ran the target repo's core.fsmonitor program",
            )


if __name__ == "__main__":
    unittest.main()
