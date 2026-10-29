"""
Script: tests/test_tagging_context.py
What: Tests for the shared lightweight tag and registry-context helpers.
Doing: Verifies candidate-tag naming, registry-context exports, and the two
command entrypoints that read the environment and write step outputs.
Why: These rules are small, but several workflows depend on them.
Goal: Keep the reduced helper surface explicit and safe.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ci_tools.tagging_context import (
    build_candidate_tag,
    export_registry_context_values,
    main_compute_candidate_tag,
    main_export_registry_context,
)


def parse_github_file(path: Path) -> dict[str, str]:
    """
    Parse a `GITHUB_OUTPUT` file the way GitHub Actions does.

    The helpers write heredoc form -- `name<<DELIM`, the value, then `DELIM` --
    so an `assertIn("candidate_tag<<", text)` check passes even when the
    value written under that name is wrong. Reading the name back out is what
    makes these tests assert the value a later workflow step would receive.
    """

    values: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        name, _, delimiter = lines[index].partition("<<")
        index += 1
        body: list[str] = []
        while index < len(lines) and lines[index] != delimiter:
            body.append(lines[index])
            index += 1
        index += 1  # the closing delimiter line
        values[name] = "\n".join(body)
    return values


class TaggingContextTests(unittest.TestCase):
    def test_builds_candidate_tag_from_sha_and_fedora_version(self) -> None:
        self.assertEqual(
            build_candidate_tag(
                github_sha="deadbeefcafebabefeedface1234567890abcdef",
                fedora_version="43",
            ),
            "candidate-deadbee-43",
        )

    def test_export_registry_context_values(self) -> None:
        self.assertEqual(
            export_registry_context_values(
                repository_owner="Danathar",
            ),
            {
                "image_org": "danathar",
                "image_registry": "ghcr.io/danathar",
            },
        )

    def test_main_export_registry_context_writes_outputs_and_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output.txt"
            env_path = Path(temp_dir) / "github-env.txt"
            with patch.dict(
                os.environ,
                {
                    "GITHUB_REPOSITORY_OWNER": "Danathar",
                    "GITHUB_OUTPUT": str(output_path),
                    "GITHUB_ENV": str(env_path),
                },
                clear=False,
            ):
                main_export_registry_context()

            output_text = output_path.read_text(encoding="utf-8")
            env_text = env_path.read_text(encoding="utf-8")
            self.assertIn("image_org<<", output_text)
            self.assertIn("danathar", output_text)
            self.assertIn("IMAGE_REGISTRY<<", env_text)
            self.assertIn("ghcr.io/danathar", env_text)


class TaggingEntrypointTests(unittest.TestCase):
    """
    The command entrypoint `ci_tools.cli` dispatches for tag naming.

    The pure helpers above are covered, but a workflow step never calls them.
    It runs a command that has to read the right environment variable names,
    write the right output name, and put the helper's result in it. Each of
    those wirings is a separate way to be wrong while every helper test
    still passes.
    """

    def run_entrypoint(self, entrypoint, env: dict[str, str]) -> tuple[dict[str, str], str]:
        """
        Run one entrypoint with exactly `env` plus a GITHUB_OUTPUT path.

        The environment is cleared rather than extended: a CI runner already
        exports GITHUB_SHA and GITHUB_REF_NAME, and inheriting them would let a
        test pass on a value it never set.
        """

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output.txt"
            stdout = io.StringIO()
            with (
                patch.dict(
                    os.environ,
                    {**env, "GITHUB_OUTPUT": str(output_path)},
                    clear=True,
                ),
                redirect_stdout(stdout),
            ):
                entrypoint()
            return parse_github_file(output_path), stdout.getvalue()

    def test_compute_candidate_tag_writes_the_short_sha_tag_as_an_output(self) -> None:
        outputs, printed = self.run_entrypoint(
            main_compute_candidate_tag,
            {"GITHUB_SHA": "deadbeefcafebabefeedface1234567890abcdef", "FEDORA_VERSION": "43"},
        )

        self.assertEqual(outputs["candidate_tag"], "candidate-deadbee-43")
        self.assertIn("candidate-deadbee-43", printed)

    def test_the_signing_job_builds_the_same_candidate_tag(self) -> None:
        """
        build.yml's sign-candidate-image works the candidate tag out again in shell,
        from the commit and the Fedora version, rather than trusting the build job's
        output. promote_stable.py looks the tag up by this function's shape, so the two
        must agree.
        """

        build = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "build.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('candidate_tag="candidate-${GITHUB_SHA::7}-${FEDORA_VERSION}"', build)
        self.assertEqual(
            build_candidate_tag(github_sha="0123456789abcdef", fedora_version="44"),
            "candidate-0123456-44",
        )


if __name__ == "__main__":
    unittest.main()
