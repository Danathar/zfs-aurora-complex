"""
Script: tests/test_risk_tiers_doc.py
What: Joins every tracked file to a tier of docs/risk-tiers.md, the way the page itself assigns one.
Doing: Reads each tier's assigning paragraph for its paths and its `area/*` labels, expands the labels through `.github/labeler.yml`, and set-compares what is left over to a ledger.
Why: The page tells a reviewer how much scrutiny a change needs, and a file it assigns to no tier gets none by default.
Goal: Make a new file that lands in no tier -- or a ledger entry that has quietly gained one -- fail here.

The page has four tests that read it, and each asks one question of one
section: is `.github/rulesets/` in Tier 3, does Tier 3 cover the seven rule 2
files, does every Tier 3 path earn `area/safety-critical`, does Tier 0 repeat
the `paths-ignore` rule. None of them asks the page's own question in the other
direction -- given a file in this tree, which tier is it?

The page answers that in two ways, and this file uses both:

  * Each tier opens with a paragraph that names paths (`tests/`, `Containerfile`,
    the Tier 3 bullet list). Those paths are that tier's.
  * The same paragraph ends "Labelled `area/...`", and "Assigning a tier when the
    labels disagree" says to take the highest tier any label implies. So every
    file a label's globs match in `.github/labeler.yml` is in that label's tier.

A tracked file that neither reaches is in no tier.

Which tier the rest belong in is a maintainer's decision, not a test's. So
they sit in UNCLASSIFIED below, and the comparison is set EQUALITY: a new file
in no tier fails until it is tiered or recorded, and a recorded file that has
since been tiered (or deleted) fails until its entry is removed. The ledger can
only shrink by someone deciding.
"""

from __future__ import annotations

import json
import re
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only where PyYAML is absent
    yaml = None

from tests.test_labeler_config import (
    REPO_ROOT,
    glob_to_regex,
    label_globs,
    tracked_files,
)

RISK_TIERS = REPO_ROOT / "docs" / "risk-tiers.md"
LABELER = REPO_ROOT / ".github" / "labeler.yml"

TIER_HEADING_RE = re.compile(r"^### Tier (\d) ")
BACKTICKED_RE = re.compile(r"`([^`]+)`")
LABEL_RE = re.compile(r"`(area/[\w-]+)`")
# "`AGENTS.md` section 0 rule 2" cites a file for its contents; it does not put
# AGENTS.md in the tier.
CITATION_RE = re.compile(r"`[^`]+` section \d")

# Backticked tokens in an assigning paragraph that are not a path from the
# repository root: None for a non-path, else where the file really is. Every
# other token must exist as written.
NOT_ROOT_PATHS: dict[str, str | None] = {
    "main": None,  # the branch
    "packages: write": None,  # a permission
    "branch-protection.md": "docs/branch-protection.md",  # link text, relative to docs/
    "build.yml": ".github/workflows/build.yml",  # "every workflow and not only `build.yml`"
    "usr/lib/modules-load.d/zfs.conf": "files/usr/lib/modules-load.d/zfs.conf",  # under `files/`
    "usr/lib/tmpfiles.d/zfs-aurora-complex.conf": "files/usr/lib/tmpfiles.d/zfs-aurora-complex.conf",
    "defaults.json": "ci/defaults.json",  # "`ci/` (the parts of `defaults.json` ...)"
    "inputs.lock.json": "ci/inputs.lock.json",
    # The ci/defaults.json keys Tier 3 names; checked against the file below.
    "DEFAULT_ZFS_MINOR_VERSION": None,
    "DEFAULT_BASE_IMAGE": None,
    "STABLE_SIGNAL_IMAGE": None,
    "DEFAULT_BUILD_CONTAINER_IMAGE": None,
}

