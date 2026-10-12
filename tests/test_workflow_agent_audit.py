"""
Script: tests/test_workflow_agent_audit.py
What: Tests .github/workflows/agent-audit.yml -- which merged pull requests it counts as written by
an agent, and which of them it fails -- by extracting the step's shell body from the workflow and
executing it against a `gh` stub that serves pull request and commit fixtures.
Doing: Runs the step under `bash -e`, the shell GitHub uses for a step with no `shell:` key, in a
temporary directory with `gh` replaced by a script. The stub answers `gh pr list` the way `gh`
does (nothing unless `--state merged`, at most `--limit`, only the `--json` fields), `gh pr view`
from a per-pull-request fixture, and `gh api repos/<repo>/commits/<sha> --jq <filter>` by running
the filter over a REST-shaped commit.
Why: The step's one exemption decides whether an unsigned commit fails the run. A merge commit is
exempt only when the pull request went into `main` and every parent after the first is outside
the commits the pull request lists. Exempting any commit with two or more parents would let
an agent pull request merge another unmerged branch and skip the check; nothing ran the
step before this file existed, so that rule could be loosened again, or the parent lookup read as "no parents", with
every test still green.
Goal: Make each half of the exemption, the signature rule, the agent selection, the enforcement
date and each refusal fail here, on the pull request that breaks it.

This runs the workflow's own text. Copying the step into the test would assert that the copy works.
Each rule has a near miss beside it (a merge from `main` beside a merge of a listed commit, a
signature quoted mid-body beside a final one), because a rule tested only on inputs that
obviously match still passes when its condition is deleted.

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
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "agent-audit.yml"
STEP = "Audit merged agent pull requests"

# A step with no `shell:` key runs under `bash -e {0}`: no pipefail unless the body sets it.
GITHUB_BASH = ["bash", "--noprofile", "--norc", "-e"]

REPO = "Danathar/zfs-aurora-complex"
HIVE = {"login": "app/danathar-atomic-hive", "is_bot": True}
MAINTAINER = {"login": "Danathar", "is_bot": False}
RENOVATE = {"login": "app/renovate", "is_bot": True}
SIGNATURE = "— hive: agent=quality backend=claude model=claude-opus-5-5"
ENFORCED = "2026-10-08T12:00:00Z"
BEFORE = "2026-10-07T12:00:00Z"

# Commits on `main` that a pull request does not list, and commits on another unmerged branch.
MAIN_0 = "a0" * 20
MAIN_1 = "a1" * 20

GH_STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FIXTURES/calls"
case "$1 $2" in
  "pr list")
    shift 2
    state=open limit=30 fields=
    while [ $# -gt 0 ]; do
      case "$1" in
        --state) state="$2" ;;
        --limit) limit="$2" ;;
        --json) fields="$2" ;;
        --repo|--search) ;;
        *) echo "gh pr list: unexpected argument $1" >&2; exit 64 ;;
      esac
      shift 2
    done
    [ -n "$fields" ] || { echo "Showing pull requests"; exit 0; }
    jq --arg state "$state" --argjson limit "$limit" --arg fields "$fields" '
      if $state == "merged" then .[:$limit] else [] end
      | map(with_entries(select(.key as $k | $fields | split(",") | index($k))))
    ' "$FIXTURES/prs.json"
    ;;
  "pr view")
    [ -e "$FIXTURES/view-$3.json" ] || { echo "HTTP 502: Bad Gateway" >&2; exit 1; }
    cat "$FIXTURES/view-$3.json"
    ;;
  "api repos/"*)
    sha="${2##*/}"
    [ "$3" = --jq ] || { echo "gh api: expected --jq" >&2; exit 64; }
    if [ ! -e "$FIXTURES/commit-$sha.json" ]; then
      echo "HTTP 502: Bad Gateway" >&2
      exit 1
    fi
    jq -r "$4" "$FIXTURES/commit-$sha.json"
    ;;
  *)
    echo "gh stub: unexpected call $*" >&2
    exit 64
    ;;
esac
"""


