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
        # build.yml has the five package-write jobs; no other workflow has one.
        self.assertEqual(checked, 5, "the scan found a different number of package-write jobs")


class SigningIsolationTests(unittest.TestCase):
    """
    Only code-owned code runs where the signing key is (#177, option B).

    The key reaches a job only through the `production-signing` environment. In such
    a job the only local action allowed is the code-owned install-signing-tools, and
    the only Python allowed is `python3 -I ci_tools/sign_image.py`, which imports
    nothing outside the standard library (tests/test_sign_image.py). `python3 -m
    ci_tools.cli` would load every module the CLI imports into the key's process,
    and any other local action is code the owner does not have to approve.
    """

    SIGNING_JOBS = frozenset({"sign-akmods-cache", "sign-candidate-image"})
    ALLOWED_LOCAL_ACTION = "./.github/actions/install-signing-tools"

    def setUp(self) -> None:
        self.jobs = PackageWriteRefTests._jobs((WORKFLOW_DIR / "build.yml").read_text(encoding="utf-8"))

    def test_exactly_the_two_signing_jobs_use_the_environment(self) -> None:
        declared = {
            name
            for name, job in self.jobs.items()
            if re.search(r"^    environment:[ \t]*production-signing[ \t]*$", job, re.MULTILINE)
        }
        self.assertEqual(declared, self.SIGNING_JOBS)

    def test_signing_jobs_run_no_other_local_action(self) -> None:
        for name in self.SIGNING_JOBS:
            local = set(re.findall(r"uses:[ \t]*(\./\S+)", self.jobs[name]))
            with self.subTest(job=name):
                self.assertEqual(local, {self.ALLOWED_LOCAL_ACTION})

    def test_signing_jobs_run_python_only_as_the_isolated_signer(self) -> None:
        for name in self.SIGNING_JOBS:
            python_lines = [
                line.strip() for line in self.jobs[name].splitlines() if re.search(r"\bpython", line)
            ]
            with self.subTest(job=name):
                self.assertTrue(python_lines, "the signing job no longer calls the signer")
                for line in python_lines:
                    self.assertTrue(
                        line.endswith("python3 -I ci_tools/sign_image.py"),
                        f"{name} runs Python other than the isolated signer: {line}",
                    )

    def test_the_key_is_referenced_only_in_the_signing_jobs(self) -> None:
        for path in sorted([*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")]):
            text = path.read_text(encoding="utf-8")
            if "\njobs:\n" not in text:
                continue
            for name, job in PackageWriteRefTests._jobs(text).items():
                if path.name == "build.yml" and name in self.SIGNING_JOBS:
                    continue
                with self.subTest(workflow=path.name, job=name):
                    self.assertNotIn("SIGNING_SECRET", job)

    def test_the_allowed_local_action_calls_no_repository_code(self) -> None:
        action = (REPO_ROOT / ".github" / "actions" / "install-signing-tools" / "action.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("uses: ./", action)
        self.assertNotIn("python", action)


class SigningTargetTests(unittest.TestCase):
    """
    The signing jobs decide where to sign and which tag to move from code-owned inputs.

    build-zfs-akmods and build-candidate-image run code nobody has to approve. If the
    signing jobs took a repository or a tag from their outputs, a forged output could
    sign another package or point `latest` at an image with `promote_to_stable=false`.
    These run the steps' own shell, from the repository root, with forged inputs.
    """

    SHA = "0123456789abcdef0123456789abcdef01234567"

    @staticmethod
    def _step_script(name: str) -> str:
        text = (WORKFLOW_DIR / "build.yml").read_text(encoding="utf-8")
        step = text.split(f"name: {name}\n", 1)[1]
        body = step.split("        run: |\n", 1)[1]
        lines = []
        for line in body.splitlines():
            if line and not line.startswith("          "):
                break
            lines.append(line)
        return textwrap.dedent("\n".join(lines))

    # Logs each call's arguments on one line; `inspect` answers with
    # $REGISTRY_DIGEST, the digest the stub registry holds for the tag.
    SKOPEO_STUB = (
        "#!/bin/bash\n"
        'printf "%s\\n" "$*" >> "${STUB_LOG}"\n'
        'if [ "$1" = inspect ]; then printf "%s\\n" "${REGISTRY_DIGEST}"; fi\n'
    )

    def _run(self, name: str, env: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], Path]:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        temp = Path(holder.name)
        bindir = temp / "bin"
        bindir.mkdir()
        python_stub = bindir / "python3"
        python_stub.write_text('#!/bin/bash\nprintf "%s" "${IMAGE_REPO}" > "${STUB_SEEN}"\n', encoding="utf-8")
        python_stub.chmod(0o755)
        skopeo_stub = bindir / "skopeo"
        skopeo_stub.write_text(self.SKOPEO_STUB, encoding="utf-8")
        skopeo_stub.chmod(0o755)
        output = temp / "github_output"
        full_env = {
            "PATH": f"{bindir}:/usr/bin:/bin",
            "GITHUB_REPOSITORY_OWNER": "Danathar",
            "GITHUB_SHA": self.SHA,
            "GITHUB_RUN_ID": "42",
            "GITHUB_OUTPUT": str(output),
            "STUB_SEEN": str(temp / "seen"),
            "STUB_LOG": str(temp / "skopeo.log"),
            **env,
        }
        result = subprocess.run(
            ["bash", "-c", self._step_script(name)],
            cwd=REPO_ROOT,
            env=full_env,
            capture_output=True,
            text=True,
            check=False,
        )
        return result, temp

    def test_the_candidate_target_comes_from_owned_inputs(self) -> None:
        from ci_tools.tagging_context import build_candidate_tag

        expected_tag = build_candidate_tag(github_sha=self.SHA, fedora_version="44")
        result, temp = self._run(
            "Work out what to sign", {"FEDORA_VERSION": "44", "REPORTED_CANDIDATE_TAG": expected_tag}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (temp / "github_output").read_text(encoding="utf-8").splitlines(),
            [
                "image_repo=ghcr.io/danathar/zfs-aurora-complex",
                f"candidate_tag={expected_tag}",
                f"transient_tag={expected_tag}-unsigned-42",
            ],
        )

    def test_a_forged_candidate_tag_or_fedora_version_is_refused(self) -> None:
        good_tag = f"candidate-{self.SHA[:7]}-44"
        injected = "\nimage_repo=ghcr.io/someone/zfs-aurora-complex"
        for fedora, reported in (
            ("44", "latest"),
            ("44", "stable-1-abc"),
            ("44;latest", good_tag),
            ("", good_tag),
            # Both outputs come from jobs nobody has to approve, so a forger can
            # make the reported tag agree with a bad Fedora version. The number
            # check alone must refuse these, including a second GITHUB_OUTPUT line.
            ("44;latest", f"{good_tag};latest"),
            (f"44{injected}", f"{good_tag}{injected}"),
        ):
            with self.subTest(fedora=fedora, reported=reported):
                result, temp = self._run(
                    "Work out what to sign", {"FEDORA_VERSION": fedora, "REPORTED_CANDIDATE_TAG": reported}
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn("Refusing to sign", result.stderr)
                self.assertFalse((temp / "github_output").exists())

    def test_the_akmods_repository_comes_from_defaults_not_the_build_job(self) -> None:
        signed = {"IMAGE_DIGEST": "sha256:" + "0" * 64, "COSIGN_PRIVATE_KEY": "k"}
        result, temp = self._run(
            "Sign shared akmods cache image",
            {"AKMODS_IMAGE": "ghcr.io/danathar/zfs-aurora-complex-akmods:main-44", **signed},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((temp / "seen").read_text(encoding="utf-8"), "ghcr.io/danathar/zfs-aurora-complex-akmods")

        for forged in ("ghcr.io/danathar/zfs-aurora-complex:main-44", "ghcr.io/someone/zfs-aurora-complex-akmods:main-44"):
            with self.subTest(forged=forged):
                result, temp = self._run("Sign shared akmods cache image", {"AKMODS_IMAGE": forged, **signed})
                self.assertEqual(result.returncode, 1)
                self.assertIn("Refusing to sign", result.stderr)
                self.assertFalse((temp / "seen").exists(), "the signer ran on a forged repository")


    def test_the_pushed_digest_must_be_what_this_runs_transient_tag_holds(self) -> None:
        repo = "ghcr.io/danathar/zfs-aurora-complex"
        transient = f"candidate-{self.SHA[:7]}-44-unsigned-42"
        pushed = "sha256:" + "1" * 64
        env = {"IMAGE_REPO": repo, "TRANSIENT_TAG": transient, "IMAGE_DIGEST": pushed}

        result, temp = self._run("Confirm the pushed digest", {**env, "REGISTRY_DIGEST": pushed})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (temp / "skopeo.log").read_text(encoding="utf-8").splitlines(),
            [f"inspect --no-tags --format {{{{ .Digest }}}} docker://{repo}:{transient}"],
        )

        result, _temp = self._run("Confirm the pushed digest", {**env, "REGISTRY_DIGEST": "sha256:" + "2" * 64})
        self.assertEqual(result.returncode, 1)
        self.assertIn("Refusing to sign", result.stderr)

    def test_the_candidate_tag_moves_only_to_the_signed_digest(self) -> None:
        repo = "ghcr.io/danathar/zfs-aurora-complex"
        tag = f"candidate-{self.SHA[:7]}-44"
        signed = "sha256:" + "1" * 64
        env = {"IMAGE_REPO": repo, "CANDIDATE_TAG": tag, "IMAGE_DIGEST": signed}

        result, temp = self._run("Point the candidate tag at the signed digest", {**env, "REGISTRY_DIGEST": signed})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (temp / "skopeo.log").read_text(encoding="utf-8").splitlines(),
            [
                f"copy --multi-arch=all --preserve-digests docker://{repo}@{signed} docker://{repo}:{tag}",
                f"inspect --no-tags --format {{{{ .Digest }}}} docker://{repo}:{tag}",
            ],
        )

        result, _temp = self._run(
            "Point the candidate tag at the signed digest", {**env, "REGISTRY_DIGEST": "sha256:" + "2" * 64}
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not match signed digest", result.stderr)


class SigningJobWiringTests(unittest.TestCase):
    """
    Every step of sign-candidate-image uses the target the job worked out itself.

    SigningTargetTests runs "Work out what to sign" in isolation. That is worth
    nothing if a later step reads the repository or the tag from
    build-candidate-image instead of from `steps.target`, so these pin each
    step's inputs and the outputs the job takes from other jobs at all.
    """

    def setUp(self) -> None:
        jobs = PackageWriteRefTests._jobs((WORKFLOW_DIR / "build.yml").read_text(encoding="utf-8"))
        self.jobs = jobs
        self.job = jobs["sign-candidate-image"]

    def _step_env(self, name: str) -> dict[str, str]:
        step = self.job.split(f"name: {name}\n", 1)[1]
        block = re.search(r"^        env:\n((?:          \S.*\n)+)", step, re.MULTILINE)
        self.assertIsNotNone(block, f"{name} has no env block")
        assert block is not None
        pairs = (line.strip().split(": ", 1) for line in block.group(1).splitlines())
        return {key: value for key, value in pairs}

    def test_only_the_digest_fedora_version_and_reported_tag_come_from_other_jobs(self) -> None:
        refs = re.findall(r"needs\.([\w-]+)\.outputs\.([\w-]+)", self.job)
        self.assertEqual(
            set(refs),
            {
                ("build-zfs-akmods", "fedora_version"),
                ("build-candidate-image", "candidate_tag"),
                ("build-candidate-image", "digest"),
            },
        )
        # The reported tag is only ever cross-checked, never used.
        self.assertEqual(refs.count(("build-candidate-image", "candidate_tag")), 1)
        self.assertEqual(
            self._step_env("Work out what to sign")["REPORTED_CANDIDATE_TAG"],
            "${{ needs.build-candidate-image.outputs.candidate_tag }}",
        )

    def test_each_step_after_the_target_uses_the_target(self) -> None:
        repo = "${{ steps.target.outputs.image_repo }}"
        digest = "${{ needs.build-candidate-image.outputs.digest }}"
        expected = {
            "Confirm the pushed digest": {
                "IMAGE_REPO": repo,
                "TRANSIENT_TAG": "${{ steps.target.outputs.transient_tag }}",
                "IMAGE_DIGEST": digest,
            },
            "Sign candidate image": {
                "IMAGE_REPO": repo,
                "IMAGE_DIGEST": digest,
                "COSIGN_PRIVATE_KEY": "${{ secrets.SIGNING_SECRET }}",
            },
            "Point the candidate tag at the signed digest": {
                "IMAGE_REPO": repo,
                "CANDIDATE_TAG": "${{ steps.target.outputs.candidate_tag }}",
                "IMAGE_DIGEST": digest,
            },
        }
        for name, env in expected.items():
            with self.subTest(step=name):
                self.assertEqual(self._step_env(name), env)

    def test_the_digest_is_confirmed_before_it_is_signed_and_signed_before_the_tag_moves(self) -> None:
        order = [
            self.job.index(f"name: {name}\n")
            for name in (
                "Work out what to sign",
                "Confirm the pushed digest",
                "Sign candidate image",
                "Point the candidate tag at the signed digest",
            )
        ]
        self.assertEqual(order, sorted(order))

    def test_promotion_waits_for_the_candidate_signature(self) -> None:
        # promote-stable moves `latest`; without this need it could run while the
        # candidate is still unsigned.
        self.assertIn("sign-candidate-image", PackageWriteRefTests._needs(self.jobs["promote-stable"]))


class ProductionSigningTests(unittest.TestCase):
    """
    The production path signs everything it publishes.

    The production signing key is scoped to a main-only environment. Everything
    here pins the consequences: the signing jobs declare that environment, the
    publish action only pushes, and the shared akmods cache cannot be rebuilt by
    a caller that denies it.
    """

    def test_main_signing_jobs_declare_the_environment(self) -> None:
        # Creating the environment in repository settings gates nothing on its
        # own; the jobs must declare it. (This exact settings-only mistake was
        # caught in review of the runtime-validation proposal.)
        text = (WORKFLOW_DIR / "build.yml").read_text(encoding="utf-8")
        for job in ("sign-candidate-image:", "sign-akmods-cache:"):
            block = text.split(f"\n  {job}\n", 1)[1]
            block = re.split(r"\n  [a-z][a-z0-9-]*:\n", block)[0]
            self.assertIn(
                "environment: production-signing",
                block.split("\n    steps:")[0],
                f"{job.rstrip(':')} must declare the production-signing environment; "
                "without it the secret stays reachable from any ref once the "
                "repository-level copy is removed, or signing silently breaks.",
            )

    def test_publish_action_holds_no_key(self) -> None:
        # The action that runs in the unsigned build job must not accept a key at
        # all; signing happens only in the isolated signing job.
        action = (
            REPO_ROOT / ".github" / "actions" / "publish-native-image" / "action.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("cosign_private_key", action)
        self.assertNotIn("sign_image", action)

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
        step = action.split("name: Push transient tag with podman", 1)[1]
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
                '  if [ "${prev}" = "--digestfile" ]; then\n'
                '    printf "sha256:%064d" 0 > "${arg}"\n'
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
                "GITHUB_OUTPUT": str(root / "github_output"),
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
            # The digest podman reported is what the signing job will sign.
            self.assertEqual(
                (root / "github_output").read_text(encoding="utf-8").splitlines(),
                ["transient_tag=latest-unsigned-1", "digest=sha256:" + "0" * 64],
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
