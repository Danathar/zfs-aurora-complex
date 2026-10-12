"""
Script: tests/e2e/test_cli_end_to_end.py
What: End-to-end tests that run `python3 -m ci_tools.cli` as a real subprocess.
Doing: Executes commands with a controlled environment and asserts on exit status, stderr, and the files they actually write.
Why: Workflow YAML depends on the process boundary -- exit status and the GITHUB_OUTPUT file format -- which no in-process test observes.
Goal: Catch a break in the contract between a workflow step and the command it runs, here rather than in a production build.

Nothing is mocked. See tests/e2e/README.md for what this tier does and does not
answer, and why no coverage number moves because of it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GITHUB_DIR = REPO_ROOT / ".github"

# A subprocess timeout, not a style choice. Without one a command that blocks on
# an unexpected read hangs the CI job until the 15-minute job timeout instead of
# failing with a readable message.
COMMAND_TIMEOUT_SECONDS = 60

# `python3 -m ci_tools.cli <command>` as the workflows write it. `[^\s|&;]+`
# stops at a shell pipe so `akmods-build-and-publish 2>&1 | tee ...` yields the
# command and not the redirection.
WORKFLOW_INVOCATION_RE = re.compile(r"python3 -m ci_tools\.cli\s+([^\s|&;]+)")


def _run_cli(
    command: str,
    *,
    env: dict[str, str],
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Run one CLI command in a child process with exactly the environment given.

    The environment is built from scratch rather than copied from os.environ:
    a developer machine or a GitHub runner already exports GITHUB_SHA,
    GITHUB_ACTOR and friends, and inheriting them would let a test pass on a
    value it never set.
    """

    child_env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(REPO_ROOT)}
    child_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "ci_tools.cli", command],
        cwd=str(cwd or REPO_ROOT),
        env=child_env,
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
        check=False,
    )


def _parse_github_file(path: Path) -> dict[str, str]:
    """
    Parse a GITHUB_OUTPUT or GITHUB_ENV file the way GitHub Actions does.

    The format is `name<<DELIMITER`, then the value lines, then a line holding
    only the delimiter. Parsing it properly is the point: a substring assertion
    such as `assertIn("image_name<<", text)` passes on a file with a missing
    terminator, which GitHub would reject at runtime.
    """

    values: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        header = lines[index]
        if "<<" not in header:
            raise AssertionError(f"line {index + 1} is not a heredoc header: {header!r}")
        name, delimiter = header.split("<<", 1)
        index += 1
        body: list[str] = []
        while index < len(lines) and lines[index] != delimiter:
            body.append(lines[index])
            index += 1
        if index >= len(lines):
            raise AssertionError(f"{name} is never terminated by its delimiter {delimiter!r}")
        index += 1
        values[name] = "\n".join(body)
    return values


def _repo_defaults() -> dict[str, str]:
    return json.loads((REPO_ROOT / "ci" / "defaults.json").read_text(encoding="utf-8"))


def _workflow_invoked_commands() -> dict[str, set[str]]:
    """Map each CLI command name to the `.github/` files that invoke it."""

    invoked: dict[str, set[str]] = {}
    for path in sorted(GITHUB_DIR.rglob("*")):
        if not path.is_file() or path.suffix not in {".yml", ".yaml"}:
            continue
        for command in WORKFLOW_INVOCATION_RE.findall(path.read_text(encoding="utf-8")):
            invoked.setdefault(command, set()).add(str(path.relative_to(REPO_ROOT)))
    return invoked


def _parser_accepted_commands() -> set[str]:
    """
    Ask the real parser, in a real process, which command names it accepts.

    Deliberately obtained by provoking an invalid choice rather than by running
    each command. Running them is not safe: the CLI dispatches as soon as the
    parser accepts a name, and `akmods-build-and-publish` with no environment
    set falls through to `just build`, `just login`, `just push` and
    `just manifest` whenever /tmp/akmods happens to exist -- an absolute path,
    so no choice of working directory isolates it. A test must not be able to
    publish anything (AGENTS.md section 0 rule 6).

    An unknown name is rejected during parsing, before any handler is reached,
    and argparse names every accepted choice when it does.
    """

    result = _run_cli("definitely-not-a-command", env={})
    match = re.search(r"choose from ([^)]*)\)", result.stderr)
    if match is None:
        raise AssertionError(
            "could not read the accepted commands out of argparse's error:\n" + result.stderr
        )
    accepted = {name.strip().strip("'\"") for name in match.group(1).split(",")}
    accepted.discard("")
    return accepted


