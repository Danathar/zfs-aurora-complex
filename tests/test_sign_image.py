"""
Script: tests/test_sign_image.py
What: Tests for published-image signing.
Doing: Verifies digest-ref construction, missing-key failure, and the exact cosign command sequence without touching a live registry.
Why: Signing moved out of workflow YAML and needs direct coverage now that it is code.
Goal: Keep tag-to-digest signing behavior explicit, testable, and easy to refactor safely.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
import unittest.mock

from ci_tools.common import CiToolError
from ci_tools.sign_image import image_digest_ref, image_tag_ref, sign_published_image


class SignImageTests(unittest.TestCase):
    def test_builds_expected_refs(self) -> None:
        self.assertEqual(
            image_tag_ref("danathar", "zfs-aurora-complex", "latest"),
            "docker://ghcr.io/danathar/zfs-aurora-complex:latest",
        )
        self.assertEqual(
            image_digest_ref("danathar", "zfs-aurora-complex", "sha256:abc"),
            "ghcr.io/danathar/zfs-aurora-complex@sha256:abc",
        )

    def test_requires_signing_key(self) -> None:
        with self.assertRaises(CiToolError):
            sign_published_image(
                image_org="danathar",
                image_name="zfs-aurora-complex",
                image_tag="latest",
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

        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "cosign.pub"
            key_path.write_text("public-key", encoding="utf-8")
            env = {
                "COSIGN_PUBLIC_KEY_PATH": str(key_path),
            }
            with unittest.mock.patch.dict(os.environ, env, clear=False):
                digest_ref = sign_published_image(
                    image_org="danathar",
                    image_name="zfs-aurora-complex",
                    image_tag="candidate-deadbee-43",
                    cosign_private_key="private-key",
                    digest_lookup=lambda _ref: "sha256:stable",
                    command_runner=fake_run_cmd,
                )

        all_args = [arg for call_args, _capture, _env in calls for arg in call_args]
        self.assertNotIn("--registry-username", all_args)
        self.assertNotIn("--registry-password", all_args)
        self.assertEqual(
            digest_ref,
            "ghcr.io/danathar/zfs-aurora-complex@sha256:stable",
        )
        self.assertEqual(
            calls[0][0][:6],
            [
                "cosign",
                "sign",
                "--yes",
                "--new-bundle-format=false",
                "--use-signing-config=false",
                "--registry-referrers-mode=legacy",
            ],
        )
        self.assertEqual(calls[0][1], False)
        self.assertEqual(
            calls[0][2],
            {
                "COSIGN_PASSWORD": "",
                "COSIGN_PRIVATE_KEY": "private-key",
            },
        )
        self.assertEqual(
            calls[1][0][:4],
            ["cosign", "verify", "--new-bundle-format=false", "--key"],
        )
        self.assertEqual(calls[1][0][4], str(key_path))
        self.assertEqual(calls[1][2], None)

    def test_cosign_password_comes_from_environment_when_set(self) -> None:
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

        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "cosign.pub"
            key_path.write_text("public-key", encoding="utf-8")
            env = {
                "COSIGN_PUBLIC_KEY_PATH": str(key_path),
                "COSIGN_PASSWORD": "real-password",
            }
            with unittest.mock.patch.dict(os.environ, env, clear=False):
                sign_published_image(
                    image_org="danathar",
                    image_name="zfs-aurora-complex",
                    image_tag="latest",
                    cosign_private_key="private-key",
                    digest_lookup=lambda _ref: "sha256:stable",
                    command_runner=fake_run_cmd,
                )

        self.assertEqual(calls[0][2]["COSIGN_PASSWORD"], "real-password")

    def test_public_key_resolution_is_cwd_independent(self) -> None:
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

        previous_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            try:
                digest_ref = sign_published_image(
                    image_org="danathar",
                    image_name="zfs-aurora-complex",
                    image_tag="latest",
                    cosign_private_key="private-key",
                    digest_lookup=lambda _ref: "sha256:stable",
                    command_runner=fake_run_cmd,
                )
            finally:
                os.chdir(previous_cwd)

        repo_key = Path(__file__).resolve().parent.parent / "cosign.pub"
        self.assertEqual(calls[1][0][4], str(repo_key))
        self.assertEqual(digest_ref, "ghcr.io/danathar/zfs-aurora-complex@sha256:stable")


if __name__ == "__main__":
    unittest.main()
