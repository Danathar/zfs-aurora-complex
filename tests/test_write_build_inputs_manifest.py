"""
Script: tests/test_write_build_inputs_manifest.py
What: Tests for the build-inputs manifest writer.
Doing: Writes a manifest into a temporary artifact directory and validates its schema.
Why: The manifest is the audit trail for replaying and investigating CI runs.
Goal: Keep the recorded input fields stable as the workflows evolve.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ci_tools.write_build_inputs_manifest as script


def _env() -> dict[str, str]:
    return {
        "GITHUB_REPOSITORY": "Danathar/zfs-aurora-complex",
        "GITHUB_WORKFLOW": "Build And Promote Main Image",
        "GITHUB_RUN_ID": "123456",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_RUN_NUMBER": "99",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": "deadbeefcafefeed1234567890abcdef12345678",
        "GITHUB_ACTOR": "dbaggett",
        "USE_INPUT_LOCK": "false",
        "LOCK_FILE_PATH": "ci/inputs.lock.json",
        "FEDORA_VERSION": "43",
        "KERNEL_RELEASE": "6.18.16-200.fc43.x86_64",
        "DETECTED_KERNEL_RELEASES": "6.18.13-200.fc43.x86_64 6.18.16-200.fc43.x86_64",
        "BASE_IMAGE_REF": "ghcr.io/ublue-os/aurora:latest",
        "BASE_IMAGE_NAME": "ghcr.io/ublue-os/aurora",
        "BASE_IMAGE_TAG": "latest-20260307.1",
        "BASE_IMAGE_PINNED": "ghcr.io/ublue-os/aurora@sha256:base",
        "BASE_IMAGE_DIGEST": "sha256:base",
        "BUILD_CONTAINER_REF": "ghcr.io/ublue-os/devcontainer:latest",
        "BUILD_CONTAINER_PINNED": "ghcr.io/ublue-os/devcontainer@sha256:build",
        "BUILD_CONTAINER_DIGEST": "sha256:build",
        "ZFS_MINOR_VERSION": "2.4",
        "AKMODS_UPSTREAM_REF": "abcdef1234567890abcdef1234567890abcdef12",
    }


class WriteBuildInputsManifestTests(unittest.TestCase):
    def test_writes_manifest_with_expected_schema_and_resolved_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_dir = Path(temp_dir) / "artifacts"
            artifact_path = artifact_dir / "build-inputs.json"

            with patch.object(script, "ARTIFACT_DIR", artifact_dir):
                with patch.object(script, "ARTIFACT_PATH", artifact_path):
                    with patch.dict(os.environ, _env(), clear=True):
                        script.main()

            document = json.loads(artifact_path.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], 1)
        self.assertTrue(document["generated_at"].endswith("Z"))
        self.assertEqual(document["repository"], "Danathar/zfs-aurora-complex")
        self.assertEqual(document["workflow"], "Build And Promote Main Image")
        self.assertEqual(
            document["run"],
            {
                "id": 123456,
                "attempt": 2,
                "number": 99,
                "ref": "refs/heads/main",
                "sha": "deadbeefcafefeed1234567890abcdef12345678",
                "actor": "dbaggett",
            },
        )
        self.assertEqual(
            set(document["inputs"].keys()),
            {
                "use_input_lock",
                "lock_file_path",
                "fedora_version",
                "kernel_release",
                "detected_kernel_releases",
                "base_image_ref",
                "base_image_name",
                "base_image_tag",
                "base_image_pinned",
                "base_image_digest",
                "build_container_ref",
                "build_container_pinned",
                "build_container_digest",
                "zfs_minor_version",
                "akmods_upstream_ref",
            },
        )
        self.assertFalse(document["inputs"]["use_input_lock"])
        self.assertEqual(
            document["inputs"]["detected_kernel_releases"],
            ["6.18.13-200.fc43.x86_64", "6.18.16-200.fc43.x86_64"],
        )
        self.assertEqual(document["inputs"]["base_image_digest"], "sha256:base")
        self.assertEqual(
            document["inputs"]["akmods_upstream_ref"],
            "abcdef1234567890abcdef1234567890abcdef12",
        )


if __name__ == "__main__":
    unittest.main()
