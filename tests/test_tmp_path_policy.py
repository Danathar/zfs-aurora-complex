"""
Script: tests/test_tmp_path_policy.py
What: Blocks fixed-name folders in the shared temporary folder from shipped files.
Doing: Scans every tracked text file outside tests/ and .claude/ for `/tmp/<name>` where `/tmp`
starts an absolute path, and holds the hits to .github/policies/tmp-paths.json in both
directions: a hit passes only when its file and its exact `/tmp/<name>` are listed, and a listed
entry that matches nothing fails.
Why: /tmp is world-writable and shared. Any local account can create a fixed-name folder there
first, and a root process that then works through it can be steered with a symlink. This
repository shipped one such unit (#291); the fix confined it with PrivateTmp=yes. This check stops
the next one at review time.
Goal: A new fixed `/tmp/<name>` fails here until it is replaced with a safe alternative or
listed, with a reason, in the same pull request.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = REPO_ROOT / ".github" / "policies" / "tmp-paths.json"

POLICY_NAME = POLICY_PATH.relative_to(REPO_ROOT).as_posix()

# Not shipped: tests and local agent configuration.
EXCLUDED_PREFIXES = ("tests/", ".claude/")

TMP_PATH = re.compile(r"/tmp/[A-Za-z0-9._-]+")
# Text just before a match that makes `/tmp` a path segment, not the start of a path:
# `/var/tmp/x` and `https://host/tmp/x`.
INSIDE_PATH = re.compile(r"/[A-Za-z0-9._-]+$")

ADVICE = (
    "Use PrivateTmp=yes on the unit, RuntimeDirectory=/StateDirectory=, `mktemp -d` "
    "(or $RUNNER_TEMP in workflows), or a root-owned /run/<name>. If the use is safe, list the "
    f"(file, tmp_path) pair with a reason in {POLICY_NAME}."
)


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True
    ).stdout
    return [name for name in out.decode("utf-8").split("\0") if name]


def scan(root: Path, files: list[str]) -> list[tuple[str, int, str]]:
    """Each (file, line number, `/tmp/<name>`) in scope under *root*, skipping binary files."""

    hits = []
    for name in files:
        if name.startswith(EXCLUDED_PREFIXES) or name == POLICY_NAME:
            continue
        path = root / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for match in TMP_PATH.finditer(line):
                if INSIDE_PATH.search(line[: match.start()]):
                    continue
                hits.append((name, number, match.group()))
    return hits


def problems(hits: list[tuple[str, int, str]], allowed: list[dict[str, str]]) -> list[str]:
    """Hits not in the allowlist, then allowlist entries that no hit uses."""

    listed = {(entry["file"], entry["tmp_path"]) for entry in allowed}
    found = {(name, tmp) for name, _, tmp in hits}
    messages = [
        f"{name}:{number}: {tmp} is a fixed folder name in the shared temporary folder. {ADVICE}"
        for name, number, tmp in hits
        if (name, tmp) not in listed
    ]
    messages += [
        f"{POLICY_PATH.name}: stale entry, {name} has no {tmp}. Remove it."
        for name, tmp in sorted(listed - found)
    ]
    return messages


def policy_entries() -> list[dict[str, str]]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))["allowed"]


class RealTreeTests(unittest.TestCase):
    def test_tree_matches_the_allowlist(self) -> None:
        found = problems(scan(REPO_ROOT, tracked_files(REPO_ROOT)), policy_entries())
        self.assertEqual(found, [], "\n".join(found))

    def test_every_entry_has_the_expected_shape(self) -> None:
        for entry in policy_entries():
            with self.subTest(entry=entry):
                self.assertEqual(set(entry), {"file", "tmp_path", "why"})
                self.assertRegex(entry["tmp_path"], r"^/tmp/[A-Za-z0-9._-]+$")
                self.assertTrue(entry["why"].strip())
                self.assertFalse(entry["file"].startswith(EXCLUDED_PREFIXES))


class ScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def plant(self, name: str, text: str | bytes) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")

    def check(self, allowed: list[dict[str, str]] | None = None) -> list[str]:
        files = sorted(
            p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file()
        )
        return problems(scan(self.root, files), allowed or [])

    def test_planted_path_fails_with_file_line_and_alternatives(self) -> None:
        unit = "files/usr/lib/systemd/system/x.service.d/10-x.conf"
        self.plant(unit, "[Service]\nExecStart=/usr/bin/mkdir -p /tmp/scratch\n")
        found = self.check()
        self.assertEqual(len(found), 1)
        wanted = (
            f"{unit}:2",
            "/tmp/scratch",
            "PrivateTmp=yes",
            "RuntimeDirectory=",
            "StateDirectory=",
            "mktemp -d",
            "$RUNNER_TEMP",
            "/run/<name>",
        )
        for text in wanted:
            self.assertIn(text, found[0])

    def test_same_text_under_tests_or_claude_passes(self) -> None:
        self.plant("tests/fixtures/x.conf", "/tmp/scratch\n")
        self.plant("tests/e2e/run.sh", "/tmp/scratch\n")
        self.plant(".claude/settings.json", '"/tmp/scratch"\n')
        self.assertEqual(self.check(), [])

    def test_the_allowlist_itself_is_not_scanned(self) -> None:
        self.plant(POLICY_NAME, '{"allowed": [{"tmp_path": "/tmp/scratch"}]}\n')
        self.assertEqual(self.check(), [])

    def test_var_tmp_and_url_paths_pass(self) -> None:
        self.plant("files/x.conf", "/var/tmp/x\nhttps://example.com/tmp/y\n")
        self.assertEqual(self.check(), [])

    def test_binary_file_is_skipped(self) -> None:
        self.plant("files/blob.bin", b"\0\x01/tmp/scratch\n")
        self.assertEqual(self.check(), [])

    def test_allowlisted_pair_passes(self) -> None:
        self.plant("ci_tools/x.py", 'P = "/tmp/akmods"\n')
        entry = {"file": "ci_tools/x.py", "tmp_path": "/tmp/akmods", "why": "runner"}
        self.assertEqual(self.check([entry]), [])

    def test_other_name_in_a_listed_file_fails(self) -> None:
        self.plant("ci_tools/x.py", 'P = "/tmp/akmods"\nQ = "/tmp/other"\n')
        entry = {"file": "ci_tools/x.py", "tmp_path": "/tmp/akmods", "why": "runner"}
        found = self.check([entry])
        self.assertEqual(len(found), 1)
        self.assertIn("ci_tools/x.py:2", found[0])
        self.assertIn("/tmp/other", found[0])

    def test_same_name_in_another_file_fails(self) -> None:
        self.plant("ci_tools/x.py", 'P = "/tmp/akmods"\n')
        self.plant("ci_tools/y.py", 'P = "/tmp/akmods"\n')
        entry = {"file": "ci_tools/x.py", "tmp_path": "/tmp/akmods", "why": "runner"}
        found = self.check([entry])
        self.assertEqual(len(found), 1)
        self.assertIn("ci_tools/y.py:1", found[0])

    def test_entry_matching_nothing_fails(self) -> None:
        self.plant("ci_tools/x.py", "P = 1\n")
        entry = {"file": "ci_tools/x.py", "tmp_path": "/tmp/akmods", "why": "runner"}
        found = self.check([entry])
        self.assertEqual(len(found), 1)
        self.assertIn("stale", found[0])


if __name__ == "__main__":
    unittest.main()
