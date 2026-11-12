"""
Script: tests/test_workflow_agent_signature.py
What: Tests .github/workflows/agent-signature.yml -- which open pull requests it checks, and which
descriptions it passes -- and holds its signature test to the one in agent-audit.yml.
Doing: Reads both workflows with PyYAML. Compares the `signature_def` each step assigns, then runs
the new step's shell body under `bash -e` with PR_BODY set to each description below.
Why: The open-pull-request check exists so the monthly audit finds nothing to fail. That only
holds while both ask the same question: a copy that drifted would pass a description the audit
later fails, or fail one it accepts. The trigger, the token and the way the description reaches
the shell are the rest of what makes a `pull_request` job reading untrusted text safe, and each is
a one-line edit away from not being.
Goal: Make a drifted signature test, a widened token, a `pull_request_target` trigger, a
description interpolated into the script, or a check that passes an unsigned description fail
here.

This runs the workflow's own text, as tests/test_workflow_agent_audit.py does, and the
descriptions mirror that file's signature cases, so the same input is judged the same way by
both workflows.

PyYAML is guarded for the reason tests/test_workflow_nightly_compliance.py gives.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only where PyYAML is absent
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
WORKFLOW_PATH = WORKFLOWS / "agent-signature.yml"
AUDIT_PATH = WORKFLOWS / "agent-audit.yml"
RULESET = REPO_ROOT / ".github" / "rulesets" / "main.json"
STEP = "Look for the signature line in the description"
JOB_NAME = "Check the agent signature line"

# A step with no `shell:` key runs under `bash -e {0}`: no pipefail unless the body sets it.
GITHUB_BASH = ["bash", "--noprofile", "--norc", "-e"]

SIGNATURE = "— hive: agent=quality backend=claude model=claude-opus-5-5"
DEFINITION_RE = re.compile(r"^signature_def='([^']*)'$", re.MULTILINE)


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(workflow: dict) -> list[dict]:
    return [step for job in workflow["jobs"].values() for step in job["steps"]]


def _definition(run: str) -> str:
    found = DEFINITION_RE.findall(run)
    if len(found) != 1:
        raise AssertionError(f"expected one signature_def assignment, found {len(found)}")
    return found[0]


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class WorkflowShapeTests(unittest.TestCase):
    """The trigger, the token and the job condition."""

    def setUp(self) -> None:
        self.workflow = _load(WORKFLOW_PATH)
        self.job = self.workflow["jobs"]["signature"]

    def test_it_runs_on_pull_request_only_including_an_edited_description(self) -> None:
        # PyYAML reads the bare `on:` key as the boolean True.
        on = self.workflow.get("on", self.workflow.get(True))
        self.assertEqual(list(on), ["pull_request"])
        self.assertEqual(
            sorted(on["pull_request"]["types"]), ["edited", "opened", "reopened", "synchronize"]
        )

    def test_the_job_has_no_token_permissions_and_runs_no_action(self) -> None:
        self.assertNotIn("permissions", self.workflow)
        self.assertEqual(self.job["permissions"], {})
        self.assertEqual(list(self.workflow["jobs"]), ["signature"])
        self.assertEqual(self.job["name"], JOB_NAME)
        for step in self.job["steps"]:
            self.assertNotIn("uses", step)

    def test_it_checks_the_pull_requests_the_hive_app_opened(self) -> None:
        # The audit fails a missing line only on these; this repository has no `ai-fix/` path.
        self.assertEqual(
            self.job["if"], "github.event.pull_request.user.login == 'danathar-atomic-hive[bot]'"
        )

    def test_the_description_reaches_the_script_only_through_the_environment(self) -> None:
        (step,) = self.job["steps"]
        self.assertEqual(step["name"], STEP)
        self.assertEqual(step["env"], {"PR_BODY": "${{ github.event.pull_request.body }}"})
        self.assertNotIn("${{", step["run"])

    def test_it_is_advisory_not_required_by_the_ruleset(self) -> None:
        self.assertNotIn(JOB_NAME, RULESET.read_text(encoding="utf-8"))


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class SameSignatureTestAsTheAuditTests(unittest.TestCase):
    def test_the_definition_is_the_audits_word_for_word(self) -> None:
        audit = [
            step["run"] for step in _steps(_load(AUDIT_PATH)) if "signature_def=" in step["run"]
        ]
        self.assertEqual(len(audit), 1)
        (step,) = self.job_steps()
        self.assertEqual(_definition(step["run"]), _definition(audit[0]))

    def test_the_definition_is_the_one_the_issue_names(self) -> None:
        # Guards the comparison above against both sides losing the test together.
        (step,) = self.job_steps()
        self.assertIn('test("^— hive: [a-z_]+=")', _definition(step["run"]))

    def job_steps(self) -> list[dict]:
        return _load(WORKFLOW_PATH)["jobs"]["signature"]["steps"]


@unittest.skipIf(yaml is None, "PyYAML is not installed")
@unittest.skipIf(shutil.which("bash") is None, "bash is not installed")
@unittest.skipIf(shutil.which("jq") is None, "jq is not installed")
class SignatureStepTests(unittest.TestCase):
    """Executes the step's shell body with each description."""

    def setUp(self) -> None:
        (step,) = _load(WORKFLOW_PATH)["jobs"]["signature"]["steps"]
        self.assertNotIn("shell", step)
        self.body = step["run"]

    def _run(self, description: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*GITHUB_BASH, "-c", self.body],
            env={"PATH": "/usr/bin:/bin", "PR_BODY": description},
            capture_output=True,
            text=True,
            check=False,
        )

    def assertSigned(self, description: str) -> None:
        result = self._run(description)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"Signed: {SIGNATURE}", result.stdout)

    def assertUnsigned(self, description: str) -> None:
        result = self._run(description)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("::error::", result.stdout)

    def test_a_signed_description_passes(self) -> None:
        self.assertSigned(f"Closes #1\n\n{SIGNATURE}\n")

    def test_a_description_written_in_the_browser_passes(self) -> None:
        # GitHub stores a description edited on the web with CRLF line endings.
        self.assertSigned(f"Closes #1\r\n\r\n{SIGNATURE}\r\n")

    def test_an_empty_description_fails(self) -> None:
        self.assertUnsigned("")

    def test_a_description_with_no_signature_fails(self) -> None:
        self.assertUnsigned("Closes #1\n\nFiled by the scanner agent.\n")

    def test_a_signature_quoted_before_the_last_line_does_not_sign(self) -> None:
        self.assertUnsigned(f"The line looks like\n\n{SIGNATURE}\n\nand this closes #1.\n")

    def test_a_hidden_marker_below_the_signature_does_not_unsign_it(self) -> None:
        self.assertSigned(f"Closes #1\n\n{SIGNATURE}\n\n<!-- hive:issue-link-fixed -->\n")

    def test_a_hidden_marker_does_not_stand_in_for_a_signature(self) -> None:
        self.assertUnsigned("Closes #1\n\n<!-- — hive: agent=quality -->\n")

    def test_a_line_without_a_key_value_field_does_not_sign(self) -> None:
        self.assertUnsigned("Closes #1\n\n— hive: scanner\n")

    def test_shell_syntax_in_the_description_is_text(self) -> None:
        # The description is data. Nothing in it may run, and it must not end the step early.
        with tempfile.TemporaryDirectory() as temp_dir:
            marker = Path(temp_dir) / "ran"
            self.assertSigned(f"$(touch {marker}) `touch {marker}` '\"\n\n{SIGNATURE}\n")
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