def _sha(name: str) -> str:
    """A 40-hex commit hash that reads back to `name` in a failure message."""

    return name.encode().hex().ljust(40, "0")[:40]


def _commit(name: str, *, signed: bool, parents: list[str], body: str = "why") -> dict:
    """One commit as `gh pr view --json commits` lists it, plus its parents for the REST stub."""

    message = body + ("\n\nSigned-off-by: quality <quality@hive.kubestellar.io>" if signed else "")
    return {
        "oid": _sha(name),
        "messageHeadline": name,
        "messageBody": message,
        "parents": parents,
    }


def _pr(
    number: int,
    commits: list[dict],
    *,
    author: dict = HIVE,
    body: str = f"Closes #1\n\n{SIGNATURE}\n",
    base: str = "main",
    head: str = "quality/test-x",
    merged: str = ENFORCED,
) -> dict:
    return {
        "number": number,
        "title": f"pull request {number}",
        "author": author,
        "baseRefName": base,
        "headRefName": head,
        "mergedAt": merged,
        "mergedBy": MAINTAINER,
        "body": body,
        "labels": [],
        "url": f"https://github.com/{REPO}/pull/{number}",
        "commits": commits,
    }


def _branch_commit(name: str = "c1") -> dict:
    return _commit(name, signed=True, parents=[MAIN_0])


