"""
Script: tests/test_akmods_configure_zfs_target.py
What: Tests for configuring the upstream akmods ZFS publish target.
Doing: Mocks yq and the cloned images.yaml path so no external tools are needed.
Why: This helper writes the shared-cache publish destination, which should stay explicit and safe.
Goal: Catch regressions in env validation, owner normalization, and yq command composition.
"""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_tools.common import CiToolError
import ci_tools.akmods_configure_zfs_target as script


def _env() -> dict[str, str]:
    return {
        "FEDORA_VERSION": "43",
        "AKMODS_REPO": "zfs-aurora-complex-akmods",
        "AKMODS_DESCRIPTION": "Shared cache image for pre-built zfs akmod RPMs",
        "GITHUB_REPOSITORY_OWNER": "Danathar",
    }


class AkmodsConfigureZfsTargetTests(unittest.TestCase):
    def test_fails_when_images_yaml_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            images_yaml = Path(temp_dir) / "images.yaml"
            with patch.object(script, "IMAGES_YAML", images_yaml):
                with patch.dict(os.environ, _env(), clear=True):
                    with self.assertRaises(CiToolError) as context:
                        script.main()

        self.assertIn(str(images_yaml), str(context.exception))

    def test_updates_target_with_normalized_owner_and_prints_final_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            images_yaml = Path(temp_dir) / "images.yaml"
            images_yaml.write_text("images: {}\n", encoding="utf-8")

            def fake_run_cmd(
                args: list[str],
                *,
                capture_output: bool = True,
                cwd: str | None = None,
                env=None,
            ) -> str:
                del cwd, env
                if args[1] == "-i":
                    self.assertFalse(capture_output)
                    self.assertEqual(os.environ["FEDORA_VERSION"], "43")
                    self.assertEqual(os.environ["IMAGE_ORG"], "danathar")
                    self.assertEqual(
                        os.environ["AKMODS_REPO"],
                        "zfs-aurora-complex-akmods",
                    )
                    self.assertEqual(
                        os.environ["AKMODS_DESCRIPTION"],
                        "Shared cache image for pre-built zfs akmod RPMs",
                    )
                    return ""
                return "org: danathar\nname: zfs-aurora-complex-akmods\n"

            with patch.object(script, "IMAGES_YAML", images_yaml):
                with patch.dict(os.environ, _env(), clear=True):
                    with patch(
                        "ci_tools.akmods_configure_zfs_target.run_cmd",
                        side_effect=fake_run_cmd,
                    ) as run_cmd:
                        stdout = io.StringIO()
                        with redirect_stdout(stdout):
                            script.main()

            self.assertEqual(run_cmd.call_count, 2)
            first_args = run_cmd.call_args_list[0].args[0]
            self.assertEqual(first_args[0:2], ["yq", "-i"])
            self.assertIn(".images[strenv(FEDORA_VERSION)].main.zfs", first_args[2])
            self.assertIn('"org": strenv(IMAGE_ORG)', first_args[2])
            self.assertIn('"name": strenv(AKMODS_REPO)', first_args[2])
            self.assertEqual(first_args[3], str(images_yaml))

            self.assertEqual(
                run_cmd.call_args_list[1].args[0],
                ["yq", '.images["43"].main.zfs', str(images_yaml)],
            )
            self.assertIn("org: danathar", stdout.getvalue())
            self.assertIn("name: zfs-aurora-complex-akmods", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
