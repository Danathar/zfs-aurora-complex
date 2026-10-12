"""
Script: tests/test_acmm_waivers.py
What: Joins .acmm.yml's waivers to the tree they make claims about.
Doing: Reads the file as text, with no YAML parser. For every waiver, checks that each in-repository file it names as present exists, and that each workflow it says this repository deliberately does not have is absent.
Why: A waiver says "this criterion is met somewhere else, and the file is absent on purpose". The absence half is a claim about the tree: add `ai-fix.yml` and the waiver becomes a false statement that an in-repo agent path does not exist, in a repository where docs/SECURITY-AI.md and docs/multi-agent.md both rest on that.
Goal: Make an in-repo AI workflow appear, or a file a waiver relies on disappear, fail here.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ACMM = REPO_ROOT / ".acmm.yml"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# Workflow filenames the waivers say this repository does not have.
ABSENT_BY_DESIGN = (
    "ai-fix.yml",
    "copilot-review-apply.yml",
    "apply-copilot.yml",
    "auto-review.yml",
)


def waivers() -> dict[str, str]:
    """{waiver id: its text}, split on the `- id:` lines."""

    text = ACMM.read_text(encoding="utf-8").split("\nwaivers:\n", 1)[1]
    parts = re.split(r"(?m)^  - id: (\S+)\n", text)
    return dict(zip(parts[1::2], parts[2::2]))


class WaiverTests(unittest.TestCase):
    def test_the_scan_finds_the_waivers(self) -> None:
        self.assertGreaterEqual(len(waivers()), 4)
        for waiver_id in waivers():
            self.assertTrue(waiver_id.startswith("acmm:"), waiver_id)

    def test_every_waiver_says_who_satisfies_it(self) -> None:
        for waiver_id, body in waivers().items():
            with self.subTest(waiver=waiver_id):
                self.assertRegex(body, r"(?m)^    satisfied_by: \S")
                self.assertRegex(body, r"(?m)^    reason: >$")

    def test_the_workflows_the_waivers_say_are_absent_are_absent(self) -> None:
        text = ACMM.read_text(encoding="utf-8")
        for name in ABSENT_BY_DESIGN:
            with self.subTest(workflow=name):
                self.assertIn(name, text, "the waiver no longer mentions this file; update the list")
                self.assertFalse((WORKFLOWS / name).exists())

    def test_the_files_a_waiver_names_as_satisfying_it_exist(self) -> None:
        for waiver_id, body in waivers().items():
            satisfied = re.search(r"(?m)^    satisfied_by: (.+)$", body).group(1)
            for path in re.findall(r"[\w./-]+\.(?:md|yml)", satisfied):
                with self.subTest(waiver=waiver_id, path=path):
                    self.assertTrue((REPO_ROOT / path).is_file())

    def test_every_repository_document_the_reasons_cite_exists(self) -> None:
        # The header cites a document in Hive's repository; only this repository's are checked.
        ours = "\n".join(l for l in ACMM.read_text("utf-8").splitlines() if "hivecommons/hive" not in l)
        for path in sorted(set(re.findall(r"\bdocs/[\w-]+\.md\b", ours))):
            with self.subTest(path=path):
                self.assertTrue((REPO_ROOT / path).is_file())

    def test_the_hive_section_the_header_cites_exists(self) -> None:
        security = (REPO_ROOT / "docs" / "SECURITY-AI.md").read_text(encoding="utf-8")
        self.assertIn('"One autonomous path, and it is Hive"', ACMM.read_text("utf-8").replace("\n#", "\n"))
        self.assertRegex(security, r"(?m)^## One autonomous path, and it is Hive$")


if __name__ == "__main__":
    unittest.main()
