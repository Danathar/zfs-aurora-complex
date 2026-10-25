"""
Script: tests/test_action_ci_context.py
What: Executes the `run:` bodies of the three shared setup composite actions --
.github/actions/load-ci-defaults, .github/actions/prepare-registry-context and
.github/actions/install-signing-tools -- and checks each action's declared `outputs:` block
against the names its step actually writes.
Doing: Extracts every step from the three action.yml files with PyYAML, runs the shell bodies
under bash with `GITHUB_OUTPUT`/`GITHUB_ENV` pointed at scratch files, and parses what the step
wrote. The apt step runs against a `sudo` stub that records what it was asked to install without
installing it.
Why: These three actions are the highest-fan-in CI surface in the repository -- 14 `uses:` sites
across build.yml, build-pr.yml, akmods-failure-triage.yml and
nightly-compliance.yml -- and no tier executes any of them. tests/test_export_repo_defaults.py and
tests/test_tagging_context.py call the Python entry points directly, so they never see the
action.yml wrapper; tests/e2e/ dispatches `ci_tools.cli` commands rather than action steps; and
tests/check_coverage.py excludes composite actions from its manifest by design.
Goal: Hold the seam between the Python helpers and the YAML that publishes their results. An
output renamed on either side of that seam -- in OUTPUT_NAME_MAP, in export_registry_context_values,
or in an action's `outputs:` block -- currently produces an empty string in every consuming
workflow expression and no error anywhere. It fails here instead.

The steps' text is executed rather than copied. A renamed or deleted step fails the extraction
loudly instead of leaving this file asserting nothing.

Nothing here installs a package. `sudo` is a stub that records and returns; PATH deliberately
omits the directories where a real `sudo` lives.

PyYAML is not a pytest dependency; CI installs it by name (see .github/workflows/test.yml).
The import is guarded so the suite still runs under `python3 -m unittest discover -s tests`
with nothing installed, matching tests/test_action_publish_native_image.py.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only where PyYAML is absent
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTIONS_DIR = REPO_ROOT / ".github" / "actions"
DEFAULTS_FILE = REPO_ROOT / "ci" / "defaults.json"

RUNNER_SHELL = ["bash", "--noprofile", "--norc", "-eo", "pipefail"]

# Enough PATH to reach python3 and coreutils, and nothing else. Stub directories are prepended
# per case, so a privileged call the test forgot to stub fails rather than running for real.
SAFE_PATH = "/usr/bin:/bin"

# A line already in GITHUB_OUTPUT before the step runs. GitHub reuses one file for the whole job,
# so a step that truncated it would silently drop an earlier step's outputs.
PRIOR_OUTPUT_LINE = "resolved_by=an-earlier-step"

# Records the privileged command and does not run it, so no package manager is invoked.
SUDO_STUB = r"""#!/bin/sh
{
  printf 'CMD\n'
  for arg in "$@"; do
    printf 'ARG\t%s\n' "${arg}"
  done
  printf 'ENDOFCALL\n'
} >> "${STUB_LOG}"
exit 0
"""

# The `value:` of a composite action output, as GitHub writes it: one bare step-output reference.
OUTPUT_VALUE_PATTERN = re.compile(r"^\$\{\{\s*steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)\s*\}\}$")


def _action(name: str) -> dict:
    path = ACTIONS_DIR / name / "action.yml"
    if not path.exists():
        raise AssertionError(f"missing composite action: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _shell_steps(name: str) -> list[dict]:
    steps = [step for step in _action(name)["runs"]["steps"] if step.get("run")]
    if not steps:
        raise AssertionError(f"{name}/action.yml no longer has any step with a `run:` body")
    return steps


def _only_shell_step(name: str) -> dict:
    steps = _shell_steps(name)
    if len(steps) != 1:
        raise AssertionError(
            f"{name}/action.yml now has {len(steps)} shell steps; this test resolves one"
        )
    return steps[0]


def _declared_outputs(name: str) -> dict[str, str]:
    """Return each declared output name mapped to its `value:` expression."""

    outputs = _action(name).get("outputs") or {}
    return {output: spec["value"] for output, spec in outputs.items()}


def _parse_github_file(text: str) -> dict[str, str]:
    """
    Parse the `GITHUB_OUTPUT`/`GITHUB_ENV` file format the way the runner does.

    Both `name=value` and the heredoc form the helpers emit are accepted, because which one a
    value takes is an implementation detail of ci_tools.common that this test does not pin.
    """

    values: dict[str, str] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line:
            continue
        if "<<" in line:
            key, delimiter = line.split("<<", 1)
            body: list[str] = []
            while index < len(lines) and lines[index] != delimiter:
                body.append(lines[index])
                index += 1
            if index >= len(lines):
                raise AssertionError(f"unterminated heredoc for {key!r} in step output")
            index += 1
            values[key] = "\n".join(body)
        elif "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
        else:
            raise AssertionError(f"unparseable step output line: {line!r}")
    return values


class _StepRun:
    """What one executed step left behind."""

    def __init__(self, completed: subprocess.CompletedProcess, outputs: str, env_file: str) -> None:
        self.completed = completed
        self.outputs = _parse_github_file(outputs)
        self.env = _parse_github_file(env_file)
        self.raw_outputs = outputs


def _run_step(body: str, *, env: dict[str, str], cwd: Path, path_prefix: Path | None = None) -> _StepRun:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        output_file = root / "github-output"
        env_file = root / "github-env"
        output_file.write_text(PRIOR_OUTPUT_LINE + "\n", encoding="utf-8")
        env_file.write_text("", encoding="utf-8")

        script = root / "step.sh"
        script.write_text(body, encoding="utf-8")

        search_path = SAFE_PATH if path_prefix is None else f"{path_prefix}:{SAFE_PATH}"
        step_env = {
            "PATH": search_path,
            "HOME": str(root),
            "GITHUB_OUTPUT": str(output_file),
            "GITHUB_ENV": str(env_file),
        }
        step_env.update(env)

        completed = subprocess.run(
            RUNNER_SHELL + [str(script)],
            env=step_env,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return _StepRun(
            completed,
            output_file.read_text(encoding="utf-8"),
            env_file.read_text(encoding="utf-8"),
        )


@unittest.skipIf(yaml is None, "PyYAML is required to read the action definitions")
class LoadCiDefaultsActionTest(unittest.TestCase):
    """
    .github/actions/load-ci-defaults publishes ci/defaults.json as step outputs.

    Every build workflow starts with this action and then reads
    `steps.defaults.outputs.image_name`, `.akmods_repo` and friends. A GitHub Actions expression
    that names an output the action does not declare, or an output the step never wrote,
    evaluates to the empty string: the build would go on and push to a path with a missing
    segment rather than fail.
    """

    def setUp(self) -> None:
        self.action = "load-ci-defaults"
        with DEFAULTS_FILE.open("r", encoding="utf-8") as handle:
            self.defaults = json.load(handle)

    def _run(self) -> _StepRun:
        return _run_step(_only_shell_step(self.action)["run"], env={}, cwd=REPO_ROOT)

    def test_declared_outputs_name_the_step_that_produces_them(self) -> None:
        step_ids = {step.get("id") for step in _shell_steps(self.action)}
        for output, value in _declared_outputs(self.action).items():
            with self.subTest(output=output):
                match = OUTPUT_VALUE_PATTERN.match(value.strip())
                self.assertIsNotNone(
                    match, f"output {output!r} is not a bare step-output reference: {value!r}"
                )
                self.assertIn(
                    match.group(1),
                    step_ids,
                    f"output {output!r} reads from step {match.group(1)!r}, which does not exist",
                )
                self.assertEqual(
                    match.group(2),
                    output,
                    f"output {output!r} publishes the step output {match.group(2)!r}",
                )

    def test_step_writes_exactly_the_declared_outputs(self) -> None:
        run = self._run()
        self.assertEqual(run.completed.returncode, 0, run.completed.stderr)

        produced = set(run.outputs) - {"resolved_by"}
        self.assertEqual(
            produced,
            set(_declared_outputs(self.action)),
            "the names ci_tools writes and the names action.yml declares have drifted apart",
        )

    def test_step_publishes_the_checked_in_default_values(self) -> None:
        run = self._run()
        self.assertEqual(run.completed.returncode, 0, run.completed.stderr)

        self.assertEqual(run.outputs["image_name"], self.defaults["IMAGE_NAME"])
        self.assertEqual(run.outputs["akmods_repo"], self.defaults["AKMODS_REPO"])
        self.assertEqual(run.outputs["default_base_image"], self.defaults["DEFAULT_BASE_IMAGE"])
        self.assertEqual(
            run.outputs["default_build_container_image"],
            self.defaults["DEFAULT_BUILD_CONTAINER_IMAGE"],
        )
        self.assertEqual(
            run.outputs["default_zfs_minor_version"], self.defaults["DEFAULT_ZFS_MINOR_VERSION"]
        )
        self.assertEqual(
            run.outputs["akmods_upstream_repo"], self.defaults["AKMODS_UPSTREAM_REPO"]
        )

    def test_optional_pin_is_published_as_an_empty_output_rather_than_omitted(self) -> None:
        """
        `akmods_upstream_ref` is the explicit fork pin and is empty while the fork floats.

        The workflows branch on that emptiness, so the output has to exist and be empty -- not be
        skipped, which would make the action's declared output unresolvable.
        """

        run = self._run()
        self.assertEqual(run.completed.returncode, 0, run.completed.stderr)
        self.assertIn("akmods_upstream_ref", run.outputs)
        self.assertEqual(run.outputs["akmods_upstream_ref"], self.defaults["AKMODS_UPSTREAM_REF"])
        self.assertEqual(run.outputs["akmods_upstream_track"], self.defaults["AKMODS_UPSTREAM_TRACK"])

    def test_step_appends_to_the_shared_output_file(self) -> None:
        run = self._run()
        self.assertEqual(run.completed.returncode, 0, run.completed.stderr)
        self.assertEqual(
            run.outputs.get("resolved_by"),
            "an-earlier-step",
            "the step truncated GITHUB_OUTPUT and dropped an earlier step's output",
        )

    def test_step_also_exports_the_env_names_workflow_steps_read(self) -> None:
        """
        The same helper writes GITHUB_ENV under the original upper-case names.

        Steps that are not action outputs -- the akmods jobs read `AKMODS_REPO` from the
        environment -- depend on this half of the contract.
        """

        run = self._run()
        self.assertEqual(run.completed.returncode, 0, run.completed.stderr)
        for name in ("IMAGE_NAME", "AKMODS_REPO", "AKMODS_UPSTREAM_REPO", "DEFAULT_BASE_IMAGE"):
            with self.subTest(name=name):
                self.assertEqual(run.env.get(name), self.defaults[name])

    def test_missing_required_default_fails_the_step_before_writing_anything(self) -> None:
        """
        A defaults file that lost a required key must stop the job.

        Publishing an empty `image_name` instead would let the build push to `ghcr.io/<org>/`
        and only fail much later, or not at all.
        """

        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / "checkout"
            (checkout / "ci").mkdir(parents=True)
            for package in ("ci_tools", "shared"):
                shutil.copytree(REPO_ROOT / package, checkout / package)
            damaged = dict(self.defaults)
            del damaged["IMAGE_NAME"]
            (checkout / "ci" / "defaults.json").write_text(
                json.dumps(damaged), encoding="utf-8"
            )

            run = _run_step(_only_shell_step(self.action)["run"], env={}, cwd=checkout)

        self.assertNotEqual(
            run.completed.returncode,
            0,
            "a defaults file missing IMAGE_NAME was accepted",
        )
        self.assertIn("IMAGE_NAME", run.completed.stderr)
        self.assertEqual(
            set(run.outputs),
            {"resolved_by"},
            "the step wrote outputs even though validation failed",
        )


@unittest.skipIf(yaml is None, "PyYAML is required to read the action definitions")
class PrepareRegistryContextActionTest(unittest.TestCase):
    """
    .github/actions/prepare-registry-context normalizes the GHCR path.

    Both results are consumed as strings inside workflow expressions -- `image_registry` is
    concatenated into an image reference.
    """

    def setUp(self) -> None:
        self.action = "prepare-registry-context"

    def _run(self, *, owner: str) -> _StepRun:
        return _run_step(
            _only_shell_step(self.action)["run"],
            env={"GITHUB_REPOSITORY_OWNER": owner},
            cwd=REPO_ROOT,
        )

    def test_declared_outputs_name_the_step_that_produces_them(self) -> None:
        step_ids = {step.get("id") for step in _shell_steps(self.action)}
        for output, value in _declared_outputs(self.action).items():
            with self.subTest(output=output):
                match = OUTPUT_VALUE_PATTERN.match(value.strip())
                self.assertIsNotNone(
                    match, f"output {output!r} is not a bare step-output reference: {value!r}"
                )
                self.assertIn(match.group(1), step_ids)
                self.assertEqual(match.group(2), output)

    def test_step_writes_exactly_the_declared_outputs(self) -> None:
        run = self._run(owner="Danathar")
        self.assertEqual(run.completed.returncode, 0, run.completed.stderr)
        self.assertEqual(set(run.outputs) - {"resolved_by"}, set(_declared_outputs(self.action)))

    def test_mixed_case_owner_becomes_a_lowercase_ghcr_path(self) -> None:
        """GHCR rejects an upper-case path segment, and GitHub owner names preserve case."""

        run = self._run(owner="DanAthar")
        self.assertEqual(run.completed.returncode, 0, run.completed.stderr)
        self.assertEqual(run.outputs["image_org"], "danathar")
        self.assertEqual(run.outputs["image_registry"], "ghcr.io/danathar")

    def test_step_also_exports_the_upper_case_env_names(self) -> None:
        run = self._run(owner="DanAthar")
        self.assertEqual(run.completed.returncode, 0, run.completed.stderr)
        self.assertEqual(run.env.get("IMAGE_ORG"), "danathar")
        self.assertEqual(run.env.get("IMAGE_REGISTRY"), "ghcr.io/danathar")

    def test_missing_owner_fails_the_step(self) -> None:
        """`GITHUB_REPOSITORY_OWNER` is always set on a runner, so an absent value is an error."""

        run = _run_step(_only_shell_step(self.action)["run"], env={}, cwd=REPO_ROOT)
        self.assertNotEqual(
            run.completed.returncode, 0, "the step accepted a missing GITHUB_REPOSITORY_OWNER"
        )
        self.assertEqual(set(run.outputs), {"resolved_by"})

    def test_step_appends_to_the_shared_output_file(self) -> None:
        run = self._run(owner="Danathar")
        self.assertEqual(run.completed.returncode, 0, run.completed.stderr)
        self.assertEqual(run.outputs.get("resolved_by"), "an-earlier-step")


@unittest.skipIf(yaml is None, "PyYAML is required to read the action definitions")
class InstallSigningToolsActionTest(unittest.TestCase):
    """
    .github/actions/install-signing-tools provisions skopeo and cosign for the publish jobs.

    The apt half is a shell step nothing executes; the cosign half is a pinned third-party
    action whose pin is the repository's supply-chain boundary for the tool that signs every
    published image.
    """

    def setUp(self) -> None:
        self.action = "install-signing-tools"

    def test_apt_step_refreshes_the_index_before_installing_skopeo(self) -> None:
        """
        `apt-get install` without a preceding `update` fails on a stale runner index.

        The two calls are recorded in order, so dropping or reordering them fails here rather
        than as a 404 on a package file inside a publish job.
        """

        step = _only_shell_step(self.action)
        with tempfile.TemporaryDirectory() as tmp:
            stubs = Path(tmp) / "bin"
            stubs.mkdir()
            log = Path(tmp) / "sudo.log"
            sudo = stubs / "sudo"
            sudo.write_text(SUDO_STUB, encoding="utf-8")
            sudo.chmod(0o755)

            run = _run_step(
                step["run"],
                env={"STUB_LOG": str(log)},
                cwd=REPO_ROOT,
                path_prefix=stubs,
            )
            recorded = log.read_text(encoding="utf-8") if log.exists() else ""

        self.assertEqual(run.completed.returncode, 0, run.completed.stderr)

        calls = [
            [line.split("\t", 1)[1] for line in block.splitlines() if line.startswith("ARG\t")]
            for block in recorded.split("ENDOFCALL\n")
            if "ARG\t" in block
        ]
        self.assertEqual(
            calls,
            [["apt-get", "update"], ["apt-get", "install", "-y", "skopeo"]],
            f"unexpected privileged calls: {recorded!r}",
        )

    def test_apt_step_is_the_only_shell_in_the_action(self) -> None:
        """A second shell step would need its own stubs; this asserts the harness stays honest."""

        self.assertEqual(len(_shell_steps(self.action)), 1)

    def test_cosign_installer_is_pinned_to_a_commit_and_a_release(self) -> None:
        """
        cosign produces the signatures consumers verify, so a floating installer is a supply
        -chain hole: `@v4` or a `cosign-release` of `main` would let an upstream change swap the
        signing binary without a reviewable diff here.
        """

        uses = [step["uses"] for step in _action(self.action)["runs"]["steps"] if step.get("uses")]
        cosign = [ref for ref in uses if ref.startswith("sigstore/cosign-installer@")]
        self.assertEqual(len(cosign), 1, f"expected one cosign-installer step, got {uses!r}")

        _, _, ref = cosign[0].partition("@")
        self.assertRegex(ref, r"^[0-9a-f]{40}$", "cosign-installer is not pinned to a full commit")

        with_block = next(
            step["with"]
            for step in _action(self.action)["runs"]["steps"]
            if str(step.get("uses", "")).startswith("sigstore/cosign-installer@")
        )
        self.assertRegex(
            with_block["cosign-release"],
            r"^v\d+\.\d+\.\d+$",
            "cosign-release must name an exact version",
        )


if __name__ == "__main__":  # pragma: no cover - convenience for direct execution
    unittest.main()
