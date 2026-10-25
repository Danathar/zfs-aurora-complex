"""
Script: tests/test_action_prepare_main_akmods.py
What: Executes the shell of .github/actions/prepare-main-akmods/action.yml -- the composite
action that resolves every build input, probes the shared akmods cache, and rebuilds and
republishes that cache when it no longer matches the kernel and ZFS version this run resolved.
Doing: Extracts each step's `run:` body and `env:` block with PyYAML, resolves the `${{ inputs.* }}`
and `${{ steps.<id>.outputs.* }}` expressions the way GitHub resolves them (refusing any
expression this harness does not model), and runs the bodies under bash against a `python3` stub
that records the argv and environment of every `ci_tools.cli` invocation the steps spawn.
Why: This action is the front half of the main build, and no other test executes any of its
shell. tests/test_workflow_build_container.py and tests/test_prompt_catalog.py read this file as
text for its restricted-run input, tests/e2e/ dispatches `ci_tools.cli` commands directly rather
than through the steps that wire them, and tests/check_coverage.py leaves action YAML out of its
manifest by design. What that leaves
untested is not the helpers -- those are covered -- but the wiring between them, where several
decisions live that are invisible once a job is green: the build's `set -o pipefail` (without it
`tee` succeeds, the step exits 0 and reports a rebuild that never happened), the `2>&1` that puts
the compiler's stderr into the log the classification step later reads, the `rebuilt=true` marker
written only on the success path, the log path that two steps must agree on, the hand-off of
the resolved *patch* version to the akmods build as `ZFS_VERSION`, the name the fork reads, and
the checksum that stands between a downloaded `just` and the PATH every later step runs with.
Goal: Make a broken hand-off between these steps fail here rather than as a shared cache that was
republished wrong, a green run that signed nothing, or a sticky issue that never opened.

The action's text is executed rather than copied. A renamed or deleted step, a newly wired input,
or an expression form this harness does not model fails loudly instead of leaving a test asserting
nothing.

Nothing here builds a kernel module or touches a registry. `python3` is a stub and PATH omits the
directories where a real interpreter lives, so a step that stopped being stubbed would fail with
"command not found" rather than start a build. `curl` is a stub too, so the step that installs a
pinned `just` is run against a download this file supplies rather than one from GitHub.

PyYAML is not a pytest dependency; CI installs it by name (see .github/workflows/test.yml). The
import is guarded so the suite still runs under `python3 -m unittest discover -s tests` with
nothing installed, matching tests/test_issue_templates.py.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only where PyYAML is absent
    yaml = None

import ci_tools.write_build_inputs_manifest as manifest_writer
from ci_tools.cli import command_map
from ci_tools.resolve_build_inputs import ResolvedBuildInputs, write_resolved_build_outputs

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTION_PATH = REPO_ROOT / ".github" / "actions" / "prepare-main-akmods" / "action.yml"

BUILD_STEP = "Build and publish shared self-hosted ZFS akmods image"
CLASSIFY_STEP = "Classify akmods build failure"
CACHE_STEP = "Check for existing shared akmods cache"
VERIFY_STEP = "Verify the rebuilt cache matches the resolved ZFS version"
REFUSE_STEP = "Refuse to refresh the shared akmods cache from a restricted run"
JUST_STEP = "Install pinned just for the akmods Justfile"

# Every step of this action that carries shell, and the one `ci_tools.cli` command it must spawn.
# `None` marks the two steps that are shell of their own rather than a call into a helper: the
# refusal gate and the pinned `just` install. Keeping the whole set here means a step added to the action without a
# decision about what it runs turns this file red.
STEP_COMMANDS = {
    "Resolve build inputs for this run": "resolve-build-inputs",
    "Write build inputs manifest": "write-build-inputs-manifest",
    CACHE_STEP: "check-akmods-cache",
    REFUSE_STEP: None,
    JUST_STEP: None,
    "Clone resolved upstream akmods tooling": "akmods-clone-pinned",
    "Configure shared ZFS target in self-hosted namespace": "akmods-configure-zfs-target",
    BUILD_STEP: "akmods-build-and-publish",
    CLASSIFY_STEP: "classify-akmods-failure",
    "Resolve shared akmods cache digest": "pin-akmods-cache",
    VERIFY_STEP: "check-akmods-cache",
}

# PATH must reach the stub and must NOT reach a real interpreter, which lives in /usr/bin on a
# runner. Only coreutils and a shell are offered.
SAFE_PATH = "/bin"

# How GitHub invokes a `shell: bash` step: the body is written to a file and run with `-e` and
# `-o pipefail` already set. Running the bodies any other way would make this harness kinder than
# a runner -- a step that fails there would pass here.
RUNNER_SHELL = ["bash", "--noprofile", "--norc", "-eo", "pipefail"]

# What the action is given by the calling workflow.
INPUTS = {
    "use_input_lock": "false",
    "lock_file": "ci/build-inputs.lock.json",
    "build_container_ref": "quay.io/fedora/fedora-bootc:42",
    "akmods_repo": "akmods-zfs",
    "akmods_upstream_repo": "https://github.com/Danathar/ublue-akmods",
    "rebuild_akmods": "false",
    "allow_cache_rebuild": "true",
    "registry_actor": "danathar",
    "registry_token": "ghp-test-token",
}

# What the `resolve` step would have published by the time the later steps run. The ZFS values are
# the interesting pair: the minor *line* and the exact *patch* resolved on it. Several steps must
# be handed one and not the other, and the two are indistinguishable in a passing job.
ZFS_MINOR_LINE = "2.4"
ZFS_PATCH_VERSION = "2.4.4"

RESOLVE_OUTPUTS = {
    "version": "42",
    "kernel_release": "6.16.4-200.fc42.x86_64",
    "detected_kernel_releases": "6.16.4-200.fc42.x86_64",
    "base_image_ref": "ghcr.io/ublue-os/kinoite-main:42",
    "base_image_name": "kinoite-main",
    "base_image_tag": "42",
    "base_image_pinned": "ghcr.io/ublue-os/kinoite-main@sha256:" + "a" * 64,
    "base_image_digest": "sha256:" + "a" * 64,
    "build_container_ref": "quay.io/fedora/fedora-bootc:42",
    "build_container_pinned": "quay.io/fedora/fedora-bootc@sha256:" + "b" * 64,
    "build_container_digest": "sha256:" + "b" * 64,
    "zfs_minor_version": ZFS_MINOR_LINE,
    "zfs_version": ZFS_PATCH_VERSION,
    "akmods_upstream_ref": "d" * 40,
    "use_input_lock": "false",
    "lock_file_path": "ci/build-inputs.lock.json",
}

STEP_OUTPUTS = {
    "resolve": RESOLVE_OUTPUTS,
    # The cache probe and the pin step each resolve the shared tag on their own, so their pinned
    # refs are given different digests here: a step wired to the wrong one then reads a value the
    # test can tell apart.
    "cache": {
        "exists": "true",
        "akmods_image": "ghcr.io/danathar/akmods-zfs:42-6.16.4-200.fc42.x86_64",
        "akmods_image_pinned": "ghcr.io/danathar/akmods-zfs@sha256:" + "f" * 64,
    },
    # The local image ID the build step records between `just build` and `just push`.
    "akmods_build": {
        "built_image_id": "9" * 64,
    },
    "pin_akmods": {
        "akmods_image": "ghcr.io/danathar/akmods-zfs:42-6.16.4-200.fc42.x86_64",
        "akmods_image_pinned": "ghcr.io/danathar/akmods-zfs@sha256:" + "e" * 64,
        "akmods_image_digest": "sha256:" + "e" * 64,
    },
}

# What the stubbed build prints. Two streams, distinguishable, because whether the step captures
# both is exactly the question the log-capture test asks.
BUILD_STDOUT = "building zfs akmod for 6.16.4-200.fc42.x86_64"
BUILD_STDERR = "zfs_context.c:412:9: error: implicit declaration of function 'bio_set_dev'"

# Where the build step buffers its output. Hard-coded in the step's shell, so hard-coded here.
BUILD_LOG_PATH = "artifacts/akmods-build.log"

# A line already in GITHUB_OUTPUT before the step runs. GitHub reuses one file for the whole job,
# so a step that truncated it would silently drop an earlier step's outputs.
PRIOR_OUTPUT_LINE = "resolved_by=an-earlier-step"

PYTHON_STUB = r"""#!/bin/sh
# Records argv one element per line and the whole environment, then replays the configured
# streams and exit status. The record is written in one block so a non-zero exit cannot leave a
# half-written entry for the next call to be appended to.
{
  printf 'CMD\n'
  for arg in "$@"; do
    printf 'ARG\t%s\n' "${arg}"
  done
  /usr/bin/env | while IFS= read -r line; do
    printf 'ENV\t%s\n' "${line}"
  done
  printf 'ENDOFCALL\n'
} >> "${STUB_LOG}"

