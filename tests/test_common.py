"""
Script: tests/test_common.py
What: Direct tests for shared CI helper behavior.
Doing: Mocks command wrappers and inspect helpers.
Why: Several workflow helpers rely on these common skopeo contracts.
Goal: Keep low-level image helper failure behavior clear.
"""

from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_tools.common import (
    COSIGN_TIMEOUT,
    GIT_REMOTE_TIMEOUT,
    REGISTRY_METADATA_TIMEOUT,
    REGISTRY_RETRY_ATTEMPTS,
    REGISTRY_RETRY_DELAY_SECONDS,
    REGISTRY_TRANSFER_TIMEOUT,
    CiToolError,
    cosign_verify,
    git_ls_remote_resolve,
    is_missing_image_error,
    registry_auth_dir,
    registry_auth_file,
    registry_host,
    run_cmd,
    run_cmd_with_retries,
    run_json_cmd,
    skopeo_copy,
    skopeo_inspect_digest,
    skopeo_inspect_json,
    skopeo_inspect_json_optional,
    write_github_env,
    write_github_outputs,
)
from shared.command_args import SECRET_ARG_FLAGS


def parse_github_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if "<<" in line:
            key, delimiter = line.split("<<", 1)
            index += 1
            value_lines: list[str] = []
            while index < len(lines) and lines[index] != delimiter:
                value_lines.append(lines[index])
                index += 1
            values[key] = "\n".join(value_lines)
        else:
            key, value = line.split("=", 1)
            values[key] = value
        index += 1
    return values


class GitLsRemoteResolveTests(unittest.TestCase):
    BRANCH_SHA = "3333333333333333333333333333333333333333"
    LIGHTWEIGHT_SHA = "4444444444444444444444444444444444444444"
    TAG_OBJECT_SHA = "1111111111111111111111111111111111111111"
    PEELED_COMMIT_SHA = "2222222222222222222222222222222222222222"

    def _resolve(self, output: str, ref: str) -> str:
        with patch("ci_tools.common.run_cmd", return_value=output):
            return git_ls_remote_resolve("https://example.invalid/repo.git", ref)

    def test_resolves_branch_ref(self) -> None:
        output = f"{self.BRANCH_SHA}\trefs/heads/main\n"
        self.assertEqual(self._resolve(output, "main"), self.BRANCH_SHA)

    def test_resolves_lightweight_tag_ref(self) -> None:
        output = f"{self.LIGHTWEIGHT_SHA}\trefs/tags/v1.0.0\n"
        self.assertEqual(self._resolve(output, "v1.0.0"), self.LIGHTWEIGHT_SHA)

    def test_annotated_tag_resolves_to_peeled_commit(self) -> None:
        # An annotated tag lists the tag object first, then the peeled `^{}`
        # commit. The commit is what `git checkout` lands on later, so it must
        # be the value returned here.
        output = (
            f"{self.TAG_OBJECT_SHA}\trefs/tags/v2.4.0\n"
            f"{self.PEELED_COMMIT_SHA}\trefs/tags/v2.4.0^{{}}\n"
        )
        self.assertEqual(self._resolve(output, "v2.4.0"), self.PEELED_COMMIT_SHA)

    def test_branch_preferred_over_same_named_tag(self) -> None:
        output = (
            f"{self.BRANCH_SHA}\trefs/heads/main\n"
            f"{self.LIGHTWEIGHT_SHA}\trefs/tags/main\n"
        )
        self.assertEqual(self._resolve(output, "main"), self.BRANCH_SHA)

    def test_raises_when_no_resolvable_sha(self) -> None:
        with self.assertRaises(CiToolError):
            self._resolve("not-a-sha\trefs/heads/main\n", "main")


