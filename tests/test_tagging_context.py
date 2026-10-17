"""
Script: tests/test_tagging_context.py
What: Tests for the shared lightweight tag and registry-context helpers.
Doing: Verifies candidate-tag naming and registry-context exports.
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
    build_candidate_tag,
    export_registry_context_values,
    main_export_registry_context,
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

    def test_export_registry_context_values(self) -> None:
        self.assertEqual(
            export_registry_context_values(
                repository_owner="Danathar",
            ),
            {
                "image_org": "danathar",
                "image_registry": "ghcr.io/danathar",
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
            self.assertIn("IMAGE_REGISTRY<<", env_text)
            self.assertIn("ghcr.io/danathar", env_text)


if __name__ == "__main__":
    unittest.main()
