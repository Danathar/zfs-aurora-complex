"""
Script: tests/test_action_publish_native_image.py
What: Executes the shell that publishes a candidate image: the retag and push steps of
.github/actions/publish-native-image/action.yml, and the two steps of build.yml's
sign-candidate-image job that confirm the pushed digest and then point the candidate tag at it.
Doing: Extracts each step's `run:` body with PyYAML and runs it under bash with the step's `env:`
block resolved the way GitHub resolves it, against stub `buildah`, `podman` and `skopeo` binaries
that record their argv and model a tiny tag-to-digest registry.
Why: This sequence keeps a user-facing tag from ever pointing at unsigned content: the build job
pushes a transient tag and reports the digest podman stored; the signing job confirms the
transient tag holds that digest, signs it, and only then copies *that digest* to the candidate
tag and verifies the tag resolves to it. All of that is shell, and no other tier runs it --
`tests/e2e/` dispatches `ci_tools.cli` commands and never a workflow step, and composite actions
and workflow steps are outside `tests/check_coverage.py`'s manifest by design. A promotion that
copied from the mutable transient *tag* instead of the signed digest, that dropped
`--preserve-digests`, or whose digest cross-check stopped failing the step would publish exactly
the thing this sequence exists to prevent, with every existing test still green.
Goal: Make "the tag users pull resolves to the digest this run signed" fail here rather than in
the registry, where the only symptom is a signature that does not match what is being pulled.

The steps' text is executed rather than copied. A renamed or deleted step fails the extraction
loudly instead of leaving this file silently asserting nothing.

Nothing here reaches a registry. `buildah`, `podman` and `skopeo` are stubs; PATH deliberately
omits the directories where the real tools live, so a step that stopped being stubbed would fail
rather than talk to ghcr.io. The stub registry is a directory of files mapping a reference to the
digest it resolves to, which is enough to tell "copied the digest" from "copied the tag".

PyYAML is not a pytest dependency; CI installs it by name (see .github/workflows/test.yml).
The import is guarded so the suite still runs under `python3 -m unittest discover -s tests`
with nothing installed, matching tests/test_workflow_build_container.py.
"""

from __future__ import annotations

import base64
import json
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
ACTION_PATH = REPO_ROOT / ".github" / "actions" / "publish-native-image" / "action.yml"
BUILD_PATH = REPO_ROOT / ".github" / "workflows" / "build.yml"
SIGNING_JOB = "sign-candidate-image"

RETAG_STEP = "Retag local image for transient publication"
PUSH_STEP = "Push transient tag with podman"
CONFIRM_STEP = "Confirm the pushed digest"
SIGN_STEP = "Sign candidate image"
PROMOTE_STEP = "Point the candidate tag at the signed digest"

# PATH must reach the stubs and must NOT reach a real buildah/podman/skopeo, which live in
# /usr/bin on a runner. Only the stub directory plus a shell is offered, so an unstubbed call
# fails with "command not found" instead of contacting a registry.
SAFE_PATH = "/usr/bin:/bin"

REGISTRY = "ghcr.io/danathar"
IMAGE = "zfs-aurora-complex"
# The shape build.yml's sign-candidate-image job works out (`candidate-<sha7>-<fedora>`). Not
# `latest`: that job must never move `latest`, so a fixture tag of `latest` would let a
# promotion step that wrote to `:latest` instead of the candidate tag pass.
TAG = "candidate-0123456-44"
RUN_ID = "34266369977"
TRANSIENT_TAG = f"{TAG}-unsigned-{RUN_ID}"

SIGNED_DIGEST = "sha256:" + "a" * 64
STALE_DIGEST = "sha256:" + "b" * 64
REENCODED_DIGEST = "sha256:" + "c" * 64

REGISTRY_USER = "danathar"
REGISTRY_PASSWORD = "not-a-real-token-1234"