class CommonTests(unittest.TestCase):
    def test_skopeo_inspect_digest_requires_digest_field(self) -> None:
        with patch(
            "ci_tools.common.skopeo_inspect_json",
            return_value={"Name": "example"},
        ), self.assertRaises(CiToolError) as context:
            skopeo_inspect_digest("docker://ghcr.io/example/image:tag")

        self.assertIn("docker://ghcr.io/example/image:tag", str(context.exception))

    def test_is_missing_image_error_matches_known_markers(self) -> None:
        self.assertTrue(is_missing_image_error("manifest unknown"))
        self.assertTrue(is_missing_image_error("Error: reading manifest: name unknown"))
        self.assertTrue(is_missing_image_error("404 Not Found"))
        self.assertFalse(is_missing_image_error("unauthorized: authentication required"))

    def test_skopeo_inspect_json_optional_returns_none_for_missing_image(self) -> None:
        with patch(
            "ci_tools.common.skopeo_inspect_json",
            side_effect=CiToolError("manifest unknown"),
        ):
            self.assertIsNone(
                skopeo_inspect_json_optional("docker://ghcr.io/example/image:tag")
            )

    def test_skopeo_inspect_json_optional_reraises_other_errors(self) -> None:
        with patch(
            "ci_tools.common.skopeo_inspect_json",
            side_effect=CiToolError("unauthorized: authentication required"),
        ), self.assertRaises(CiToolError):
            skopeo_inspect_json_optional("docker://ghcr.io/example/image:tag")

    def test_skopeo_inspect_json_optional_returns_result_on_success(self) -> None:
        with patch(
            "ci_tools.common.skopeo_inspect_json",
            return_value={"Digest": "sha256:abc"},
        ):
            self.assertEqual(
                skopeo_inspect_json_optional("docker://ghcr.io/example/image:tag", creds="a:b"),
                {"Digest": "sha256:abc"},
            )

    def test_skopeo_copy_omits_digest_flags_by_default(self) -> None:
        with patch("ci_tools.common.run_cmd") as run_cmd_mock:
            skopeo_copy("docker://src:tag", "docker://dst:tag")

        args = run_cmd_mock.call_args.args[0]
        self.assertNotIn("--preserve-digests", args)
        self.assertFalse(any(arg.startswith("--multi-arch=") for arg in args))

    def test_skopeo_copy_adds_preserve_digests_and_multi_arch_when_requested(self) -> None:
        with patch("ci_tools.common.run_cmd") as run_cmd_mock:
            skopeo_copy(
                "docker://src:tag",
                "docker://dst:tag",
                preserve_digests=True,
                multi_arch="all",
            )

        args = run_cmd_mock.call_args.args[0]
        self.assertIn("--preserve-digests", args)
        self.assertIn("--multi-arch=all", args)

    def test_cosign_verify_does_not_pass_new_bundle_format_flag(self) -> None:
        # cosign v2.4.1 (preinstalled in the akmods build container) does not
        # recognize --new-bundle-format at all, and v3.1.2 verifies this
        # repo's legacy-format signatures fine without it -- verified against
        # both real binaries against a real signed image. This call must work
        # unmodified in either environment.
        with patch("ci_tools.common.run_cmd") as run_cmd_mock:
            cosign_verify("ghcr.io/example/image@sha256:abc", key_path="/tmp/cosign.pub")

        args = run_cmd_mock.call_args.args[0]
        self.assertEqual(
            args, ["cosign", "verify", "--key", "/tmp/cosign.pub", "ghcr.io/example/image@sha256:abc"]
        )
        self.assertNotIn("--new-bundle-format=false", args)

    def test_cosign_verify_passes_no_credential_flags_at_all(self) -> None:
        # The credential travels in a DOCKER_CONFIG auth file, so the argv is
        # byte-for-byte the anonymous one. See CredentialArgvTests for the
        # file itself; this only pins that no flag came back.
        with patch("ci_tools.common.run_cmd") as run_cmd_mock:
            cosign_verify(
                "ghcr.io/example/image@sha256:abc",
                key_path="/tmp/cosign.pub",
                creds="octocat:ghs-token",
            )

        args = run_cmd_mock.call_args.args[0]
        self.assertEqual(
            args,
            ["cosign", "verify", "--key", "/tmp/cosign.pub", "ghcr.io/example/image@sha256:abc"],
        )
        self.assertNotIn("--registry-username", args)
        self.assertNotIn("--registry-password", args)

    def test_cosign_verify_propagates_failure(self) -> None:
        with patch(
            "ci_tools.common.run_cmd",
            side_effect=CiToolError("no signatures found"),
        ), self.assertRaises(CiToolError) as context:
            cosign_verify("ghcr.io/example/image@sha256:abc", key_path="/tmp/cosign.pub")

        self.assertIn("no signatures found", str(context.exception))

    def test_run_cmd_redacts_secret_args_in_failure_message(self) -> None:
        args = [
            "skopeo",
            "copy",
            "--src-creds",
            "actor:src-secret",
            "--dest-creds=actor:dest-secret",
            "--registry-username",
            "secret-user",
            "--registry-password=secret-password",
        ]
        error = subprocess.CalledProcessError(
            1,
            args,
            output="",
            stderr="failed",
        )
        with (
            patch("ci_tools.common.subprocess.run", side_effect=error),
            self.assertRaises(CiToolError) as context,
        ):
            run_cmd(args)

        message = str(context.exception)
        self.assertNotIn("src-secret", message)
        self.assertNotIn("dest-secret", message)
        self.assertNotIn("secret-user", message)
        self.assertNotIn("secret-password", message)
        self.assertIn("--src-creds ***REDACTED***", message)
        self.assertIn("--dest-creds=***REDACTED***", message)

    def test_run_json_cmd_redacts_secret_args_in_failure_message(self) -> None:
        with (
            patch("ci_tools.common.run_cmd", return_value="not-json"),
            self.assertRaises(CiToolError) as context,
        ):
            run_json_cmd(["skopeo", "inspect", "--creds", "actor:json-secret"])

        message = str(context.exception)
        self.assertNotIn("json-secret", message)
        self.assertIn("--creds ***REDACTED***", message)

    def test_run_cmd_passes_timeout_through_to_subprocess(self) -> None:
        with patch("ci_tools.common.subprocess.run") as subprocess_run:
            subprocess_run.return_value = subprocess.CompletedProcess([], 0, stdout="ok")
            run_cmd(["skopeo", "inspect", "example"], timeout=12.5)

        self.assertEqual(subprocess_run.call_args.kwargs["timeout"], 12.5)

    def test_run_cmd_defaults_to_no_timeout(self) -> None:
        # Callers with no defensible ceiling must keep waiting rather than
        # inherit an arbitrary one.
        with patch("ci_tools.common.subprocess.run") as subprocess_run:
            subprocess_run.return_value = subprocess.CompletedProcess([], 0, stdout="ok")
            run_cmd(["rpm", "-E", "%fedora"])

        self.assertIsNone(subprocess_run.call_args.kwargs["timeout"])

    def test_run_cmd_raises_ci_tool_error_on_timeout(self) -> None:
        # A hung child must fail the job the same way a nonzero exit does,
        # not escape as a bare subprocess.TimeoutExpired traceback.
        with (
            patch(
                "ci_tools.common.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["skopeo", "inspect"], 120.0),
            ),
            self.assertRaises(CiToolError) as context,
        ):
            run_cmd(["skopeo", "inspect", "example"], timeout=120.0)

        self.assertIn("timed out", str(context.exception))

    def test_run_cmd_redacts_secret_args_in_timeout_message(self) -> None:
        args = ["skopeo", "copy", "--src-creds", "actor:timeout-secret"]
        with (
            patch(
                "ci_tools.common.subprocess.run",
                side_effect=subprocess.TimeoutExpired(args, 30.0),
            ),
            self.assertRaises(CiToolError) as context,
        ):
            run_cmd(args, timeout=30.0)

        message = str(context.exception)
        self.assertNotIn("timeout-secret", message)
        self.assertIn("--src-creds ***REDACTED***", message)

    def test_timeout_message_is_not_classified_as_a_missing_image(self) -> None:
        # This is the one that matters for reuse decisions:
        # skopeo_inspect_json_optional classifies inspect failures by message
        # text, and returning None for a timeout would turn "we could not
        # tell" into "the image does not exist" -- which downstream code reads
        # as a definitive answer.
        with (
            patch(
                "ci_tools.common.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["skopeo", "inspect"], 120.0),
            ),
            self.assertRaises(CiToolError) as context,
        ):
            run_cmd(["skopeo", "inspect", "example"], timeout=120.0)

        message = str(context.exception)
        self.assertFalse(is_missing_image_error(message))

    def test_skopeo_inspect_json_optional_reraises_timeouts(self) -> None:
        # End-to-end form of the check above, through the real classifier.
        with (
            patch(
                "ci_tools.common.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["skopeo", "inspect"], 120.0),
            ),
            self.assertRaises(CiToolError),
        ):
            skopeo_inspect_json_optional("ghcr.io/example/image:tag")

    def test_registry_and_signing_helpers_bound_their_calls(self) -> None:
        # Each helper that talks to a registry, a git remote, or a signature
        # store passes a ceiling. Asserting the constant rather than a literal
        # keeps this test about "is it bounded", not about the exact number.
        cases = (
            (
                "inspect",
                lambda: skopeo_inspect_digest("ghcr.io/example/image:tag"),
                REGISTRY_METADATA_TIMEOUT,
            ),
            (
                "copy",
                lambda: skopeo_copy("docker://a", "docker://b"),
                REGISTRY_TRANSFER_TIMEOUT,
            ),
            (
                "ls-remote",
                lambda: git_ls_remote_resolve("https://example.invalid/r.git", "main"),
                GIT_REMOTE_TIMEOUT,
            ),
            (
                "cosign-verify",
                lambda: cosign_verify("ghcr.io/example/image@sha256:x", key_path="cosign.pub"),
                COSIGN_TIMEOUT,
            ),
        )
        for label, invoke, expected_timeout in cases:
            with self.subTest(helper=label):
                with patch("ci_tools.common.run_cmd") as run_cmd_mock:
                    run_cmd_mock.return_value = (
                        '{"Digest": "sha256:x"}'
                        if label == "inspect"
                        else "0000000000000000000000000000000000000000\trefs/heads/main"
                    )
                    invoke()

                self.assertEqual(
                    run_cmd_mock.call_args.kwargs.get("timeout"), expected_timeout
                )

    def test_write_github_outputs_uses_safe_heredoc_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = Path(temp_dir) / "output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_file)}, clear=False):
                write_github_outputs(
                    {
                        "single": "value",
                        "newline": "first\nsecond",
                        "equals": "a=b",
                        "literal_eof": "contains EOF text",
                    }
                )

            self.assertEqual(
                parse_github_file(output_file),
                {
                    "single": "value",
                    "newline": "first\nsecond",
                    "equals": "a=b",
                    "literal_eof": "contains EOF text",
                },
            )

    def test_write_github_env_uses_safe_heredoc_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / "env"
            with patch.dict(os.environ, {"GITHUB_ENV": str(env_file)}, clear=False):
                write_github_env(
                    {
                        "SINGLE": "value",
                        "NEWLINE": "first\nsecond",
                        "EQUALS": "a=b",
                        "LITERAL_EOF": "contains EOF text",
                    }
                )

            self.assertEqual(
                parse_github_file(env_file),
                {
                    "SINGLE": "value",
                    "NEWLINE": "first\nsecond",
                    "EQUALS": "a=b",
                    "LITERAL_EOF": "contains EOF text",
                },
            )



