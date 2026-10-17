"""
Script: tests/test_ai_ops_runbook_doc.py
What: Joins docs/ai-ops-runbook.md to the workflows it has an entry for, and to the code and config the entries make claims about.
Doing: Reads each workflow as text (no YAML parser) and checks that every file in .github/workflows/ has exactly one `### `file` -- "name"` entry whose quoted name is the workflow's `name:`, that every job name an entry quotes is a job `name:` in that file, and that the schedules, defaults, artifact names, issue titles and hook messages the entries state are the ones in the workflows, `ci_tools/` and the hook.
Why: The page is a lookup a reader trusts at the moment something is red. An entry for a workflow that no longer exists, a missing entry for a new one, or a quoted job name that was renamed sends the reader to the wrong place.
Goal: Make a workflow added without a runbook entry, a renamed workflow or job, or a changed schedule or title fail here.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "ai-ops-runbook.md"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
HOOK = REPO_ROOT / ".claude" / "hooks" / "gate-git-diff.sh"

ENTRY_HEADING_RE = re.compile(r'(?m)^### `([\w.-]+\.yml)` — "([^"]+)"$')
NAME_RE = re.compile(r"(?m)^( *)name: (.+?)\s*$")

# Names an entry quotes, per workflow, that must be jobs (or the workflow) in that file.
QUOTED_JOBS = {
    "build.yml": ["Evaluate Stable Signal Gate"],
    "build-pr.yml": ["Build PR Image (No Push)"],
    "test.yml": ["Python Unit Tests"],
    "labeler.yml": ["Apply area labels"],
    "akmods-failure-triage.yml": ["Manage sticky akmods failure issue"],
    "auto-issues.yml": ["Open, update or close the tracking issue"],
    "agent-audit.yml": ["Read back merged agent pull requests"],
}


def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def workflow_text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def workflow_name(name: str) -> str:
    match = NAME_RE.search(workflow_text(name))
    assert match and not match.group(1), f"{name} has no top-level name:"
    return match.group(2).strip("'\"")


def job_names(name: str) -> set[str]:
    return {m.group(2).strip("'\"") for m in NAME_RE.finditer(workflow_text(name)) if m.group(1) == "    "}


def entries() -> dict[str, str]:
    """{workflow file: the entry's text} for every `###` entry under 'Workflow runs'."""

    body = doc_text().split("## Workflow runs", 1)[1].split("\n## ", 1)[0]
    parts = re.split(r"(?m)^### ", body)[1:]
    out = {}
    for part in parts:
        match = ENTRY_HEADING_RE.match("### " + part)
        assert match, f"unparseable entry heading: {part.splitlines()[0]}"
        assert match.group(1) not in out, f"two entries for {match.group(1)}"
        out[match.group(1)] = "### " + part
    return out


def flat(text: str) -> str:
    return " ".join(text.split())


class EntryCoverageTests(unittest.TestCase):
    def test_every_workflow_has_an_entry_and_every_entry_a_workflow(self) -> None:
        on_disk = sorted(p.name for p in WORKFLOWS.glob("*.yml"))
        self.assertGreater(len(on_disk), 5)
        self.assertEqual(sorted(entries()), on_disk)

    def test_each_entry_quotes_the_workflows_own_name(self) -> None:
        for file, text in entries().items():
            with self.subTest(workflow=file):
                heading = ENTRY_HEADING_RE.match(text)
                self.assertEqual(heading.group(2), workflow_name(file))

    def test_every_job_name_an_entry_quotes_is_a_job_in_that_workflow(self) -> None:
        for file, quoted in QUOTED_JOBS.items():
            for job in quoted:
                with self.subTest(workflow=file, job=job):
                    self.assertIn(job, job_names(file))
                    self.assertIn(f'"{job}"', flat(entries()[file]).replace('" and "', '", "'))

    def test_the_nightly_entry_names_both_jobs_by_id_and_name(self) -> None:
        text = entries()["nightly-compliance.yml"]
        for job in ("published-image", "suite"):
            self.assertIn(f"`{job}`", text)
            self.assertIn(job, job_names("nightly-compliance.yml"))

    def test_every_workflow_is_named_by_the_page_in_the_run_list_commands(self) -> None:
        for workflow in re.findall(r"--workflow ([\w.-]+\.yml)", doc_text()):
            with self.subTest(workflow=workflow):
                self.assertTrue((WORKFLOWS / workflow).is_file())


class ScheduleAndDefaultTests(unittest.TestCase):
    def test_build_runs_daily_at_0600_utc(self) -> None:
        self.assertIn('cron: "00 06 * * *"', workflow_text("build.yml"))
        self.assertIn("daily at 06:00 UTC", flat(entries()["build.yml"]))

    def test_nightly_runs_at_0500_utc(self) -> None:
        self.assertIn('cron: "00 05 * * *"', workflow_text("nightly-compliance.yml"))
        self.assertIn("at 05:00 UTC", flat(entries()["nightly-compliance.yml"]))

    def test_agent_audit_is_monthly_over_31_days(self) -> None:
        text = workflow_text("agent-audit.yml")
        self.assertRegex(text, r"cron: '\d+ \d+ 1 \* \*'")
        self.assertIn("31 days ago", text)
        self.assertIn("monthly", entries()["agent-audit.yml"])
        self.assertIn("last 31 days", flat(entries()["agent-audit.yml"]))

    def test_agent_audit_enforcement_date_is_the_one_the_page_gives(self) -> None:
        self.assertIn('enforce_from="2026-10-08"', workflow_text("agent-audit.yml"))
        self.assertIn("merged before 2026-10-08", flat(entries()["agent-audit.yml"]))

    def test_a_dispatch_of_build_defaults_to_promoting(self) -> None:
        text = workflow_text("build.yml")
        declaration = text.split("      promote_to_stable:", 1)[1].split("\n      ", 5)[:5]
        self.assertIn("default: true", "\n".join(declaration))
        self.assertIn("defaults `promote_to_stable` to `true`", flat(entries()["build.yml"]))

    def test_the_lint_step_the_test_entry_names_exists(self) -> None:
        self.assertIn("- name: Lint with ruff", workflow_text("test.yml"))
        self.assertIn('step "Lint with ruff"', flat(entries()["test.yml"]))


class AutoIssuesTests(unittest.TestCase):
    def test_it_watches_the_three_workflows_the_entry_names(self) -> None:
        match = re.search(r"workflows: \[(.*?)\]", workflow_text("auto-issues.yml"))
        watched = [w.strip().strip('"') for w in match.group(1).split(",")]
        self.assertEqual(
            sorted(watched),
            sorted(workflow_name(f) for f in ("nightly-compliance.yml", "agent-audit.yml", "build.yml")),
        )
        entry = entries()["auto-issues.yml"]
        for file in ("nightly-compliance.yml", "agent-audit.yml", "build.yml"):
            self.assertIn(f"`{file}`", entry)

    def test_the_title_and_the_artifact_prefixes_are_the_workflows(self) -> None:
        text = workflow_text("auto-issues.yml")
        self.assertIn('title="Run failing on main: $WORKFLOW"', text)
        self.assertIn("`Run failing on main:`", flat(entries()["auto-issues.yml"]))
        self.assertIn("grep -q '^akmods-failure-'", text)
        self.assertIn("grep -q '^build-inputs-'", text)
        entry = entries()["auto-issues.yml"]
        self.assertIn("`akmods-failure-*`", entry)
        self.assertIn("`build-inputs-*`", entry)


class StickyIssueTests(unittest.TestCase):
    def test_the_two_title_prefixes_are_the_classifiers(self) -> None:
        source = (REPO_ROOT / "ci_tools" / "classify_akmods_failure.py").read_text(encoding="utf-8")
        text = flat(doc_text())
        for prefix in ("Upstream ZFS/kernel incompatibility:", "Unclassified akmods build failure:"):
            with self.subTest(prefix=prefix):
                self.assertIn(f'"{prefix} {{', source)
                self.assertIn(prefix.replace(":", ""), text.replace(":", ""))

    def test_the_label_is_the_one_the_triage_workflow_uses(self) -> None:
        self.assertIn("akmods-failure", workflow_text("akmods-failure-triage.yml"))
        self.assertIn("label `akmods-failure`", doc_text())


class GateHookTests(unittest.TestCase):
    def test_the_hook_prints_blocked_and_exits_2(self) -> None:
        source = HOOK.read_text(encoding="utf-8")
        self.assertIn("'blocked: ", source)
        self.assertRegex(source, r"(?m)^  exit 2$")

    def test_the_post_edit_lint_hook_the_page_describes_is_registered(self) -> None:
        settings = (REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")
        self.assertIn("PostToolUse", settings)
        self.assertIn("ruff check", settings)


class RenovateEntryTests(unittest.TestCase):
    def test_the_test_the_entry_cites_exists(self) -> None:
        self.assertIn("test_container_literals_match_the_checked_in_default", doc_text())
        source = (REPO_ROOT / "tests" / "test_workflow_build_container.py").read_text(encoding="utf-8")
        self.assertIn("def test_container_literals_match_the_checked_in_default", source)

    def test_renovate_sets_automerge_false(self) -> None:
        self.assertIn('"automerge": false', (REPO_ROOT / "renovate.json").read_text(encoding="utf-8"))

    def test_renovate_rebases_only_a_conflicting_branch(self) -> None:
        self.assertIn("`rebaseWhen` to `conflicted`", doc_text())
        self.assertIn('"rebaseWhen": "conflicted"', (REPO_ROOT / "renovate.json").read_text(encoding="utf-8"))


class QuotedHeadingTests(unittest.TestCase):
    """`[page](./x.md), "Heading"` points at a heading that must exist in that page."""

    LINK_THEN_HEADING = re.compile(r'\[`?([\w./-]+\.md)`?\]\(([^)#]+\.md)\)(?:,| on| in) "([^"]+)"')

    def test_every_quoted_heading_exists_in_the_page_it_follows(self) -> None:
        text = flat(doc_text())
        found = self.LINK_THEN_HEADING.findall(text)
        self.assertGreaterEqual(len(found), 5)
        missing = []
        for _, target, heading in found:
            page = (DOC.parent / target).read_text(encoding="utf-8")
            headings = {h.strip("` ").lower() for h in re.findall(r"(?m)^#+ (.+)$", page)}
            if not any(h.startswith(heading.lower()) for h in headings):
                missing.append(f"{target}: {heading}")
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
