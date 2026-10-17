"""
Script: tests/test_workflow_build_container.py
What: Guards how the privileged akmods build container is selected, that package-write
jobs are bound to `main`, and that the production path signs what it publishes.
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

import base64
import json
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

# The workflows whose jobs run the privileged akmods build container.
PRIVILEGED_CONTAINER_WORKFLOWS = ("build.yml",)

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


class PackageWriteRefTests(unittest.TestCase):
    """
    A job holding `packages: write` runs only on `main`.

    Any workflow with `workflow_dispatch` can be started against another ref, and then it
    runs that ref's code. A package-write token there can overwrite or delete registry
    tags. Each such job must either guard on `github.ref` itself, use the
    `production-signing` environment (restricted to `main` in repository settings), or
    need a job that guards on `github.ref`.
    """

    MAIN_GUARD = "github.ref == 'refs/heads/main'"

    @staticmethod
    def _jobs(text: str) -> dict[str, str]:
        # Jobs are the two-space-indented keys under the top-level `jobs:` key; each
        # body runs to the next one. Whole-line comments are stripped first so a
        # commented-out line cannot count as a guard.
        text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        body = text.split("\njobs:\n", 1)[1]
        parts = re.split(r"^  ([A-Za-z0-9_-]+):[ \t]*$", body, flags=re.MULTILINE)
        return dict(zip(parts[1::2], parts[2::2]))

    @staticmethod
    def _needs(job: str) -> list[str]:
        inline = re.search(r"^    needs:[ \t]*(\S.*)$", job, re.MULTILINE)
        if inline:
            return [name.strip() for name in inline.group(1).strip("[]").split(",")]
        block = re.search(r"^    needs:[ \t]*\n((?:      - .*\n)+)", job, re.MULTILINE)
        return re.findall(r"^      - (\S+)", block.group(1), re.MULTILINE) if block else []

    def test_every_package_writer_is_bound_to_main(self) -> None:
        checked = 0
        paths = sorted([*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")])
        for path in paths:
            text = path.read_text(encoding="utf-8")
            if "\njobs:\n" not in text:
                continue
            jobs = self._jobs(text)
            guarded = {
                name
                for name, job in jobs.items()
                if re.search(rf"^    if:[ \t]*{re.escape(self.MAIN_GUARD)}", job, re.MULTILINE)
            }
            for name, job in jobs.items():
                if not re.search(r"^      packages:[ \t]*write[ \t]*$", job, re.MULTILINE):
                    continue
                checked += 1
                with self.subTest(workflow=path.name, job=name):
                    self.assertTrue(
                        name in guarded
                        or re.search(
                            r"^    environment:[ \t]*production-signing[ \t]*$", job, re.MULTILINE
                        )
                        or any(need in guarded for need in self._needs(job)),
                        f"{path.name}:{name} holds packages: write but nothing binds it to main",
                    )
        # build.yml has the four package-write jobs; no other workflow has one.
        self.assertEqual(checked, 4, "the scan found a different number of package-write jobs")


class ProductionSigningTests(unittest.TestCase):
    """
    The production path signs everything it publishes.

    The production signing key is scoped to a main-only environment. Everything
    here pins the consequences: the signing jobs declare that environment, the
    publish action refuses to push without a key, and the shared akmods cache
    cannot be rebuilt by a caller that denies it.
    """

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

    def test_publish_action_fails_closed_without_a_key(self) -> None:
        action = (
            REPO_ROOT / ".github" / "actions" / "publish-native-image" / "action.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "if: inputs.cosign_private_key == ''",
            action,
            "publish-native-image must refuse to push when there is no signing key.",
        )

    def test_publish_action_pushes_without_the_token_in_argv(self) -> None:
        # Gotcha 6 in docs/signing-and-bootc.md: `/proc/<pid>/cmdline` is mode
        # 0444, so `podman push --creds USER:TOKEN` shows the token to every uid
        # on the runner while the push runs. Runs the step's own shell with a
        # stub podman and checks what podman was actually handed: no token in
        # argv, and an `--authfile` that is 0600 and really carries the
        # credential, so a step that silently stopped authenticating fails too.
        action = (
            REPO_ROOT / ".github" / "actions" / "publish-native-image" / "action.yml"
        ).read_text(encoding="utf-8")
        step = action.split("- name: Push transient tag with podman", 1)[1]
        body = step.split("      run: |\n", 1)[1].split("\n\n", 1)[0]
        script = textwrap.dedent(body)
        token = "ghs_not-a-real-token"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bindir = root / "bin"
            bindir.mkdir()
            seen = root / "seen"
            stub = bindir / "podman"
            stub.write_text(
                "#!/bin/bash\n"
                'printf "%s\\n" "$@" > "${STUB_SEEN}.argv"\n'
                'prev=""\n'
                'for arg in "$@"; do\n'
                '  if [ "${prev}" = "--authfile" ]; then\n'
                '    stat -c "%a" "${arg}" > "${STUB_SEEN}.mode"\n'
                '    cat "${arg}" > "${STUB_SEEN}.auth"\n'
                "  fi\n"
                "  prev=${arg}\n"
                "done\n",
                encoding="utf-8",
            )
            stub.chmod(0o755)
            env = {
                "PATH": f"{bindir}:/usr/bin:/bin",
                "HOME": str(root),
                "TMPDIR": str(root),
                "STUB_SEEN": str(seen),
                "IMAGE_REGISTRY": "ghcr.io/example",
                "IMAGE_NAME": "image",
                "TRANSIENT_IMAGE_TAG": "latest-unsigned-1",
                "REGISTRY_USER": "actor",
                "REGISTRY_PASSWORD": token,
            }
            completed = subprocess.run(
                ["bash", "-c", script],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            argv = Path(f"{seen}.argv").read_text(encoding="utf-8")
            self.assertNotIn(token, argv)
            self.assertNotIn("--creds", argv)
            self.assertIn("--authfile", argv)
            self.assertEqual(Path(f"{seen}.mode").read_text(encoding="utf-8").strip(), "600")
            auths = json.loads(Path(f"{seen}.auth").read_text(encoding="utf-8"))["auths"]
            self.assertEqual(
                auths,
                {"ghcr.io": {"auth": base64.b64encode(f"actor:{token}".encode()).decode()}},
            )
            # The temporary directory holding the credential is gone afterwards.
            self.assertEqual([path for path in root.iterdir() if path.name.startswith("tmp")], [])
            self.assertNotIn(token, completed.stdout + completed.stderr)

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
