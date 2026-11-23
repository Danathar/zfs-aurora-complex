"""
Script: tests/test_codeowners.py
What: Joins `.github/CODEOWNERS` to the owner-merge paths, to the `area/safety-critical` globs in `.github/labeler.yml`, and to the ruleset.
Doing: Parses each CODEOWNERS line, checks it is exactly the nine owner-merge paths, resolves each against the tracked tree, and checks every owned file also carries the safety-critical label.
Why: By the owner's decision of 2026-11-23, GitHub no longer requires code-owner review. The Hive's human_merge_paths setting holds the image-signing paths, every workflow, the rulesets and this file for a person to merge, and CODEOWNERS mirrors that list so GitHub asks the owner to review them. A line dropped here stops that request for a signing path; a catch-all line would ask the owner to review every pull request.
Goal: Make a dropped or added line, a stray `*`, or code-owner review turned back on in the ruleset fail here, in the pull request that does it.

The Hive setting itself is not in this repository, so OWNER_MERGE_PATHS below
is the copy of it this test checks against. Change both together.

tests/test_labeler_config.py already joins the label to the Tier 3 list in
docs/risk-tiers.md both ways, so the owner-merge paths are also Tier 3.
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

# The paths the Hive's auto_merge.human_merge_paths lists for this repository
# (owner decision 2026-11-23, with workflows added back the same day), in CODEOWNERS
# syntax.
OWNER_MERGE_PATHS = (
    "/cosign.pub",
    "/ci_tools/sign_image.py",
    "/ci_tools/promote_stable.py",
    "/files/scripts/configure_signing_policy.py",
    "/.github/actions/install-signing-tools/",
    "/.github/actions/publish-native-image/",
    "/.github/workflows/",
    "/.github/rulesets/",
    "/.github/CODEOWNERS",
)


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

    def test_the_file_lists_exactly_the_owner_merge_paths(self) -> None:
        self.assertEqual(sorted(p for p, _ in self.entries), sorted(OWNER_MERGE_PATHS))

    def test_there_is_no_catch_all_line(self) -> None:
        # A catch-all would ask the owner to review every pull request.
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
        # A line with no owner un-owns its path, so GitHub stops asking the owner to review it.
        for pattern, owners in self.entries:
            with self.subTest(pattern=pattern):
                self.assertEqual(owners, [OWNER])

    def test_every_pattern_matches_a_tracked_file(self) -> None:
        for pattern, _ in self.entries:
            with self.subTest(pattern=pattern):
                self.assertNotEqual(owned(pattern, self.tracked), [], f"{pattern} owns nothing")

    def test_the_file_owns_itself(self) -> None:
        # The Hive holds a change to this file for the owner too, so it lists itself.
        self.assertIn("/.github/CODEOWNERS", [pattern for pattern, _ in self.entries])

    def test_the_ruleset_does_not_require_code_owner_review(self) -> None:
        # Owner decision 2026-11-23. Turning it back on would put every pull request that
        # touches these paths behind an approval, which the docs no longer describe.
        rules = json.loads(RULESET.read_text(encoding="utf-8"))["rules"]
        (pull_request,) = [rule for rule in rules if rule["type"] == "pull_request"]
        self.assertIs(pull_request["parameters"]["require_code_owner_review"], False)


@unittest.skipIf(yaml is None, "PyYAML not installed")
class CodeownersMatchTheSafetyLabel(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tracked = tracked_files()
        cls.owned = {f for pattern, _ in entries() for f in owned(pattern, cls.tracked)}
        globs = label_globs(yaml.safe_load(LABELER.read_text(encoding="utf-8")))[SAFETY_LABEL]
        regexes = [glob_to_regex(g) for g in globs]
        cls.labelled = {f for f in cls.tracked if any(r.match(f) for r in regexes)}

    def test_every_owned_file_is_safety_critical(self) -> None:
        self.assertGreater(len(self.owned), 5)
        self.assertEqual(
            sorted(self.owned - self.labelled),
            [],
            "CODEOWNERS owns files that are not Tier 3",
        )


if __name__ == "__main__":
    unittest.main()
