"""
Script: tests/test_sign_image.py
What: Tests for published-image signing.
Doing: Verifies digest-ref construction, missing-key failure, and the exact cosign command sequence without touching a live registry.
Why: Signing moved out of workflow YAML and needs direct coverage now that it is code.
Goal: Keep tag-to-digest signing behavior explicit, testable, and easy to refactor safely.
"""

from __future__ import annotations

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from typing import ClassVar

from ci_tools.common import CiToolError
from ci_tools.sign_image import (
    image_digest_ref,
    image_tag_ref,
    main,
    sign_published_image,
)


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
        # Pin the guard, not just "something raised": an empty SIGNING_SECRET
        # must stop before any cosign invocation and before any tag lookup.
        def exploding_run_cmd(*_args, **_kwargs) -> str:
            raise AssertionError("cosign must not run without a signing key")

        def exploding_lookup(_ref: str) -> str:
            raise AssertionError("the tag must not be resolved without a signing key")

        with self.assertRaises(CiToolError) as raised:
            sign_published_image(
                image_org="danathar",
                image_name="zfs-aurora-complex",
                image_tag="latest",
                cosign_private_key="",
                digest_lookup=exploding_lookup,
                command_runner=exploding_run_cmd,
            )

        self.assertEqual(
            str(raised.exception),
            "SIGNING_SECRET is empty; cannot sign published image.",
        )

    def test_missing_verification_key_file_fails_closed_before_signing(self) -> None:
        # A verification key that is not on disk must stop the whole operation.
        # Signing without being able to verify afterwards would publish a
        # signature nothing in this run ever proved usable, and the later
        # cache-reuse check in check_akmods_cache.py trusts that proof.
        def exploding_run_cmd(*_args, **_kwargs) -> str:
            raise AssertionError("cosign must not run without a verification key on disk")

        def exploding_lookup(_ref: str) -> str:
            raise AssertionError("the tag must not be resolved without a verification key")

        with tempfile.TemporaryDirectory() as temp_dir:
            missing_key = Path(temp_dir) / "absent-cosign.pub"
            with unittest.mock.patch.dict(
                os.environ, {"COSIGN_PUBLIC_KEY_PATH": str(missing_key)}, clear=False
            ), self.assertRaises(CiToolError) as raised:
                sign_published_image(
                    image_org="danathar",
                    image_name="zfs-aurora-complex",
                    image_tag="latest",
                    cosign_private_key="private-key",
                    digest_lookup=exploding_lookup,
                    command_runner=exploding_run_cmd,
                )

            self.assertEqual(
                str(raised.exception),
                f"Missing required verification key file: {missing_key}",
            )

    def test_blank_verification_key_path_falls_back_to_the_repository_key(self) -> None:
        # COSIGN_PUBLIC_KEY_PATH is read with .strip(), so a value that is only
        # whitespace -- what an unset workflow input expands to -- must fall
        # back to the committed cosign.pub rather than being used as a path.
        calls: list[list[str]] = []

        def fake_run_cmd(args: list[str], **_kwargs) -> str:
            calls.append(args)
            return ""

        with unittest.mock.patch.dict(
            os.environ, {"COSIGN_PUBLIC_KEY_PATH": "   "}, clear=False
        ):
            sign_published_image(
                image_org="danathar",
                image_name="zfs-aurora-complex",
                image_tag="latest",
                cosign_private_key="private-key",
                digest_lookup=lambda _ref: "sha256:stable",
                command_runner=fake_run_cmd,
            )

        repo_key = Path(__file__).resolve().parent.parent / "cosign.pub"
        self.assertEqual(calls[1][0:2], ["cosign", "verify"])
        self.assertEqual(calls[1][4], str(repo_key))

    def _assert_digest_lookup_result_fails_closed(self, lookup_result: str) -> None:
        def exploding_run_cmd(*_args, **_kwargs) -> str:
            raise AssertionError("cosign must not run on an unresolved digest")

        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "cosign.pub"
            key_path.write_text("public-key", encoding="utf-8")
            with unittest.mock.patch.dict(
                os.environ, {"COSIGN_PUBLIC_KEY_PATH": str(key_path)}, clear=False
            ), self.assertRaises(CiToolError) as raised:
                sign_published_image(
                    image_org="danathar",
                    image_name="zfs-aurora-complex",
                    image_tag="latest",
                    cosign_private_key="private-key",
                    digest_lookup=lambda _ref: lookup_result,
                    command_runner=exploding_run_cmd,
                )

        self.assertEqual(
            str(raised.exception),
            "Failed to resolve digest for docker://ghcr.io/danathar/zfs-aurora-complex:latest",
        )

    def test_empty_digest_lookup_result_fails_closed(self) -> None:
        # An empty lookup result must not be pasted into a digest ref. Without
        # this guard the ref becomes `ghcr.io/<org>/<name>@`, which is not the
        # image this run built, and the run would report a successful signing.
        self._assert_digest_lookup_result_fails_closed("")

    def test_null_digest_lookup_result_fails_closed(self) -> None:
        # A JSON `null` that reached the caller as the literal string "null" is
        # the same non-answer as an empty string and must fail the same way.
        self._assert_digest_lookup_result_fails_closed("null")

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

        all_args = [arg for call_args, *_ in calls for arg in call_args]
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

    def test_explicit_digest_is_signed_without_resolving_the_tag(self) -> None:
        # The shared akmods cache tag is republished by more than one workflow,
        # so a caller that already pinned a digest must be able to sign exactly
        # that digest. Re-resolving the tag here would sign whatever a
        # concurrent run had most recently pushed instead.
        calls: list[list[str]] = []

        def fake_run_cmd(args: list[str], **_kwargs) -> str:
            calls.append(args)
            return ""

        def exploding_lookup(_ref: str) -> str:
            raise AssertionError("tag must not be resolved when a digest is supplied")

        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "cosign.pub"
            key_path.write_text("public-key", encoding="utf-8")
            with unittest.mock.patch.dict(
                os.environ, {"COSIGN_PUBLIC_KEY_PATH": str(key_path)}, clear=False
            ):
                digest_ref = sign_published_image(
                    image_org="danathar",
                    image_name="zfs-aurora-complex-akmods",
                    image_tag="main-43",
                    cosign_private_key="private-key",
                    image_digest="sha256:pinned",
                    digest_lookup=exploding_lookup,
                    command_runner=fake_run_cmd,
                )

        self.assertEqual(
            digest_ref,
            "ghcr.io/danathar/zfs-aurora-complex-akmods@sha256:pinned",
        )
        # Both the sign and the verify call must target the pinned digest.
        self.assertEqual(calls[0][-1], "ghcr.io/danathar/zfs-aurora-complex-akmods@sha256:pinned")
        self.assertEqual(calls[1][-1], "ghcr.io/danathar/zfs-aurora-complex-akmods@sha256:pinned")

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


