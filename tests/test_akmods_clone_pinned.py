"""
Script: tests/test_akmods_clone_pinned.py
What: Tests the local patching we apply to the cloned upstream akmods Justfile.
Doing: Verifies the publish-name rule is rewritten to use images.yaml.
Why: The native repo must not silently fall back to upstream's hardcoded
`akmods-zfs` publish target.
Goal: Fail fast if upstream Justfile structure changes under our patch step.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_tools import akmods_clone_pinned as script


class AkmodsClonePinnedTests(unittest.TestCase):
    def test_patch_publish_name_resolution_rewrites_upstream_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            worktree = Path(temp_dir)
            justfile = worktree / "Justfile"
            justfile.write_text(
                script.UPSTREAM_AKMODS_NAME_LINE + "\n",
                encoding="utf-8",
            )

            with patch.object(script, "AKMODS_WORKTREE", worktree):
                with patch.object(script, "JUSTFILE", justfile):
                    script.patch_publish_name_resolution()

            content = justfile.read_text(encoding="utf-8")
            self.assertIn(script.PATCHED_AKMODS_NAME_LINE, content)
            self.assertNotIn(script.UPSTREAM_AKMODS_NAME_LINE, content)

    def test_patch_publish_name_resolution_fails_if_upstream_line_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            worktree = Path(temp_dir)
            justfile = worktree / "Justfile"
            justfile.write_text("akmods_name := 'unexpected'\n", encoding="utf-8")

            with patch.object(script, "AKMODS_WORKTREE", worktree):
                with patch.object(script, "JUSTFILE", justfile):
                    with self.assertRaisesRegex(RuntimeError, "no longer matches"):
                        script.patch_publish_name_resolution()


if __name__ == "__main__":
    unittest.main()