@unittest.skipIf(yaml is None, "PyYAML is not installed")
@unittest.skipIf(shutil.which("bash") is None, "bash is not installed")
@unittest.skipIf(shutil.which("jq") is None, "jq is not installed")
@unittest.skipIf(shutil.which("date") is None, "date is not installed")
class AuditStepTests(unittest.TestCase):
    """Executes the step's shell body against a `gh` stub."""

    def setUp(self) -> None:
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        step = next(
            step
            for job in workflow["jobs"].values()
            for step in job["steps"]
            if step.get("name") == STEP
        )
        self.assertNotIn("shell", step)
        self.body = step["run"]
        self.assertNotIn("${{", self.body)

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.root = Path(temp_dir.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.fixtures = self.root / "fixtures"
        self.fixtures.mkdir()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(GH_STUB, encoding="utf-8")
        gh.chmod(0o755)
        self.path = f"{bin_dir}:/usr/bin:/bin"
        self.summary = self.root / "summary.md"

    def _run(
        self,
        prs: list[dict],
        *,
        since: str = "2026-10-01",
        drop: tuple[str, ...] = (),
        unviewable: tuple[int, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        """Writes the fixtures for `prs` and runs the step.

        `drop` names commits whose parent lookup fails; `unviewable` numbers pull requests whose
        `gh pr view` fails.
        """

        listed = []
        for pr in prs:
            commits = pr.pop("commits")
            listed.append(pr)
            view = {"number": pr["number"], "closingIssuesReferences": [{"number": 1}], "commits": []}
            for commit in commits:
                parents = commit.pop("parents")
                view["commits"].append(commit)
                if commit["messageHeadline"] in drop:
                    continue
                rest = {"sha": commit["oid"], "parents": [{"sha": sha} for sha in parents]}
                (self.fixtures / f"commit-{commit['oid']}.json").write_text(
                    json.dumps(rest), encoding="utf-8"
                )
            if pr["number"] not in unviewable:
                (self.fixtures / f"view-{pr['number']}.json").write_text(
                    json.dumps(view), encoding="utf-8"
                )
        (self.fixtures / "prs.json").write_text(json.dumps(listed), encoding="utf-8")
        self.summary.write_text("", encoding="utf-8")
        return subprocess.run(
            [*GITHUB_BASH, "-c", self.body],
            cwd=self.work,
            env={
                "PATH": self.path,
                "HOME": str(self.root),
                "FIXTURES": str(self.fixtures),
                "GH_TOKEN": "stub",
                "REPO": REPO,
                "SINCE": since,
                "GITHUB_STEP_SUMMARY": str(self.summary),
            },
            capture_output=True,
            text=True,
            check=False,
        )

    def _findings(self) -> list[str]:
        return json.loads((self.work / "result.json").read_text(encoding="utf-8"))["findings"]

    def _commit_lookups(self) -> list[str]:
        calls = (self.fixtures / "calls").read_text(encoding="utf-8").splitlines()
        return sorted(call.split()[1].rsplit("/", 1)[1] for call in calls if call.startswith("api "))

    def assertPasses(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self._findings(), [])

    def assertUnsigned(self, result: subprocess.CompletedProcess[str], number: int, name: str) -> None:
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(
            self._findings(),
            [f"#{number}: commit(s) {_sha(name)[:7]} carry no Signed-off-by trailer"],
        )

    # The merge exemption.

    def test_an_update_from_main_is_exempt(self) -> None:
        # "Update branch": first parent the branch tip, second a `main` commit the PR does not list.
        c1 = _branch_commit()
        merge = _commit("m1", signed=False, parents=[c1["oid"], MAIN_1])
        result = self._run([_pr(10, [c1, merge])])
        self.assertPasses(result)
        self.assertIn("| 2 | all (1 merge exempt) |", self.summary.read_text(encoding="utf-8"))

    def test_a_merge_of_a_commit_the_pull_request_lists_is_not_exempt(self) -> None:
        # Another unmerged branch merged in: its commits are listed on this pull request.
        c1 = _branch_commit()
        other = _commit("o1", signed=True, parents=[MAIN_0])
        merge = _commit("m1", signed=False, parents=[c1["oid"], other["oid"]])
        self.assertUnsigned(self._run([_pr(11, [c1, other, merge])]), 11, "m1")

    def test_a_merge_as_the_first_commit_on_the_branch_is_not_exempt(self) -> None:
        # The first parent is the `main` commit the branch started from; only later parents count.
        other = _commit("o1", signed=True, parents=[MAIN_0])
        merge = _commit("m1", signed=False, parents=[MAIN_0, other["oid"]])
        self.assertUnsigned(self._run([_pr(12, [other, merge])]), 12, "m1")

    def test_an_octopus_with_one_listed_parent_is_not_exempt(self) -> None:
        c1 = _branch_commit()
        other = _commit("o1", signed=True, parents=[MAIN_0])
        merge = _commit("m1", signed=False, parents=[c1["oid"], MAIN_1, other["oid"]])
        self.assertUnsigned(self._run([_pr(13, [c1, other, merge])]), 13, "m1")

    def test_an_update_merge_on_a_pull_request_into_another_branch_is_not_exempt(self) -> None:
        c1 = _branch_commit()
        merge = _commit("m1", signed=False, parents=[c1["oid"], MAIN_1])
        self.assertUnsigned(self._run([_pr(14, [c1, merge], base="release")]), 14, "m1")

    def test_a_merge_subject_on_a_one_parent_commit_is_not_exempt(self) -> None:
        # Parents decide what a merge is, not the subject line anyone can write.
        c1 = _branch_commit()
        fake = _commit("m1", signed=False, parents=[c1["oid"]], body="Merge branch 'main'")
        self.assertUnsigned(self._run([_pr(15, [c1, fake])]), 15, "m1")

    def test_an_unsigned_ordinary_commit_fails(self) -> None:
        c1 = _branch_commit()
        c2 = _commit("c2", signed=False, parents=[c1["oid"]])
        self.assertUnsigned(self._run([_pr(16, [c1, c2])]), 16, "c2")

    def test_a_sign_off_quoted_mid_line_is_not_a_trailer(self) -> None:
        c1 = _commit("c1", signed=False, parents=[MAIN_0], body="Adds the Signed-off-by: check")
        self.assertUnsigned(self._run([_pr(17, [c1])]), 17, "c1")

    def test_parents_are_looked_up_only_for_unsigned_commits(self) -> None:
        c1 = _branch_commit()
        merge = _commit("m1", signed=False, parents=[c1["oid"], MAIN_1])
        self.assertPasses(self._run([_pr(18, [c1, merge])]))
        self.assertEqual(self._commit_lookups(), [merge["oid"]])

    def test_a_failed_parent_lookup_fails_the_run(self) -> None:
        # Read as "no parents", an update merge would just be unsigned; read as anything else it
        # could be waved through. Either way the run must stop rather than report.
        c1 = _branch_commit()
        merge = _commit("m1", signed=False, parents=[c1["oid"], MAIN_1])
        result = self._run([_pr(19, [c1, merge])], drop=("m1",))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no parents read", result.stderr)
        self.assertEqual(self.summary.read_text(encoding="utf-8"), "")

    def test_a_failed_commit_listing_fails_the_run(self) -> None:
        # A pull request whose commits were never read must not be reported as clean. pipefail
        # stops the step at the listing; without it, the report's own lookup refuses #24.
        prs = [_pr(23, [_branch_commit()]), _pr(24, [_branch_commit("c2")])]
        result = self._run(prs, unviewable=(24,))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.summary.read_text(encoding="utf-8"), "")

    # The signature rule and who counts as an agent.

    def test_a_hive_pull_request_without_a_signature_fails(self) -> None:
        result = self._run([_pr(20, [_branch_commit()], body="Closes #1\n")])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(len(self._findings()), 1)
        self.assertTrue(self._findings()[0].startswith("#20: opened by the Hive app with no"))

    def test_a_signature_quoted_before_the_last_line_does_not_sign(self) -> None:
        body = f"The line looks like\n\n{SIGNATURE}\n\nand this closes #1.\n"
        result = self._run([_pr(21, [_branch_commit()], body=body)])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(self._findings()[0].startswith("#21: opened by the Hive app with no"))

    def test_a_signed_hive_pull_request_passes(self) -> None:
        self.assertPasses(self._run([_pr(22, [_branch_commit()])]))

    def test_agent_pull_requests_are_selected_by_app_or_signature(self) -> None:
        unsigned = [_commit("u1", signed=False, parents=[MAIN_0])]
        prs = [
            _pr(30, [dict(c) for c in unsigned]),
            _pr(31, [dict(c) for c in unsigned], author=MAINTAINER),
            _pr(33, [dict(c) for c in unsigned], author=MAINTAINER, body="no line"),
            _pr(34, [dict(c) for c in unsigned], author=RENOVATE, body="no line", head="renovate/x"),
        ]
        result = self._run(prs)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(sorted(f.split(":")[0] for f in self._findings()), ["#30", "#31"])
        self.assertIn("2 of the 4 pull requests merged", self.summary.read_text(encoding="utf-8"))

    # The enforcement date.

    def test_a_miss_merged_before_enforcement_is_reported_not_failed(self) -> None:
        c1 = _branch_commit()
        other = _commit("o1", signed=True, parents=[MAIN_0])
        merge = _commit("m1", signed=False, parents=[c1["oid"], other["oid"]])
        result = self._run([_pr(40, [c1, other, merge], merged=BEFORE)])
        self.assertPasses(result)
        summary = self.summary.read_text(encoding="utf-8")
        self.assertIn("#### Before enforcement", summary)
        self.assertIn(f"#40: commit(s) {_sha('m1')[:7]} carry no Signed-off-by trailer", summary)

    # Refusals: each stops with exit 2 rather than auditing part of the window.

    def test_a_since_that_is_not_a_real_date_is_refused(self) -> None:
        for since in ("2026-10", "yesterday", "2026-02-30"):
            with self.subTest(since=since):
                result = self._run([], since=since)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_a_window_that_fills_the_list_cap_is_refused(self) -> None:
        prs = [_pr(n, [], author=MAINTAINER, body="") for n in range(1, 501)]
        result = self._run(prs)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("reached the 500 cap", result.stdout)

    def test_a_pull_request_listing_100_commits_is_refused(self) -> None:
        commits = [_commit(f"c{i}", signed=True, parents=[MAIN_0]) for i in range(100)]
        result = self._run([_pr(50, commits)])
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("#50 list 100 commits", result.stdout)


if __name__ == "__main__":
    unittest.main()
