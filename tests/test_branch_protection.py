"""
Script: tests/test_branch_protection.py
What: Joins docs/branch-protection.md to .github/rulesets/main.json, the ruleset that keeps `main`
behind a pull request, and joins the ruleset's required check to the workflow job that reports it.
Doing: Compares the page's rule bullets and commands to the JSON (rule types, target, bypass list,
approval count, required check names and `integration_id`). Reads test.yml as text, with no YAML
parser, and checks the required check is a job named exactly that, that the workflow triggers on
every pull request (a bare `pull_request:` with no path or branch filter), and that the job has no
`if:`, `needs:` or matrix. Checks the ruleset id the page records is the one the `PUT` updates.
Why: Merging to `main` publishes, and the ruleset is what stops a direct push. It can break in two
quiet ways. A loosened file (a bypass actor, a dropped rule) reopens the direct push the next time
someone `PUT`s it. A required check that some pull requests never get leaves them waiting forever,
and the obvious fix then is deleting the ruleset. The page explaining it is a second copy of the
JSON and can drift from it.
Goal: Make a loosened ruleset, a renamed or filtered required job, or a page that no longer
matches the file fail here, on the pull request that causes it.

What this cannot check: the live ruleset is repository configuration, and reading it needs the
GitHub API. The page carries the commands that show it.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGE = REPO_ROOT / "docs" / "branch-protection.md"
RULESET = REPO_ROOT / ".github" / "rulesets" / "main.json"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
TEST_WORKFLOW = WORKFLOWS / "test.yml"
RISK_TIERS = REPO_ROOT / "docs" / "risk-tiers.md"
SECURITY = REPO_ROOT / "docs" / "SECURITY-AI.md"

GITHUB_ACTIONS_APP_ID = 15368
REPO_SLUG = "Danathar/zfs-aurora-complex"
RULE_TYPES = {"deletion", "non_fast_forward", "pull_request", "required_status_checks"}
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4}

BACKTICKED_RE = re.compile(r"`([^`]+)`")


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def outside_fences(text: str) -> str:
    kept, fenced = [], False
    for line in text.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            fenced = not fenced
            continue
        if not fenced:
            kept.append(line)
    return "\n".join(kept)


def section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if match is None:
        raise AssertionError(f"{PAGE.name} has no '## {heading}' section")
    return match.group(1)


def bullets(body: str) -> list[str]:
    """Top-level `- ` bullets with their continuation lines, squashed."""
    items: list[str] = []
    current: str | None = None
    for line in body.splitlines():
        if line.startswith("- "):
            if current is not None:
                items.append(squash(current))
            current = line[2:]
        elif current is not None and line.startswith("  "):
            current += " " + line
        elif current is not None:
            items.append(squash(current))
            current = None
    if current is not None:
        items.append(squash(current))
    return items


def load_ruleset() -> dict:
    return json.loads(RULESET.read_text(encoding="utf-8"))


def rules_by_type(ruleset: dict) -> dict[str, dict]:
    return {rule["type"]: rule.get("parameters", {}) for rule in ruleset["rules"]}


def required_checks(ruleset: dict) -> list[dict]:
    return rules_by_type(ruleset).get("required_status_checks", {}).get("required_status_checks", [])


def job_blocks(workflow_text: str) -> dict[str, str]:
    """Map each top-level job id in a workflow to its text, without a YAML parser."""
    _, _, jobs = workflow_text.partition("\njobs:\n")
    blocks: dict[str, str] = {}
    current: str | None = None
    for line in jobs.splitlines():
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if match:
            current = match.group(1)
            blocks[current] = ""
        elif current is not None:
            blocks[current] += line + "\n"
    return blocks


class RulesetKeepsMainBehindAPullRequest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ruleset = load_ruleset()
        cls.rules = rules_by_type(cls.ruleset)

    def test_it_is_enforced_on_the_default_branch(self) -> None:
        self.assertEqual(self.ruleset["target"], "branch")
        self.assertEqual(self.ruleset["enforcement"], "active")
        self.assertEqual(self.ruleset["conditions"]["ref_name"]["include"], ["~DEFAULT_BRANCH"])
        self.assertEqual(self.ruleset["conditions"]["ref_name"].get("exclude", []), [])

    def test_nothing_may_bypass_it(self) -> None:
        # A bypass for Actions or for an App hands back the direct push, and it is the fix
        # someone reaches for the first time a workflow's push to main is refused.
        self.assertEqual(self.ruleset.get("bypass_actors"), [])

    def test_it_carries_the_four_rules_and_no_others(self) -> None:
        self.assertEqual(set(self.rules), RULE_TYPES)
        self.assertEqual(len(self.ruleset["rules"]), len(RULE_TYPES), "a rule type is listed twice")

    def test_a_pull_request_needs_no_approval_a_sole_maintainer_cannot_give(self) -> None:
        # Nobody can approve their own pull request. One approval on a single-maintainer
        # repository means nothing can merge, including the change that relaxes this.
        self.assertEqual(self.rules["pull_request"]["required_approving_review_count"], 0)

    def test_every_required_check_is_pinned_to_github_actions(self) -> None:
        checks = required_checks(self.ruleset)
        self.assertTrue(checks, "the ruleset requires no check")
        for check in checks:
            with self.subTest(context=check.get("context")):
                self.assertEqual(check.get("integration_id"), GITHUB_ACTIONS_APP_ID)


class EveryPullRequestGetsTheRequiredCheck(unittest.TestCase):
    """A required check a pull request never gets leaves that pull request unmergeable."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = TEST_WORKFLOW.read_text(encoding="utf-8")
        cls.contexts = [check["context"] for check in required_checks(load_ruleset())]

    def test_each_required_check_is_a_job_in_test_yml(self) -> None:
        self.assertTrue(self.contexts)
        names = {
            re.search(r"^    name:\s*(.+?)\s*$", block, re.MULTILINE).group(1)
            for block in job_blocks(self.text).values()
            if re.search(r"^    name:", block, re.MULTILINE)
        }
        for context in self.contexts:
            with self.subTest(context=context):
                self.assertIn(context, names, "no job reports this check; a pull request would wait forever")

    def test_the_job_runs_on_every_pull_request(self) -> None:
        header = self.text.split("\njobs:\n", 1)[0]
        # Bare `pull_request:` trigger, no `paths`/`branches`/`types` filter beneath it.
        self.assertRegex(header, r"(?m)^  pull_request:\s*$")
        trigger = re.search(r"(?m)^  pull_request:\s*\n((?:    .*\n)*)", header)
        self.assertEqual(trigger.group(1) if trigger else "", "", "the pull_request trigger is filtered")
        for context in self.contexts:
            for job_id, block in job_blocks(self.text).items():
                if re.search(rf"^    name:\s*{re.escape(context)}\s*$", block, re.MULTILINE):
                    with self.subTest(job=job_id):
                        for key in ("if:", "needs:", "strategy:"):
                            self.assertNotRegex(block, rf"(?m)^    {re.escape(key)}")


