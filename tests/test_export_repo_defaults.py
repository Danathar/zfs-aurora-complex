"""
Script: tests/test_export_repo_defaults.py
What: Tests for exporting checked-in repository defaults to GitHub Actions.
Doing: Replaces the file-backed defaults loader with a small fixture and verifies both step outputs and step env exports, and that a missing required key stops the step before anything is exported.
Why: Workflow YAML now depends on this command to stay in sync with `ci/defaults.json`.
Goal: Keep one source of truth for default refs and image names.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ci_tools.export_repo_defaults import OPTIONAL_KEYS, OUTPUT_NAME_MAP, main


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
            "DEFAULT_ZFS_MINOR_VERSION": "2.4",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output.txt"
            env_path = Path(temp_dir) / "github-env.txt"
            with (
                patch.dict(
                    os.environ,
                    {"GITHUB_OUTPUT": str(output_path), "GITHUB_ENV": str(env_path)},
                    clear=False,
                ),
                patch("ci_tools.export_repo_defaults.load_repo_defaults", return_value=defaults),
            ):
                main()

            output_text = output_path.read_text(encoding="utf-8")
            env_text = env_path.read_text(encoding="utf-8")
            self.assertIn("image_name<<", output_text)
            self.assertIn("zfs-aurora-complex", output_text)
            self.assertIn("default_build_container_image<<", output_text)
            self.assertIn("ghcr.io/example/build:latest", output_text)
            self.assertIn("IMAGE_NAME<<", env_text)
            self.assertIn("DEFAULT_ZFS_MINOR_VERSION<<", env_text)


class MissingDefaultsTests(unittest.TestCase):
    """
    `ci/defaults.json` is the only source for the image name, the akmods repo
    and the base images. A required key that is missing or empty must stop
    the step: exported as an empty string, it would reach later steps as a
    blank image name or registry path instead of failing here.
    """

    def full_defaults(self) -> dict[str, str]:
        return {key: f"value-for-{key.lower()}" for key in OUTPUT_NAME_MAP}

    def run_main(self, defaults: dict[str, str]) -> tuple[str, str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output.txt"
            env_path = Path(temp_dir) / "github-env.txt"
            output_path.touch()
            env_path.touch()
            try:
                with (
                    patch.dict(
                        os.environ,
                        {"GITHUB_OUTPUT": str(output_path), "GITHUB_ENV": str(env_path)},
                        clear=False,
                    ),
                    patch("ci_tools.export_repo_defaults.load_repo_defaults", return_value=defaults),
                    redirect_stdout(io.StringIO()),
                ):
                    main()
            finally:
                self.written = (
                    output_path.read_text(encoding="utf-8"),
                    env_path.read_text(encoding="utf-8"),
                )
        return self.written

    def test_each_required_key_missing_stops_before_exporting(self) -> None:
        for key in sorted(set(OUTPUT_NAME_MAP) - OPTIONAL_KEYS):
            for broken in ("missing", "empty"):
                with self.subTest(key=key, broken=broken):
                    defaults = self.full_defaults()
                    if broken == "missing":
                        del defaults[key]
                    else:
                        defaults[key] = ""
                    with self.assertRaises(RuntimeError) as raised:
                        self.run_main(defaults)
                    self.assertEqual(
                        str(raised.exception),
                        f"ci/defaults.json is missing required keys: {key}",
                    )
                    self.assertEqual(self.written, ("", ""))

    def test_names_every_missing_key_in_sorted_order(self) -> None:
        # All six at once, so a set's own iteration order is very unlikely to
        # come out sorted by chance and hide a dropped sort.
        required = sorted(set(OUTPUT_NAME_MAP) - OPTIONAL_KEYS)
        self.assertEqual(len(required), 6)
        defaults = {key: self.full_defaults()[key] for key in OPTIONAL_KEYS}
        with self.assertRaises(RuntimeError) as raised:
            self.run_main(defaults)
        self.assertEqual(
            str(raised.exception),
            "ci/defaults.json is missing required keys: AKMODS_REPO, AKMODS_UPSTREAM_REPO, "
            "DEFAULT_BASE_IMAGE, DEFAULT_BUILD_CONTAINER_IMAGE, DEFAULT_ZFS_MINOR_VERSION, "
            "IMAGE_NAME",
        )

    def test_optional_keys_may_be_absent_and_export_as_empty(self) -> None:
        self.assertEqual(OPTIONAL_KEYS, {"AKMODS_UPSTREAM_REF", "AKMODS_UPSTREAM_TRACK"})
        defaults = self.full_defaults()
        for key in OPTIONAL_KEYS:
            del defaults[key]
        output_text, env_text = self.run_main(defaults)
        # Heredoc form with an empty body: `name<<DELIM`, an empty line, `DELIM`.
        self.assertRegex(output_text, r"(?m)^akmods_upstream_ref<<(\S+)\n\n\1$")
        self.assertRegex(env_text, r"(?m)^AKMODS_UPSTREAM_TRACK<<(\S+)\n\n\1$")
        self.assertIn("value-for-image_name", output_text)


if __name__ == "__main__":
    unittest.main()
