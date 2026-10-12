"""
Script: tests/test_allowed_python_rows.py
What: Runs every Python script `.claude/settings.json` allows unprompted beside an untracked module named after each thing it imports.
Doing: Reads the `Bash(python3 <script>...)` allow rows, collects each script's imports from its AST, copies the script into a scratch directory next to one marker-writing module per import, runs it, and asserts no marker was written.
Why: Python puts a script's own directory first on `sys.path`, so an untracked `tests/argparse.py` runs before the script's first line, and no check the script makes afterwards can see it.
Goal: Keep every allowed Python row unable to run a file git does not track, and have the list of imports come from the script rather than from this test.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS = REPO_ROOT / ".claude" / "settings.json"

# `Bash(python3 tests/run_tests.py:*)` and the bare `Bash(python3 tests/x.py)`.
SCRIPT_ROW = re.compile(r"^Bash\(python3? (?P<script>[^\s:)]+\.py)(?::\*)?\)$")
PYTHON_ROW = re.compile(r"^Bash\(python3?\b")


def allowed_python_rows() -> list[str]:
    permissions = json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]
    return [rule for rule in permissions["allow"] if PYTHON_ROW.match(rule)]


def imported_top_level_names(script: Path) -> set[str]:
    """Every absolute module a script names in an import, anywhere in the file."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(script.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


class AllowedPythonRowTests(unittest.TestCase):
    def test_every_python_row_names_one_script(self) -> None:
        # A row of any other shape (`python3 -c`, `python3 -m`, a bare
        # `python3:*`) runs code no script below can trim a path for.
        rows = allowed_python_rows()
        self.assertTrue(rows, "no python3 allow row found; this test would pass vacuously")
        for rule in rows:
            with self.subTest(rule=rule):
                match = SCRIPT_ROW.match(rule)
                self.assertIsNotNone(match, f"{rule} is not `Bash(python3 <script>.py...)`")
                self.assertTrue((REPO_ROOT / match["script"]).is_file(), rule)

    def test_no_untracked_module_beside_an_allowed_script_is_imported(self) -> None:
        for rule in allowed_python_rows():
            match = SCRIPT_ROW.match(rule)
            if match is None:
                continue
            source = REPO_ROOT / match["script"]
            names = imported_top_level_names(source)
            with self.subTest(script=match["script"], imports=sorted(names)):
                self.assertIn("sys", names)
                with tempfile.TemporaryDirectory() as temp_dir:
                    directory = Path(temp_dir)
                    shutil.copy(source, directory / source.name)
                    marker = directory / "ran"
                    for name in names:
                        (directory / f"{name}.py").write_text(
                            f"open({str(marker)!r}, 'a').write({name + ' '!r})\n",
                            encoding="utf-8",
                        )
                    result = subprocess.run(
                        [sys.executable, str(directory / source.name), "--help"],
                        capture_output=True,
                        text=True,
                        timeout=60,
                        check=False,
                    )
                    ran = marker.read_text(encoding="utf-8") if marker.exists() else ""
                    self.assertEqual(ran, "", f"untracked modules ran: {ran}")
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