class RegistryAuthDirTests(unittest.TestCase):
    """
    The file that replaced the credential flags.

    `registry_auth_dir` is the single place a registry token is written down,
    so its three properties are worth pinning directly: the file is in the
    format both tools read, it is mode `0600`, and it is gone once the call
    returns. The flag-and-env wiring that consumes it is covered by
    `CredentialArgvTests` below.
    """

    _CREDS = "Danathar:registry-token-value"

    def test_the_auth_file_is_the_docker_format_both_tools_read(self) -> None:
        with registry_auth_dir(self._CREDS, "docker://ghcr.io/example/image:tag") as auth_dir:
            payload = json.loads(Path(registry_auth_file(auth_dir)).read_text(encoding="utf-8"))

        # `{"auths": {"<host>": {"auth": base64("user:token")}}}` is what
        # skopeo's --authfile and cosign's DOCKER_CONFIG both expect. Decode
        # rather than compare a precomputed constant, so a wrongly-encoded
        # value fails here instead of at a registry.
        self.assertEqual(list(payload["auths"]), ["ghcr.io"])
        self.assertEqual(
            base64.b64decode(payload["auths"]["ghcr.io"]["auth"]).decode("utf-8"),
            self._CREDS,
        )

    def test_the_auth_file_is_owner_only_and_removed_afterwards(self) -> None:
        with registry_auth_dir(self._CREDS, "docker://ghcr.io/example/image:tag") as auth_dir:
            config_path = Path(registry_auth_file(auth_dir))
            mode = stat.S_IMODE(config_path.stat().st_mode)

        # The whole point of moving off argv is that the credential is not
        # readable by other processes on the runner. A world-readable file
        # would trade one exposure for another.
        self.assertEqual(mode, 0o600, f"auth file is {mode:o}, not 0600")
        self.assertFalse(config_path.exists(), "the auth file outlived the call")

    def test_a_local_only_copy_gets_an_auth_file_with_no_registry_entry(self) -> None:
        # A `dir:` end is not a registry. It must not turn into an auths key.
        with registry_auth_dir(self._CREDS, "dir:/var/tmp/layout") as auth_dir:
            payload = json.loads(Path(registry_auth_file(auth_dir)).read_text(encoding="utf-8"))

        self.assertEqual(payload, {"auths": {}})

    def test_no_credential_means_no_directory_and_no_override(self) -> None:
        # The anonymous callers must keep resolving credentials the way they
        # do today -- from whatever `docker/login-action` left behind -- so an
        # empty credential yields nothing to point a tool at.
        with registry_auth_dir(None, "docker://ghcr.io/example/image:tag") as auth_dir:
            self.assertEqual(auth_dir, "")
        with registry_auth_dir("", "docker://ghcr.io/example/image:tag") as auth_dir:
            self.assertEqual(auth_dir, "")

    def test_registry_host_reads_the_host_off_each_reference_shape(self) -> None:
        cases = {
            # skopeo's transport-prefixed form, and cosign's bare form.
            "docker://ghcr.io/danathar/image:main-44": "ghcr.io",
            "ghcr.io/danathar/image@sha256:abc": "ghcr.io",
            "localhost:5000/image:tag": "localhost:5000",
            # Local transports authenticate against nothing.
            "dir:/var/tmp/akmods": "",
            "oci:/var/tmp/layout:tag": "",
            "containers-storage:localhost/image:tag": "",
            # A short reference is a Docker Hub namespace, not a hostname.
            "library/fedora:43": "https://index.docker.io/v1/",
            "fedora:43": "https://index.docker.io/v1/",
        }
        for image_ref, expected in cases.items():
            with self.subTest(image_ref=image_ref):
                self.assertEqual(registry_host(image_ref), expected)


