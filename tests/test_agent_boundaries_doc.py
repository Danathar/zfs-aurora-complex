"""
Script: tests/test_agent_boundaries_doc.py
What: Joins docs/agent-boundaries.md to the files its gate table and its CODEOWNERS section rest
on -- .claude/settings.json, .github/rulesets/main.json and .github/policies/workflow-permissions.json
-- and to the repository paths it names.
Doing: Reads each claim the page makes about what is denied, what only prompts, which hook is
registered, what the ruleset requires, and recomputes it
from the file, as text or JSON, with no YAML parser.
Why: The page is the one place that says which limits on an agent a tool enforces. Every row is
a fact about a file someone can change without opening the page. A row that still says "denied"
after the deny rule went, or a CODEOWNERS row after code-owner review was switched off,
tells a reader a gate exists that does not.
Goal: Make the page fail here when the gates move under it.

The linked pages are not re-checked here. tests/test_docs_consistency.py resolves every link and
anchor, and each linked page has a doc test of its own.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "agent-boundaries.md"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
RULESET = REPO_ROOT / ".github" / "rulesets" / "main.json"

# Paths the page names in backticks. Globs and placeholders are excluded by the character class.
_PATH_RE = re.compile(r"`((?:\.github|\.claude|docs|tests)/[A-Za-z0-9_./-]+)`")

# The three places GitHub reads a CODEOWNERS file from.
CODEOWNERS_PATHS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Collapse line wrapping, so a phrase is found wherever the page happens to break it."""
    return " ".join(text.split())


def _section(heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", _doc(), flags=re.DOTALL | re.MULTILINE
    )
    if match is None:
        raise AssertionError(f"docs/agent-boundaries.md has no '## {heading}' section")
    return match.group(1)


def _gate_row(gate: str) -> str:
    """The table row whose first cell links or names `gate`."""
    rows = [
        line
        for line in _section("The gates").splitlines()
        if line.startswith("|") and gate in line.split("|")[1]
    ]
    if len(rows) != 1:
        raise AssertionError(f"expected one gate row for {gate}, found {len(rows)}")
    return rows[0]


def _permissions() -> dict[str, list[str]]:
    return json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]


class NamedPathTests(unittest.TestCase):
    def test_every_path_the_page_names_exists(self) -> None:
        paths = _PATH_RE.findall(_doc())
        self.assertGreaterEqual(len(paths), 5, "the page names almost no repository paths")
        missing = sorted(p for p in set(paths) if not (REPO_ROOT / p).exists())
        self.assertEqual(missing, [], "docs/agent-boundaries.md names paths that do not exist")


class SettingsRowTests(unittest.TestCase):
    """The `.claude/settings.json` and hook rows against the settings file."""

    def test_what_the_row_calls_denied_is_denied(self) -> None:
        row = _gate_row(".claude/settings.json")
        deny = set(_permissions()["deny"])
        claims = {
            "`cosign.key`": "Read(./cosign.key)",
            "`.env`": "Read(./.env)",
            "Signing": "Bash(cosign sign:*)",
            "registry pushes": "Bash(podman push:*)",
            "merging": "Bash(gh pr merge:*)",
            "dispatching a workflow": "Bash(gh workflow run:*)",
            "releases": "Bash(gh release:*)",
            "secrets": "Bash(gh secret set:*)",
            "labels": "Bash(gh label create:*)",
            "`git reset --hard`": "Bash(git reset --hard:*)",
            "`git clean`": "Bash(git clean:*)",
            "`zpool`": "Bash(zpool:*)",
        }
        for phrase, rule in claims.items():
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, row)
                self.assertIn(rule, deny)

    def test_what_the_row_says_only_asks_does_ask(self) -> None:
        self.assertIn("It asks before an ordinary push, commit or pull request", _gate_row(".claude/settings.json"))
        ask = set(_permissions()["ask"])
        for rule in ("Bash(git push:*)", "Bash(git commit:*)", "Bash(gh pr create:*)"):
            with self.subTest(rule=rule):
                self.assertIn(rule, ask)

    def test_the_label_rule_is_split_where_the_page_splits_it(self) -> None:
        # The page says creating, editing or deleting a label is denied and adding one
        # to a pull request only prompts. Denying `gh pr edit` would make that false.
        text = _flat(_section("What is asked, not enforced"))
        self.assertIn("denies creating, editing or deleting a label", text)
        self.assertIn("with `gh pr edit` only prompts", text)
        permissions = _permissions()
        for verb in ("create", "edit", "delete"):
            with self.subTest(verb=verb):
                self.assertIn(f"Bash(gh label {verb}:*)", permissions["deny"])
        self.assertIn("Bash(gh pr edit:*)", permissions["ask"])
        self.assertNotIn("Bash(gh pr edit:*)", permissions["deny"])

    def test_the_hook_the_page_names_is_the_one_registered(self) -> None:
        _gate_row(".claude/hooks/gate-git-diff.sh")
        hooks = json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        commands = [hook["command"] for entry in hooks for hook in entry["hooks"]]
        self.assertEqual(commands, ['"$CLAUDE_PROJECT_DIR"/.claude/hooks/gate-git-diff.sh'])

    def test_the_runner_is_the_only_test_command_allowed(self) -> None:
        # The page says the runner is a gate only because the settings file allows it
        # and not pytest itself.
        self.assertIn("allows it and not `pytest` itself", _flat(_section("The gates")))
        allow = _permissions()["allow"]
        self.assertIn("Bash(python3 tests/run_tests.py:*)", allow)
        self.assertEqual([rule for rule in allow if "pytest" in rule or "unittest" in rule], [])

    def test_only_the_claude_rows_say_claude_code_only(self) -> None:
        claude_only = [
            line.split("|")[1]
            for line in _section("The gates").splitlines()
            if line.startswith("|") and "Claude Code sessions only" in line
        ]
        self.assertEqual(len(claude_only), 3, claude_only)
        for cell in claude_only:
            with self.subTest(cell=cell):
                self.assertRegex(cell, r"\.claude/|tests/run_tests\.py")