[ -n "${STUB_STDOUT}" ] && printf '%s\n' "${STUB_STDOUT}"
[ -n "${STUB_STDERR}" ] && printf '%s\n' "${STUB_STDERR}" >&2
exit "${STUB_EXIT:-0}"
"""

# Stands in for curl: records its argv one element per line, then writes the file named by
# CURL_BODY (or a few bytes that match no checksum) to the path given after `-o`.
CURL_STUB = r"""#!/bin/sh
printf '%s\n' "$@" > "${CURL_LOG}"
out=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then
    out="$2"
    shift
  fi
  shift
done
[ -n "${out}" ] || exit 22
if [ -n "${CURL_BODY}" ]; then
  cat "${CURL_BODY}" > "${out}"
else
  printf 'not the release this action pinned\n' > "${out}"
fi
"""

INPUT_EXPRESSION = re.compile(r"^\$\{\{\s*inputs\.([A-Za-z0-9_-]+)\s*\}\}$")
STEP_OUTPUT_EXPRESSION = re.compile(
    r"^\$\{\{\s*steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)\s*\}\}$"
)


def _action() -> dict:
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))


def _step(name: str) -> dict:
    """
    Return the composite step called `name`.

    Located by name rather than by position so a step inserted above it cannot silently move
    these tests onto different shell.
    """

    for step in _action()["runs"]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(
        f"prepare-main-akmods/action.yml has no step named {name!r}; this test executes that "
        "step's shell and cannot find it"
    )


def _step_body(name: str) -> str:
    body = _step(name).get("run")
    if not body:
        raise AssertionError(
            f"prepare-main-akmods/action.yml step {name!r} no longer has a `run:` body"
        )
    return body


def _step_env(name: str) -> dict[str, str]:
    """
    Resolve the step's own `env:` block the way GitHub resolves it.

    The environment the shell sees is built from the action's text, not from a copy of it, so a
    renamed environment variable or a newly wired input reaches the shell here exactly as it would
    on a runner. Only bare `${{ inputs.NAME }}`, bare `${{ steps.ID.outputs.NAME }}` and plain
    literals are accepted: this harness cannot honestly claim to resolve `github.*` or a composed
    expression, and passing one through as a literal would make the test assert the wrong thing.
    """

    resolved: dict[str, str] = {}
    for key, raw in (_step(name).get("env") or {}).items():
        value = str(raw).strip()

        match = INPUT_EXPRESSION.match(value)
        if match:
            input_name = match.group(1)
            if input_name not in INPUTS:
                raise AssertionError(
                    f"prepare-main-akmods/action.yml step {name!r} reads inputs.{input_name}, "
                    "which this test does not supply"
                )
            resolved[key] = INPUTS[input_name]
            continue

        match = STEP_OUTPUT_EXPRESSION.match(value)
        if match:
            step_id, output = match.group(1), match.group(2)
            if output not in STEP_OUTPUTS.get(step_id, {}):
                raise AssertionError(
                    f"prepare-main-akmods/action.yml step {name!r} reads "
                    f"steps.{step_id}.outputs.{output}, which this test does not supply"
                )
            resolved[key] = STEP_OUTPUTS[step_id][output]
            continue

        if "${{" in value:
            raise AssertionError(
                f"prepare-main-akmods/action.yml step {name!r} sets {key}={value!r}; this test "
                "only resolves bare ${{ inputs.NAME }} and ${{ steps.ID.outputs.NAME }} "
                "expressions and must be taught the new one before it can keep executing this step"
            )
        resolved[key] = value
    return resolved


class Invocation:
    """One recorded `python3` call: its argv and the environment the step exported for it."""

    def __init__(self, argv: list[str], env: dict[str, str]):
        self.argv = argv
        self.env = env

    @property
    def command(self) -> str:
        """The `ci_tools.cli` subcommand, or "" when the call was not shaped like one."""

        if self.argv[:2] == ["-m", "ci_tools.cli"] and len(self.argv) == 3:
            return self.argv[2]
        return ""


class Result:
    """
    What a step leaves behind: its exit status, its streams, every stubbed call, and the files it
    wrote.

    The working directory is a temporary one that is removed as soon as the step finishes, so its
    contents are read out here rather than left as a path for a test to open later.
    """

    def __init__(
        self,
        completed: subprocess.CompletedProcess,
        log: str,
        github_output: str,
        written: dict[str, str],
    ):
        self.returncode = completed.returncode
        self.stdout = completed.stdout
        self.stderr = completed.stderr
        self.calls = _parse_log(log)
        self.github_output = github_output
        # Relative path -> contents, for every file the step created in its working directory.
        self.written = written

    @property
    def only_call(self) -> Invocation:
        if len(self.calls) != 1:
            raise AssertionError(
                f"expected exactly one python3 call, recorded {len(self.calls)}"
            )
        return self.calls[0]


def _parse_log(log: str) -> list[Invocation]:
    """
    Split the stub log into one Invocation per recorded call.

    Each block starts with `CMD` and ends with a bare `ENDOFCALL`, so an environment value
    containing a newline cannot be mistaken for the start of the next call.
    """

    calls: list[Invocation] = []
    argv: list[str] = []
    env: dict[str, str] = {}
    for line in log.split("\n"):
        if line == "ENDOFCALL":
            calls.append(Invocation(argv, env))
            argv, env = [], {}
            continue
        if "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        if key == "ARG":
            argv.append(value)
        elif key == "ENV" and "=" in value:
            name, _, val = value.partition("=")
            env[name] = val
    return calls


def _run_step(
    name: str,
    *,
    exit_code: int = 0,
    curl_body: Path | None = None,
    env_overrides: dict[str, str] | None = None,
) -> Result:
    """
    Execute one step's `run:` body under bash with its resolved `env:`, a stubbed python3 and a
    stubbed curl.

    `env_overrides` replaces values from the step's own `env:` block after it is resolved; it is
    how a test hands the `just` install a checksum that matches the download it supplies.
    """

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bindir = root / "bin"
        bindir.mkdir()
        stub = bindir / "python3"
        stub.write_text(PYTHON_STUB, encoding="utf-8")
        stub.chmod(0o755)
        curl = bindir / "curl"
        curl.write_text(CURL_STUB, encoding="utf-8")
        curl.chmod(0o755)
        runner_temp = root / "runner-temp"
        runner_temp.mkdir()

        log = root / "log"
        log.touch()
        github_output = root / "github-output"
        github_output.write_text(f"{PRIOR_OUTPUT_LINE}\n", encoding="utf-8")

        env = {
            "PATH": f"{bindir}:{SAFE_PATH}",
            "HOME": str(root),
            "STUB_LOG": str(log),
            "STUB_STDOUT": BUILD_STDOUT,
            "STUB_STDERR": BUILD_STDERR,
            "STUB_EXIT": str(exit_code),
            "GITHUB_OUTPUT": str(github_output),
            "GITHUB_PATH": str(root / "github-path"),
            "RUNNER_TEMP": str(runner_temp),
            "CURL_LOG": str(root / "curl-log"),
            "CURL_BODY": str(curl_body) if curl_body else "",
        }
        env.update(_step_env(name))
        env.update(env_overrides or {})

        script = root / "step.sh"
        script.write_text(_step_body(name), encoding="utf-8")

        completed = subprocess.run(
            RUNNER_SHELL + [str(script)],
            env=env,
            cwd=tmp,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        # Everything the step wrote is read out before the directory disappears. `bin/` and the
        # harness files are ours, not the step's; GITHUB_PATH and the curl argv record stay in
        # `written` because the `just` tests read them.
        ours = {"log", "github-output", "step.sh"}
        written = {
            str(path.relative_to(root)): path.read_text(encoding="utf-8", errors="replace")
            for path in sorted(root.rglob("*"))
            if path.is_file()
            and bindir not in path.parents
            and str(path.relative_to(root)) not in ours
        }
        return Result(
            completed,
            log.read_text(encoding="utf-8"),
            github_output.read_text(encoding="utf-8"),
            written,
        )


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class BuildAndPublishStepTests(unittest.TestCase):
    """
    The one step of this action that is shell rather than a single helper call.

    Four lines decide whether a failed kernel-module build is reported as a failure, whether the
    compiler's own message survives to be classified, and whether the run claims to have
    republished a shared cache it did not.
    """

    def test_a_successful_build_appends_its_rebuilt_marker_to_the_job_output_file(self) -> None:
        """
        `rebuilt=true` is what tells the caller a freshly published cache still needs signing.

        The action's `rebuilt` output is read straight from this step rather than recomputed from
        the `if:` condition that guards it, so this line is the only thing that distinguishes
        "republished the shared cache" from "reused it". It must be appended: GitHub gives the
        whole job one output file, so truncating it would drop the earlier steps' outputs.
        """

        result = _run_step(BUILD_STEP)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.github_output,
            f"{PRIOR_OUTPUT_LINE}\nrebuilt=true\n",
        )

    def test_a_failed_build_fails_the_step_even_though_tee_succeeds(self) -> None:
        """
        The build's status has to survive the pipe into `tee`, which succeeds whatever the build
        did.

        Without pipefail the pipeline's status is `tee`'s, so a kernel module that failed to
        compile leaves the step green, the run goes on to publish and sign whatever the cache
        already held, and `rebuilt=true` is written for a rebuild that never happened. The step
        sets `set -o pipefail` itself and the runner's own `bash -eo pipefail` sets it again;
        this asserts the observable result the two are there to produce, so removing either one
        while the other still holds is not a failure -- removing both is.
        """

        result = _run_step(BUILD_STEP, exit_code=3)
        self.assertEqual(result.returncode, 3)

    def test_the_step_takes_the_runner_shell_that_supplies_that_protection(self) -> None:
        """
        `shell: bash` is what gets the body `-e` and `-o pipefail` from the runner.

        Any other value -- `sh`, or a `bash {0}` override with its own flags -- silently drops
        both, and every step of this action then continues past a failed command. The step's own
        `set -o pipefail` covers only the pipeline it precedes.
        """

        for name in STEP_COMMANDS:
            with self.subTest(step=name):
                self.assertEqual(_step(name).get("shell"), "bash")

    def test_a_failed_build_never_claims_it_rebuilt_the_cache(self) -> None:
        """
        The failure path must leave `rebuilt` unset so the action's default of 'false' applies.

        A `rebuilt=true` written after a failed build would send the signing job after a digest
        this run never published.
        """

        result = _run_step(BUILD_STEP, exit_code=3)
        self.assertNotIn("rebuilt=", result.github_output)

    def test_both_streams_of_the_build_reach_the_log_the_classifier_reads(self) -> None:
        """
        `2>&1` in front of the pipe is what makes failure classification possible.

        A kernel module build reports compile errors on stderr. Without the redirection the log
        holds only the progress chatter from stdout, and `classify-akmods-failure` -- which reads
        that file and nothing else -- would see no evidence, classify every failure as `unknown`,
        and file a sticky issue that says nothing about why the build broke.
        """

        result = _run_step(BUILD_STEP, exit_code=1)
        log = result.written.get(BUILD_LOG_PATH)
        self.assertIsNotNone(log, f"the step wrote no {BUILD_LOG_PATH}: {sorted(result.written)}")
        self.assertIn(BUILD_STDERR, log)
        self.assertIn(BUILD_STDOUT, log)

    def test_the_step_creates_its_own_log_directory(self) -> None:
        """
        `mkdir -p artifacts` runs before the pipeline, in a working directory that has no
        `artifacts/` yet -- the state of a fresh checkout, since nothing earlier in this action
        creates it. Without it `tee` cannot open the file, and pipefail then fails the step before
        the build has run at all.
        """

        result = _run_step(BUILD_STEP)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(BUILD_LOG_PATH, result.written)

    def test_the_build_log_path_is_the_one_the_classify_step_is_told_to_read(self) -> None:
        """
        Two steps must agree on a path that appears in each of them as a literal.

        The build tees to a path written into its shell; the classification step is handed a path
        in its `env:` block. Nothing but agreement between those two literals makes the payload
        describe the failure that just happened, and a job in which the build failed is exactly
        the job where nobody is checking.
        """

        classify_log = _step_env(CLASSIFY_STEP)["AKMODS_FAILURE_LOG"]
        result = _run_step(BUILD_STEP, exit_code=1)
        self.assertIn(
            classify_log,
            result.written,
            f"the build step wrote no {classify_log}, which is where the classify step reads",
        )

    def test_the_build_is_handed_the_exact_resolved_patch_version_not_the_minor_line(self) -> None:
        """
        The akmods build gets the patch version this run resolved, as `ZFS_VERSION`.

        The akmods fork's Justfile reads `ZFS_VERSION` as an override of its own pinned OpenZFS
        version, and its ZFS build script accepts only a full X.Y.Z. Under any other name the
        value is ignored and the fork builds its own pin, which lags a new OpenZFS patch, so the
        image would carry a zfs-version label that does not match the module it ships. The fork
        no longer reads `ZFS_MINOR_VERSION` at all, so passing the value only under that name
        is the failure this pins against. The line ("2.4") sits in a nearby output and would be
        refused by the fork, so it must not be the value either.
        """

        env = _run_step(BUILD_STEP).only_call.env
        self.assertEqual(env["ZFS_VERSION"], ZFS_PATCH_VERSION)
        self.assertNotEqual(env["ZFS_VERSION"], ZFS_MINOR_LINE)
        self.assertNotIn("ZFS_MINOR_VERSION", env)

    def test_the_build_is_given_the_credentials_and_target_the_shared_cache_push_needs(
        self,
    ) -> None:
        """
        The whole environment this step exports, pinned as a mapping.

        The cache image is a GHCR package published from this step, so a dropped credential turns
        into an authentication failure thousands of lines into a build log, and a changed
        `AKMODS_KERNEL` or `AKMODS_TARGET` silently builds and publishes the wrong thing under the
        tag production consumes.
        """

        call = _run_step(BUILD_STEP).only_call
        self.assertEqual(call.command, "akmods-build-and-publish")
        self.assertEqual(call.env["AKMODS_KERNEL"], "main")
        self.assertEqual(call.env["AKMODS_TARGET"], "zfs")
        self.assertEqual(call.env["AKMODS_VERSION"], RESOLVE_OUTPUTS["version"])
        self.assertEqual(call.env["KERNEL_RELEASE"], RESOLVE_OUTPUTS["kernel_release"])
        self.assertEqual(call.env["AKMODS_REPO"], INPUTS["akmods_repo"])
        self.assertEqual(call.env["REGISTRY_ACTOR"], INPUTS["registry_actor"])
        self.assertEqual(call.env["REGISTRY_TOKEN"], INPUTS["registry_token"])
        self.assertEqual(call.env["GITHUB_TOKEN"], INPUTS["registry_token"])
        self.assertEqual(call.env["CI"], "1")


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class PinnedJustInstallTests(unittest.TestCase):
    """
    The step that puts a pinned, checksum-verified `just` first on PATH for the akmods build.

    The akmods Justfile needs a newer `just` than the runner image ships, so this step downloads
    one. Its checksum line is the only thing between a download from the network and a binary that
    every later step in the job runs, and its condition is the only thing that makes the newer
    `just` present when the build step needs it.
    """

    def _release_tarball(self, directory: Path) -> Path:
        """A tarball shaped like the upstream release: a `just` at its root that reports a version."""

        program = directory / "just"
        program.write_text("#!/bin/sh\necho 'just from the test tarball'\n", encoding="utf-8")
        program.chmod(0o755)
        tarball = directory / "just.tar.gz"
        with tarfile.open(tarball, "w:gz") as archive:
            archive.add(program, arcname="just")
        return tarball

    def test_a_download_that_does_not_match_the_pin_never_reaches_path(self) -> None:
        """
        A tampered or truncated download fails the step before anything is extracted.

        Without the `sha256sum --check` line, or with the step carrying on past it, whatever the
        network returned would be unpacked and put first on PATH for the build that holds the
        registry token. The download here is a well-formed release tarball, so only the
        checksum stands between it and PATH.
        """

        with tempfile.TemporaryDirectory() as tmp:
            result = _run_step(JUST_STEP, curl_body=self._release_tarball(Path(tmp)))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("github-path", result.written)
        self.assertFalse(
            [path for path in result.written if path.startswith("runner-temp/just-bin/")],
            sorted(result.written),
        )

    def test_a_download_that_matches_the_pin_goes_first_on_path_for_the_later_steps(self) -> None:
        """
        The verified binary is unpacked into its own directory and that directory is handed to
        GITHUB_PATH, which is how a composite step changes PATH for the steps after it.

        Writing the directory anywhere else leaves the build step running the runner image's old `just`, which fails on the Justfile's
        first line.
        """

        with tempfile.TemporaryDirectory() as tmp:
            tarball = self._release_tarball(Path(tmp))
            digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
            result = _run_step(
                JUST_STEP, curl_body=tarball, env_overrides={"JUST_SHA256": digest}
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("runner-temp/just-bin/just", result.written)
        path_lines = result.written["github-path"].splitlines()
        self.assertEqual(len(path_lines), 1, path_lines)
        self.assertTrue(path_lines[0].endswith("/runner-temp/just-bin"), path_lines)
        # The step runs the binary it installed, so a broken unpack fails here, not in the build.
        self.assertIn("just from the test tarball", result.stdout)

    def test_the_download_is_the_pinned_release_for_this_runner(self) -> None:
        env = _step_env(JUST_STEP)
        version = env["JUST_VERSION"]
        result = _run_step(JUST_STEP)
        argv = result.written["curl-log"].splitlines()
        self.assertIn(
            f"https://github.com/casey/just/releases/download/{version}/"
            f"just-{version}-x86_64-unknown-linux-musl.tar.gz",
            argv,
        )

    def test_the_pin_is_a_full_digest_of_a_version_new_enough_for_the_justfile(self) -> None:
        """
        The Justfile starts with `set lists`, which `just` before 1.57 rejects.

        A Renovate bump that lowered the version, or a digest pasted short, would only show up on
        the next cache rebuild, which may be weeks away.
        """

        env = _step_env(JUST_STEP)
        self.assertRegex(env["JUST_SHA256"], r"^[0-9a-f]{64}$")
        version = tuple(int(part) for part in env["JUST_VERSION"].split("."))
        self.assertGreaterEqual(version, (1, 57))

    def test_just_is_installed_on_exactly_the_condition_the_build_runs(self) -> None:
        """
        A narrower condition lets the build run with the runner's old `just`; a different one
        downloads a binary on runs that never use it.
        """

        def condition(name: str) -> str:
            return " ".join(str(_step(name)["if"]).split())

        self.assertEqual(condition(JUST_STEP), condition(BUILD_STEP))


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class RestrictedRunRefusalTests(unittest.TestCase):
    """
    The gate that stops a run with no signing key from republishing shared production state.

    Branch runs cannot sign, so anything they published to the shared cache tag would be unsigned
    and every later run would correctly reject it -- after the fact, from a different job.
    """

    def test_the_refusal_fails_the_job(self) -> None:
        """
        Failing loudly is the documented choice. A gate that warned and continued would leave the
        run to fall through into the build step it exists to prevent.
        """

        result = _run_step(REFUSE_STEP)
        self.assertEqual(result.returncode, 1)

    def test_the_whole_explanation_goes_to_stderr(self) -> None:
        """
        All three lines are redirected, not just the first.

        GitHub surfaces a failed step's stderr in the job annotation; a line left on stdout is
        buried in the log. The recovery instruction is the line that matters most, and it is the
        last one -- the easiest to lose when the redirection is added line by line.
        """

        result = _run_step(REFUSE_STEP)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.strip().split("\n")), 3)

    def test_the_refusal_names_the_way_out(self) -> None:
        """
        The message has to say how to seed the cache, or the branch run is a dead end for whoever
        hits it: the fix is a dispatch of a *different* workflow with a non-default input.
        """

        stderr = _run_step(REFUSE_STEP).stderr
        self.assertIn("workflow_dispatch", stderr)
        self.assertIn("rebuild_akmods=true", stderr)

    def test_the_gate_fires_for_both_reasons_a_rebuild_would_happen(self) -> None:
        """
        The condition must cover the requested rebuild *and* the missing cache.

        Only the first is obvious. A restricted run against an empty or stale cache reaches the
        build step through the second half of this condition, and dropping it would let exactly
        the case the gate exists for -- a branch run finding no reusable cache -- publish unsigned
        shared state.
        """

        condition = " ".join(str(_step(REFUSE_STEP)["if"]).split())
        self.assertEqual(
            condition,
            "inputs.allow_cache_rebuild != 'true' && (inputs.rebuild_akmods == 'true' || "
            "steps.cache.outputs.exists != 'true')",
        )

    def test_the_build_step_runs_on_exactly_the_condition_the_gate_refuses(self) -> None:
        """
        The gate and the build it guards must be triggered by the same thing.

        The gate only protects anything while its condition is the rebuild condition with the
        permission check added. If the build step's condition ever broadens, a restricted run
        reaches the build without the gate firing first.
        """

        build_condition = " ".join(str(_step(BUILD_STEP)["if"]).split())
        self.assertEqual(
            build_condition,
            "inputs.rebuild_akmods == 'true' || steps.cache.outputs.exists != 'true'",
        )


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class StepWiringTests(unittest.TestCase):
    """
    What each step actually spawns, taken from running it rather than from reading it.

    The helpers behind these commands are covered module by module; what nothing covered is
    whether the step still calls the helper it is named for, with the environment that helper
    reads.
    """

    def test_every_shell_step_is_accounted_for(self) -> None:
        """
        A step added to this action without a decision about what it runs turns this red.

        The point of the check is the direction nobody notices: a new step whose shell no test
        executes looks exactly like a passing suite.
        """

        named = [
            step.get("name")
            for step in _action()["runs"]["steps"]
            if step.get("run")
        ]
        self.assertEqual(sorted(named), sorted(STEP_COMMANDS))

    def test_each_step_spawns_the_helper_it_is_named_for(self) -> None:
        """
        Every step runs exactly one `python3 -m ci_tools.cli <command>`, and that command exists.

        A renamed CLI command is caught by `ci_tools.cli` itself at runtime -- in the job, on the
        runner, after the checkout and the login. Comparing against the live `command_map()`
        catches it in the pull request that renames it.
        """

        known = set(command_map())
        for name, command in STEP_COMMANDS.items():
            with self.subTest(step=name):
                calls = _run_step(name).calls
                if command is None:
                    self.assertEqual(calls, [])
                    continue
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0].argv, ["-m", "ci_tools.cli", command])
                self.assertIn(command, known)

    def test_the_cache_probe_carries_credentials_and_the_exact_patch_version(self) -> None:
        """
        The first probe runs before any registry login, and it matches on the patch version.

        Both matter for the same reason: a probe that cannot authenticate, or that matches only
        the minor line, reports a reusable cache when there is none usable -- and matching the
        line alone is the specific bug the action's comment records, where a cache holding 2.4.3
        satisfied a run that resolved 2.4.4 and the new patch never reached the image.
        """

        env = _run_step(CACHE_STEP).only_call.env
        self.assertEqual(env["ZFS_VERSION"], ZFS_PATCH_VERSION)
        self.assertEqual(env["REGISTRY_ACTOR"], INPUTS["registry_actor"])
        self.assertEqual(env["REGISTRY_TOKEN"], INPUTS["registry_token"])

    def test_the_post_rebuild_verification_demands_a_match(self) -> None:
        """
        The same command runs twice with opposite meanings, separated by one variable.

        The first call asks whether a cache is reusable and is allowed to answer no. The second
        proves the cache this run just published holds the ZFS version it resolved, and must fail
        when it does not. Without `REQUIRE_MATCH`, the verification step degrades into a second
        probe that reports a mismatch and lets the run continue.
        """

        env = _run_step(VERIFY_STEP).only_call.env
        self.assertEqual(env["REQUIRE_MATCH"], "true")
        self.assertEqual(env["ZFS_VERSION"], ZFS_PATCH_VERSION)
        self.assertEqual(env["AKMODS_REPO"], INPUTS["akmods_repo"])

    def test_the_post_rebuild_verification_carries_the_registry_login(self) -> None:
        """The cache check requires the pair, so this step must be handed it explicitly."""

        env = _run_step(VERIFY_STEP).only_call.env
        self.assertEqual(env["REGISTRY_ACTOR"], INPUTS["registry_actor"])
        self.assertEqual(env["REGISTRY_TOKEN"], INPUTS["registry_token"])

    def test_the_post_rebuild_verification_checks_the_digest_the_run_consumes(self) -> None:
        """
        The verification is handed the pin step's digest, and runs after that step.

        The pin step's `akmods_image_pinned` is what the candidate build consumes and the signing
        job signs. If the verification resolved the shared tag itself, or were handed the earlier
        cache probe's pin, it could pass on one image while the run signs and builds another.
        """

        env = _run_step(VERIFY_STEP).only_call.env
        self.assertEqual(
            env["AKMODS_IMAGE_PINNED"], STEP_OUTPUTS["pin_akmods"]["akmods_image_pinned"]
        )
        self.assertNotEqual(
            env["AKMODS_IMAGE_PINNED"], STEP_OUTPUTS["cache"]["akmods_image_pinned"]
        )

        names = [step.get("name") for step in _action()["runs"]["steps"]]
        self.assertLess(
            names.index("Resolve shared akmods cache digest"), names.index(VERIFY_STEP)
        )
        # The action's outputs must publish that same pin on the rebuild path, or the digest
        # verified here and the digest the workflow builds from come from different steps.
        outputs = _action()["outputs"]
        for output in ("akmods_image_pinned", "akmods_image_digest"):
            with self.subTest(output=output):
                self.assertTrue(
                    outputs[output]["value"].startswith(
                        f"${{{{ steps.pin_akmods.outputs.{output}"
                    )
                )

    def test_the_pin_is_bound_to_the_image_the_build_step_recorded(self) -> None:
        """
        The pin step is handed the build step's recorded image ID, and runs after that step.

        The pin reads the shared tag back after the push. Only the ID recorded before the push
        lets it tell the image this run built from one a branch run swapped onto the tag in
        between; without it the pin step refuses, so a broken wiring fails the rebuild closed.
        """

        env = _run_step("Resolve shared akmods cache digest").only_call.env
        self.assertEqual(
            env["AKMODS_BUILT_IMAGE_ID"], STEP_OUTPUTS["akmods_build"]["built_image_id"]
        )
        steps = _action()["runs"]["steps"]
        ids = [step.get("id") for step in steps]
        self.assertLess(ids.index("akmods_build"), ids.index("pin_akmods"))
        # The pin only runs where the build ran. A reused cache never reaches it: its digest
        # comes from the cache probe, which verified that digest's signature instead.
        self.assertIn(
            f"({_step(BUILD_STEP)['if']})", _step("Resolve shared akmods cache digest")["if"]
        )

    def test_the_input_resolution_step_is_given_a_token_for_the_release_lookup(self) -> None:
        """
        `GITHUB_TOKEN` here is not about permissions, it is about rate limits.

        `ci_tools/zfs_release.py` queries api.github.com, hosted runners share IPs, and the
        unauthenticated limit is per IP -- so an unset token makes ZFS version resolution fail
        intermittently for reasons unrelated to this repository.
        """

        env = _run_step("Resolve build inputs for this run").only_call.env
        self.assertEqual(env["GITHUB_TOKEN"], INPUTS["registry_token"])


# The one input the build-inputs record names differently from the `resolve` step's output.
MANIFEST_ALIASES = {"fedora_version": "version"}

# What the runner itself exports to every step, which the record also reads.
RUNNER_ENV = {
    "GITHUB_REPOSITORY": "Danathar/zfs-aurora-complex",
    "GITHUB_WORKFLOW": "Build And Promote Main Image",
    "GITHUB_RUN_ID": "123456",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_RUN_NUMBER": "99",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_SHA": "f" * 40,
    "GITHUB_ACTOR": "danathar",
}


def _distinct_resolve_outputs() -> dict[str, str]:
    """
    RESOLVE_OUTPUTS with every value unique to its own name.

    In the shared fixture `version` and `base_image_tag` are both "42" and the two kernel fields
    are equal, as they often are on a real run, so a step that read one for the other would still
    produce the right-looking value. Here no two names can be confused.
    """

    distinct = {name: f"{name}-value" for name in RESOLVE_OUTPUTS}
    distinct["use_input_lock"] = "true"
    distinct["detected_kernel_releases"] = "detected-kernel-a detected-kernel-b"
    return distinct


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class ResolvedInputRecordTests(unittest.TestCase):
    """
    The resolved inputs travel by name through four hand-written lists: the outputs
    `write_resolved_build_outputs` writes, the `RESOLVE_OUTPUTS` fixture above, the `env:` block of
    the "Write build inputs manifest" step, and the `require_env` calls in
    ci_tools/write_build_inputs_manifest.py.

    Each list was checked only against itself. Wiring `BASE_IMAGE_PINNED` to
    `steps.resolve.outputs.base_image_ref` recorded the floating tag as the pinned base, and
    renaming an output in `write_resolved_build_outputs` left this file's fixture still supplying
    the old name; both kept every test green.
    """

    def test_the_fixture_supplies_exactly_what_the_resolve_step_writes(self) -> None:
        resolved = ResolvedBuildInputs(
            **{
                field.name: (
                    False
                    if field.type == "bool"
                    else ("a", "b")
                    if field.name == "detected_kernel_releases"
                    else f"{field.name}-value"
                )
                for field in dataclasses.fields(ResolvedBuildInputs)
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "github-output"
            output_path.touch()
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_path)}):
                write_resolved_build_outputs(resolved)
            text = output_path.read_text(encoding="utf-8")
        written = re.findall(r"^([A-Za-z0-9_]+)<<", text, re.MULTILINE)

        self.assertEqual(sorted(written), sorted(RESOLVE_OUTPUTS))

    def test_the_record_holds_each_resolved_input_under_its_own_name(self) -> None:
        distinct = _distinct_resolve_outputs()
        with mock.patch.dict(RESOLVE_OUTPUTS, distinct):
            step_env = _run_step("Write build inputs manifest").only_call.env

        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp) / "artifacts"
            with (
                mock.patch.object(manifest_writer, "ARTIFACT_DIR", artifact_dir),
                mock.patch.object(
                    manifest_writer, "ARTIFACT_PATH", artifact_dir / "build-inputs.json"
                ),
                mock.patch.dict(os.environ, {**RUNNER_ENV, **step_env}, clear=True),
                redirect_stdout(io.StringIO()),
            ):
                manifest_writer.main()
            document = json.loads((artifact_dir / "build-inputs.json").read_text(encoding="utf-8"))
        recorded = document["inputs"]

        self.assertEqual(
            sorted(MANIFEST_ALIASES.get(name, name) for name in recorded),
            sorted(RESOLVE_OUTPUTS),
            "the record and the resolve step disagree about which inputs exist",
        )
        for name, value in recorded.items():
            output = MANIFEST_ALIASES.get(name, name)
            expected: object = distinct[output]
            if output == "use_input_lock":
                expected = expected == "true"
            elif output == "detected_kernel_releases":
                expected = str(expected).split()
            with self.subTest(input=name):
                self.assertEqual(value, expected)


if __name__ == "__main__":
    unittest.main()
