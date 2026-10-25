"""
Script: tests/test_action_build_native_image.py
What: Executes the two shell steps of .github/actions/build-native-image/action.yml -- the
akmods-cache announcement and the `buildah build` invocation that produces every image this
repository ships.
Doing: Extracts each step's `run:` body and `env:` block with PyYAML, resolves the block's
`${{ inputs.* }}` expressions the way GitHub resolves them (refusing any expression this
harness does not model), and runs the body under bash against a `buildah` stub that records
its argv one argument per line.
Why: This action is the single place where the base image, the akmods cache, the in-image
signing policy and the six provenance labels are turned into build flags, and no tier executes
it. tests/test_workflow_build_container.py text-matches workflow
YAML, tests/e2e/ dispatches `ci_tools.cli` commands rather than workflow steps, and
tests/check_coverage.py excludes composite actions from its manifest by design. A build that
lost `--tls-verify=true`, dropped `--format docker` (which host `bootc upgrade` depends on),
built a different Containerfile, or silently stopped labelling the akmods cache digest it
consumed would ship a wrong or unprovenanced image with every existing test green.
Goal: Make a changed build contract fail here rather than on a booted machine, where the
symptom is an image that will not upgrade or whose provenance labels cannot be trusted.

The steps' text is executed rather than copied. A renamed or deleted step, or an input this
harness does not supply, fails loudly instead of leaving this file asserting nothing.

Nothing here builds a container. `buildah` is a stub and PATH deliberately omits the
directories where the real tool lives, so a step that stopped being stubbed would fail rather
than pull from ghcr.io.

PyYAML is not a pytest dependency; CI installs it by name (see .github/workflows/test.yml).
The import is guarded so the suite still runs under `python3 -m unittest discover -s tests`
with nothing installed, matching tests/test_action_publish_native_image.py.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only where PyYAML is absent
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTION_PATH = REPO_ROOT / ".github" / "actions" / "build-native-image" / "action.yml"

ANNOUNCE_STEP = "Show pinned akmods cache input"
BUILD_STEP = "Build image with buildah"

# PATH must reach the stub and must NOT reach a real buildah, which lives in /usr/bin on a
# runner. Only the stub directory plus a shell is offered, so an unstubbed call fails with
# "command not found" instead of contacting a registry.
SAFE_PATH = "/usr/bin:/bin"

# One realistic set of `with:` values, matching what .github/workflows/build.yml passes: the
# base and akmods refs arrive already digest-pinned by the preceding jobs.
IMAGE_NAME = "zfs-aurora-complex"
IMAGE_TAG = "candidate-34266369977"
BASE_IMAGE = "ghcr.io/ublue-os/aurora-dx@sha256:" + "1" * 64
AKMODS_IMAGE = "ghcr.io/danathar/akmods-zfs@sha256:" + "2" * 64
STABLE_SIGNAL_IMAGE = "ghcr.io/ublue-os/aurora-dx:stable"
STABLE_SIGNAL_DIGEST = "sha256:" + "4" * 64
AKMODS_UPSTREAM_REF = "5" * 40
ZFS_VERSION = "2.3.4"
IMAGE_REPO = f"ghcr.io/danathar/{IMAGE_NAME}"
SIGNING_KEY_FILENAME = f"{IMAGE_NAME}.pub"

FULL_INPUTS = {
    "image_name": IMAGE_NAME,
    "image_tag": IMAGE_TAG,
    "base_image": BASE_IMAGE,
    "akmods_image": AKMODS_IMAGE,
    "akmods_upstream_ref": AKMODS_UPSTREAM_REF,
    "stable_signal_image": STABLE_SIGNAL_IMAGE,
    "stable_signal_digest": STABLE_SIGNAL_DIGEST,
    "zfs_version": ZFS_VERSION,
    "image_repo": IMAGE_REPO,
    "signing_key_filename": SIGNING_KEY_FILENAME,
}

# Records argv one argument per line so an empty-valued flag is visible as an empty element
# rather than vanishing into a joined string, then exits with $STUB_EXIT so a build failure
# can be modelled.
STUB = r"""#!/bin/sh
for arg in "$@"; do
  printf '%s\n' "${arg}" >> "${STUB_ARGV}"
