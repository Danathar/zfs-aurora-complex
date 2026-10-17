"""
Script: tests/test_codeowners.py
What: Joins `.github/CODEOWNERS` to the `area/safety-critical` globs in `.github/labeler.yml` and to the ruleset that makes it a gate.
Doing: Parses each CODEOWNERS line, resolves it against the tracked tree, and checks both ways that it owns exactly the files the safety-critical label matches.
Why: The ruleset requires the code owner's approval for these paths, and that approval is the only thing that keeps a person on the merge button for Tier 3. A path missing here merges on green checks alone, and a catch-all line puts every pull request behind an approval a sole maintainer cannot give their own.
Goal: Make a dropped line, a stray `*`, or a code-owner flag turned off fail here, in the pull request that does it.

tests/test_labeler_config.py already joins the label to the Tier 3 list in
docs/risk-tiers.md both ways, so joining CODEOWNERS to the label is enough to
join it to the page.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.test_labeler_config import SAFETY_LABEL, glob_to_regex, label_globs, tracked_files, yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CODEOWNERS = REPO_ROOT / ".github" / "CODEOWNERS"
LABELER = REPO_ROOT / ".github" / "labeler.yml"
RULESET = REPO_ROOT / ".github" / "rulesets" / "main.json"

OWNER = "@Danathar"


def entries() -> list[tuple[str, list[str]]]:
    """(pattern, owners) for every rule line, in file order."""
    found = []
    for line in CODEOWNERS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            pattern, *owners = line.split()
            found.append((pattern, owners))
    return found


def owned(pattern: str, tracked: list[str]) -> list[str]:
    """Tracked files an anchored CODEOWNERS pattern covers.

    Only the two anchored forms this file uses: `/dir/` owns everything under
    it, `/file` owns that file. test_every_pattern_is_anchored keeps it so.
    """
    path = pattern.lstrip("/")
    if path.endswith("/"):
        return [f for f in tracked if f.startswith(path)]
    return [f for f in tracked if f == path]


class CodeownersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entries = entries()
        cls.tracked = tracked_files()

    def test_the_file_has_rules(self) -> None:
        self.assertGreater(len(self.entries), 10)

    def test_there_is_no_catch_all_line(self) -> None:
        # A catch-all puts every pull request behind the owner's approval, and the owner
        # cannot approve their own.
        for pattern, _ in self.entries:
            with self.subTest(pattern=pattern):
                self.assertNotIn("*", pattern)
                self.assertNotIn(pattern, ("/", "/**"))

    def test_every_pattern_is_anchored(self) -> None:
        # An unanchored pattern matches at any depth, which owned() does not model.
        for pattern, _ in self.entries:
            with self.subTest(pattern=pattern):
                self.assertTrue(pattern.startswith("/"), pattern)

    def test_every_line_is_owned_by_the_owner(self) -> None:
        # A line with no owner un-owns its path, which turns the gate off for it.
        for pattern, owners in self.entries:
            with self.subTest(pattern=pattern):
                self.assertEqual(owners, [OWNER])

    def test_every_pattern_matches_a_tracked_file(self) -> None:
        for pattern, _ in self.entries:
            with self.subTest(pattern=pattern):
                self.assertNotEqual(owned(pattern, self.tracked), [], f"{pattern} owns nothing")

    def test_the_file_owns_itself(self) -> None:
        # Otherwise a pull request could drop a Tier 3 line and merge without the owner.
        self.assertIn("/.github/CODEOWNERS", [pattern for pattern, _ in self.entries])

    def test_the_ruleset_requires_code_owner_review(self) -> None:
        rules = json.loads(RULESET.read_text(encoding="utf-8"))["rules"]
        (pull_request,) = [rule for rule in rules if rule["type"] == "pull_request"]
        self.assertIs(pull_request["parameters"]["require_code_owner_review"], True)


@unittest.skipIf(yaml is None, "PyYAML not installed")
class CodeownersMatchTheSafetyLabel(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tracked = tracked_files()
        cls.owned = {f for pattern, _ in entries() for f in owned(pattern, cls.tracked)}
        globs = label_globs(yaml.safe_load(LABELER.read_text(encoding="utf-8")))[SAFETY_LABEL]
        regexes = [glob_to_regex(g) for g in globs]
        cls.labelled = {f for f in cls.tracked if any(r.match(f) for r in regexes)}

    def test_every_safety_critical_file_is_owned(self) -> None:
        self.assertGreater(len(self.labelled), 5)
        self.assertEqual(
            sorted(self.labelled - self.owned),
            [],
            "area/safety-critical files no CODEOWNERS line owns; they merge without the owner",
        )

    def test_every_owned_file_is_safety_critical(self) -> None:
        self.assertEqual(
            sorted(self.owned - self.labelled),
            [],
            "CODEOWNERS owns files that are not Tier 3; they now wait for the owner too",
        )


if __name__ == "__main__":
    unittest.main()
