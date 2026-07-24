"""
Script: tests/test_common.py
What: Direct tests for shared CI helper behavior.
Doing: Mocks command wrappers and inspect helpers.
Why: Several workflow helpers rely on these common skopeo contracts.
Goal: Keep low-level image helper failure behavior clear.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_tools.common import (
    CiToolError,
    git_ls_remote_resolve,
    is_missing_image_error,
    run_cmd,
    run_json_cmd,
    skopeo_copy,
    skopeo_inspect_digest,
    skopeo_inspect_json_optional,
    write_github_env,
    write_github_outputs,
)


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


if __name__ == "__main__":
    unittest.main()
