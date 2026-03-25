"""
Script: tests/test_sign_image.py
What: Tests for published-image signing.
Doing: Verifies digest-ref construction, missing-key failure, and the exact cosign command sequence without touching a live registry.
Why: Signing moved out of workflow YAML and needs direct coverage now that it is code.
Goal: Keep tag-to-digest signing behavior explicit, testable, and easy to refactor safely.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from ci_tools.common import CiToolError
from ci_tools.sign_image import image_digest_ref, image_tag_ref, sign_published_image


class SignImageTests(unittest.TestCase):
    def test_builds_expected_refs(self) -> None:
        self.assertEqual(
            image_tag_ref("danathar", "zfs-aurora-containerfile", "latest"),
            "docker://ghcr.io/danathar/zfs-aurora-containerfile:latest",
        )
        self.assertEqual(
            image_digest_ref("danathar", "zfs-aurora-containerfile", "sha256:abc"),
            "ghcr.io/danathar/zfs-aurora-containerfile@sha256:abc",
        )

    def test_requires_signing_key(self) -> None:
        with self.assertRaises(CiToolError):
            sign_published_image(
                image_org="danathar",
                image_name="zfs-aurora-containerfile",
                image_tag="latest",
                registry_actor="actor",
                registry_token="token",
                cosign_private_key="",
            )

    def test_signs_and_verifies_digest_for_one_tag(self) -> None:
        calls: list[tuple[list[str], bool, dict[str, str] | None]] = []

        def fake_run_cmd(
            args: list[str],
            *,
            capture_output: bool = True,
            cwd: str | None = None,
            env: dict[str, str] | None = None,
        ) -> str:
            del cwd
            calls.append((args, capture_output, env))
            return ""

        with patch("ci_tools.sign_image.Path") as path_class:
            path_class.return_value.exists.return_value = True

            digest_ref = sign_published_image(
                image_org="danathar",
                image_name="zfs-aurora-containerfile",
                image_tag="candidate-deadbee-43",
                registry_actor="actor",
                registry_token="token",
                cosign_private_key="private-key",
                digest_lookup=lambda _ref: "sha256:stable",
                command_runner=fake_run_cmd,
            )

        self.assertEqual(
            digest_ref,
            "ghcr.io/danathar/zfs-aurora-containerfile@sha256:stable",
        )
        self.assertEqual(calls[0][0][:4], ["cosign", "sign", "--yes", "--key"])
        self.assertEqual(calls[0][1], False)
        self.assertEqual(
            calls[0][2],
            {
                "COSIGN_PASSWORD": "",
                "COSIGN_PRIVATE_KEY": "private-key",
            },
        )
        self.assertEqual(calls[1][0][:3], ["cosign", "verify", "--key"])
        self.assertEqual(calls[1][2], None)


if __name__ == "__main__":
    unittest.main()
