"""
Script: tests/test_composite_actions_are_executed.py
What: Requires every composite action under .github/actions/ that carries shell to be read by a
tests/test_action_*.py file, apart from a short waiver list naming the ports still to land.
Doing: Finds each `action.yml` with at least one `run:` step, then looks for the action's path
(`.github/actions/<name>`) in the text of every tests/test_action_*.py. An action with shell and
no such file fails unless it is waived; a waived action that has gained a test fails, and so does
a waiver naming an action that does not exist or has no shell.
Why: Coverage tools do not measure action YAML, and tests/check_coverage.py leaves it out of its
manifest by design, so an action whose shell no test runs shows up in no number anywhere. That
is how #219 shipped: the akmods build step handed the ZFS version over under a name the build no
longer reads, and putting the bug back still passed every test. The sister repository had met
the same gap one action at a time, five times. The gate was approved on #227.
Goal: A new composite action, or an existing one whose test file is deleted, fails here instead
of reaching build.yml untested.

This is a presence check, not proof of coverage: it holds that a test file names the action, not
that it runs every step. The test_action_*.py files are what run the steps; this file only makes
sure each action has one. Shell is found with a line pattern rather than PyYAML, so the gate runs
with nothing installed; where PyYAML is present -- CI installs it by name -- one more test holds
the pattern to what YAML itself reads from every real action.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only where PyYAML is absent
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTIONS_DIR = REPO_ROOT / ".github" / "actions"
TESTS_DIR = REPO_ROOT / "tests"
ACTION_TEST_GLOB = "test_action_*.py"

# Actions with shell whose test file has not been ported from Danathar/zfs-kinoite-complex yet,
# each with the issue that ports it. The port removes its entries in the same pull request: an
# entry left behind once a test names the action fails test_no_waived_action_has_a_test.
WAIVED_ACTIONS: dict[str, int] = {}

# A `run:` key at the start of a line, either opening a step (`- run:`) or inside one. Anchoring
# on the line start keeps out comments and `runs:`.
RUN_KEY = re.compile(r"^[ \t]*(?:-[ \t]+)?run[ \t]*:", re.MULTILINE)


def has_shell(action_text: str) -> bool:
    return RUN_KEY.search(action_text) is not None


def references_action(test_text: str, name: str) -> bool:
    # The lookahead stops `prepare-main-akmods` matching a path to `prepare-main-akmods-v2`.
    return re.search(r"\.github/actions/" + re.escape(name) + r"(?![\w-])", test_text) is not None


def action_dirs() -> list[Path]:
    return sorted(path for path in ACTIONS_DIR.iterdir() if path.is_dir())


def actions_with_shell() -> list[str]:
    return [
        path.name
        for path in action_dirs()
        if has_shell((path / "action.yml").read_text(encoding="utf-8"))
    ]


def action_test_texts() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(TESTS_DIR.glob(ACTION_TEST_GLOB))
    }


def files_reading(name: str, texts: dict[str, str]) -> list[str]:
    return [file_name for file_name, text in texts.items() if references_action(text, name)]


class CompositeActionsAreExecutedTests(unittest.TestCase):
    def test_the_actions_directory_holds_actions(self) -> None:
        # A moved or renamed directory would otherwise make every check below pass on nothing.
        dirs = action_dirs()
        self.assertTrue(dirs, f"no composite actions found under {ACTIONS_DIR}")
        for path in dirs:
            with self.subTest(action=path.name):
                self.assertTrue(
                    (path / "action.yml").is_file(),
                    f"{path.relative_to(REPO_ROOT)} has no action.yml; this gate reads only that "
                    "name, so an action.yaml would escape it",
                )

    def test_every_action_with_shell_is_read_by_a_test_or_waived(self) -> None:
        texts = action_test_texts()
        for name in actions_with_shell():
            if name in WAIVED_ACTIONS:
                continue
            with self.subTest(action=name):
                self.assertTrue(
                    files_reading(name, texts),
                    f".github/actions/{name}/action.yml has run: steps, but no "
                    f"tests/{ACTION_TEST_GLOB} references .github/actions/{name}. Add a test "
                    "that executes its shell.",
                )

    def test_no_waived_action_has_a_test(self) -> None:
        texts = action_test_texts()
        for name, issue in WAIVED_ACTIONS.items():
            with self.subTest(action=name):
                self.assertEqual(
                    files_reading(name, texts),
                    [],
                    f"{name} is waived for #{issue} but a test now references it; remove its "
                    "entry from WAIVED_ACTIONS",
                )

    def test_every_waiver_names_an_action_with_shell(self) -> None:
        names = {path.name for path in action_dirs()}
        with_shell = set(actions_with_shell())
        for name, issue in WAIVED_ACTIONS.items():
            with self.subTest(action=name):
                self.assertIn(
                    name, names, f"WAIVED_ACTIONS names {name} (#{issue}), which does not exist"
                )
                self.assertIn(
                    name,
                    with_shell,
                    f"WAIVED_ACTIONS names {name} (#{issue}), which has no run: steps to test",
                )


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class PatternAgreesWithYamlTests(unittest.TestCase):
    def test_the_pattern_finds_shell_where_yaml_does(self) -> None:
        for path in action_dirs():
            text = (path / "action.yml").read_text(encoding="utf-8")
            steps = (yaml.safe_load(text).get("runs") or {}).get("steps") or []
            with self.subTest(action=path.name):
                self.assertEqual(has_shell(text), any("run" in step for step in steps))


class HelperTests(unittest.TestCase):
    def test_a_run_key_is_found_opening_a_step_and_inside_one(self) -> None:
        self.assertTrue(has_shell("    steps:\n      - run: echo hi\n"))
        self.assertTrue(has_shell("      - name: x\n        shell: bash\n        run: |\n"))

    def test_runs_and_a_commented_run_are_not_shell(self) -> None:
        self.assertFalse(has_shell("runs:\n  using: composite\n  steps:\n    - uses: a/b@c\n"))
        self.assertFalse(has_shell("    # run: echo hi\n"))

    def test_an_action_path_must_end_at_the_name(self) -> None:
        name = "load-ci-defaults"
        self.assertTrue(references_action(f"see .github/actions/{name}/action.yml", name))
        self.assertTrue(references_action(f"`.github/actions/{name}`.", name))
        self.assertFalse(references_action(f".github/actions/{name}-v2", name))
        self.assertFalse(references_action(name, name))


if __name__ == "__main__":
    unittest.main()
