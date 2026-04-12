"""
Script: tests/test_akmods_clone_pinned.py
What: Tests for cloning the resolved akmods fork checkout.
Doing: Verifies the helper reads repo defaults, fetches one commit, and fails if Git resolves the wrong SHA.
Why: The native repo now relies on the fork itself carrying the publish-name logic instead of patching the clone at runtime.
Goal: Keep the clone step deterministic and fail closed on ref drift.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from ci_tools import akmods_clone_pinned as script


class AkmodsClonePinnedTests(unittest.TestCase):
    def test_main_clones_exact_resolved_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            worktree = Path(temp_dir) / "akmods"

            with patch.object(script, "AKMODS_WORKTREE", worktree):
                with patch(
                    "ci_tools.akmods_clone_pinned.require_env_or_default",
                    side_effect=["https://github.com/Danathar/akmods.git", "abcdef123456"],
                ):
                    with patch("ci_tools.akmods_clone_pinned.run_cmd", side_effect=["", "", "", "", "abcdef123456\n"]) as run_cmd:
                        script.main()

        self.assertEqual(
            run_cmd.call_args_list,
            [
                call(["git", "init", "."], cwd=str(worktree)),
                call(["git", "remote", "add", "origin", "https://github.com/Danathar/akmods.git"], cwd=str(worktree)),
                call(["git", "fetch", "--depth", "1", "origin", "abcdef123456"], cwd=str(worktree)),
                call(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=str(worktree)),
                call(["git", "rev-parse", "HEAD"], cwd=str(worktree)),
            ],
        )

    def test_main_rejects_resolved_sha_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            worktree = Path(temp_dir) / "akmods"

            with patch.object(script, "AKMODS_WORKTREE", worktree):
                with patch(
                    "ci_tools.akmods_clone_pinned.require_env_or_default",
                    side_effect=["https://github.com/Danathar/akmods.git", "abcdef123456"],
                ):
                    with patch("ci_tools.akmods_clone_pinned.run_cmd", side_effect=["", "", "", "", "deadbeef\n"]):
                        with self.assertRaisesRegex(RuntimeError, "Akmods ref mismatch"):
                            script.main()


if __name__ == "__main__":
    unittest.main()