class CommandWiringTests(unittest.TestCase):
    """
    Both directions of the workflow-to-CLI mapping.

    tests/test_cli.py checks the command map against a list written in the test
    file, so renaming a command and the test together passes while every
    workflow still invokes the old name. The failure then surfaces in a
    production build. These two assertions read the workflows instead.
    """

    def test_every_command_a_workflow_invokes_is_accepted_by_the_real_parser(self) -> None:
        invoked = _workflow_invoked_commands()
        self.assertTrue(invoked, "found no `python3 -m ci_tools.cli` invocations under .github/")

        accepted = _parser_accepted_commands()
        # Guard the guard: if argparse ever changes its message, the regex above
        # could match something small and every assertion below would pass on an
        # empty set.
        self.assertGreater(len(accepted), 1, f"implausible accepted-command set: {accepted}")

        for command, sources in sorted(invoked.items()):
            with self.subTest(command=command):
                self.assertIn(
                    command,
                    accepted,
                    f"{command} is invoked by {', '.join(sorted(sources))} "
                    f"but the CLI does not accept it",
                )

    def test_every_registered_command_is_invoked_by_something_under_github(self) -> None:
        # A command nobody calls is either dead or a workflow step that was
        # meant to be added. Either way it should be noticed on the pull
        # request that orphans it rather than left to accumulate.
        from ci_tools.cli import command_map

        invoked = set(_workflow_invoked_commands())
        registered = set(command_map())
        self.assertEqual(
            registered - invoked,
            set(),
            "registered but never invoked under .github/",
        )


