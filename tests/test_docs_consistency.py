"""
Script: tests/test_docs_consistency.py
What: Holds the mechanically checkable claims the documentation makes about the tree, across every tracked Markdown file.
Doing: Resolves every relative link and every link anchor, checks that every workflow and every composite action is named by some document, and scans the Markdown for phrasings that say `main` is unprotected now that a ruleset is applied.
Why: Doc drift is a defect in this repository, not a nit -- AGENTS.md section 0 rule 3 exists because it has happened. Several dozen pages now cross-link; a renamed heading or moved file breaks links nobody re-reads.
Goal: Turn "someone should re-read the docs" into something CI does on every pull request.

Only the claims a machine can settle. Whether a sentence is *true* is a review question; whether the file it names exists is not, and that is the class of error that actually accumulates. The per-document joins live in each page's own test (tests/test_*_doc.py); the documentation map's coverage is tests/test_documentation_guide_doc.py.

No PyYAML and no third-party parser: this file must run on a runner that installed only pytest.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
ACTION_DIR = REPO_ROOT / ".github" / "actions"

# Markdown inline links. Bare `<http://...>` autolinks and reference-style
# definitions are not used in this tree, so this is the whole surface.
LINK_RE = re.compile(r"\]\(([^)\s]+)\)")

# Phrasings that said `main` was unprotected before the ruleset was applied. A document that
# needs to say it again should first be sure it is, then change this list. The historical
# record in docs/production-boundary-proposal.md says "no branch protection" in the past tense
# and is not matched by these.
STALE_UNPROTECTED = (
    r"`main` is not branch-protected",
    r"\*\*not branch-protected\*\*",
    r"`main` is not protected",
    r"branch protection if it is ever enabled",
    r"no ruleset or branch protection",
    r"main has no protection",
)


def workflow_paths() -> list[Path]:
    """Every workflow GitHub runs: each `.yml` and `.yaml` file in WORKFLOW_DIR."""

    return sorted([*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")])


def action_paths() -> list[Path]:
    """Every local composite action: each `action.yml` and `action.yaml` one level under ACTION_DIR."""

    return sorted([*ACTION_DIR.glob("*/action.yml"), *ACTION_DIR.glob("*/action.yaml")])


def tracked_markdown() -> list[Path]:
    listing = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "*.md"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.split()
    return [REPO_ROOT / name for name in listing]


def heading_slugs(path: Path) -> set[str]:
    """GitHub's anchor slugs for a markdown file's headings."""

    slugs = set()
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        if in_fence or not line.startswith("#"):
            continue
        heading = line.lstrip("#").strip()
        slugs.add(re.sub(r"[^\w\s-]", "", heading).strip().lower().replace(" ", "-"))
    return slugs


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class LinkTests(unittest.TestCase):
    def test_the_scan_finds_something(self) -> None:
        # Guard the guard: every assertion below reports an empty list on
        # success, and an empty list is also what a broken scan produces.
        self.assertGreater(len(tracked_markdown()), 30)
        self.assertGreater(sum(len(LINK_RE.findall(d.read_text("utf-8"))) for d in tracked_markdown()), 200)

    def test_every_relative_link_resolves(self) -> None:
        broken = []
        for doc in tracked_markdown():
            for match in LINK_RE.finditer(doc.read_text(encoding="utf-8")):
                target = match.group(1)
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                path = target.partition("#")[0]
                if not path:
                    continue
                if not (doc.parent / path).exists():
                    broken.append(f"{doc.relative_to(REPO_ROOT)} -> {target}")
        self.assertEqual(broken, [], "relative links that do not resolve")

    def test_every_link_anchor_names_a_real_heading(self) -> None:
        broken = []
        for doc in tracked_markdown():
            for match in LINK_RE.finditer(doc.read_text(encoding="utf-8")):
                target = match.group(1)
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                path, _, anchor = target.partition("#")
                if not anchor:
                    continue
                destination = doc if not path else doc.parent / path
                if not destination.exists() or destination.suffix != ".md":
                    continue
                if anchor not in heading_slugs(destination):
                    broken.append(f"{doc.relative_to(REPO_ROOT)} -> {target}")
        self.assertEqual(broken, [], "link anchors naming no such heading")


class WorkflowCoverageTests(unittest.TestCase):
    def test_every_workflow_is_described_somewhere_in_the_docs(self) -> None:
        # A workflow nothing documents is one nobody knows runs.
        prose = "\n".join(doc.read_text(encoding="utf-8") for doc in tracked_markdown())
        undocumented = [path.name for path in workflow_paths() if path.name not in prose]
        self.assertEqual(undocumented, [], "workflows named in no document")

    def test_every_composite_action_is_named_by_a_document(self) -> None:
        prose = "\n".join(doc.read_text(encoding="utf-8") for doc in tracked_markdown())
        actions = action_paths()
        self.assertGreater(len(actions), 1)
        unnamed = [a.parent.name for a in actions if a.parent.name not in prose]
        self.assertEqual(unnamed, [], "composite actions named in no document")

    def test_every_action_directory_has_an_action_file(self) -> None:
        self.assertEqual(
            {path.parent for path in action_paths()},
            {path for path in ACTION_DIR.iterdir() if path.is_dir()},
            "a directory under .github/actions has no action.yml or action.yaml",
        )

    def test_quality_page_covers_every_workflow_that_can_fail_a_change(self) -> None:
        quality = (REPO_ROOT / "docs" / "quality.md").read_text(encoding="utf-8")
        for workflow in ("build.yml", "build-pr.yml", "test.yml"):
            with self.subTest(workflow=workflow):
                self.assertIn(workflow, quality)


class RulesetRecordTests(unittest.TestCase):
    def test_no_document_says_main_is_unprotected(self) -> None:
        hits = []
        for doc in tracked_markdown():
            text = squash(doc.read_text(encoding="utf-8"))
            for pattern in STALE_UNPROTECTED:
                for match in re.finditer(pattern, text):
                    hits.append(f"{doc.relative_to(REPO_ROOT)}: ...{text[max(0, match.start() - 40) : match.end() + 20]}...")
        self.assertEqual(hits, [])

    def test_the_proposal_that_declined_the_ruleset_says_it_was_superseded(self) -> None:
        # The proposal's Status table says there was no ruleset on 2026-07-29. A ruleset file now
        # exists; the page has to say so, or a reader concludes `main` is open.
        self.assertTrue((REPO_ROOT / ".github" / "rulesets" / "main.json").is_file())
        proposal = squash((REPO_ROOT / "docs" / "production-boundary-proposal.md").read_text("utf-8"))
        self.assertIn("branch-protection.md", proposal)
        self.assertIn("Superseded on 2026-10-11", proposal)


if __name__ == "__main__":
    unittest.main()
