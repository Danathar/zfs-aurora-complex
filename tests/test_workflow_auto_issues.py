"""
Script: tests/test_workflow_auto_issues.py
What: Tests .github/workflows/auto-issues.yml -- which workflows it watches, and what its one shell
body does with a finished run -- by extracting the body from the workflow and executing it against
a recording `gh` stub.
Doing: Parses the workflows with PyYAML. Joins the `workflow_run` list to every scheduled workflow's
`name:`. Runs the step under the shell GitHub uses for `shell: bash`, with `gh` replaced by a
script that records each call and its body and answers `gh issue list` from a fixture.
Why: A scheduled run that fails tells nobody; this workflow is what turns it into an issue. It can
stop doing that without any visible change: a watched workflow renamed, a new scheduled workflow
never added, a failed issue listing read as "no issue open", or a stranger's issue with the same
title taking the report.
Goal: Make each of those fail here, on the pull request that causes it, rather than as a silent
week of red scheduled runs.

This runs the workflow's own text. Copying the step into the test would assert that the copy works.

PyYAML is guarded for the reason tests/test_workflow_nightly_compliance.py gives.
"""

from __future__ import annotations

import json
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
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOW_PATH = WORKFLOW_DIR / "auto-issues.yml"
STEP = "Track the unattended run"

# Scheduled workflows this one deliberately does not watch, by `name:`, with the reason. A new
# scheduled workflow must land in `workflows:` or here, so the choice is made in a diff.
UNWATCHED: dict[str, str] = {}

BUILD = "Build And Promote Main Image"

GITHUB_BASH = ["bash", "--noprofile", "--norc", "-eo", "pipefail"]

REPO = "Danathar/zfs-aurora-complex"
TITLE = "Run failing on main: Nightly compliance"
BUILD_TITLE = "Run failing on main: Build And Promote Main Image"
PUSH_BUILD_TITLE = "Push build failing on main: Build And Promote Main Image"
BOT = {"login": "app/github-actions", "is_bot": True}
HUMAN = {"login": "someone", "is_bot": False}