class CredentialArgvTests(unittest.TestCase):
    """
    Where the registry credential lands -- and, above all, where it does not.

    Gotcha 6 in `docs/signing-and-bootc.md` forbids passing a registry secret
    in command argv, because `/proc/<pid>/cmdline` is world-readable for the
    lifetime of the process. `redact_command_args` cannot enforce that rule:
    it only cleans up *error text*, and does nothing about the argv of a
    running child. These tests enforce it at the only place that can -- the
    argv the helpers actually build.

    Each test reads the auth file from inside the mocked command, because the
    real helper deletes it as soon as the command returns. Asserting on the
    file as well as the argv is what stops "the token is not in argv" from
    being satisfied by a helper that quietly stopped authenticating at all.
    """

    _CREDS = "Danathar:registry-token-value"
    _SECRET = "registry-token-value"

    def assert_secret_is_absent(self, argv: list[str]) -> None:
        """Assert the credential appears nowhere in the argv, behind a flag or not."""
        self.assertNotIn(
            self._SECRET,
            " ".join(argv),
            f"the token is in {argv!r}, where /proc/<pid>/cmdline exposes it",
        )
        for flag in SECRET_ARG_FLAGS:
            self.assertNotIn(flag, argv, f"{flag} takes a secret value in argv")

    def read_auth(self, path: str) -> dict[str, str]:
        """Return `{host: "user:token"}` as recorded in a helper-written auth file."""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return {
            host: base64.b64decode(entry["auth"]).decode("utf-8")
            for host, entry in payload["auths"].items()
        }

    def test_skopeo_inspect_json_hands_the_credential_over_in_an_auth_file(self) -> None:
        seen: dict[str, dict[str, str]] = {}

        def capture(argv, **_kwargs):
            self.assertIn("--authfile", argv)
            seen["auths"] = self.read_auth(argv[argv.index("--authfile") + 1])
            return {}

        with patch("ci_tools.common.run_json_cmd", side_effect=capture) as run_json_cmd_mock:
            skopeo_inspect_json("docker://ghcr.io/example/image:tag", creds=self._CREDS)

        argv = run_json_cmd_mock.call_args.args[0]
        self.assertEqual(argv[:2], ["skopeo", "inspect"])
        self.assertEqual(argv[-1], "docker://ghcr.io/example/image:tag")
        self.assert_secret_is_absent(argv)
        self.assertEqual(seen["auths"], {"ghcr.io": self._CREDS})
        self.assertEqual(
            run_json_cmd_mock.call_args.kwargs["timeout"], REGISTRY_METADATA_TIMEOUT
        )

    def test_skopeo_inspect_json_omits_the_auth_file_when_no_creds_are_given(self) -> None:
        # The anonymous form has to stay anonymous: `--authfile` pointing at a
        # credential-free file is not the same request as not passing it, and
        # it would shadow a Docker login the job already performed.
        with patch("ci_tools.common.run_json_cmd") as run_json_cmd_mock:
            skopeo_inspect_json("docker://ghcr.io/example/image:tag")

        self.assertEqual(
            run_json_cmd_mock.call_args.args[0],
            ["skopeo", "inspect", "docker://ghcr.io/example/image:tag"],
        )

    def test_skopeo_copy_authenticates_both_ends_from_the_auth_file(self) -> None:
        # The promotion shape: candidate tag to stable tag inside one private
        # registry, which needs the credential on each side.
        seen: dict[str, dict[str, str]] = {}

        def capture(argv, **_kwargs):
            # Assert the flags exist before indexing off them, so dropping one
            # reads as a failed assertion rather than a ValueError.
            self.assertIn("--src-authfile", argv)
            self.assertIn("--dest-authfile", argv)
            src = argv[argv.index("--src-authfile") + 1]
            dest = argv[argv.index("--dest-authfile") + 1]
            self.assertEqual(src, dest)
            seen["auths"] = self.read_auth(src)
            return ""

        source = "docker://ghcr.io/example/image:candidate"
        destination = "docker://ghcr.io/example/image:stable"
        with patch("ci_tools.common.run_cmd", side_effect=capture) as run_cmd_mock:
            skopeo_copy(source, destination, creds=self._CREDS)

        argv = run_cmd_mock.call_args.args[0]
        # Source and destination stay last, after the flags.
        self.assertEqual(argv[-2:], [source, destination])
        self.assert_secret_is_absent(argv)
        self.assertEqual(seen["auths"], {"ghcr.io": self._CREDS})

    def test_skopeo_copy_into_a_local_layout_still_authenticates_the_pull(self) -> None:
        # The akmods-cache shape: a registry pull into a local `dir:` layout.
        # The local end contributes no auths entry, but the registry end must
        # keep its credential or the cache check loses its ability to read a
        # private image.
        seen: dict[str, dict[str, str]] = {}

        def capture(argv, **_kwargs):
            seen["auths"] = self.read_auth(argv[argv.index("--src-authfile") + 1])
            return ""

        with patch("ci_tools.common.run_cmd", side_effect=capture) as run_cmd_mock:
            skopeo_copy(
                "docker://ghcr.io/example/image@sha256:abc",
                "dir:/var/tmp/akmods",
                creds=self._CREDS,
            )

        self.assert_secret_is_absent(run_cmd_mock.call_args.args[0])
        self.assertEqual(seen["auths"], {"ghcr.io": self._CREDS})

    def test_skopeo_copy_omits_credential_flags_when_none_is_given(self) -> None:
        with patch("ci_tools.common.run_cmd") as run_cmd_mock:
            skopeo_copy("docker://src:tag", "docker://dst:tag")

        argv = run_cmd_mock.call_args.args[0]
        self.assertNotIn("--src-authfile", argv)
        self.assertNotIn("--dest-authfile", argv)
        self.assertNotIn("--src-creds", argv)
        self.assertNotIn("--dest-creds", argv)

    def test_cosign_verify_hands_the_credential_over_through_docker_config(self) -> None:
        seen: dict[str, dict[str, str]] = {}

        def capture(argv, **kwargs):
            del argv
            auth_dir = kwargs["env"]["DOCKER_CONFIG"]
            seen["auths"] = self.read_auth(registry_auth_file(auth_dir))
            return ""

        with patch("ci_tools.common.run_cmd", side_effect=capture) as run_cmd_mock:
            cosign_verify(
                "ghcr.io/example/image@sha256:abc",
                key_path="/tmp/cosign.pub",
                creds=self._CREDS,
            )

        self.assert_secret_is_absent(run_cmd_mock.call_args.args[0])
        self.assertEqual(seen["auths"], {"ghcr.io": self._CREDS})
        self.assertEqual(run_cmd_mock.call_args.kwargs["timeout"], COSIGN_TIMEOUT)

    def test_cosign_verify_without_creds_sets_no_docker_config_override(self) -> None:
        # A job that ran `docker/login-action` relies on its own Docker config.
        # Overriding DOCKER_CONFIG with nothing in it would hide that login.
        with patch("ci_tools.common.run_cmd") as run_cmd_mock:
            cosign_verify("ghcr.io/example/image@sha256:abc", key_path="/tmp/cosign.pub")

        self.assertIsNone(run_cmd_mock.call_args.kwargs.get("env"))


