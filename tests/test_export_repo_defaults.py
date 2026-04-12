"""
Script: tests/test_export_repo_defaults.py
What: Tests for exporting checked-in repository defaults to GitHub Actions.
Doing: Replaces the file-backed defaults loader with a small fixture and verifies both step outputs and step env exports.
Why: Workflow YAML now depends on this command to stay in sync with `ci/defaults.json`.
Goal: Keep one source of truth for default refs and image names.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_tools.export_repo_defaults import main


class ExportRepoDefaultsTests(unittest.TestCase):
    def test_main_writes_outputs_and_env(self) -> None:
        defaults = {
            "AKMODS_UPSTREAM_REPO": "https://example.invalid/akmods.git",
            "AKMODS_UPSTREAM_TRACK": "main",
            "AKMODS_UPSTREAM_REF": "abcdef123456",
            "IMAGE_NAME": "zfs-aurora-complex",
            "AKMODS_REPO": "zfs-aurora-complex-akmods",
            "DEFAULT_BASE_IMAGE": "ghcr.io/example/base:latest",
            "DEFAULT_BUILD_CONTAINER_IMAGE": "ghcr.io/example/build:latest",
            "DEFAULT_BREW_IMAGE": "ghcr.io/example/brew:latest",
            "DEFAULT_ZFS_MINOR_VERSION": "2.4",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output.txt"
            env_path = Path(temp_dir) / "github-env.txt"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_path), "GITHUB_ENV": str(env_path)}, clear=False):
                with patch("ci_tools.export_repo_defaults.load_repo_defaults", return_value=defaults):
                    main()

            output_text = output_path.read_text(encoding="utf-8")
            env_text = env_path.read_text(encoding="utf-8")
            self.assertIn("image_name=zfs-aurora-complex", output_text)
            self.assertIn("default_brew_image=ghcr.io/example/brew:latest", output_text)
            self.assertIn("IMAGE_NAME=zfs-aurora-complex", env_text)
            self.assertIn("DEFAULT_ZFS_MINOR_VERSION=2.4", env_text)


if __name__ == "__main__":
    unittest.main()
