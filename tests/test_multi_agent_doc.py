"""
Script: tests/test_multi_agent_doc.py
What: Joins docs/multi-agent.md to the workflows, ruleset, settings and renovate.json it makes claims about.
Doing: Reads the workflows as text (no YAML parser) and the ruleset, settings and renovate.json as JSON, and checks that the orchestrator paths the page lists as absent are absent, that no workflow names a model provider or reacts to a label, that the concurrency and ruleset values it quotes are the files' values, and that the merge denial and no-automerge claims hold.
Why: The page says this repository has no in-repo agent path and no automation that merges. Those are statements about the absence of things, which nothing else fails when someone adds one, and the ruleset and concurrency values are copies that drift.
Goal: Make adding an orchestrator, a model-calling workflow, a label trigger or an automerge rule, or editing a quoted value, fail here until the page is changed in the same pull request.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "multi-agent.md"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RULESET = REPO_ROOT / ".github" / "rulesets" / "main.json"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
RENOVATE = REPO_ROOT / "renovate.json"

# The grep the page tells the reader to run.
MODEL_PROVIDER_RE = re.compile(r"anthropic|openai|claude-code|codex", re.IGNORECASE)


def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def workflow_files() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml"))


def ruleset_rule(kind: str) -> dict:
    rules = json.loads(RULESET.read_text(encoding="utf-8"))["rules"]
    (rule,) = [r for r in rules if r["type"] == kind]
    return rule


class NoOrchestratorTests(unittest.TestCase):
    def listed_paths(self) -> list[str]:
        block = re.search(r"```text\n(.*?)```", doc_text(), flags=re.DOTALL)
        self.assertIsNotNone(block, "the page no longer lists the orchestrator paths")
        return [line.strip() for line in block.group(1).splitlines() if line.strip()]

    def test_the_list_has_the_paths_it_claims(self) -> None:
        self.assertEqual(len(self.listed_paths()), 5)

    def test_none_of_the_listed_paths_exists(self) -> None:
        present = [p for p in self.listed_paths() if (REPO_ROOT / p.rstrip("/")).exists()]
        self.assertEqual(present, [], "the page says these do not exist")

    def test_no_workflow_names_a_model_provider_or_agent_cli(self) -> None:
        # The page tells the reader that this grep prints nothing.
        named = [w.name for w in workflow_files() if MODEL_PROVIDER_RE.search(w.read_text("utf-8"))]
        self.assertEqual(named, [], "the page says no workflow runs an AI model")

    def test_no_workflow_reacts_to_a_label_or_a_comment(self) -> None:
        # "No workflow reacts to a label": an `issues`, `issue_comment` or `labeled` trigger
        # would be the way an issue label hands work to an agent.
        offenders = []
        for workflow in workflow_files():
            text = workflow.read_text(encoding="utf-8")
            if re.search(r"(?m)^  (issues|issue_comment|pull_request_review_comment):", text) or re.search(
                r"\blabeled\b", text
            ):
                offenders.append(workflow.name)
        self.assertEqual(offenders, [])


class ConcurrencyClaimTests(unittest.TestCase):
    def concurrency(self, name: str) -> str:
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        match = re.search(r"(?m)^concurrency:\n((?:  .*\n|\n)+)", text)
        self.assertIsNotNone(match, f"{name} has no concurrency block")
        return match.group(1)

    def test_the_three_pull_request_workflows_cancel_an_older_run_for_the_same_ref(self) -> None:
        for name in ("test.yml", "build-pr.yml", "build-branch.yml"):
            with self.subTest(workflow=name):
                block = self.concurrency(name)
                self.assertIn("github.ref", block)
                self.assertIn("cancel-in-progress: true", block)

    def test_main_runs_never_race_to_publish(self) -> None:
        block = self.concurrency("build.yml")
        self.assertIn("${{ github.workflow }}-${{ github.ref", block)
        self.assertIn("cancel-in-progress: true", block)


class RulesetClaimTests(unittest.TestCase):
    def test_strict_required_status_checks_policy_is_false(self) -> None:
        params = ruleset_rule("required_status_checks")["parameters"]
        self.assertIs(params["strict_required_status_checks_policy"], False)
        self.assertIn("`strict_required_status_checks_policy` to `false`", " ".join(doc_text().split()))

    def test_the_required_check_is_the_one_the_page_names(self) -> None:
        params = ruleset_rule("required_status_checks")["parameters"]
        contexts = [c["context"] for c in params["required_status_checks"]]
        self.assertEqual(contexts, ["Python Unit Tests"])
        self.assertIn("The required check is `Python Unit Tests`", doc_text())

    def test_no_approval_and_no_bypass(self) -> None:
        self.assertEqual(ruleset_rule("pull_request")["parameters"]["required_approving_review_count"], 0)
        self.assertEqual(json.loads(RULESET.read_text("utf-8"))["bypass_actors"], [])
        text = " ".join(doc_text().split())
        self.assertIn("it requires no approving review, and it allows no bypass actors", text)

    def test_there_is_no_merge_queue_rule(self) -> None:
        # The page says the queue is unavailable; a ruleset that grew a merge_queue rule makes
        # the "serialise by hand" advice stale.
        types = [r["type"] for r in json.loads(RULESET.read_text("utf-8"))["rules"]]
        self.assertNotIn("merge_queue", types)


class MergeClaimTests(unittest.TestCase):
    def test_settings_deny_gh_pr_merge(self) -> None:
        deny = json.loads(SETTINGS.read_text("utf-8"))["permissions"]["deny"]
        self.assertIn("Bash(gh pr merge:*)", deny)

    def test_renovate_has_no_automerge_turned_on(self) -> None:
        text = RENOVATE.read_text(encoding="utf-8")
        self.assertNotRegex(text, r'"automerge"\s*:\s*true')
        self.assertIn('"automerge": false', text)
        self.assertNotIn("automergeType", text)

    def test_the_rule_the_page_cites_is_rule_6_and_says_what_the_page_says(self) -> None:
        agents = " ".join((REPO_ROOT / "AGENTS.md").read_text("utf-8").split())
        self.assertRegex(agents, r"6\. \*\*Do not push, promote, tag, or delete published artifacts")
        self.assertIn("section 0 rule 6 says not to push, promote, tag, or delete", " ".join(doc_text().split()))

    def test_no_label_in_the_labeler_reads_as_an_auto_merge_approval(self) -> None:
        labeler = (REPO_ROOT / ".github" / "labeler.yml").read_text(encoding="utf-8")
        labels = re.findall(r"(?m)^(['\"]?)([^\s#'\"][^:]*)\1:\s*$", labeler)
        self.assertGreater(len(labels), 3)
        for _, label in labels:
            with self.subTest(label=label):
                self.assertTrue(label.startswith("area/"), label)


class InstructionFileTests(unittest.TestCase):
    def test_every_tool_row_points_at_a_file_that_exists(self) -> None:
        for path in ("CLAUDE.md", "AGENTS.md", "GEMINI.md", ".claude", ".cursor/rules", ".github/copilot-instructions.md"):
            with self.subTest(path=path):
                self.assertTrue((REPO_ROOT / path).exists())
                self.assertIn(path, doc_text())


if __name__ == "__main__":
    unittest.main()
