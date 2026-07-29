"""
Script: tests/test_workflow_build_container.py
What: Guards how the privileged akmods build container is selected, that branch runs
stay read-only against shared production state, and that only branches may publish
unsigned (and only explicitly).
Doing: Reads the workflow files as text and asserts no run-time override exists and that
every hardcoded container literal matches the checked-in default.
Why: That container runs --privileged, as root, with `/` bind-mounted and a package-write
token, so whoever chooses it controls what the akmods cache contains -- and the final image
installs whatever RPMs that cache provides.
Goal: Make a re-introduced override, or a digest that silently drifts from ci/defaults.json,
fail here rather than in production.

Deliberately parses with plain text matching rather than PyYAML: the CI test job installs
only pytest and ruff (see .github/workflows/test.yml), and these assertions do not need a
real YAML parse.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

# The two workflows whose jobs run the privileged akmods build container.
PRIVILEGED_CONTAINER_WORKFLOWS = ("build.yml", "build-branch.yml")

# Captures the rest of the line, not just non-whitespace: a GitHub expression
# like `${{ inputs.x || 'y' }}` contains spaces, and a pattern anchored on \S+
# would simply fail to match it -- silently passing the very case this is meant
# to catch.
CONTAINER_IMAGE_RE = re.compile(r"^\s*image:\s*(?P<image>.+?)\s*$", re.MULTILINE)


def _default_build_container() -> str:
    defaults = json.loads((REPO_ROOT / "ci" / "defaults.json").read_text(encoding="utf-8"))
    return defaults["DEFAULT_BUILD_CONTAINER_IMAGE"]


class BuildContainerSelectionTests(unittest.TestCase):
    def test_no_workflow_accepts_a_build_container_override(self) -> None:
        # A free-text workflow_dispatch input naming this image let anyone who
        # could dispatch the workflow run arbitrary code --privileged with `/`
        # mounted, then publish a cache that later trusted jobs sign, build
        # from, and promote. It cannot be validated inside the job either:
        # `container:` starts before any step runs, so a guard would execute
        # inside the container it was meant to gate. The only safe form is no
        # override at all.
        for name in sorted(p.name for p in WORKFLOW_DIR.glob("*.yml")):
            text = (WORKFLOW_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn(
                "build_container_image:",
                text,
                f"{name} declares a build-container override input; "
                "the build container must only be changeable by editing "
                "ci/defaults.json and the workflow literals in a reviewed PR.",
            )

    def test_privileged_container_image_is_never_expression_driven(self) -> None:
        for name in PRIVILEGED_CONTAINER_WORKFLOWS:
            text = (WORKFLOW_DIR / name).read_text(encoding="utf-8")
            for match in CONTAINER_IMAGE_RE.finditer(text):
                image = match.group("image")
                self.assertNotIn(
                    "${{",
                    image,
                    f"{name} selects a container image from an expression ({image}); "
                    "the privileged akmods container must be a fixed literal.",
                )

    def test_container_literals_match_the_checked_in_default(self) -> None:
        # A job's `container:` block cannot read step outputs, so it cannot read
        # ci/defaults.json and the literal is kept in sync by hand. Nothing
        # enforced that until this test: a drifted literal would mean the job
        # runs one image while the build-inputs manifest records another.
        expected = _default_build_container()
        self.assertIn("@sha256:", expected, "the default build container must be digest-pinned")

        for name in PRIVILEGED_CONTAINER_WORKFLOWS:
            text = (WORKFLOW_DIR / name).read_text(encoding="utf-8")
            devcontainer_images = [
                match.group("image")
                for match in CONTAINER_IMAGE_RE.finditer(text)
                if "devcontainer" in match.group("image")
            ]
            self.assertTrue(
                devcontainer_images,
                f"{name} no longer names the akmods build container; update this test "
                "if that job legitimately moved or was removed.",
            )
            for image in devcontainer_images:
                self.assertEqual(
                    image.strip("'\""),
                    expected,
                    f"{name} runs {image}, but ci/defaults.json says {expected}. "
                    "These must stay identical.",
                )


class BranchIsolationTests(unittest.TestCase):
    """
    Branch runs must be read-only against shared production state.

    The production signing key is scoped to a main-only environment, so branch
    refs cannot sign. Everything here pins the consequences: the branch
    workflow never references the secret at all, never mutates the shared
    akmods cache, and publishes only explicitly-unsigned test images. build.yml
    must never take the unsigned path.
    """

    def _branch_workflow(self) -> str:
        return (WORKFLOW_DIR / "build-branch.yml").read_text(encoding="utf-8")

    def test_branch_workflow_never_references_the_signing_secret(self) -> None:
        # The strongest form of "branches cannot sign": not a guard around the
        # secret, but no reference to it anywhere in the file. A branch push
        # cannot exfiltrate or use a secret its workflow never requests.
        self.assertNotIn("SIGNING_SECRET", self._branch_workflow())

    def test_branch_cache_refresh_is_denied_for_everyone(self) -> None:
        text = self._branch_workflow()
        self.assertIn(
            'allow_cache_rebuild: "false"',
            text,
            "build-branch.yml must deny shared akmods cache republishing to ALL branch "
            "runs; without a signing key a branch rebuild publishes an unsigned cache "
            "that every later run rejects, and a compromised write credential could "
            "replace the signed cache at will.",
        )

    def test_branch_workflow_has_no_cache_signing_job(self) -> None:
        # The job cannot work without the key; leaving it in place would fail
        # loudly on every human cache rebuild with a misleading message.
        self.assertNotIn("sign-branch-akmods-cache", self._branch_workflow())

    def test_branch_publish_is_explicitly_unsigned(self) -> None:
        self.assertIn('allow_unsigned: "true"', self._branch_workflow())

    def test_build_yml_never_allows_unsigned_publish(self) -> None:
        text = (WORKFLOW_DIR / "build.yml").read_text(encoding="utf-8")
        self.assertNotIn(
            "allow_unsigned",
            text,
            "build.yml must never pass allow_unsigned; the production path's "
            "no-key-no-push guard is the invariant that keeps user-facing tags signed.",
        )

    def test_main_signing_jobs_declare_the_environment(self) -> None:
        # Creating the environment in repository settings gates nothing on its
        # own; the jobs must declare it. (This exact settings-only mistake was
        # caught in review of the runtime-validation proposal.)
        text = (WORKFLOW_DIR / "build.yml").read_text(encoding="utf-8")
        for job in ("build-candidate-image:", "sign-akmods-cache:"):
            block = text.split(job, 1)[1]
            block = re.split(r"\n  [a-z][a-z0-9-]*:\n", block)[0]
            self.assertIn(
                "environment: production-signing",
                block.split("\n    steps:")[0],
                f"{job.rstrip(':')} must declare the production-signing environment; "
                "without it the secret stays reachable from any ref once the "
                "repository-level copy is removed, or signing silently breaks.",
            )

    def test_publish_action_fails_closed_by_default(self) -> None:
        action = (
            REPO_ROOT / ".github" / "actions" / "publish-native-image" / "action.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('default: "false"', action)
        self.assertIn(
            "inputs.cosign_private_key == '' && inputs.allow_unsigned != 'true'",
            action,
            "publish-native-image must refuse to push when there is no key unless the "
            "caller explicitly opted into unsigned publication.",
        )

    def test_prepare_action_supports_denying_cache_rebuild(self) -> None:
        action = (
            REPO_ROOT / ".github" / "actions" / "prepare-main-akmods" / "action.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("allow_cache_rebuild:", action)
        # The guard must fail the run rather than silently skipping the rebuild,
        # which would leave the caller believing a usable cache exists.
        self.assertIn("Refuse to refresh the shared akmods cache", action)


if __name__ == "__main__":
    unittest.main()