done
exit "${STUB_EXIT:-0}"
"""

INPUT_EXPRESSION = re.compile(r"^\$\{\{\s*inputs\.([A-Za-z0-9_]+)\s*\}\}$")


def _action() -> dict:
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))


def _step(name: str) -> dict:
    """
    Return the composite step called `name`.

    Located by name rather than by position: a step inserted above it must not silently move
    these tests onto different shell.
    """

    for step in _action()["runs"]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(
        f"build-native-image/action.yml has no step named {name!r}; this test executes that "
        "step's shell and cannot find it"
    )


def _step_body(name: str) -> str:
    body = _step(name).get("run")
    if not body:
        raise AssertionError(
            f"build-native-image/action.yml step {name!r} no longer has a `run:` body"
        )
    return body


def _step_env(name: str, inputs: dict[str, str]) -> dict[str, str]:
    """
    Resolve the step's own `env:` block against `inputs`.

    The environment the shell sees is built from the action's text, not from a copy of it, so
    a renamed environment variable or a newly wired input reaches the shell here exactly as it
    would on a runner. Any expression other than a bare `${{ inputs.NAME }}` is rejected: this
    harness cannot honestly claim to resolve `github.*`, `env.*` or a composed expression, and
    silently passing one through as a literal would make the test assert the wrong thing.
    """

    resolved: dict[str, str] = {}
    for key, raw in (_step(name).get("env") or {}).items():
        value = str(raw)
        match = INPUT_EXPRESSION.match(value.strip())
        if not match:
            raise AssertionError(
                f"build-native-image/action.yml step {name!r} sets {key}={value!r}; this test "
                "only resolves bare ${{ inputs.NAME }} expressions and must be taught the new "
                "one before it can keep executing this step"
            )
        input_name = match.group(1)
        if input_name not in inputs:
            raise AssertionError(
                f"build-native-image/action.yml step {name!r} reads inputs.{input_name}, which "
                "this test does not supply"
            )
        resolved[key] = inputs[input_name]
    return resolved


class Result:
    """What a step leaves behind: its exit status, its output, and buildah's exact argv."""

    def __init__(self, completed: subprocess.CompletedProcess, argv: list[str]):
        self.returncode = completed.returncode
        self.stdout = completed.stdout
        self.stderr = completed.stderr
        self.argv = argv

    def value_after(self, flag: str) -> list[str]:
        """Every value passed immediately after `flag`, in order."""

        return [self.argv[index + 1] for index, arg in enumerate(self.argv[:-1]) if arg == flag]


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class BuildActionStepTests(unittest.TestCase):
    """
    The image build, run as shell.

    Every case executes the action's own text with its `env:` block resolved the way GitHub
    resolves it.
    """

    def _run(
        self,
        step_name: str,
        *,
        inputs: dict[str, str] | None = None,
        stub_exit: int = 0,
    ) -> Result:
        supplied = dict(FULL_INPUTS)
        supplied.update(inputs or {})

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bindir = root / "bin"
            bindir.mkdir()
            stub = bindir / "buildah"
            stub.write_text(STUB, encoding="utf-8")
            stub.chmod(0o755)

            argv_log = root / "argv"
            argv_log.touch()
            # The build runs in the repository checkout root on a runner, where ./Containerfile
            # exists; the stub never reads it, but the step's `-f ./Containerfile .` should be
            # exercised from a directory shaped like the real one.
            (root / "Containerfile").write_text("FROM scratch\n", encoding="utf-8")

            env = {
                "PATH": f"{bindir}:{SAFE_PATH}",
                "STUB_ARGV": str(argv_log),
                "STUB_EXIT": str(stub_exit),
            }
            env.update(_step_env(step_name, supplied))

            completed = subprocess.run(
                ["bash", "-c", _step_body(step_name)],
                env=env,
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            recorded = argv_log.read_text(encoding="utf-8").split("\n")
            # A trailing newline from the last recorded argument, not an empty argument.
            if recorded and recorded[-1] == "":
                recorded.pop()
            return Result(completed, recorded)

    # -- the build contract ------------------------------------------------

    def test_buildah_receives_the_whole_documented_build_contract(self) -> None:
        """
        The full argv, pinned as one list.

        Each element is load-bearing and none is checked anywhere else in the suite:
        `--tls-verify=true` is what stops the digest-pinned base and akmods refs from being
        fetched over an unverified connection; `--format docker` is the Docker v2s2 manifest
        that the action's own comment records as required for `bootc upgrade` on booted hosts;
        `-f ./Containerfile` with a `.` context is what makes the build use this repository's
        recipe and tree. Asserting the list rather than a handful of substrings means a flag
        that is dropped, reordered onto the wrong value, or quietly added shows up here.
        """

        result = self._run(BUILD_STEP)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.argv,
            [
                "build",
                "--tls-verify=true",
                "--format",
                "docker",
                "-f",
                "./Containerfile",
                "--build-arg",
                f"BASE_IMAGE={BASE_IMAGE}",
                "--build-arg",
                f"AKMODS_IMAGE={AKMODS_IMAGE}",
                "--build-arg",
                f"IMAGE_REPO={IMAGE_REPO}",
                "--build-arg",
                f"SIGNING_KEY_FILENAME={SIGNING_KEY_FILENAME}",
                "--label",
                f"org.zfs-aurora-complex.akmods-ref={AKMODS_UPSTREAM_REF}",
                "--label",
                f"org.zfs-aurora-complex.akmods-image={AKMODS_IMAGE}",
                "--label",
                f"org.zfs-aurora-complex.base-image={BASE_IMAGE}",
                "--label",
                f"org.zfs-aurora-complex.stable-signal-image={STABLE_SIGNAL_IMAGE}",
                "--label",
                f"org.zfs-aurora-complex.stable-signal-digest={STABLE_SIGNAL_DIGEST}",
                "--label",
                f"org.zfs-aurora-complex.zfs-version={ZFS_VERSION}",
                "-t",
                f"{IMAGE_NAME}:{IMAGE_TAG}",
                ".",
            ],
        )

    def test_the_akmods_label_names_the_cache_the_build_actually_consumed(self) -> None:
        """
        `akmods-image` label and `AKMODS_IMAGE` build argument must be the same reference.

        The action's own comment designates this label the reliable provenance record --
        `akmods-ref` may name a commit that never touched the shipped modules on a cache-reuse
        build -- but it is only reliable while it reports the digest this build consumed. Two
        independent occurrences of the same shell variable can drift apart in a single edit,
        and nothing else in the suite compares them.
        """

        result = self._run(BUILD_STEP)
        consumed = [
            value.split("=", 1)[1]
            for value in result.value_after("--build-arg")
            if value.startswith("AKMODS_IMAGE=")
        ]
        labelled = [
            value.split("=", 1)[1]
            for value in result.value_after("--label")
            if value.startswith("org.zfs-aurora-complex.akmods-image=")
        ]
        self.assertEqual(consumed, [AKMODS_IMAGE])
        self.assertEqual(labelled, consumed)

    def test_unset_provenance_inputs_still_emit_their_labels(self) -> None:
        """
        A caller that omits the optional inputs must still produce the same label set.

        .github/workflows/build-pr.yml passes neither `stable_signal_image` nor
        `stable_signal_digest`, so the action's `default: ""` is a real path, not a
        contrived one. Every label must still be emitted, with an empty value, so that the
        label set an image carries does not depend on which workflow built it: a consumer
        reading provenance off a pull request build should find the key and see it is empty, not have
        to distinguish "unset" from "this build never labelled it". Dropping a label whose
        value is often empty is the cheap-looking edit this guards, and the positional `-t` and
        context arguments are re-checked here because a label that vanishes takes the argument
        positions after it with it.
        """

        result = self._run(
            BUILD_STEP,
            inputs={
                "akmods_upstream_ref": "",
                "stable_signal_image": "",
                "stable_signal_digest": "",
                "zfs_version": "",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.value_after("--label"),
            [
                "org.zfs-aurora-complex.akmods-ref=",
                f"org.zfs-aurora-complex.akmods-image={AKMODS_IMAGE}",
                f"org.zfs-aurora-complex.base-image={BASE_IMAGE}",
                "org.zfs-aurora-complex.stable-signal-image=",
                "org.zfs-aurora-complex.stable-signal-digest=",
                "org.zfs-aurora-complex.zfs-version=",
            ],
        )
        # The positional arguments buildah reads by position, still where they belong.
        self.assertEqual(result.value_after("-t"), [f"{IMAGE_NAME}:{IMAGE_TAG}"])
        self.assertEqual(result.argv[-1], ".")

    def test_a_failed_build_fails_the_step(self) -> None:
        """
        `set -euo pipefail` in front of the only command in the body.

        buildah exiting non-zero has to stop the job here; a workflow that carried on would
        push, sign and promote whatever image happened to be in local storage from an earlier
        step or an earlier build of the same tag.
        """

        result = self._run(BUILD_STEP, stub_exit=1)
        self.assertNotEqual(result.returncode, 0)

    # -- input handling ----------------------------------------------------

    def test_no_step_interpolates_an_expression_into_its_shell(self) -> None:
        """
        Every `${{ }}` in this action stays in an `env:` block, never in a `run:` body.

        `image_tag` arrives as a workflow input, so it is text this action does not control. Expanded by the runner into the
        script text it would be shell source; read from the environment it is data. This is the
        static half of the guarantee, checked for every step so a step added later cannot
        reintroduce the pattern.
        """

        for step in _action()["runs"]["steps"]:
            body = step.get("run")
            if not body:
                continue
            self.assertNotIn(
                "${{",
                body,
                f"step {step.get('name')!r} interpolates a workflow expression directly into "
                "its shell; pass the value through the step's env: block instead",
            )

    def test_a_shell_metacharacter_in_a_tag_stays_one_literal_argument(self) -> None:
        """
        The dynamic half: a hostile-looking tag reaches buildah unexecuted and unsplit.

        Git allows `;`, `$` and `(` in a branch name, so a tag built from one can carry them.
        The value must arrive as a single argv element with its characters intact.
        """

        hostile = "branch;$(touch pwned)-tag"
        result = self._run(BUILD_STEP, inputs={"image_tag": hostile})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.value_after("-t"), [f"{IMAGE_NAME}:{hostile}"])
        # The command substitution reached buildah as text. Had the shell run it, the tag
        # would have been split at the `;` into a truncated `-t` value and a second command.
        self.assertNotIn("pwned", result.stdout + result.stderr)

    # -- the announcement step ---------------------------------------------

    def test_the_announcement_names_the_pinned_cache_image(self) -> None:
        """
        The log line a maintainer reads to confirm which cache a build consumed.

        It is the only place the consumed akmods reference appears in the job log before the
        build starts, and it is what a bad-modules investigation greps for; an announcement
        that printed a different input, or an empty one, would send that investigation to the
        wrong cache.
        """

        result = self._run(ANNOUNCE_STEP)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(AKMODS_IMAGE, result.stdout)


if __name__ == "__main__":
    unittest.main()
