"""
Script: tests/test_pin_akmods_cache.py
What: Tests for digest-pinning the shared akmods cache image.
Doing: Checks tag construction and conversion from a mutable tag to an immutable digest reference.
Why: The final image build must consume the exact akmods cache image verified or published earlier in the workflow.
Goal: Prevent regressions back to mutable akmods cache tags in downstream jobs.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_tools.pin_akmods_cache import akmods_cache_image_tag, main, pin_akmods_cache_image


class PinAkmodsCacheTests(unittest.TestCase):
    def test_builds_shared_cache_tag_for_fedora_version(self) -> None:
        image_tag = akmods_cache_image_tag(
            image_org="danathar",
            source_repo="zfs-aurora-complex-akmods",
            fedora_version="43",
        )

        self.assertEqual(image_tag, "ghcr.io/danathar/zfs-aurora-complex-akmods:main-43")

    def test_pins_tag_to_digest_reference(self) -> None:
        with patch(
            "ci_tools.pin_akmods_cache.skopeo_inspect_digest",
            return_value="sha256:abc123",
        ) as inspect_digest:
            image_pinned = pin_akmods_cache_image(
                "ghcr.io/danathar/zfs-aurora-complex-akmods:main-43"
            )

        self.assertEqual(image_pinned, "ghcr.io/danathar/zfs-aurora-complex-akmods@sha256:abc123")
        inspect_digest.assert_called_once_with(
            "docker://ghcr.io/danathar/zfs-aurora-complex-akmods:main-43"
        )

    def test_main_writes_mutable_and_pinned_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output"
            env = {
                "GITHUB_REPOSITORY_OWNER": "Danathar",
                "FEDORA_VERSION": "43",
                "AKMODS_REPO": "zfs-aurora-complex-akmods",
                "GITHUB_OUTPUT": str(output_path),
            }
            with patch.dict(os.environ, env, clear=False), patch(
                "ci_tools.pin_akmods_cache.skopeo_inspect_digest",
                return_value="sha256:abc123",
            ):
                main()

            output = output_path.read_text(encoding="utf-8")

        self.assertIn("akmods_image<<", output)
        self.assertIn("ghcr.io/danathar/zfs-aurora-complex-akmods:main-43", output)
        self.assertIn("akmods_image_pinned<<", output)
        self.assertIn("ghcr.io/danathar/zfs-aurora-complex-akmods@sha256:abc123", output)


if __name__ == "__main__":
    unittest.main()
