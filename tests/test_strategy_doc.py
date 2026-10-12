"""
Script: tests/test_strategy_doc.py
What: Joins docs/strategy.md to the documents, workflows and commands it points at.
Doing: Checks that the four documents the goal section links exist and carry the status the page gives them, that the README quotation and README anchor it cites exist, that the watchlist still has the two headings its `sed` command slices between and the slice yields entries, that the one-line `grep` it says prints nothing prints nothing, that the scheduled workflows it lists are the ones with a `schedule:` trigger and the workflow it says is absent is absent, and that its `gh` commands name this repository.
Why: The page stores no numbers on purpose and instead holds commands and claims about what exists. A renamed heading makes the watchlist command print nothing, which reads as "nothing is open"; a new scheduled workflow makes the "scheduled workflows that do exist" list false.
Goal: Make a renamed heading, a moved status line, a new scheduled workflow or a command aimed at another repository fail here.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "strategy.md"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
WATCHLIST = REPO_ROOT / "docs" / "maintenance-watchlist.md"
SLUG = "Danathar/zfs-aurora-complex"


def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def flat(text: str) -> str:
    return " ".join(text.split())


def bash_blocks() -> list[str]:
    return re.findall(r"(?ms)^```bash\n(.*?)```", doc_text())


class GoalSectionTests(unittest.TestCase):
    def test_the_four_documents_the_goal_links_exist(self) -> None:
        goal = doc_text().split("## Reading progress", 1)[0]
        targets = set(re.findall(r"\]\((\./[\w-]+\.md|\.\./README\.md)(?:#[\w-]+)?\)", goal))
        for expected in (
            "../README.md",
            "./safety-model.md",
            "./production-boundary-proposal.md",
            "./runtime-validation-proposal.md",
            "./maintenance-watchlist.md",
        ):
            with self.subTest(target=expected):
                self.assertIn(expected, targets)
                self.assertTrue((DOC.parent / expected).is_file())

    def test_the_status_the_page_gives_each_proposal_is_the_one_the_proposal_gives_itself(self) -> None:
        boundary = (REPO_ROOT / "docs" / "production-boundary-proposal.md").read_text("utf-8")
        runtime = (REPO_ROOT / "docs" / "runtime-validation-proposal.md").read_text("utf-8")
        self.assertIn("**Status: ADOPTED (2026-07-29).**", boundary)
        self.assertIn("adopted** on 2026-07-29", flat(doc_text()))
        self.assertIn("DECLINED by the maintainer, 2026-07-28", runtime)
        self.assertIn("**declined** a runtime test tier on 2026-07-28", flat(doc_text()))

    def test_the_main_only_signing_environment_exists_in_the_build(self) -> None:
        self.assertIn("`production-signing` environment", doc_text())
        self.assertIn("environment: production-signing", (WORKFLOWS / "build.yml").read_text("utf-8"))

    def test_the_readme_quotation_and_section_exist(self) -> None:
        readme = flat((REPO_ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIn("safe enough for one person's own machines", readme)
        self.assertIn("safe enough for one person's own machines", doc_text())
        self.assertRegex((REPO_ROOT / "README.md").read_text("utf-8"), r"(?m)^## Why This Repo Exists$")

    def test_the_sentence_it_quotes_from_the_metrics_page_is_there(self) -> None:
        metrics = (REPO_ROOT / "docs" / "metrics.md").read_text(encoding="utf-8")
        self.assertIn("There is no metrics service and no scheduled collector", flat(metrics))
        self.assertIn("There is no metrics service and no scheduled collector", flat(doc_text()))


class CommandTests(unittest.TestCase):
    def test_the_watchlist_command_slices_between_headings_that_exist(self) -> None:
        text = WATCHLIST.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^## Open items$")
        self.assertRegex(text, r"(?m)^## Related")
        self.assertIn("sed -n '/^## Open items/,/^## Related/p' docs/maintenance-watchlist.md", doc_text())

    def test_that_command_lists_at_least_one_open_entry(self) -> None:
        # Printing nothing reads as "nothing is open"; it must be the headings' doing, not a typo's.
        section = re.search(r"(?ms)^## Open items$(.*?)^## Related", WATCHLIST.read_text("utf-8"))
        self.assertIsNotNone(section)
        self.assertRegex(section.group(1), r"(?m)^### ")

    def test_the_pool_grep_the_page_says_prints_nothing_prints_nothing(self) -> None:
        self.assertIn("grep -rniE 'zpool (create|import)' .github/workflows", doc_text())
        result = subprocess.run(
            ["grep", "-rniE", "zpool (create|import)", ".github/workflows"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(result.stdout, "")

    def test_every_gh_command_names_this_repository(self) -> None:
        commands = [c for block in bash_blocks() for c in re.split(r"\n(?=\S)", block) if c.startswith("gh ")]
        self.assertGreaterEqual(len(commands), 4)
        for command in commands:
            with self.subTest(command=command.splitlines()[0]):
                self.assertIn(f"-R {SLUG}", command)

    def test_the_search_limit_the_prose_states_is_the_commands(self) -> None:
        self.assertEqual(flat(doc_text()).count("`--limit 200` caps the list"), 1)
        merged = next(b for b in bash_blocks() if "headRefName" in b)
        self.assertIn("--limit 200", merged)

    def test_the_build_workflow_command_is_the_one_the_metrics_page_gives(self) -> None:
        schedule = "gh run list --workflow build.yml --event schedule --limit 30"
        metrics = flat((REPO_ROOT / "docs" / "metrics.md").read_text("utf-8"))
        self.assertIn(schedule, metrics)
        self.assertIn(schedule.replace("gh run list", f"gh run list -R {SLUG}"), flat(doc_text()))


class ScheduledReportTests(unittest.TestCase):
    def test_the_workflows_with_a_schedule_are_the_ones_the_page_names(self) -> None:
        scheduled = sorted(p.name for p in WORKFLOWS.glob("*.yml") if re.search(r"(?m)^  schedule:", p.read_text("utf-8")))
        sentence = flat(doc_text()).split("that do exist** are", 1)[1].split("and `auto-issues.yml`", 1)[0]
        named = sorted(re.findall(r"`([\w-]+\.yml)`", sentence))
        self.assertEqual(named, scheduled)

    def test_the_report_workflow_the_page_says_is_absent_is_absent(self) -> None:
        self.assertIn("`.github/workflows/strategy-report.yml`", doc_text())
        self.assertFalse((WORKFLOWS / "strategy-report.yml").exists())

    def test_the_policy_file_it_says_covers_every_workflow_names_every_workflow(self) -> None:
        import json

        policy = json.loads((REPO_ROOT / ".github" / "policies" / "workflow-permissions.json").read_text("utf-8"))
        text = json.dumps(policy)
        for workflow in WORKFLOWS.glob("*.yml"):
            with self.subTest(workflow=workflow.name):
                self.assertIn(workflow.name, text)


if __name__ == "__main__":
    unittest.main()