class ExportRepoDefaultsEndToEndTests(unittest.TestCase):
    def test_writes_the_real_checked_in_defaults_in_a_format_github_can_parse(self) -> None:
        defaults = _repo_defaults()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output"
            env_path = Path(temp_dir) / "github-env"
            result = _run_cli(
                "export-repo-defaults",
                env={"GITHUB_OUTPUT": str(output_path), "GITHUB_ENV": str(env_path)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)

            # Parsed with the real heredoc protocol, then compared against the
            # file on disk. This is the assertion the unit test cannot make:
            # it patches load_repo_defaults, so it never reads ci/defaults.json.
            outputs = _parse_github_file(output_path)
            self.assertEqual(outputs["image_name"], defaults["IMAGE_NAME"])
            self.assertEqual(outputs["akmods_repo"], defaults["AKMODS_REPO"])
            self.assertEqual(outputs["default_base_image"], defaults["DEFAULT_BASE_IMAGE"])
            self.assertEqual(
                outputs["default_build_container_image"],
                defaults["DEFAULT_BUILD_CONTAINER_IMAGE"],
            )
            self.assertEqual(
                outputs["default_zfs_minor_version"],
                defaults["DEFAULT_ZFS_MINOR_VERSION"],
            )

            exported = _parse_github_file(env_path)
            self.assertEqual(exported["IMAGE_NAME"], defaults["IMAGE_NAME"])
            self.assertEqual(
                exported["DEFAULT_BUILD_CONTAINER_IMAGE"],
                defaults["DEFAULT_BUILD_CONTAINER_IMAGE"],
            )


class TaggingEndToEndTests(unittest.TestCase):
    def test_candidate_tag_is_written_as_a_step_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output"
            result = _run_cli(
                "compute-candidate-tag",
                env={
                    "GITHUB_OUTPUT": str(output_path),
                    "GITHUB_SHA": "deadbeefcafe0123456789",
                    "FEDORA_VERSION": "44",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                _parse_github_file(output_path)["candidate_tag"],
                "candidate-deadbee-44",
            )

    def test_branch_metadata_sanitizes_a_ref_name_that_is_not_registry_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output"
            result = _run_cli(
                "compute-branch-metadata",
                env={
                    "GITHUB_OUTPUT": str(output_path),
                    "GITHUB_REF_NAME": "Feature/Fix ZFS_2.4!",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            branch_tag = _parse_github_file(output_path)["branch_tag"]
            self.assertEqual(branch_tag, "br-feature-fix-zfs_2.4")
            # The value is about to become part of an image tag, so assert the
            # property that matters rather than only the string.
            self.assertRegex(branch_tag, r"^[a-z0-9._-]+$")

    def test_branch_image_tag_is_composed_from_the_prefix_a_prior_job_exported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output"
            result = _run_cli(
                "compose-branch-image-tag",
                env={
                    "GITHUB_OUTPUT": str(output_path),
                    # build-branch.yml passes needs.prepare-branch-metadata's
                    # branch_tag output here, so this is that value's shape.
                    "BRANCH_TAG_PREFIX": "br-feature-fix-zfs_2.4",
                    "FEDORA_VERSION": "44",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                _parse_github_file(output_path)["branch_image_tag"],
                "br-feature-fix-zfs_2.4-44",
            )

    def test_registry_context_is_written_to_both_output_and_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output"
            env_path = Path(temp_dir) / "github-env"
            result = _run_cli(
                "export-registry-context",
                env={
                    "GITHUB_OUTPUT": str(output_path),
                    "GITHUB_ENV": str(env_path),
                    "GITHUB_REPOSITORY_OWNER": "Danathar",
                    "GITHUB_ACTOR": "renovate[bot]",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            outputs = _parse_github_file(output_path)
            self.assertEqual(outputs["image_registry"], "ghcr.io/danathar")
            self.assertEqual(outputs["actor_is_bot"], "true")
            self.assertEqual(_parse_github_file(env_path)["IMAGE_ORG"], "danathar")


# The full input set write-build-inputs-manifest requires. Module level rather
# than a class attribute so it is copied, never mutated, by the tests below.
MANIFEST_ENVIRONMENT = {
    "GITHUB_REPOSITORY": "Danathar/zfs-aurora-complex",
    "GITHUB_WORKFLOW": "Build native image",
    "GITHUB_RUN_ID": "1234567890",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_RUN_NUMBER": "42",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_SHA": "deadbeefcafe0123456789",
    "GITHUB_ACTOR": "Danathar",
    "USE_INPUT_LOCK": "false",
    "LOCK_FILE_PATH": "ci/inputs.lock.json",
    "FEDORA_VERSION": "44",
    "KERNEL_RELEASE": "6.17.4-200.fc44.x86_64",
    "DETECTED_KERNEL_RELEASES": "6.17.4-200.fc44.x86_64 6.17.3-200.fc44.x86_64",
    "BASE_IMAGE_REF": "ghcr.io/ublue-os/aurora-dx@sha256:" + "a" * 64,
    "BASE_IMAGE_NAME": "ghcr.io/ublue-os/aurora-dx",
    "BASE_IMAGE_TAG": "stable",
    "BASE_IMAGE_PINNED": "true",
    "BASE_IMAGE_DIGEST": "sha256:" + "a" * 64,
    "BUILD_CONTAINER_REF": "ghcr.io/ublue-os/devcontainer@sha256:" + "b" * 64,
    "BUILD_CONTAINER_PINNED": "true",
    "BUILD_CONTAINER_DIGEST": "sha256:" + "b" * 64,
    "ZFS_MINOR_VERSION": "2.4",
    "ZFS_VERSION": "2.4.0",
    "AKMODS_UPSTREAM_REF": "c" * 40,
}


class BuildInputsManifestEndToEndTests(unittest.TestCase):
    def test_writes_a_parseable_manifest_at_the_documented_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            # cwd matters: the module writes to a relative `artifacts/` path, so
            # the workflow step's working directory decides where the artifact
            # the upload step later collects actually lands.
            result = _run_cli(
                "write-build-inputs-manifest",
                env=dict(MANIFEST_ENVIRONMENT),
                cwd=work_dir,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            manifest_path = work_dir / "artifacts" / "build-inputs.json"
            self.assertTrue(manifest_path.is_file(), "no artifacts/build-inputs.json was written")

            document = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(document["schema_version"], 1)
            self.assertEqual(document["repository"], "Danathar/zfs-aurora-complex")
            # Run identifiers are ints in the document and strings in the
            # environment; a manifest that recorded them as strings would still
            # be valid JSON and would still read correctly to a human.
            self.assertEqual(document["run"]["id"], 1234567890)
            self.assertIsInstance(document["run"]["id"], int)
            self.assertIs(document["inputs"]["use_input_lock"], False)
            self.assertEqual(
                document["inputs"]["detected_kernel_releases"],
                ["6.17.4-200.fc44.x86_64", "6.17.3-200.fc44.x86_64"],
            )
            self.assertEqual(document["inputs"]["base_image_digest"], "sha256:" + "a" * 64)

    def test_a_missing_input_stops_the_step_instead_of_writing_a_partial_manifest(self) -> None:
        environment = dict(MANIFEST_ENVIRONMENT)
        del environment["BASE_IMAGE_DIGEST"]

        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            result = _run_cli(
                "write-build-inputs-manifest",
                env=environment,
                cwd=work_dir,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("Missing required environment variable: BASE_IMAGE_DIGEST", result.stderr)
            self.assertFalse(
                (work_dir / "artifacts" / "build-inputs.json").exists(),
                "a partial manifest was written for a run whose inputs were incomplete",
            )


class ClassifyAkmodsFailureEndToEndTests(unittest.TestCase):
    """
    The classify step's process contract, which is the opposite of every other
    step's: it runs under `if: failure()` purely to explain a failure that has
    already happened, so it must exit 0 whatever it finds. A nonzero status here
    would replace the akmods build's own failure message with this step's.
    """

    def test_a_classified_failure_exits_zero_and_writes_the_uploaded_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            # cwd matters: the payload path is relative, and the upload step
            # collects `artifacts/akmods-failure.json` from the step's working
            # directory.
            log_dir = work_dir / "artifacts"
            log_dir.mkdir()
            (log_dir / "akmods-build.log").write_text(
                "ZFS_META_VERSION='2.4.1'\nZFS_META_KVER_MAX='6.19'\nconfigure: exit 1\n",
                encoding="utf-8",
            )
            output_path = work_dir / "github-output"

            result = _run_cli(
                "classify-akmods-failure",
                env={
                    "AKMODS_FAILURE_LOG": "artifacts/akmods-build.log",
                    "KERNEL_RELEASE": "7.0.4-200.fc44.x86_64",
                    "AKMODS_UPSTREAM_REF": "c" * 40,
                    "FEDORA_VERSION": "44",
                    "AKMODS_FAILURE_PAYLOAD_PATH": "artifacts/akmods-failure.json",
                    "GITHUB_OUTPUT": str(output_path),
                },
                cwd=work_dir,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload_path = work_dir / "artifacts" / "akmods-failure.json"
            self.assertTrue(payload_path.is_file(), "no artifacts/akmods-failure.json was written")
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["failure_kind"], "upstream-compat")

            outputs = _parse_github_file(output_path)
            self.assertEqual(outputs["failure_kind"], "upstream-compat")
            self.assertEqual(outputs["sticky_key"], payload["key"])

    def test_a_missing_log_still_exits_zero_so_the_build_failure_is_what_shows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            output_path = work_dir / "github-output"

            result = _run_cli(
                "classify-akmods-failure",
                env={
                    "AKMODS_FAILURE_LOG": "artifacts/akmods-build.log",  # never written
                    "KERNEL_RELEASE": "7.0.4-200.fc44.x86_64",
                    "GITHUB_OUTPUT": str(output_path),
                },
                cwd=work_dir,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("Traceback (most recent call last)", result.stderr)
            self.assertEqual(_parse_github_file(output_path)["failure_kind"], "unknown")


class ProcessContractTests(unittest.TestCase):
    """
    The two exit statuses every workflow step depends on.

    Steps run under `set -e`, so a command that reports a problem on stderr and
    exits 0 does not stop the build. tests/test_cli.py never observes this: it
    calls run_command directly, so SystemExit is raised inside the test process
    rather than becoming a status a shell can read.
    """

    def test_a_missing_required_variable_exits_one_and_names_the_variable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output"
            result = _run_cli(
                "compute-candidate-tag",
                env={"GITHUB_OUTPUT": str(output_path)},  # no FEDORA_VERSION, no GITHUB_SHA
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("Missing required environment variable: GITHUB_SHA", result.stderr)
            # A CiToolError is a reported condition, not a crash. A traceback
            # here would mean the handler in cli.main stopped catching it.
            self.assertNotIn("Traceback (most recent call last)", result.stderr)
            self.assertFalse(
                output_path.exists(),
                "a step output was written for a command that failed",
            )

    def test_an_unknown_command_exits_two_and_lists_the_accepted_names(self) -> None:
        result = _run_cli("promote-latest", env={})

        # argparse's own usage-error status. It is distinct from 1 on purpose:
        # 1 means the command ran and refused, 2 means the workflow asked for a
        # command that does not exist.
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)
        self.assertIn("promote-stable", result.stderr)


if __name__ == "__main__":
    unittest.main()
