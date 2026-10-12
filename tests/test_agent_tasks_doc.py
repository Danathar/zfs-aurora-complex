"""
Script: tests/test_agent_tasks_doc.py
What: Joins docs/agent-tasks/README.md to the tree, the ruleset, the audit workflow and the session summary it makes claims about.
Doing: Checks that the directories the page says do not exist are absent, that docs/agent-tasks/ holds only the README, that the Hive signature prefix and app login it quotes are the ones agent-audit.yml matches, that the merge methods it lists are the ruleset's, that every `gh` command targets this repository, and that .claude/session-summary.md says what the page says it says.
Why: The page tells a reader where an agent's task trail lives and what the one workflow that reads it back enforces. A second task log would make it false; a changed signature prefix would send a reader searching for a line the audit no longer looks for.
Goal: Make a new `.agent/tasks/` or log directory, a changed signature or app login, a changed merge method, or a command pointed at another repository fail here.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGE = REPO_ROOT / "docs" / "agent-tasks" / "README.md"
AUDIT = REPO_ROOT / ".github" / "workflows" / "agent-audit.yml"
RULESET = REPO_ROOT / ".github" / "rulesets" / "main.json"
SESSION_SUMMARY = REPO_ROOT / ".claude" / "session-summary.md"

SLUG = "Danathar/zfs-aurora-complex"
SIGNATURE_PREFIX = "— hive: agent="
HIVE_APP = "app/danathar-atomic-hive"


def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def flat(text: str) -> str:
    return " ".join(text.split())


class NoTaskLogTests(unittest.TestCase):
    def test_the_directories_the_page_says_are_absent_are_absent(self) -> None:
        self.assertIn("no `.agent/tasks/` directory and no `.github/agent-log/` directory", flat(page()))
        for path in (".agent/tasks", ".agent", ".github/agent-log"):
            with self.subTest(path=path):
                self.assertFalse((REPO_ROOT / path).exists())

    def test_this_directory_holds_only_the_page(self) -> None:
        self.assertEqual([p.name for p in PAGE.parent.iterdir()], ["README.md"])


class SignatureTests(unittest.TestCase):
    def test_the_prefix_the_page_quotes_is_the_one_the_audit_matches(self) -> None:
        self.assertIn(SIGNATURE_PREFIX, page())
        audit = AUDIT.read_text(encoding="utf-8")
        # The audit's jq reads `— hive: ` then `key=value`; the page's prefix is the same line.
        self.assertIn('"^— hive: [a-z_]+="', audit)
        self.assertIn("— hive: ", SIGNATURE_PREFIX)
        self.assertIn('ltrimstr("— hive: ")', audit)

    def test_the_app_login_is_the_one_the_audit_treats_as_hive(self) -> None:
        self.assertIn(f"`{HIVE_APP}`", page())
        self.assertIn(f'.author.login == "{HIVE_APP}"', AUDIT.read_text(encoding="utf-8"))

    def test_the_audit_checks_the_two_things_the_page_says_it_checks(self) -> None:
        audit = AUDIT.read_text(encoding="utf-8")
        self.assertIn("Signed-off-by", audit)
        self.assertIn("agent-audit.yml", page())
        self.assertIn("`Signed-off-by:` trailer", page())


class RulesetTests(unittest.TestCase):
    def test_the_merge_methods_the_page_lists_are_the_rulesets(self) -> None:
        rules = json.loads(RULESET.read_text(encoding="utf-8"))["rules"]
        (pull_request,) = [r for r in rules if r["type"] == "pull_request"]
        methods = pull_request["parameters"]["allowed_merge_methods"]
        self.assertEqual(sorted(methods), ["merge", "rebase", "squash"])
        self.assertIn("the ruleset allows `merge`, `squash` and `rebase`", flat(page()))


class CommandTests(unittest.TestCase):
    def test_every_command_that_names_a_repository_names_this_one(self) -> None:
        slugs = set(re.findall(r"(?:-R |repos/)([\w.-]+/[\w.-]+)", page()))
        self.assertTrue(slugs)
        self.assertEqual(slugs, {SLUG})

    def test_every_repository_path_the_page_links_exists(self) -> None:
        for target in re.findall(r"\]\((\.\./[^)#]+)\)", page()):
            with self.subTest(target=target):
                self.assertTrue((PAGE.parent / target).exists())


class SessionSummaryTests(unittest.TestCase):
    def test_the_summary_says_it_is_not_a_changelog(self) -> None:
        header = SESSION_SUMMARY.read_text(encoding="utf-8").split("\n## ", 1)[0]
        self.assertIn("Not a changelog", header)
        self.assertIn("`.claude/session-summary.md`", page())


if __name__ == "__main__":
    unittest.main()