# Records argv, then answers from $STUB_REGISTRY: one file per reference, holding the digest
# that reference resolves to. `inspect` prints it (and fails when the reference is absent, as
# skopeo does); `copy` resolves the source and writes the destination, which is what makes
# "promoted the signed digest" distinguishable from "promoted whatever the tag points at now".
# $STUB_COPY_DIGEST overrides the digest a copy lands, standing in for a manifest re-encode.
STUB = r"""#!/bin/sh
printf '%s\n' "$*" >> "${STUB_CALLS}"
key() { printf '%s' "$1" | tr -c 'A-Za-z0-9' '_'; }
tool=$(basename "$0")
if [ "${tool}" != "skopeo" ]; then
  # The push step's auth file lives in a directory it deletes on exit, so its
  # mode and contents can only be read from inside the call it was written for.
  # `--digestfile` is answered with $STUB_PUSH_DIGEST, the digest a registry
  # would report having stored.
  prev=""
  for arg in "$@"; do
    if [ "${prev}" = "--authfile" ]; then
      stat -c '%a' "${arg}" > "${STUB_AUTH}"
      cat "${arg}" >> "${STUB_AUTH}"
    fi
    if [ "${prev}" = "--digestfile" ]; then
      printf '%s' "${STUB_PUSH_DIGEST}" > "${arg}"
    fi
    prev=${arg}
  done
  exit 0
fi
subcmd=$1
shift
last=""
prev=""
for arg in "$@"; do
  prev=${last}
  last=${arg}
done
case "${subcmd}" in
  inspect)
    file="${STUB_REGISTRY}/$(key "${last}")"
    if [ ! -f "${file}" ]; then
      echo "stub skopeo: no such reference ${last}" >&2
      exit 1
    fi
    cat "${file}"
    ;;
  copy)
    source_file="${STUB_REGISTRY}/$(key "${prev}")"
    if [ ! -f "${source_file}" ]; then
      echo "stub skopeo: cannot copy absent reference ${prev}" >&2
      exit 1
    fi
    landed=$(cat "${source_file}")
    if [ -n "${STUB_COPY_DIGEST}" ]; then
      landed=${STUB_COPY_DIGEST}
    fi
    printf '%s' "${landed}" > "${STUB_REGISTRY}/$(key "${last}")"
    ;;
  *)
    echo "stub skopeo: unexpected subcommand ${subcmd}" >&2
    exit 1
    ;;
esac
"""


def _steps(where: str) -> list[dict]:
    if where == "action":
        return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))["runs"]["steps"]
    return yaml.safe_load(BUILD_PATH.read_text(encoding="utf-8"))["jobs"][SIGNING_JOB]["steps"]


def _step(name: str, where: str = "action") -> dict:
    """
    Return the step called `name`, from the composite action or from build.yml's signing job.

    Located by name rather than by position: a step inserted above it must not silently move
    these tests onto different shell.
    """

    for step in _steps(where):
        if step.get("name") == name:
            return step
    source = "publish-native-image/action.yml" if where == "action" else f"build.yml job {SIGNING_JOB}"
    raise AssertionError(
        f"{source} has no step named {name!r}; this test executes that step's shell and "
        "cannot find it"
    )


def _step_body(name: str, where: str = "action") -> str:
    body = _step(name, where).get("run")
    if not body:
        raise AssertionError(f"step {name!r} no longer has a `run:` body")
    return body


