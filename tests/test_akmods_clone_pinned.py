"""
Script: tests/test_akmods_clone_pinned.py
What: Tests for cloning the resolved akmods fork checkout.
Doing: Verifies the helper fetches one commit, fails if Git resolves the wrong SHA, and that the env-reading `main()` wrapper still forwards defaults.
Why: The native repo now relies on the fork itself carrying the publish-name logic instead of patching the clone at runtime.
Goal: Keep the clone step deterministic and fail closed on ref drift.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from ci_tools import akmods_clone_pinned as script
from ci_tools.common import CiToolError


class AkmodsClonePinnedTests(unittest.TestCase):
    def test_clone_pinned_clones_exact_resolved_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            worktree = Path(temp_dir) / "akmods"

            with patch.object(script, "AKMODS_WORKTREE", worktree):
                with patch(
                    "ci_tools.akmods_clone_pinned.run_cmd",
                    side_effect=["", "", "", "", "abcdef123456\n"],
                ) as run_cmd:
                    script.clone_pinned("https://github.com/Danathar/akmods.git", "abcdef123456")

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

    def test_clone_pinned_rejects_resolved_sha_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            worktree = Path(temp_dir) / "akmods"

            with patch.object(script, "AKMODS_WORKTREE", worktree):
                with patch(
                    "ci_tools.akmods_clone_pinned.run_cmd",
                    side_effect=["", "", "", "", "deadbeef\n"],
                ):
                    with self.assertRaisesRegex(CiToolError, "Akmods ref mismatch"):
                        script.clone_pinned("https://github.com/Danathar/akmods.git", "abcdef123456")

    def test_clone_pinned_rejects_empty_inputs(self) -> None:
        with self.assertRaisesRegex(CiToolError, "upstream_repo"):
            script.clone_pinned("", "abcdef123456")
        with self.assertRaisesRegex(CiToolError, "upstream_ref"):
            script.clone_pinned("https://github.com/Danathar/akmods.git", "")

    def test_main_forwards_env_defaults_to_clone_pinned(self) -> None:
        with patch(
            "ci_tools.akmods_clone_pinned.require_env_or_default",
            side_effect=["https://github.com/Danathar/akmods.git", "abcdef123456"],
        ) as require_env_or_default:
            with patch("ci_tools.akmods_clone_pinned.clone_pinned") as clone_pinned:
                script.main()

        self.assertEqual(
            require_env_or_default.call_args_list,
            [call("AKMODS_UPSTREAM_REPO"), call("AKMODS_UPSTREAM_REF")],
        )
        clone_pinned.assert_called_once_with(
            "https://github.com/Danathar/akmods.git", "abcdef123456"
        )


if __name__ == "__main__":
    unittest.main()
