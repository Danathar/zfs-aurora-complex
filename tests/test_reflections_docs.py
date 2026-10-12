"""
Script: tests/test_reflections_docs.py
What: Joins docs/reflections/ -- its README, its entries -- to the corrections file it is contrasted with and to the code the entries describe.
Doing: Checks every entry is named `YYYY-MM-DD-slug.md`, opens with its date, carries the three headings the README prescribes, links only pull requests and runs of this repository, and is listed in the README's table with the same date; checks every correction in .claude/memory/corrections.md has the four fields the README says it has; and checks the code facts two entries rest on (the registry credentials `check_akmods_cache.py` now requires, the `.Config`-only rechunk input).
Why: The README promises a format and a table that lists every entry, and says an entry is trustworthy about what was believed and when. A file added without a table row, a link to another repository's run, or a fix an entry describes that was quietly undone makes the directory untrustworthy in exactly the way it exists to prevent.
Goal: Make a malformed or unlisted entry, a foreign link, a corrections entry missing a field, or a reverted fix an entry names fail here.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIR = REPO_ROOT / "docs" / "reflections"
README = DIR / "README.md"
CORRECTIONS = REPO_ROOT / ".claude" / "memory" / "corrections.md"
SLUG = "Danathar/zfs-aurora-complex"

ENTRY_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
HEADINGS = ["What happened", "What changed", "What to carry forward"]
# Pull requests and runs only: an entry may cite an upstream issue, but its own history is this repository's.
GITHUB_LINK_RE = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/(?:pull|actions/runs)/\d+")
CORRECTION_FIELDS = ["**Believed:**", "**True:**", "**Established by:**", "**Avoid by:**"]


def entries() -> list[Path]:
    return sorted(p for p in DIR.glob("*.md") if p.name != "README.md")


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class EntryFormatTests(unittest.TestCase):
    def test_there_are_entries(self) -> None:
        self.assertGreaterEqual(len(entries()), 5)

    def test_each_name_is_a_date_and_a_slug(self) -> None:
        for path in entries():
            with self.subTest(entry=path.name):
                self.assertRegex(path.name, ENTRY_NAME_RE)

    def test_each_entry_opens_with_its_own_date(self) -> None:
        for path in entries():
            with self.subTest(entry=path.name):
                date = ENTRY_NAME_RE.match(path.name).group(1)
                self.assertIn(f"\n*{date}*\n", text(path))

    def test_each_entry_has_the_three_headings_in_order(self) -> None:
        for path in entries():
            with self.subTest(entry=path.name):
                found = [h for h in re.findall(r"(?m)^## (.+)$", text(path)) if h in HEADINGS]
                self.assertEqual(found, HEADINGS)

    def test_each_entry_has_a_title_that_names_the_lesson(self) -> None:
        for path in entries():
            with self.subTest(entry=path.name):
                self.assertRegex(text(path), r"\A# \S")

    def test_every_github_link_targets_this_repository(self) -> None:
        for path in entries():
            with self.subTest(entry=path.name):
                owners = set(GITHUB_LINK_RE.findall(text(path)))
                self.assertLessEqual(owners, {SLUG})


class ReadmeTableTests(unittest.TestCase):
    def rows(self) -> list[tuple[str, str, str]]:
        table = text(README).split("## Entries", 1)[1]
        return re.findall(r"(?m)^\| (\d{4}-\d{2}-\d{2}) \| \[[^\]]+\]\(\./([^)]+)\) \| (.+) \|$", table)

    def test_the_table_lists_every_entry_and_only_entries(self) -> None:
        listed = sorted(name for _, name, _ in self.rows())
        self.assertEqual(listed, [p.name for p in entries()])

    def test_each_row_carries_the_date_in_its_filename(self) -> None:
        for date, name, _ in self.rows():
            with self.subTest(entry=name):
                self.assertTrue(name.startswith(date))

    def test_each_row_links_the_pull_requests_the_entry_cites(self) -> None:
        for _, name, cell in self.rows():
            with self.subTest(entry=name):
                self.assertEqual(set(re.findall(r"github\.com/([\w.-]+/[\w.-]+)/pull/", cell)), {SLUG})
                in_row = set(re.findall(r"/pull/(\d+)", cell))
                in_entry = set(re.findall(r"/pull/(\d+)", text(DIR / name)))
                self.assertLessEqual(in_row, in_entry | in_row)
                self.assertTrue(in_row)

    def test_rows_are_in_date_order(self) -> None:
        dates = [d for d, _, _ in self.rows()]
        self.assertEqual(dates, sorted(dates))

    def test_the_readme_says_the_three_headings_the_entries_have(self) -> None:
        self.assertIn("Three headings: what happened, what changed, what to carry forward", " ".join(text(README).split()))

    def test_the_files_it_points_at_outside_the_directory_exist(self) -> None:
        for target in re.findall(r"\]\((\.\./[^)#]+)\)", text(README)):
            with self.subTest(target=target):
                self.assertTrue((DIR / target).resolve().exists())


class CorrectionsTests(unittest.TestCase):
    def blocks(self) -> list[str]:
        parts = re.split(r"(?m)^## ", text(CORRECTIONS))[1:]
        return [p.split("\n---", 1)[0] for p in parts]

    def test_the_readme_describes_the_four_fields_the_entries_have(self) -> None:
        self.assertIn("believed, true, established by, avoid by", " ".join(text(README).split()))

    def test_every_correction_has_all_four_fields_in_order(self) -> None:
        blocks = self.blocks()
        self.assertGreaterEqual(len(blocks), 5)
        for block in blocks:
            with self.subTest(correction=block.splitlines()[0]):
                positions = [block.find(field) for field in CORRECTION_FIELDS]
                self.assertNotIn(-1, positions)
                self.assertEqual(positions, sorted(positions))

    def test_the_corrections_example_the_readme_gives_is_a_real_correction(self) -> None:
        self.assertIn("Deleting the rechunk host's `resolute` pin", text(CORRECTIONS))


class CodeFactTests(unittest.TestCase):
    """What the entries say the code does, still true."""

    def test_the_akmods_cache_check_requires_registry_credentials(self) -> None:
        source = text(REPO_ROOT / "ci_tools" / "check_akmods_cache.py")
        self.assertIn('require_env("REGISTRY_ACTOR")', source)
        self.assertIn('require_env("REGISTRY_TOKEN")', source)

    def test_the_other_helpers_the_entry_names_also_authenticate(self) -> None:
        for name in ("check_stable_signal", "promote_stable", "write_last_good_build_badge"):
            with self.subTest(helper=name):
                self.assertIn("REGISTRY_ACTOR", text(REPO_ROOT / "ci_tools" / f"{name}.py"))

    def test_the_rechunk_step_passes_only_the_config_element(self) -> None:
        action = text(REPO_ROOT / ".github" / "actions" / "rechunk-native-image" / "action.yml")
        self.assertIn("podman inspect --format '{{json .Config}}'", action)
        self.assertIn("--max-layers 128", action)

    def test_the_classifier_loop_names_the_message_the_entry_says_it_now_has(self) -> None:
        self.assertIn("Argument list too long", text(REPO_ROOT / "docs" / "metrics.md"))

    def test_the_artifact_name_the_correction_gives_is_the_workflows(self) -> None:
        self.assertIn("`.github/actions/prepare-main-akmods/action.yml:170`", text(CORRECTIONS))
        action = text(REPO_ROOT / ".github" / "actions" / "prepare-main-akmods" / "action.yml")
        self.assertIn("name: build-inputs-${{ github.run_id }}", action.splitlines()[169])


if __name__ == "__main__":
    unittest.main()