class Result:
    """What a step leaves behind: its exit status, its output, and every tool call it made."""

    def __init__(
        self,
        completed: subprocess.CompletedProcess,
        calls: list[str],
        auth: str = "",
        outputs: str = "",
    ):
        self.returncode = completed.returncode
        self.outputs = outputs.splitlines()
        self.stdout = completed.stdout
        self.stderr = completed.stderr
        self.calls = calls
        # Mode on the first line, file contents after it; empty when the step
        # passed no `--authfile`.
        self.auth_mode, _, self.auth_contents = auth.partition("\n")

    def calls_matching(self, needle: str) -> list[str]:
        return [call for call in self.calls if needle in call]


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class PublishActionStepTests(unittest.TestCase):
    """
    The publication sequence, run as shell.

    Every case executes the action's own text with the `${{ inputs.* }}` and
    `${{ github.run_id }}` expressions resolved the way GitHub resolves them.
    """

    def _run(
        self,
        step_name: str,
        *,
        where: str = "action",
        registry_contents: dict[str, str] | None = None,
        copy_digest: str = "",
        push_digest: str = SIGNED_DIGEST,
    ) -> Result:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bindir = root / "bin"
            bindir.mkdir()
            for tool in ("buildah", "podman", "skopeo"):
                stub = bindir / tool
                stub.write_text(STUB, encoding="utf-8")
                stub.chmod(0o755)

            stub_registry = root / "registry"
            stub_registry.mkdir()
            for reference, digest in (registry_contents or {}).items():
                (stub_registry / re.sub(r"[^A-Za-z0-9]", "_", reference)).write_text(
                    digest, encoding="utf-8"
                )

            calls = root / "calls"
            calls.touch()
            auth = root / "auth"
            outputs = root / "github_output"

            env = {
                "PATH": f"{bindir}:{SAFE_PATH}",
                "STUB_CALLS": str(calls),
                "STUB_AUTH": str(auth),
                "STUB_REGISTRY": str(stub_registry),
                "STUB_COPY_DIGEST": copy_digest,
                "STUB_PUSH_DIGEST": push_digest,
                "GITHUB_OUTPUT": str(outputs),
                # The steps' own `env:` blocks, expressions resolved. The action's
                # steps read the first six, the signing job's steps the last four.
                "IMAGE_REGISTRY": REGISTRY,
                "IMAGE_NAME": IMAGE,
                "IMAGE_TAG": TAG,
                "TRANSIENT_IMAGE_TAG": TRANSIENT_TAG,
                "REGISTRY_USER": REGISTRY_USER,
                "REGISTRY_PASSWORD": REGISTRY_PASSWORD,
                "IMAGE_REPO": f"{REGISTRY}/{IMAGE}",
                "TRANSIENT_TAG": TRANSIENT_TAG,
                "CANDIDATE_TAG": TAG,
                "IMAGE_DIGEST": SIGNED_DIGEST,
            }

            completed = subprocess.run(
                ["bash", "-c", _step_body(step_name, where)],
                env=env,
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            recorded = [
                line
                for line in calls.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            captured = auth.read_text(encoding="utf-8") if auth.exists() else ""
            written = outputs.read_text(encoding="utf-8") if outputs.exists() else ""
            return Result(completed, recorded, captured, written)

    def _promote(self, **kwargs) -> Result:
        """Promote against a registry that already holds the pushed transient tag."""

        contents = {
            f"docker://{REGISTRY}/{IMAGE}:{TRANSIENT_TAG}": SIGNED_DIGEST,
            f"docker://{REGISTRY}/{IMAGE}@{SIGNED_DIGEST}": SIGNED_DIGEST,
        }
        contents.update(kwargs.pop("registry_contents", {}))
        return self._run(PROMOTE_STEP, where="build", registry_contents=contents, **kwargs)

    # -- retag -------------------------------------------------------------

    def test_the_transient_tag_is_cut_from_the_locally_built_image(self) -> None:
        """
        The transient tag must name the image buildah just built, not a fresh pull.

        `buildah` and `podman` share local container storage, which is the whole reason the
        next step can push a tag that was never pushed: a retag that named something else
        would publish an image this run did not build.
        """

        result = self._run(RETAG_STEP)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.calls, [f"tag {IMAGE}:{TAG} {IMAGE}:{TRANSIENT_TAG}"]
        )

    # -- push --------------------------------------------------------------

    def test_the_transient_tag_is_pushed_under_the_registry_path(self) -> None:
        """
        Local name in, registry-qualified name out.

        The local reference carries no registry, so a push that forgot to prefix
        `IMAGE_REGISTRY` on the destination would push to docker.io by default.
        """

        result = self._run(PUSH_STEP)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.calls), 1, result.calls)
        self.assertIn(
            f"{IMAGE}:{TRANSIENT_TAG} {REGISTRY}/{IMAGE}:{TRANSIENT_TAG}",
            result.calls[0],
        )

    def test_the_push_never_publishes_the_requested_tag(self) -> None:
        """
        Nothing user-facing is pushed before the signature exists.

        The sequence's entire guarantee is that the candidate tag appears only after the digest
        under it has been signed, so this step must mention the requested tag only as the stem the
        transient tag is cut from.
        """

        result = self._run(PUSH_STEP)
        self.assertNotIn(
            f"{IMAGE}:{TAG} ",
            result.calls[0] + " ",
            "the push step must publish the transient tag only",
        )

    def test_the_push_verifies_the_registry_certificate(self) -> None:
        """
        `--tls-verify=true` is spelled out rather than left to the default.

        Podman's default is already true, so dropping the flag changes nothing today and
        everything the day a runner is configured with an insecure-registry entry.
        """

        result = self._run(PUSH_STEP)
        self.assertIn("--tls-verify=true", result.calls[0])

    def test_the_registry_credential_never_reaches_the_push_argv(self) -> None:
        """
        Neither half of the credential may appear on podman's command line.

        `/proc/<pid>/cmdline` is mode 0444, so a token in argv is readable by every uid on
        the runner for as long as the push runs -- gotcha 6 in `docs/signing-and-bootc.md`.
        Asserted against the argv the stub actually received rather than against the step's
        text, so a credential reintroduced through a variable is caught too.
        """

        argv = self._run(PUSH_STEP).calls[0]
        self.assertNotIn(REGISTRY_PASSWORD, argv)
        self.assertNotIn(f"{REGISTRY_USER}:{REGISTRY_PASSWORD}", argv)
        self.assertNotIn("--creds", argv)

    def test_the_push_authenticates_from_an_auth_file_holding_the_credential(self) -> None:
        """
        The credential has to reach podman, in the file `--authfile` names.

        Keeping the token out of argv is only half the requirement: a step that dropped it
        entirely would still pass the argv assertion above while pushing anonymously, or --
        worse -- silently falling back to whatever the job-level `docker/login-action` left
        in the Docker config, so a login that quietly stopped working would stop being
        visible here. The file is read inside the stub call because the step deletes the
        directory holding it on exit.
        """

        result = self._run(PUSH_STEP)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--authfile", result.calls[0])

        encoded = base64.b64encode(
            f"{REGISTRY_USER}:{REGISTRY_PASSWORD}".encode()
        ).decode("ascii")
        self.assertEqual(
            json.loads(result.auth_contents),
            {"auths": {REGISTRY.split("/")[0]: {"auth": encoded}}},
            "the auth file must carry this credential, keyed by the registry host",
        )

    def test_the_auth_file_is_not_readable_by_other_uids(self) -> None:
        """
        `0600`, and created that way rather than chmod-ed afterwards.

        An auth file is only an improvement on argv while it is unreadable by the uids that
        can read `/proc/<pid>/cmdline`. This mirrors what `registry_auth_dir` guarantees in
        `ci_tools/common.py` for the skopeo and cosign call sites.
        """

        self.assertEqual(self._run(PUSH_STEP).auth_mode, "600")

    def test_the_push_reports_the_digest_podman_stored(self) -> None:
        """
        The signing job signs the digest this step reports, so it must be podman's own.

        It comes from `--digestfile`, what the registry said it stored, rather than from a
        later read of the transient tag.
        """

        result = self._run(PUSH_STEP)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--digestfile", result.calls[0])
        self.assertEqual(result.outputs, [f"transient_tag={TRANSIENT_TAG}", f"digest={SIGNED_DIGEST}"])

    def test_a_push_that_reports_no_usable_digest_fails_the_step(self) -> None:
        for reported in ("", "sha256:short", "latest"):
            with self.subTest(reported=reported):
                result = self._run(PUSH_STEP, push_digest=reported)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("digest=", "\n".join(result.outputs))

    # -- confirm (build.yml, sign-candidate-image) --------------------------

    def test_the_signed_digest_must_be_what_this_runs_transient_tag_holds(self) -> None:
        """
        The digest comes from a job whose code nobody has to approve, so it is checked
        against the transient tag only this run can have created before anything is signed.
        """

        result = self._run(
            CONFIRM_STEP,
            where="build",
            registry_contents={f"docker://{REGISTRY}/{IMAGE}:{TRANSIENT_TAG}": SIGNED_DIGEST},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.calls), 1, result.calls)
        self.assertIn(f"docker://{REGISTRY}/{IMAGE}:{TRANSIENT_TAG}", result.calls[0])

    def test_a_digest_the_transient_tag_does_not_hold_is_refused(self) -> None:
        result = self._run(
            CONFIRM_STEP,
            where="build",
            registry_contents={f"docker://{REGISTRY}/{IMAGE}:{TRANSIENT_TAG}": STALE_DIGEST},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to sign", result.stderr)

    def test_a_transient_tag_that_does_not_resolve_is_refused(self) -> None:
        result = self._run(CONFIRM_STEP, where="build", registry_contents={})
        self.assertNotEqual(result.returncode, 0)

    # -- promote -----------------------------------------------------------

    def test_the_promotion_copies_the_signed_digest_and_not_the_transient_tag(
        self,
    ) -> None:
        """
        The copy source must be `name@digest`, never `name:transient-tag`.

        A tag is mutable: copying from it promotes whatever it resolves to at copy time,
        which is not necessarily the digest cosign just signed. Copying by digest is what
        makes the signature and the published bits the same object.
        """

        result = self._promote()
        self.assertEqual(result.returncode, 0, result.stderr)
        copies = result.calls_matching("copy ")
        self.assertEqual(len(copies), 1, result.calls)
        self.assertIn(f"docker://{REGISTRY}/{IMAGE}@{SIGNED_DIGEST}", copies[0])
        self.assertNotIn(
            f"docker://{REGISTRY}/{IMAGE}:{TRANSIENT_TAG} ",
            copies[0] + " ",
            "the promotion must read the digest, not the transient tag",
        )

    def test_the_promotion_lands_on_the_requested_tag(self) -> None:
        """The destination is the candidate tag, registry-qualified."""

        result = self._promote()
        copies = result.calls_matching("copy ")
        self.assertTrue(
            copies[0].endswith(f"docker://{REGISTRY}/{IMAGE}:{TAG}"),
            copies[0],
        )

    def test_the_promotion_preserves_digests_and_every_architecture(self) -> None:
        """
        `--preserve-digests` and `--multi-arch=all` are both load-bearing.

        Without the first, skopeo may re-encode the manifest and drop the layer annotations
        Chunkah writes -- the entire point of the rechunk step that runs before this one.
        Without the second, a multi-architecture image is silently narrowed to one.
        """

        result = self._promote()
        copies = result.calls_matching("copy ")
        self.assertIn("--preserve-digests", copies[0])
        self.assertIn("--multi-arch=all", copies[0])

    def test_the_promotion_never_writes_latest(self) -> None:
        """
        The signing job moves only the candidate tag; promote-stable alone moves `latest`.

        A step that pointed `latest` at the digest would skip promote-stable's gates, so
        `promote_to_stable=false` would no longer keep a candidate off `latest`.
        """

        result = self._promote()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(
            [call for call in result.calls if f"{IMAGE}:latest" in call],
            result.calls,
        )

    def test_a_digest_that_changes_in_flight_fails_the_step(self) -> None:
        """
        The cross-check is the last thing standing between a signature and the bits.

        If the promoted tag resolves to anything other than the signed digest, the step must
        fail the job rather than leave the candidate tag pointing at unsigned content. Driven by
        making the copy land a different digest, which is what a manifest re-encode does.
        """

        result = self._promote(copy_digest=REENCODED_DIGEST)
        self.assertNotEqual(
            result.returncode,
            0,
            "a promoted tag that does not resolve to the signed digest must fail the step",
        )
        self.assertIn(REENCODED_DIGEST, result.stderr)
        self.assertIn(SIGNED_DIGEST, result.stderr)

    def test_the_promoted_tag_is_read_back_after_the_copy(self) -> None:
        """
        The verification must inspect the registry once the copy has landed.

        A read taken before the copy would answer for the *previous* holder of the tag --
        here a stale digest from an earlier build -- and would fail a promotion that
        succeeded, or, on a first-ever push, compare nothing against nothing.
        """

        result = self._promote(
            registry_contents={
                f"docker://{REGISTRY}/{IMAGE}:{TAG}": STALE_DIGEST,
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [call.split()[0] for call in result.calls],
            ["copy", "inspect"],
            "the step must copy the signed digest, then read the promoted tag back",
        )

    def test_a_failed_copy_of_the_signed_digest_fails_the_step(self) -> None:
        """
        A promotion that cannot copy the signed digest must fail, not report success.

        Without `set -euo pipefail` a failed copy would be ignored, and for a tag that does not
        exist yet, such as a fresh `candidate-*` tag, the read-back fails too, so the step could
        end having promoted nothing. Driven with an empty stub registry, which is that case:
        neither the signed digest nor the candidate tag resolves.
        """

        result = self._run(PROMOTE_STEP, where="build", registry_contents={})
        self.assertNotEqual(
            result.returncode,
            0,
            "a promotion whose digest lookup failed must fail the step, not exit 0 having "
            f"promoted nothing: {result.calls}",
        )


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class PublishActionSequenceTests(unittest.TestCase):
    """
    The one invariant that spans jobs: the tag that is pushed is the tag that is confirmed.

    The action's retag and push steps each compute
    `${{ inputs.image_tag }}-unsigned-${{ github.run_id }}` in their own `env:` block, and the
    signing job works the transient tag out again from the candidate tag and the run, because
    it takes no tag from the build job. If those drifted apart, the confirm step would read a
    tag this run never pushed.
    """

    TRANSIENT_EXPRESSION = "${{ inputs.image_tag }}-unsigned-${{ github.run_id }}"

    def _env_of(self, step_name: str, where: str = "action") -> dict:
        return _step(step_name, where).get("env") or {}

    def test_the_retag_and_the_push_use_the_same_transient_tag(self) -> None:
        pushed = self._env_of(PUSH_STEP)["TRANSIENT_IMAGE_TAG"]
        retagged = self._env_of(RETAG_STEP)["TRANSIENT_IMAGE_TAG"]
        self.assertEqual({pushed, retagged}, {self.TRANSIENT_EXPRESSION})

    def test_the_signing_job_derives_the_same_transient_tag(self) -> None:
        # The action is handed the candidate tag as image_tag; the signing job appends the
        # same suffix to the candidate tag it computes itself.
        target = _step_body("Work out what to sign", "build")
        self.assertIn('echo "transient_tag=${candidate_tag}-unsigned-${GITHUB_RUN_ID}"', target)
        self.assertEqual(
            self._env_of(CONFIRM_STEP, "build")["TRANSIENT_TAG"],
            "${{ steps.target.outputs.transient_tag }}",
        )

    def test_the_confirmed_digest_is_the_one_signed_and_promoted(self) -> None:
        digests = {
            self._env_of(name, "build")["IMAGE_DIGEST"] for name in (CONFIRM_STEP, SIGN_STEP, PROMOTE_STEP)
        }
        self.assertEqual(digests, {"${{ needs.build-candidate-image.outputs.digest }}"})

    def test_the_transient_tag_is_unique_per_run(self) -> None:
        """
        `github.run_id` in the transient tag is what keeps two concurrent runs apart.

        Without it both would push the same transient tag, and each could confirm the
        other's digest -- signed, but not by the run that published it.
        """

        self.assertIn("github.run_id", self._env_of(PUSH_STEP)["TRANSIENT_IMAGE_TAG"])


if __name__ == "__main__":
    unittest.main()
