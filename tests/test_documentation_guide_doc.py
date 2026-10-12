"""
Script: tests/test_documentation_guide_doc.py
What: Joins docs/documentation-guide.md's tree and reading paths, and README.md's router table, to the Markdown files the repository actually tracks.
Doing: Lists the tracked documents with `git ls-files` and checks each is on the guide's tree (a directory entry stands for the files under it), that every path the tree names exists, that every tracked `docs/*.md` is reachable from the README router and from a reading path, and that every link in a reading path resolves.
Why: The guide's last rule is "add every new document to the tree above and to the router table in README.md in the same change", and nothing enforces it. A document missing from both is one nobody is told about.
Goal: Make a document added without a place on the map, a tree entry for a file that was removed, or a router row pointing nowhere fail here.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUIDE = REPO_ROOT / "docs" / "documentation-guide.md"
README = REPO_ROOT / "README.md"

# Entries the tree lists by their directory, in the guide's own words: dated readings, dated
# lessons, and the task-trail directory whose only file is its README.
DIRECTORY_ENTRIES = ("docs/metrics/", "docs/reflections/", "docs/agent-tasks/")
# Instruction files the tree lists on one line.
AGENT_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")


def tracked_markdown() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    return sorted(out.split())


def tree_block() -> str:
    match = re.search(r"## Documentation Tree\n\n```text\n(.*?)```", GUIDE.read_text("utf-8"), re.DOTALL)
    assert match, "docs/documentation-guide.md has no Documentation Tree block"
    return match.group(1)


def tree_paths() -> set[str]:
    """Every path the tree names, as a repository-relative path.

    A line with no indent either names files (`README.md`, or `AGENTS.md / CLAUDE.md / GEMINI.md`)
    or a directory (`docs/`) whose indented lines follow.
    """

    paths: set[str] = set()
    prefix = ""
    for line in tree_block().splitlines():
        if not line.strip():
            continue
        names = line.split("<-")[0].strip()
        if line.startswith(" "):
            paths.add(prefix + names.split()[0])
        elif names.endswith("/"):
            prefix = names
            paths.add(names)
        else:
            prefix = ""
            paths.update(name.strip() for name in names.split(" / "))
    return paths


def readme_router() -> str:
    """The router table under 'Start here depending on what you want'."""

    return README.read_text(encoding="utf-8").split("Start here depending on what you want:", 1)[1].split("\n\n", 2)[1]


def reading_paths() -> str:
    return GUIDE.read_text("utf-8").split("## What To Read First (By Goal)", 1)[1].split("## Where To Put", 1)[0]


class TreeTests(unittest.TestCase):
    def test_the_tree_parses_into_something_plausible(self) -> None:
        paths = tree_paths()
        for expected in ("README.md", "CONTRIBUTING.md", "docs/glossary.md", "docs/metrics/", "tests/e2e/README.md"):
            self.assertIn(expected, paths)

    def test_every_path_the_tree_names_exists(self) -> None:
        missing = sorted(p for p in tree_paths() if not (REPO_ROOT / p.rstrip("/")).exists())
        self.assertEqual(missing, [])

    def test_every_tracked_document_is_on_the_map(self) -> None:
        paths = tree_paths()
        unmapped = []
        for doc in tracked_markdown():
            # A file is on the map when the tree names it, or names the README of the directory
            # that lists it (the prompts, commands and corrections directories each do).
            directory = doc.rsplit("/", 1)[0] if "/" in doc else ""
            readme = f"{directory}/README.md" if directory else "README.md"
            if doc in paths or readme in paths or any(doc.startswith(d) for d in DIRECTORY_ENTRIES if d in paths):
                continue
            unmapped.append(doc)
        self.assertEqual(unmapped, [], "tracked Markdown files the guide's tree does not name")

    def test_the_agent_instruction_files_the_tree_lines_up_exist(self) -> None:
        for name in AGENT_FILES:
            self.assertTrue((REPO_ROOT / name).is_file())
        self.assertIn("AGENTS.md / CLAUDE.md / GEMINI.md", tree_block())


class RouterTests(unittest.TestCase):
    def test_every_top_level_document_is_reachable_from_the_readme(self) -> None:
        router = README.read_text(encoding="utf-8")
        unrouted = [
            doc
            for doc in tracked_markdown()
            if doc.startswith("docs/")
            and doc.count("/") == 1
            and doc != "docs/documentation-guide.md"
            and f"({'./' + doc})" not in router
        ]
        self.assertEqual(unrouted, [], "docs/*.md the README router does not link")

    def test_the_router_reaches_the_guide_and_the_three_directories(self) -> None:
        router = readme_router()
        self.assertIn("(./docs/documentation-guide.md)", router)
        for directory in ("reflections", "agent-tasks"):
            self.assertIn(f"./docs/{directory}/README.md", router)

    def test_every_router_target_exists(self) -> None:
        for target in re.findall(r"\]\((\./[^)#]+)\)", readme_router()):
            with self.subTest(target=target):
                self.assertTrue((REPO_ROOT / target).exists())


class ReadingPathTests(unittest.TestCase):
    def test_every_top_level_document_is_on_a_reading_path(self) -> None:
        text = reading_paths()
        off = [
            doc
            for doc in tracked_markdown()
            if doc.startswith("docs/") and doc.count("/") == 1 and doc != "docs/documentation-guide.md"
            and f"./{Path(doc).name}" not in text and f"docs/{Path(doc).name}" not in text
        ]
        self.assertEqual(off, [], "docs/*.md no 'What To Read First' goal sends a reader to")

    def test_every_link_in_a_reading_path_resolves(self) -> None:
        for target in re.findall(r"\]\(([^)#:]+)(?:#[^)]*)?\)", reading_paths()):
            with self.subTest(target=target):
                self.assertTrue((GUIDE.parent / target).resolve().exists())


if __name__ == "__main__":
    unittest.main()
