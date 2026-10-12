"""
Script: tests/test_auto_qa_tuning.py
What: Keeps .github/auto-qa-tuning.json in step with the workflow files it describes.
Doing: Reads every `timeout-minutes:` out of .github/workflows/ and compares both directions against the declared jobs.
Why: A timeout changed in YAML leaves the declaration stale, and nothing else would notice.
Goal: Make the tuning file a description of the workflows rather than a snapshot of them.

The declarations are only worth having if they are true. A stale entry is worse
than no entry: it reports headroom against a cap that is not the cap.

Parsed with a small regex rather than PyYAML, for the reason
tests/test_workflow_build_container.py gives at length -- the CI job installs
only pytest, pytest-cov and ruff, so a PyYAML import would depend on whatever
the runner image happens to ship, and would skip silently the day it does not.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
TUNING_PATH = REPO_ROOT / ".github" / "auto-qa-tuning.json"

# A job key is two-space indented; `timeout-minutes:` is four-space indented
# inside it. Anchoring on the indentation is what keeps a `timeout-minutes`
# belonging to a step from being read as the job's.
JOB_RE = re.compile(r"^  (?P<job>[A-Za-z0-9_-]+):\s*$")
TIMEOUT_RE = re.compile(r"^    timeout-minutes:\s*(?P<minutes>\d+)\s*$")


def workflow_files() -> list[Path]:
    """Every workflow file, both suffixes GitHub accepts."""

    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


def declared_timeouts() -> dict[tuple[str, str], int]:
    """Return `{(workflow, job): minutes}` as recorded in the tuning file."""

    document = json.loads(TUNING_PATH.read_text(encoding="utf-8"))
    return {
        (entry["workflow"], entry["job"]): entry["timeout_minutes"]
        for entry in document["jobs"]
    }


def actual_timeouts() -> dict[tuple[str, str], int]:
    """Return `{(workflow, job): minutes}` as written in the workflow files."""

    found: dict[tuple[str, str], int] = {}
    # Both suffixes: GitHub accepts `.yaml`, and globbing only `.yml` would let
    # a whole workflow file escape every completeness check below while they
    # all stayed green.
    for path in sorted(workflow_files()):
        job = None
        for line in path.read_text(encoding="utf-8").splitlines():
            job_match = JOB_RE.match(line)
            if job_match:
                job = job_match.group("job")
                continue
            timeout_match = TIMEOUT_RE.match(line)
            if timeout_match and job is not None:
                found[(path.name, job)] = int(timeout_match.group("minutes"))
    return found


class AutoQaTuningTests(unittest.TestCase):
    def test_the_parser_finds_something_to_check(self) -> None:
        # Guard the guard. Every assertion below compares two dicts, and two
        # empty dicts are equal -- so a regex that stopped matching would turn
        # this whole file green rather than red.
        actual = actual_timeouts()
        self.assertGreater(len(actual), 3, f"implausibly few timeouts parsed: {actual}")
        self.assertIn(("nightly-compliance.yml", "suite"), actual)

    def test_every_workflow_timeout_is_declared(self) -> None:
        missing = sorted(set(actual_timeouts()) - set(declared_timeouts()))
        self.assertEqual(
            missing,
            [],
            "these jobs set timeout-minutes but are absent from "
            ".github/auto-qa-tuning.json; add them so the declaration stays complete",
        )

    def test_every_declaration_still_names_a_real_job(self) -> None:
        stale = sorted(set(declared_timeouts()) - set(actual_timeouts()))
        self.assertEqual(
            stale,
            [],
            "these entries in .github/auto-qa-tuning.json name a workflow job that no "
            "longer sets a timeout; remove them so the file cannot rot after a rename",
        )

    def test_declared_values_match_the_workflow_files(self) -> None:
        declared = declared_timeouts()
        actual = actual_timeouts()
        mismatched = {
            key: (declared[key], actual[key])
            for key in set(declared) & set(actual)
            if declared[key] != actual[key]
        }
        self.assertEqual(
            mismatched,
            {},
            "declared timeout does not match the workflow file (declared, actual); "
            "the tuning file reports headroom against a cap that is not the cap",
        )

    def test_the_policy_is_report_only(self) -> None:
        # Nothing in this repository rewrites a threshold on a schedule. If that
        # ever changes it should be a deliberate edit that fails here first.
        policy = json.loads(TUNING_PATH.read_text(encoding="utf-8"))["policy"]
        self.assertEqual(policy["direction"], "report-only")
        self.assertEqual(policy["statistic"], "max")
        self.assertGreater(policy["at_risk_ratio"], 0)
        self.assertLessEqual(policy["at_risk_ratio"], 1)


if __name__ == "__main__":
    unittest.main()