class RunCmdWithRetriesTests(unittest.TestCase):
    """
    The retry wrapper around a transient registry read.

    Every case patches `ci_tools.common.run_cmd`, so nothing here runs a real
    command, and patches `time.sleep`, so the delay is asserted rather than
    waited out. What matters is the count: how many attempts a caller gets, and
    that a caller who asked for retries never silently gets one attempt.
    """

    def test_a_first_attempt_that_succeeds_runs_once_and_does_not_sleep(self) -> None:
        with (
            patch("ci_tools.common.run_cmd", return_value="output\n") as run_cmd_mock,
            patch("ci_tools.common.time.sleep") as sleep_mock,
        ):
            result = run_cmd_with_retries(["podman", "pull", "example"])

        self.assertEqual(result, "output\n")
        self.assertEqual(run_cmd_mock.call_count, 1)
        sleep_mock.assert_not_called()

    def test_a_transient_failure_is_retried_and_its_later_success_returned(self) -> None:
        # The failure this exists for: one truncated blob, then a clean pull.
        with (
            patch(
                "ci_tools.common.run_cmd",
                side_effect=[CiToolError("unexpected EOF"), "output\n"],
            ) as run_cmd_mock,
            patch("ci_tools.common.time.sleep") as sleep_mock,
        ):
            result = run_cmd_with_retries(["podman", "pull", "example"])

        self.assertEqual(result, "output\n")
        self.assertEqual(run_cmd_mock.call_count, 2)
        sleep_mock.assert_called_once_with(REGISTRY_RETRY_DELAY_SECONDS)

    def test_every_attempt_failing_raises_naming_the_count_and_the_last_error(self) -> None:
        with (
            patch(
                "ci_tools.common.run_cmd",
                side_effect=CiToolError("unexpected EOF"),
            ) as run_cmd_mock,
            patch("ci_tools.common.time.sleep") as sleep_mock,
            self.assertRaises(CiToolError) as caught,
        ):
            run_cmd_with_retries(["podman", "pull", "example"], attempts=3)

        self.assertEqual(run_cmd_mock.call_count, 3)
        # Sleeps happen between attempts, not after the last one.
        self.assertEqual(sleep_mock.call_count, 2)
        self.assertEqual(
            str(caught.exception),
            "Command failed after 3 attempts: unexpected EOF",
        )

    def test_a_timeout_is_retried_like_any_other_transient_failure(self) -> None:
        # `run_cmd` raises CiToolError for a timeout too. A stalled transfer and
        # a truncated one are the same class here, so both get the same budget.
        with (
            patch(
                "ci_tools.common.run_cmd",
                side_effect=[CiToolError("Command timed out after 1800.0s: podman pull"), ""],
            ) as run_cmd_mock,
            patch("ci_tools.common.time.sleep"),
        ):
            run_cmd_with_retries(["podman", "pull", "example"])

        self.assertEqual(run_cmd_mock.call_count, 2)

    def test_keyword_arguments_reach_run_cmd_unchanged(self) -> None:
        # The wrapper must not swallow the timeout or the capture setting: a
        # retried transfer with no ceiling is the hang this repo already guards.
        with (
            patch("ci_tools.common.run_cmd", return_value="") as run_cmd_mock,
            patch("ci_tools.common.time.sleep"),
        ):
            run_cmd_with_retries(
                ["podman", "pull", "example"],
                capture_output=False,
                timeout=REGISTRY_TRANSFER_TIMEOUT,
            )

        self.assertEqual(run_cmd_mock.call_args.args[0], ["podman", "pull", "example"])
        self.assertEqual(
            run_cmd_mock.call_args.kwargs,
            {"capture_output": False, "timeout": REGISTRY_TRANSFER_TIMEOUT},
        )

    def test_a_nonsense_attempt_count_is_rejected_rather_than_run_once(self) -> None:
        # `attempts=0` would otherwise fall out of the loop and raise the
        # unreachable branch, reporting a failure that never ran.
        with (
            patch("ci_tools.common.run_cmd") as run_cmd_mock,
            self.assertRaises(ValueError),
        ):
            run_cmd_with_retries(["podman", "pull", "example"], attempts=0)

        run_cmd_mock.assert_not_called()

    def test_the_default_attempt_count_is_the_registry_constant(self) -> None:
        with (
            patch(
                "ci_tools.common.run_cmd",
                side_effect=CiToolError("unexpected EOF"),
            ) as run_cmd_mock,
            patch("ci_tools.common.time.sleep"),
            self.assertRaises(CiToolError),
        ):
            run_cmd_with_retries(["podman", "pull", "example"])

        self.assertEqual(run_cmd_mock.call_count, REGISTRY_RETRY_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
