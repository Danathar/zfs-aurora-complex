"""
Script: tests/test_tagging_context.py
What: Tests for the shared lightweight tag and registry-context helpers.
Doing: Verifies candidate-tag naming, branch-tag cleanup/composition, bot
detection, and registry-context exports.
Why: These rules are small, but several workflows depend on them.
Goal: Keep the reduced helper surface explicit and safe.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_tools.tagging_context import (
    actor_is_bot,
    build_branch_image_tag,
    build_branch_metadata,
    build_candidate_tag,
    export_registry_context_values,
    main_export_registry_context,
    sanitize_branch_name,
)


class TaggingContextTests(unittest.TestCase):
    def test_builds_candidate_tag_from_sha_and_fedora_version(self) -> None:
        self.assertEqual(
            build_candidate_tag(
                github_sha="deadbeefcafebabefeedface1234567890abcdef",
                fedora_version="43",
            ),
            "candidate-deadbee-43",
        )

    def test_builds_branch_image_tag(self) -> None:
        self.assertEqual(
            build_branch_image_tag(
                branch_tag_prefix="br-my-branch",
                fedora_version="43",
            ),
            "br-my-branch-43",
        )

    def test_sanitizes_branch_name(self) -> None:
        self.assertEqual(sanitize_branch_name("Feature/My Branch!"), "feature-my-branch")

    def test_uses_fallback_when_branch_sanitizes_to_empty(self) -> None:
        self.assertEqual(sanitize_branch_name("!!!"), "branch")

    def test_clamps_long_names(self) -> None:
        long_branch = "a" * 300
        branch_tag = build_branch_metadata(long_branch)
        self.assertLessEqual(len(branch_tag), 120)
        self.assertTrue(branch_tag.startswith("br-"))

    def test_actor_is_bot_matches_github_bot_suffix(self) -> None:
        self.assertTrue(actor_is_bot("dependabot[bot]"))
        self.assertFalse(actor_is_bot("dbaggett"))

    def test_export_registry_context_values(self) -> None:
        self.assertEqual(
            export_registry_context_values(
                repository_owner="Danathar",
                actor_name="dependabot[bot]",
            ),
            {
                "image_org": "danathar",
                "image_registry": "ghcr.io/danathar",
                "actor_is_bot": "true",
            },
        )

    def test_main_export_registry_context_writes_outputs_and_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output.txt"
            env_path = Path(temp_dir) / "github-env.txt"
            with patch.dict(
                os.environ,
                {
                    "GITHUB_REPOSITORY_OWNER": "Danathar",
                    "GITHUB_ACTOR": "dependabot[bot]",
                    "GITHUB_OUTPUT": str(output_path),
                    "GITHUB_ENV": str(env_path),
                },
                clear=False,
            ):
                main_export_registry_context()

            output_text = output_path.read_text(encoding="utf-8")
            env_text = env_path.read_text(encoding="utf-8")
            self.assertIn("image_org<<", output_text)
            self.assertIn("danathar", output_text)
            self.assertIn("actor_is_bot<<", output_text)
            self.assertIn("true", output_text)
            self.assertIn("IMAGE_REGISTRY<<", env_text)
            self.assertIn("ghcr.io/danathar", env_text)
            self.assertIn("ACTOR_IS_BOT<<", env_text)


if __name__ == "__main__":
    unittest.main()