class RulesetRowTests(unittest.TestCase):
    """The first two gate rows against the ruleset and the workflow job."""

    def test_the_ruleset_row_says_no_bypass_and_the_file_has_none(self) -> None:
        self.assertIn("It has no bypass actors.", _gate_row(".github/rulesets/main.json"))
        ruleset = json.loads(RULESET.read_text(encoding="utf-8"))
        self.assertEqual(ruleset["bypass_actors"], [])
        types = {rule["type"] for rule in ruleset["rules"]}
        self.assertLessEqual({"deletion", "non_fast_forward", "pull_request"}, types)

    def test_the_required_check_is_a_job_name_in_test_yml(self) -> None:
        row = _gate_row("Python Unit Tests")
        self.assertIn("test.yml", row)
        ruleset = json.loads(RULESET.read_text(encoding="utf-8"))
        (checks,) = [r for r in ruleset["rules"] if r["type"] == "required_status_checks"]
        contexts = [c["context"] for c in checks["parameters"]["required_status_checks"]]
        self.assertEqual(contexts, ["Python Unit Tests"])
        workflow = (REPO_ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        self.assertRegex(workflow, r"(?m)^    name: Python Unit Tests$")

    def test_the_policy_file_the_row_names_is_the_one_the_test_reads(self) -> None:
        test_source = (REPO_ROOT / "tests" / "test_workflow_permissions_policy.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow-permissions.json", test_source)
        self.assertIn(".github/policies/workflow-permissions.json", _gate_row("workflow-permissions.json"))


class CodeownersSectionTests(unittest.TestCase):
    """'Why CODEOWNERS lists only Tier 3' and the CODEOWNERS gate row against the ruleset and the tree."""

    def test_the_ruleset_asks_for_code_owner_review_and_no_other_approval(self) -> None:
        text = _flat(_section("Why CODEOWNERS lists only Tier 3"))
        self.assertIn("sets `require_code_owner_review` to `true` and needs no other approval", text)
        rules = json.loads(RULESET.read_text(encoding="utf-8"))["rules"]
        (pull_request,) = [rule for rule in rules if rule["type"] == "pull_request"]
        self.assertIs(pull_request["parameters"]["require_code_owner_review"], True)
        self.assertEqual(pull_request["parameters"]["required_approving_review_count"], 0)

    def test_the_one_codeowners_file_is_the_one_the_gate_row_names(self) -> None:
        # GitHub reads the first of these it finds. A second copy elsewhere would leave
        # the page describing a file GitHub ignores.
        present = [path for path in CODEOWNERS_PATHS if (REPO_ROOT / path).exists()]
        self.assertEqual(present, [".github/CODEOWNERS"])
        self.assertIn("`require_code_owner_review`", _gate_row(".github/CODEOWNERS"))


if __name__ == "__main__":
    unittest.main()
