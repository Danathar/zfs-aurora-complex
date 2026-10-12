"""
Script: tests/test_review_rubric_doc.py
What: Joins docs/review-rubric.md to the safety-critical list in the agent docs, the files it links, and the config it tells a reviewer to check.
Doing: Reads rubric section 2's list of "the seven files" and compares it to AGENTS.md section 0 rule 2 (via tests/test_labeler_config.py's reader), checks every file and prompt the page points at exists, and checks the claims it makes about `.coverage-thresholds.json`, `renovate.json` and `paths-ignore`.
Why: Section 2 is the rubric's blocking check: a reviewer applies it to the files it lists. A list that drifts from rule 2 either asks for a statement on a file that no longer needs one or, worse, stops asking for one on a file that does.
Goal: Make the rubric's seven files, its prompt link and its config claims fail here when rule 2 or the config moves.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tests.test_labeler_config import resolve, rule_2_paths, tracked_files

REPO_ROOT = Path(__file__).resolve().parent.parent
RUBRIC = REPO_ROOT / "docs" / "review-rubric.md"
BACKTICKED_RE = re.compile(r"`([^`\n]+)`")


def rubric() -> str:
    return RUBRIC.read_text(encoding="utf-8")


def section(number: int) -> str:
    match = re.search(rf"(?ms)^## {number}\. .*?\n(.*?)(?=^## |\Z)", rubric())
    if match is None:
        raise AssertionError(f"docs/review-rubric.md has no '## {number}.' section")
    return match.group(1)


def flat(text: str) -> str:
    return " ".join(text.split())


class SectionTwoListTests(unittest.TestCase):
    def named_files(self) -> list[str]:
        sentence = flat(section(2)).split("rule 2:", 1)[1].split("The pull request template", 1)[0]
        return [t for t in BACKTICKED_RE.findall(sentence) if "." in t]

    def test_it_names_seven_files(self) -> None:
        self.assertEqual(len(self.named_files()), 7, self.named_files())
        self.assertIn("the seven files", flat(section(2)))

    def test_they_are_exactly_the_files_rule_2_names(self) -> None:
        tracked = tracked_files()
        ours = sorted(resolve(p, tracked) for p in self.named_files())
        for doc in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
            with self.subTest(doc=doc):
                theirs = sorted(resolve(p, tracked) for p in rule_2_paths(doc))
                self.assertEqual(ours, theirs)


class LinkedFileTests(unittest.TestCase):
    def test_the_safety_critical_prompt_is_the_one_the_rubric_expands(self) -> None:
        prompt = REPO_ROOT / ".github" / "prompts" / "review-safety-critical-change.prompt.md"
        self.assertTrue(prompt.is_file())
        self.assertIn("review-safety-critical-change.prompt.md", rubric())

    def test_the_corrections_file_the_tests_section_cites_exists(self) -> None:
        self.assertIn(".claude/memory/corrections.md", section(5))
        self.assertTrue((REPO_ROOT / ".claude" / "memory" / "corrections.md").is_file())

    def test_the_agent_pointer_files_section_7_calls_pointers_exist(self) -> None:
        for path in (".github/copilot-instructions.md", ".cursor/rules", ".claude/commands"):
            with self.subTest(path=path):
                self.assertTrue((REPO_ROOT / path).exists())


class ConfigClaimTests(unittest.TestCase):
    def test_the_coverage_thresholds_file_it_tells_the_reviewer_to_check_exists(self) -> None:
        data = json.loads((REPO_ROOT / ".coverage-thresholds.json").read_text("utf-8"))
        self.assertTrue(data)
        self.assertIn("`.coverage-thresholds.json`", section(5))

    def test_renovate_refuses_to_automerge_an_openzfs_bump(self) -> None:
        config = json.loads((REPO_ROOT / "renovate.json").read_text("utf-8"))
        rules = [r for r in config["packageRules"] if "openzfs/zfs" in r.get("matchPackageNames", [])]
        self.assertTrue(rules)
        self.assertTrue(all(r.get("automerge") is False for r in rules))

    def test_a_documentation_only_change_starts_no_build(self) -> None:
        # Section 7: "paths-ignore on **/*.md and docs/**" for the build and the PR image check.
        for name in ("build.yml", "build-pr.yml"):
            with self.subTest(workflow=name):
                text = (REPO_ROOT / ".github" / "workflows" / name).read_text("utf-8")
                block = text.split("paths-ignore:", 1)[1].split("\n\n", 1)[0]
                self.assertRegex(block, r"docs/\*\*")
                self.assertRegex(block, r"\*\*/\*\.md")

    def test_the_tier_page_and_the_template_it_points_at_exist(self) -> None:
        for path in ("docs/risk-tiers.md", "CONTRIBUTING.md", ".github/pull_request_template.md"):
            self.assertTrue((REPO_ROOT / path).is_file(), path)


if __name__ == "__main__":
    unittest.main()