class ThePageMatchesTheRuleset(unittest.TestCase):
    """docs/branch-protection.md, joined to .github/rulesets/main.json both ways."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.page = PAGE.read_text(encoding="utf-8")
        cls.prose = outside_fences(cls.page)
        cls.ruleset = load_ruleset()
        cls.rules = rules_by_type(cls.ruleset)
        cls.contexts = [check["context"] for check in required_checks(cls.ruleset)]
        cls.leads: dict[str, str] = {}
        for item in bullets(section(cls.prose, "The ruleset")):
            match = re.match(r"\*\*(.+?)\*\*", item)
            if match is None:
                raise AssertionError(f"a rule bullet has no bold lead: {item[:60]}")
            cls.leads[match.group(1)] = item

    def lead(self, pattern: str) -> tuple[str, str]:
        found = [(key, body) for key, body in self.leads.items() if re.search(pattern, key, re.IGNORECASE)]
        self.assertEqual(len(found), 1, f"expected one rule bullet matching {pattern!r}: {sorted(self.leads)}")
        return found[0]

    def test_the_rules_the_page_explains_are_the_rules_in_the_file(self) -> None:
        named = set()
        for key in self.leads:
            named |= {tok for tok in BACKTICKED_RE.findall(key) if re.fullmatch(r"[a-z_]+", tok)}
            if re.search(r"\brequired checks?\b", key, re.IGNORECASE):
                named.add("required_status_checks")
        self.assertEqual(named, set(self.rules))

    def test_the_target_the_page_names_is_the_files(self) -> None:
        key, _ = self.lead(r"^Targets ")
        self.assertEqual(BACKTICKED_RE.findall(key), self.ruleset["conditions"]["ref_name"]["include"])

    def test_the_bypass_list_the_page_states_is_the_files(self) -> None:
        key, _ = self.lead(r"bypass")
        self.assertEqual(key.startswith("No bypass actors"), self.ruleset.get("bypass_actors") == [])

    def test_the_approval_count_the_page_states_is_the_files(self) -> None:
        key, _ = self.lead(r"`pull_request`")
        stated = re.search(r"with (\d+) approvals?", key)
        self.assertIsNotNone(stated, key)
        self.assertEqual(int(stated.group(1)), self.rules["pull_request"]["required_approving_review_count"])

    def test_the_required_checks_the_page_names_are_the_files(self) -> None:
        key, body = self.lead(r"required checks?")
        count = re.match(r"(\w+) required checks?", key, re.IGNORECASE)
        self.assertIsNotNone(count, key)
        self.assertEqual(NUMBER_WORDS.get(count.group(1).lower()), len(self.contexts))
        self.assertEqual(sorted(BACKTICKED_RE.findall(key)), sorted(self.contexts))
        stated_ids = {int(n) for n in re.findall(r"`integration_id` (\d+)", body)}
        self.assertEqual(stated_ids, {c["integration_id"] for c in required_checks(self.ruleset)})
        for wf in re.findall(r"`([\w-]+\.yml)`", body):
            self.assertTrue((WORKFLOWS / wf).is_file(), wf)

    def test_the_status_section_records_the_live_ruleset(self) -> None:
        status = squash(section(self.prose, "Status"))
        self.assertIn(f"lists `{self.ruleset['name']}`", status)
        ids = re.findall(r"as ruleset `(\d+)`", status)
        self.assertEqual(len(ids), 1, f"the Status section should record one numeric ruleset id: {status}")
        puts = re.findall(r"--method PUT repos/([\w.-]+/[\w.-]+)/rulesets/(\S+)", self.page)
        self.assertEqual(puts, [(REPO_SLUG, ids[0])])

    def test_every_command_on_the_page_names_this_repository_and_the_committed_file(self) -> None:
        slugs = set(re.findall(r"gh api (?:--method \w+ )?repos/([\w.-]+/[\w.-]+)/", self.page))
        self.assertEqual(slugs, {REPO_SLUG})
        for verb in ("POST", "PUT"):
            inputs = re.findall(rf"--method {verb} .*?\\\n\s*--input (\S+)", self.page)
            self.assertEqual(inputs, [".github/rulesets/main.json"], verb)

    def test_the_status_branch_it_leaves_uncovered_is_the_one_a_workflow_pushes(self) -> None:
        match = re.search(r"`status` branch that `([\w-]+\.yml)` pushes", squash(self.prose))
        self.assertIsNotNone(match, "the page no longer names the workflow that pushes `status`")
        source = (WORKFLOWS / match.group(1)).read_text(encoding="utf-8")
        self.assertRegex(source, r"git push \S+ HEAD:status\b")


class TheDocsThatRouteAChangeNameIt(unittest.TestCase):
    def test_risk_tiers_puts_the_ruleset_in_tier_3(self) -> None:
        text = RISK_TIERS.read_text(encoding="utf-8")
        tier_3 = text.split("### Tier 3", 1)[1].split("### Tier 2", 1)[0]
        self.assertIn("`.github/rulesets/`", tier_3)

    def test_security_ai_points_at_the_page(self) -> None:
        self.assertRegex(SECURITY.read_text(encoding="utf-8"), r"\(\.?/?branch-protection\.md[)#]")


if __name__ == "__main__":
    unittest.main()