# Tracked files that docs/risk-tiers.md assigns to no tier: no path in any
# tier's assigning paragraph covers them, and no `area/*` label the page maps to
# a tier matches them in .github/labeler.yml. Each value says what the file is.
# Picking a tier for one is a maintainer's call; when it is made, delete the
# entry here in the same pull request.
UNCLASSIFIED = {
    ".github/ISSUE_TEMPLATE/bug-report.yml": "issue form; no label covers ISSUE_TEMPLATE",
    ".github/ISSUE_TEMPLATE/build-failure.yml": "issue form; no label covers ISSUE_TEMPLATE",
    ".github/ISSUE_TEMPLATE/config.yml": "issue chooser config",
    ".github/ISSUE_TEMPLATE/coverage-gap.yml": "issue form; no label covers ISSUE_TEMPLATE",
    "pyproject.toml": "ruff configuration the CI unit job runs under",
    ".acmm.yml": "ACMM waiver declarations read by the Hive evaluator",
}


def paragraphs(lines: list[str]) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
        elif current:
            out.append("\n".join(current))
            current = []
    if current:
        out.append("\n".join(current))
    return out


def assigning_blocks(text: str) -> dict[int, str]:
    """{tier: the text that says what is in it}.

    That is the paragraph right under the heading, plus any bullet list directly
    after it (Tier 3's). It stops at the next paragraph -- "**What it requires:**"
    or, for Tier 0, "Worth knowing" -- because those name files as examples or
    consequences, not as members.
    """

    lines = text.splitlines()
    starts = [
        (i, int(m.group(1))) for i, line in enumerate(lines) if (m := TIER_HEADING_RE.match(line))
    ]
    blocks: dict[int, str] = {}
    for start, tier in starts:
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("#")),
            len(lines),
        )
        paras = paragraphs(lines[start + 1 : end])
        taken = paras[:1]
        for para in paras[1:]:
            if not para.lstrip().startswith("- "):
                break
            taken.append(para)
        blocks[tier] = "\n\n".join(taken)
    return blocks


def named_paths(block: str) -> list[str]:
    """Backticked tokens in a block that are paths in this tree."""

    block = CITATION_RE.sub("", block)
    found = []
    for token in BACKTICKED_RE.findall(block):
        # "`ci/defaults.json`'s ZFS and base-image values" still names the file.
        candidate = token.rstrip("/")
        path_shaped = "/" in token or "." in token or candidate[:1].isupper()
        if path_shaped and (REPO_ROOT / candidate).exists():
            found.append(token)
    return found