GH_STUB = """#!/usr/bin/env bash
n=$(( $(cat "$GH_LOG/count" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$GH_LOG/count"
printf '%s\\n' "$@" > "$GH_LOG/call-$n.argv"
case "$1 $2" in
  "issue list")
    if [ "${GH_LIST_RC:-0}" -ne 0 ]; then
      echo "gh: HTTP 502" >&2
      exit "$GH_LIST_RC"
    fi
    cat "$GH_LOG/open-issues.json"
    ;;
  "api "*|"api repos/"*)
    if [ "${GH_API_RC:-0}" -ne 0 ]; then
      echo "gh: HTTP 502" >&2
      exit "$GH_API_RC"
    fi
    cat "$GH_LOG/artifacts.txt"
    ;;
  "issue create"|"issue comment")
    cat > "$GH_LOG/call-$n.body"
    ;;
esac
"""


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # PyYAML reads the bare key `on` as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    return triggers if isinstance(triggers, dict) else {}


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class WatchedWorkflowsTests(unittest.TestCase):
    """The `workflow_run` list against the scheduled workflows that exist."""

    def setUp(self) -> None:
        self.watched = set(_triggers(_load(WORKFLOW_PATH))["workflow_run"]["workflows"])
        self.names = {}
        self.scheduled = set()
        for path in sorted([*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")]):
            workflow = _load(path)
            self.names[workflow["name"]] = path.name
            if "schedule" in _triggers(workflow):
                self.scheduled.add(workflow["name"])

    def test_every_watched_name_is_a_workflow(self) -> None:
        # workflow_run matches on `name:`; a renamed workflow silently stops being watched.
        self.assertEqual(sorted(self.watched - set(self.names)), [])

    def test_every_scheduled_workflow_is_watched_or_deliberately_unwatched(self) -> None:
        self.assertEqual(sorted(self.scheduled - self.watched - set(UNWATCHED)), [])

    def test_unwatched_entries_name_scheduled_workflows_that_are_not_watched(self) -> None:
        self.assertEqual(sorted(set(UNWATCHED) - self.scheduled), [])
        self.assertEqual(sorted(set(UNWATCHED) & self.watched), [])


@unittest.skipIf(yaml is None, "PyYAML is not installed")
@unittest.skipIf(shutil.which("bash") is None, "bash is not installed")
@unittest.skipIf(shutil.which("jq") is None, "jq is not installed")
class TrackStepTests(unittest.TestCase):
    """Executes the step's shell body against a recording `gh`."""

    def setUp(self) -> None:
        step = next(
            step
            for job in _load(WORKFLOW_PATH)["jobs"].values()
            for step in job["steps"]
            if step.get("name") == STEP
        )
        self.assertEqual(step.get("shell"), "bash")
        self.body = step["run"]
        self.assertNotIn("${{", self.body)

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.root = Path(temp_dir.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "gh-log"
        self.log.mkdir()
        gh = self.bin / "gh"
        gh.write_text(GH_STUB, encoding="utf-8")
        gh.chmod(0o755)
        # The step finds jq on the runner's PATH; point at the real one here.
        (self.bin / "jq").symlink_to(shutil.which("jq"))

    def _run(
        self,
        *,
        conclusion: str,
        open_issues: list[dict] | None = None,
        event: str = "schedule",
        head_branch: str = "main",
        head_repo: str = REPO,
        list_rc: int = 0,
        workflow: str = "Nightly compliance",
        artifacts: str = "",
        api_rc: int = 0,
    ) -> subprocess.CompletedProcess[str]:
        (self.log / "open-issues.json").write_text(json.dumps(open_issues or []), encoding="utf-8")
        (self.log / "artifacts.txt").write_text(artifacts, encoding="utf-8")
        return subprocess.run(
            [*GITHUB_BASH, "-c", self.body],
            env={
                "PATH": f"{self.bin}:/usr/bin:/bin",
                "HOME": str(self.root),
                "GH_LOG": str(self.log),
                "GH_LIST_RC": str(list_rc),
                "GH_API_RC": str(api_rc),
                "RUN_ID": "42",
                "GH_TOKEN": "stub",
                "REPO": REPO,
                "DEFAULT_BRANCH": "main",
                "WORKFLOW": workflow,
                "EVENT": event,
                "HEAD_BRANCH": head_branch,
                "HEAD_REPO": head_repo,
                "HEAD_SHA": "0123abcd",
                "CONCLUSION": conclusion,
                "RUN_URL": "https://github.com/Danathar/zfs-aurora-complex/actions/runs/42",
            },
            capture_output=True,
            text=True,
            check=False,
        )

    def _calls(self) -> list[list[str]]:
        """Each recorded gh argv, in call order."""

        count_file = self.log / "count"
        count = int(count_file.read_text()) if count_file.exists() else 0
        return [
            (self.log / f"call-{n}.argv").read_text(encoding="utf-8").splitlines()
            for n in range(1, count + 1)
        ]

    def _writes(self) -> list[list[str]]:
        return [
            call for call in self._calls() if call[:2] != ["issue", "list"] and call[0] != "api"
        ]

    def _body(self, n: int) -> str:
        return (self.log / f"call-{n}.body").read_text(encoding="utf-8")

    def test_a_failure_with_no_issue_open_opens_one(self) -> None:
        result = self._run(conclusion="failure")
        self.assertEqual(result.returncode, 0, result.stderr)
        writes = self._writes()
        self.assertEqual(len(writes), 1, writes)
        self.assertEqual(writes[0][:2], ["issue", "create"])
        self.assertIn(TITLE, writes[0])
        self.assertNotIn("--label", writes[0])
        body = self._body(2)
        self.assertIn("actions/runs/42", body)
        self.assertIn("0123abcd", body)
        # A backtick inside the double-quoted printf argument is command
        # substitution; `:latest` once ran as a command and vanished.
        self.assertNotIn("leaves  where", body)
        self.assertIn("`:latest`", body)

    def test_a_timeout_counts_as_a_failure(self) -> None:
        self._run(conclusion="timed_out")
        self.assertEqual([call[:2] for call in self._writes()], [["issue", "create"]])

    def test_a_further_failure_comments_on_the_bots_open_issue(self) -> None:
        issues = [{"number": 7, "title": TITLE, "author": BOT}]
        result = self._run(conclusion="failure", open_issues=issues)
        self.assertEqual(result.returncode, 0, result.stderr)
        writes = self._writes()
        self.assertEqual(len(writes), 1, writes)
        self.assertEqual(writes[0][:3], ["issue", "comment", "7"])
        self.assertIn("actions/runs/42", self._body(2))

    def test_an_issue_someone_else_opened_with_the_same_title_is_not_used(self) -> None:
        issues = [{"number": 3, "title": TITLE, "author": HUMAN}]
        self._run(conclusion="failure", open_issues=issues)
        writes = self._writes()
        self.assertEqual([call[:2] for call in writes], [["issue", "create"]])

    def test_another_workflows_issue_is_not_used(self) -> None:
        issues = [{"number": 5, "title": "Run failing on main: Prune registry", "author": BOT}]
        self._run(conclusion="failure", open_issues=issues)
        self.assertEqual([call[:2] for call in self._writes()], [["issue", "create"]])

    def test_a_pass_closes_the_bots_open_issue(self) -> None:
        issues = [{"number": 7, "title": TITLE, "author": BOT}]
        result = self._run(conclusion="success", open_issues=issues)
        self.assertEqual(result.returncode, 0, result.stderr)
        writes = self._writes()
        self.assertEqual(len(writes), 1, writes)
        self.assertEqual(writes[0][:3], ["issue", "close", "7"])

    def test_a_pass_never_closes_someone_elses_issue(self) -> None:
        issues = [{"number": 3, "title": TITLE, "author": HUMAN}]
        self._run(conclusion="success", open_issues=issues)
        self.assertEqual(self._writes(), [])

    def test_a_cancelled_run_neither_opens_nor_closes(self) -> None:
        issues = [{"number": 7, "title": TITLE, "author": BOT}]
        for conclusion in ("cancelled", "skipped", "action_required"):
            with self.subTest(conclusion=conclusion):
                result = self._run(conclusion=conclusion, open_issues=issues)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self._calls(), [])

    def test_runs_that_say_nothing_about_main_are_ignored(self) -> None:
        cases = {
            "another branch": {"head_branch": "feature"},
            "another repository": {"head_repo": "someone/zfs-aurora-complex"},
            "a push of a workflow other than the build": {"event": "push"},
            "a pull request": {"event": "pull_request"},
        }
        for label, overrides in cases.items():
            with self.subTest(label):
                result = self._run(conclusion="failure", **overrides)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self._calls(), [])

    def test_a_dispatched_run_neither_opens_nor_closes(self) -> None:
        # Prune registry's schedule is a dry run and a dispatch can delete: a green dispatch (or
        # dry run) is not evidence about the other mode, and a person dispatching is watching.
        issues = [{"number": 7, "title": TITLE, "author": BOT}]
        for conclusion in ("failure", "success"):
            with self.subTest(conclusion=conclusion):
                result = self._run(
                    conclusion=conclusion, event="workflow_dispatch", open_issues=issues
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self._calls(), [])

    def test_a_build_failure_with_no_akmods_payload_opens_the_scheduled_failure_issue(self) -> None:
        result = self._run(
            conclusion="failure", workflow=BUILD, artifacts="build-inputs-42\nsome-other\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        writes = self._writes()
        self.assertEqual([call[:2] for call in writes], [["issue", "create"]])
        self.assertIn(BUILD_TITLE, writes[0])

    def test_a_build_failure_with_an_akmods_payload_is_left_to_the_triage_workflow(self) -> None:
        # akmods-failure-triage.yml keeps the sticky `akmods-failure` issue for this case.
        result = self._run(
            conclusion="failure",
            workflow=BUILD,
            artifacts="build-inputs-42\nakmods-failure-42\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._writes(), [])

    def test_a_green_build_that_built_closes_the_issue(self) -> None:
        issues = [{"number": 9, "title": BUILD_TITLE, "author": BOT}]
        result = self._run(
            conclusion="success", workflow=BUILD, open_issues=issues, artifacts="build-inputs-42\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[:3] for call in self._writes()], [["issue", "close", "9"]])

    def test_a_green_build_the_gate_skipped_does_not_close_the_issue(self) -> None:
        # The stable-signal gate skips a scheduled run and the run still reports success.
        issues = [{"number": 9, "title": BUILD_TITLE, "author": BOT}]
        result = self._run(conclusion="success", workflow=BUILD, open_issues=issues, artifacts="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._writes(), [])

    def test_a_failed_push_build_on_main_opens_its_own_issue(self) -> None:
        # At L6 Hive merges its own pull requests; the push build that starts has nobody watching.
        # An open scheduled-build issue is not the one to comment on: it tracks other evidence.
        issues = [{"number": 9, "title": BUILD_TITLE, "author": BOT}]
        result = self._run(
            conclusion="failure",
            workflow=BUILD,
            event="push",
            open_issues=issues,
            artifacts="build-inputs-42\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        writes = self._writes()
        self.assertEqual([call[:2] for call in writes], [["issue", "create"]])
        self.assertIn(PUSH_BUILD_TITLE, writes[0])
        # Call 1 lists the run's artifacts, call 2 the open issues, call 3 creates.
        self.assertIn("`push` run", self._body(3))

    def test_a_further_push_failure_comments_on_the_push_issue(self) -> None:
        issues = [
            {"number": 9, "title": BUILD_TITLE, "author": BOT},
            {"number": 11, "title": PUSH_BUILD_TITLE, "author": BOT},
        ]
        result = self._run(
            conclusion="failure",
            workflow=BUILD,
            event="push",
            open_issues=issues,
            artifacts="build-inputs-42\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[:3] for call in self._writes()], [["issue", "comment", "11"]])

    def test_a_failed_push_build_with_an_akmods_payload_is_left_to_the_triage_workflow(self) -> None:
        result = self._run(
            conclusion="failure",
            workflow=BUILD,
            event="push",
            artifacts="build-inputs-42\nakmods-failure-42\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._writes(), [])

    def test_a_green_push_build_closes_only_the_push_issue(self) -> None:
        # A scheduled build always rebuilds the akmods cache and a push build reuses it, so a
        # green push run says nothing about a failure in the schedule-only rebuild path.
        issues = [
            {"number": 9, "title": BUILD_TITLE, "author": BOT},
            {"number": 11, "title": PUSH_BUILD_TITLE, "author": BOT},
        ]
        result = self._run(
            conclusion="success",
            workflow=BUILD,
            event="push",
            open_issues=issues,
            artifacts="build-inputs-42\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[:3] for call in self._writes()], [["issue", "close", "11"]])

    def test_a_green_scheduled_build_closes_both_build_issues(self) -> None:
        # It did everything a push build does, and the akmods rebuild besides.
        issues = [
            {"number": 9, "title": BUILD_TITLE, "author": BOT},
            {"number": 11, "title": PUSH_BUILD_TITLE, "author": BOT},
        ]
        result = self._run(
            conclusion="success", workflow=BUILD, open_issues=issues, artifacts="build-inputs-42\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [call[:3] for call in self._writes()],
            [["issue", "close", "9"], ["issue", "close", "11"]],
        )

    def test_a_green_push_build_the_gate_would_skip_still_needs_build_inputs(self) -> None:
        issues = [{"number": 11, "title": PUSH_BUILD_TITLE, "author": BOT}]
        result = self._run(
            conclusion="success", workflow=BUILD, event="push", open_issues=issues, artifacts=""
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._writes(), [])

    def test_a_push_build_on_another_branch_is_ignored(self) -> None:
        result = self._run(
            conclusion="failure", workflow=BUILD, event="push", head_branch="feature"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._calls(), [])

    def test_a_dispatched_build_neither_opens_nor_closes(self) -> None:
        issues = [{"number": 9, "title": BUILD_TITLE, "author": BOT}]
        for conclusion in ("failure", "success"):
            with self.subTest(conclusion=conclusion):
                result = self._run(
                    conclusion=conclusion,
                    workflow=BUILD,
                    event="workflow_dispatch",
                    open_issues=issues,
                    artifacts="build-inputs-42\n",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self._calls(), [])

    def test_a_failed_artifact_listing_fails_the_step_instead_of_guessing(self) -> None:
        result = self._run(conclusion="failure", workflow=BUILD, api_rc=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._writes(), [])

    def test_the_artifact_listing_is_only_read_for_the_build(self) -> None:
        self._run(conclusion="failure")
        self.assertFalse(any(call[0] == "api" for call in self._calls()))

    def test_the_token_cannot_reach_the_build(self) -> None:
        # Least privilege, and the maintainer's rule that nothing new may touch the build path.
        workflow = _load(WORKFLOW_PATH)
        permissions = workflow["jobs"]["track"]["permissions"]
        self.assertEqual(permissions, {"issues": "write", "actions": "read"})
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertNotIn("actions/checkout", text)
        self.assertNotIn("secrets.", text)

    def test_a_failed_issue_listing_fails_the_step_instead_of_filing_a_duplicate(self) -> None:
        result = self._run(conclusion="failure", list_rc=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._writes(), [])


if __name__ == "__main__":
    unittest.main()
