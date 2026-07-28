"""
Script: tests/test_workflow_build_container.py
What: Guards how the privileged akmods build container is selected in workflows.
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


if __name__ == "__main__":
    unittest.main()