def covers(path: str, file: str) -> bool:
    if path.endswith("/"):
        return file.startswith(path)
    return file == path


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class EveryTrackedFileHasATier(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = RISK_TIERS.read_text(encoding="utf-8")
        cls.blocks = assigning_blocks(cls.text)
        cls.globs = label_globs(yaml.safe_load(LABELER.read_text(encoding="utf-8")))
        cls.tracked = tracked_files()
        cls.tier_labels = {tier: LABEL_RE.findall(block) for tier, block in cls.blocks.items()}
        cls.tier_paths = {tier: named_paths(block) for tier, block in cls.blocks.items()}

    def tier_of(self, file: str) -> set[int]:
        tiers = set()
        for tier in self.blocks:
            if any(covers(path, file) for path in self.tier_paths[tier]):
                tiers.add(tier)
            for label in self.tier_labels[tier]:
                if any(glob_to_regex(g).match(file) for g in self.globs.get(label, [])):
                    tiers.add(tier)
        return tiers

    def test_the_parse_finds_four_tiers(self) -> None:
        self.assertEqual(sorted(self.blocks), [0, 1, 2, 3])
        self.assertGreater(len(self.tracked), 100)

    def test_the_assigning_paragraphs_are_the_ones_expected(self) -> None:
        # Anchors on each block's opening so a parser that grabs the wrong
        # paragraph fails here, not as a mysterious ledger diff.
        self.assertIn("seven files", self.blocks[3])
        self.assertIn("`.github/rulesets/`", self.blocks[3])
        self.assertIn("The rest of the image", self.blocks[2])
        self.assertIn("`tests/`", self.blocks[1])
        self.assertIn("`README.md` badges", self.blocks[0])
        for tier, block in self.blocks.items():
            self.assertNotIn("What it requires", block, f"Tier {tier}")
            self.assertNotIn("Worth knowing", block, f"Tier {tier}")

    def test_a_citation_does_not_put_its_file_in_a_tier(self) -> None:
        self.assertIn("`AGENTS.md` section 0 rule 2", self.blocks[3])
        self.assertNotIn("AGENTS.md", self.tier_paths[3])

    def test_every_path_a_tier_names_is_in_the_tree(self) -> None:
        # named_paths() drops what does not exist, so check every token here: a
        # rename the page did not follow would otherwise just vanish from the tier.
        seen: set[str] = set()
        for tier, block in self.blocks.items():
            for token in BACKTICKED_RE.findall(CITATION_RE.sub("", block)):
                if token.startswith("area/"):
                    continue
                seen.add(token)
                target = NOT_ROOT_PATHS.get(token, token)
                if target is None:
                    continue
                with self.subTest(tier=tier, token=token):
                    self.assertTrue(
                        (REPO_ROOT / target.rstrip("/")).exists(),
                        f"Tier {tier} names `{token}`, which is not in the tree",
                    )
        self.assertEqual(
            sorted(set(NOT_ROOT_PATHS) - seen),
            [],
            "NOT_ROOT_PATHS entries no tier paragraph uses any more; delete them",
        )

    def test_the_defaults_keys_tier_3_names_exist(self) -> None:
        defaults = json.loads((REPO_ROOT / "ci" / "defaults.json").read_text(encoding="utf-8"))
        for key in (
            "DEFAULT_ZFS_MINOR_VERSION",
            "DEFAULT_BASE_IMAGE",
            "STABLE_SIGNAL_IMAGE",
            "DEFAULT_BUILD_CONTAINER_IMAGE",
        ):
            with self.subTest(key=key):
                self.assertIn(f"`{key}`", self.blocks[3])
                self.assertIn(key, defaults)

    def test_every_label_the_labeler_attaches_is_in_exactly_one_tier(self) -> None:
        by_label: dict[str, list[int]] = {}
        for tier, labels in self.tier_labels.items():
            for label in labels:
                by_label.setdefault(label, []).append(tier)
        self.assertEqual(
            sorted(by_label),
            sorted(self.globs),
            "docs/risk-tiers.md and .github/labeler.yml disagree on which area/* labels exist",
        )
        for label, tiers in by_label.items():
            self.assertEqual(len(tiers), 1, f"{label} is claimed by Tiers {tiers}")

    def test_the_files_in_no_tier_are_exactly_the_ledger(self) -> None:
        untiered = {f for f in self.tracked if not self.tier_of(f)}
        new = sorted(untiered - set(UNCLASSIFIED))
        self.assertEqual(
            new,
            [],
            "tracked files docs/risk-tiers.md puts in no tier; give each a tier "
            "(a path in a tier's opening paragraph, or an area/* label) or record "
            "it in UNCLASSIFIED",
        )
        stale = sorted(set(UNCLASSIFIED) - untiered)
        self.assertEqual(
            stale,
            [],
            "UNCLASSIFIED entries that now have a tier or are no longer tracked; delete them",
        )

    def test_known_files_land_in_the_tier_the_page_says(self) -> None:
        # Spot checks through both routes: a named path and a label.
        expected = {
            "ci_tools/promote_stable.py": 3,
            ".github/rulesets/main.json": 3,
            "Containerfile": 2,
            ".github/actions/build-native-image/action.yml": 2,
            "docs/risk-tiers.md": 1,
            "tests/test_cli.py": 1,
            # The agent permission boundary: Tier 1 by path, Tier 3 by what a
            # change to it lets every later session do unprompted.
            "tests/run_tests.py": 3,
            ".claude/settings.json": 3,
            ".claude/hooks/gate-git-diff.sh": 3,
            ".editorconfig": 0,
            "LICENSE": 0,
            # The image payload: build-image.sh installs these onto every
            # booted machine, so reverting one is a Tier 3 change.
            "files/usr/lib/modules-load.d/zfs.conf": 3,
            "files/usr/lib/tmpfiles.d/zfs-aurora-complex.conf": 3,
        }
        for file, tier in expected.items():
            with self.subTest(file=file):
                self.assertEqual(max(self.tier_of(file)), tier)


if __name__ == "__main__":
    unittest.main()
