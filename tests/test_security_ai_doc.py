"""
Script: tests/test_security_ai_doc.py
What: Joins docs/SECURITY-AI.md to the workflows, actions and tools its claims about secrets, workflow triggers and the safety-critical file list rest on.
Doing: Reads the workflows as text (no YAML parser). Checks that the seven files the page lists as needing a human decision are the ones AGENTS.md section 0 rule 2 names, that `SIGNING_SECRET` is read only by build.yml jobs that declare the `production-signing` environment, that no secret other than `SIGNING_SECRET` and `GITHUB_TOKEN` is referenced anywhere, and that each workflow's trigger is what the trigger table says.
Why: The page is the statement of what an agent may touch and what a branch can cause. Its secret inventory and trigger table are copies of workflow facts; a second secret, a signing step outside the protected environment, or a changed trigger makes the page understate the blast radius.
Goal: Make a new secret, an unprotected use of the signing secret or a changed trigger fail here until the page says so.

The agent-permission table is joined to `.claude/settings.json` in tests/test_agent_permission_boundary.py; the label vocabulary is joined to the labeler in tests/test_labeler_config.py; the ruleset is joined in tests/test_branch_protection.py. This file covers the rest.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.test_labeler_config import resolve, rule_2_paths, tracked_files

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "SECURITY-AI.md"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
ACTIONS = REPO_ROOT / ".github" / "actions"

SECRET_REF_RE = re.compile(r"secrets\.([A-Za-z0-9_]+)")
# Secrets the page's inventory lists.
INVENTORY = {"SIGNING_SECRET", "GITHUB_TOKEN"}


def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def flat(text: str) -> str:
    return " ".join(text.split())


def workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def uncommented(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def jobs(text: str) -> dict[str, str]:
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"(?m)^  ([\w-]+):\n", body)
    return dict(zip(parts[1::2], parts[2::2]))


class SafetyCriticalListTests(unittest.TestCase):
    def test_the_human_decision_list_is_rule_2(self) -> None:
        section = doc_text().split("Changing what gets signed, how tags propagate, or the promotion path", 1)[1]
        sentence = flat(section).split("- **Widening", 1)[0]
        named = re.findall(r"`([^`]+\.(?:py|yml))`|`(publish-native-image)`", sentence)
        names = [a or f".github/actions/{b}" for a, b in named]
        tracked = tracked_files()
        ours = sorted(resolve(n, tracked) for n in names)
        self.assertEqual(len(ours), 7, names)
        self.assertEqual(ours, sorted(resolve(p, tracked) for p in rule_2_paths("AGENTS.md")))

    def test_the_four_files_of_the_permission_boundary_exist(self) -> None:
        table = doc_text().split("## The agent permission boundary", 1)[1].split("What that boundary does", 1)[0]
        paths = re.findall(r"\]\((\.\./[^)]+)\)", table)
        self.assertEqual(len(paths), 4)
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue((DOC.parent / path).exists())


class SecretInventoryTests(unittest.TestCase):
    def test_no_secret_is_referenced_beyond_the_inventory(self) -> None:
        used: set[str] = set()
        for path in [*WORKFLOWS.glob("*.yml"), *ACTIONS.glob("*/action.yml")]:
            used.update(SECRET_REF_RE.findall(uncommented(path.read_text("utf-8"))))
        self.assertEqual(used - INVENTORY, set(), "the page's inventory says nothing else in CI is secret")
        self.assertIn("SIGNING_SECRET", used)

    def test_the_inventory_table_lists_exactly_those_two(self) -> None:
        table = doc_text().split("### Secret inventory", 1)[1].split("\n\n", 3)[1]
        listed = set(re.findall(r"(?m)^\| `([A-Z_]+)` \|", table))
        self.assertEqual(listed, INVENTORY)

    def test_only_build_yml_reads_the_signing_secret(self) -> None:
        readers = [
            p.name for p in WORKFLOWS.glob("*.yml") if "secrets.SIGNING_SECRET" in uncommented(p.read_text("utf-8"))
        ]
        self.assertEqual(readers, ["build.yml"])
        # The composite action receives it as an input, from build.yml's job.
        action = (ACTIONS / "publish-native-image" / "action.yml").read_text("utf-8")
        self.assertNotIn("secrets.", uncommented(action))

    def test_every_job_that_reads_it_declares_the_production_signing_environment(self) -> None:
        reading = {n: b for n, b in jobs(workflow("build.yml")).items() if "secrets.SIGNING_SECRET" in uncommented(b)}
        self.assertTrue(reading, "no build.yml job reads the signing secret any more")
        for name, block in reading.items():
            with self.subTest(job=name):
                self.assertRegex(block, r"(?m)^    environment: production-signing$")

    def test_pr_runs_never_name_the_signing_environment_or_key(self) -> None:
        for name in ("build-pr.yml",):
            with self.subTest(workflow=name):
                text = uncommented(workflow(name))
                self.assertNotIn("production-signing", text)
                self.assertNotIn("COSIGN_PRIVATE_KEY", text)
                self.assertNotIn("SIGNING_SECRET", text)

    def test_the_secret_command_the_page_calls_good_names_this_repository(self) -> None:
        self.assertIn("gh secret set SIGNING_SECRET -R Danathar/zfs-aurora-complex < cosign.key", doc_text())


class TriggerTableTests(unittest.TestCase):
    def table(self) -> dict[str, str]:
        section = doc_text().split("## What a branch or pull request can actually cause here", 1)[1]
        section = section.split("So a branch pushed", 1)[0]
        rows = {}
        for line in section.splitlines():
            match = re.match(r"^\| `([\w.-]+\.yml)` \| (.*?) \| (.*) \|$", line)
            if match:
                rows[match.group(1)] = match.group(2)
        return rows

    def test_the_table_has_the_two_build_workflows(self) -> None:
        self.assertEqual(sorted(self.table()), ["build-pr.yml", "build.yml"])

    def test_each_trigger_is_the_workflows(self) -> None:
        on = {name: workflow(name).split("\njobs:\n", 1)[0] for name in self.table()}
        self.assertRegex(on["build.yml"], r"(?m)^  schedule:")
        self.assertRegex(on["build.yml"], r"(?ms)^  push:\n    branches:\n      - main")
        self.assertRegex(on["build.yml"], r"(?m)^  workflow_dispatch:")
        self.assertRegex(on["build-pr.yml"], r"(?m)^  pull_request:")

    def test_the_pull_request_workflow_stops_before_push_or_signing(self) -> None:
        text = uncommented(workflow("build-pr.yml"))
        self.assertNotIn("sign-image", text)
        self.assertNotIn("promote-stable", text)
        self.assertNotRegex(text, r"(?m)^\s+packages: write")


class NoModelKeyTests(unittest.TestCase):
    def test_no_workflow_or_action_references_a_model_provider_key(self) -> None:
        self.assertIn("No workflow here holds a model API key", doc_text())
        for path in [*WORKFLOWS.glob("*.yml"), *ACTIONS.glob("*/action.yml")]:
            with self.subTest(path=path.name):
                self.assertNotRegex(path.read_text("utf-8"), r"(?i)anthropic|openai|_api_key")


if __name__ == "__main__":
    unittest.main()