class SignImageMainTests(unittest.TestCase):
    """
    Cover `main()`, the layer between the workflow step's environment and
    `sign_published_image`.

    Everything above this class calls `sign_published_image` with explicit
    keywords, so none of it observes which environment variable supplies which
    argument. That mapping is the whole job of `main()`: a swap of `IMAGE_NAME`
    and `IMAGE_TAG` here signs a real, valid signature over the wrong image, and
    the verification step that follows would pass on it.
    """

    ENV: ClassVar[dict[str, str]] = {
        "IMAGE_ORG": "danathar",
        "IMAGE_NAME": "zfs-aurora-complex",
        "IMAGE_TAG": "43-20260906",
        "COSIGN_PRIVATE_KEY": "cosign-private-key-material",
    }

    def _run_main(self, env: dict[str, str]) -> list[dict[str, str]]:
        """Run `main()` under `env` and return the recorded signing calls."""

        calls: list[dict[str, str]] = []

        def fake_sign(**kwargs: str) -> str:
            calls.append(kwargs)
            return "ghcr.io/danathar/zfs-aurora-complex@sha256:recorded"

        with (
            unittest.mock.patch.dict(os.environ, env, clear=True),
            unittest.mock.patch("ci_tools.sign_image.sign_published_image", fake_sign),
        ):
            main()

        return calls

    def test_each_variable_reaches_the_signing_parameter_it_names(self) -> None:
        # Compared as a whole dict on purpose. Asserting one field at a time
        # would still pass if two of the others were exchanged, which is the
        # exact mistake this function can make.
        calls = self._run_main({**self.ENV, "IMAGE_DIGEST": "sha256:pinned"})

        self.assertEqual(
            calls,
            [
                {
                    "image_org": "danathar",
                    "image_name": "zfs-aurora-complex",
                    "image_tag": "43-20260906",
                    "cosign_private_key": "cosign-private-key-material",
                    "image_digest": "sha256:pinned",
                }
            ],
        )

    def test_an_owner_with_capitals_is_lowercased_before_it_becomes_a_registry_path(
        self,
    ) -> None:
        # GHCR paths are lowercase. `Danathar` is how the org is spelled on
        # GitHub, and it is what `github.repository_owner` expands to, so the
        # normalization has to happen here rather than in the workflow.
        calls = self._run_main({**self.ENV, "IMAGE_ORG": "Danathar"})

        self.assertEqual(calls[0]["image_org"], "danathar")

    def test_a_digest_exported_by_an_earlier_step_is_stripped_before_use(self) -> None:
        # A digest read back out of `$GITHUB_OUTPUT`, or produced by a shell
        # command substitution, commonly arrives with a trailing newline.
        # Unstripped it becomes part of the `@sha256:...` ref and the registry
        # lookup fails on a ref that looks correct in the log.
        calls = self._run_main({**self.ENV, "IMAGE_DIGEST": "  sha256:pinned\n"})

        self.assertEqual(calls[0]["image_digest"], "sha256:pinned")

    def test_an_unset_digest_becomes_the_empty_string_that_selects_tag_lookup(
        self,
    ) -> None:
        # `sign_published_image` treats an empty digest as "resolve the tag".
        # Passing `None` through instead would build the ref `...@None`.
        calls = self._run_main(dict(self.ENV))

        self.assertEqual(calls[0]["image_digest"], "")

    def test_a_blank_digest_variable_also_selects_tag_lookup(self) -> None:
        # A workflow that always sets `IMAGE_DIGEST: ${{ steps.x.outputs.d }}`
        # sets it to the empty string when that step did not run.
        calls = self._run_main({**self.ENV, "IMAGE_DIGEST": "   "})

        self.assertEqual(calls[0]["image_digest"], "")

    def test_a_missing_required_variable_stops_before_anything_is_signed(self) -> None:
        def exploding_sign(**_kwargs: str) -> str:
            raise AssertionError("nothing may be signed with an incomplete environment")

        for missing in sorted(self.ENV):
            with self.subTest(missing=missing):
                env = {name: value for name, value in self.ENV.items() if name != missing}

                with (
                    unittest.mock.patch.dict(os.environ, env, clear=True),
                    unittest.mock.patch(
                        "ci_tools.sign_image.sign_published_image", exploding_sign
                    ),
                    self.assertRaises(CiToolError) as raised,
                ):
                    main()

                # The variable is named so the workflow log says which step
                # input is missing rather than only that signing failed.
                self.assertEqual(
                    str(raised.exception),
                    f"Missing required environment variable: {missing}",
                )


if __name__ == "__main__":
    unittest.main()
