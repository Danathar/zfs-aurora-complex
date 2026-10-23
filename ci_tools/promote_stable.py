"""
Script: ci_tools/promote_stable.py
What: Promotes the tested candidate tag to stable tags in the same image repository.
Doing: Refuses when a newer run already promoted, then copies the candidate digest to one
immutable audit tag, then to `latest`.
Why: Candidate-first promotion keeps broken builds from advancing the normal user-facing tag.
Goal: Update stable tags without rebuilding the image a second time.
"""

from __future__ import annotations

import os
import re

from ci_tools.common import (
    REPO_ROOT,
    CiToolError,
    normalize_owner,
    registry_auth_dir,
    registry_auth_file,
    require_env,
    run_cmd,
    run_json_cmd,
    skopeo_copy,
    skopeo_inspect_digest,
)
from ci_tools.tagging_context import build_candidate_tag

# The immutable audit tag `main()` writes: `stable-<run_number>-<sha_short>`.
STABLE_RE = re.compile(r"^stable-\d+-[0-9a-f]+$")


def list_repository_tags(repository_ref: str, *, creds: str | None) -> list[str]:
    """
    Return every tag in one image repository (`docker://host/org/name`).

    `creds` reaches `skopeo` as an `--authfile`, never as `--creds`; see
    `registry_auth_dir` for why.
    """
    with registry_auth_dir(creds, repository_ref) as auth_dir:
        command = ["skopeo", "list-tags"]
        if auth_dir:
            command.extend(["--authfile", registry_auth_file(auth_dir)])
        command.append(repository_ref)
        listing = run_json_cmd(command)
    tags = listing.get("Tags")
    if not isinstance(tags, list):
        raise CiToolError(f"Missing tag list in skopeo list-tags output for {repository_ref}")
    return [str(tag) for tag in tags]


def refuse_older_than_published(*, run_number: str, tags: list[str]) -> None:
    """
    Refuse to promote when a newer run has already promoted.

    Every promotion writes `stable-<run>-<sha7>` before it moves `latest`, so
    the highest run number among those tags is the newest build that reached
    users. Re-running an old, finished run keeps that run's
    `GITHUB_RUN_NUMBER`, and the in-progress `concurrency` cancel in
    `build.yml` does not stop it. Without this check such a re-run would move
    `latest` back to an older image, and an older image may be unable to
    import pools whose features a newer one enabled (`docs/SECURITY-AI.md`).

    An equal run number is allowed: that is a re-run of the same build, which
    resolves the same candidate. A deliberate rollback is a new dispatch,
    which gets a new, higher run number.

    `GITHUB_RUN_NUMBER` counts per workflow. If promotion ever moves to a
    workflow whose count is lower than the existing `stable-*` tags, this check
    refuses every promotion until those tags are removed. That is the
    fail-closed direction.
    """
    if not run_number.isdigit():
        raise CiToolError(f"GITHUB_RUN_NUMBER is not a run number: {run_number!r}")
    published = [int(tag.split("-")[1]) for tag in tags if STABLE_RE.match(tag)]
    if published and max(published) > int(run_number):
        raise CiToolError(
            f"Refusing to promote run {run_number}: run {max(published)} has already "
            "promoted a newer image to latest. Re-running an older build's promotion "
            "would move latest backwards. To roll back on purpose, start a new run."
        )


def verify_candidate_signature(*, image_org: str, image_name: str, candidate_digest: str) -> None:
    """
    Re-verify the candidate signature in this job before promoting it.

    The candidate was already signed and verified when it was published, but
    that happened in a different job on a different runner. Promotion is the
    step that actually points `latest` at a digest, so it should be able to
    prove locally -- not infer from an earlier job -- that the digest it is
    about to promote carries a valid signature from this repo's committed key.

    Verification uses the same committed `cosign.pub` that is baked into the
    image and that booted systems enforce, so a key mismatch fails here rather
    than on a user's machine at `bootc upgrade` time.
    """

    verification_key = os.environ.get("COSIGN_PUBLIC_KEY_PATH", "").strip() or str(
        REPO_ROOT / "cosign.pub"
    )
    if not os.path.exists(verification_key):
        raise CiToolError(f"Missing required verification key file: {verification_key}")

    digest_ref = f"ghcr.io/{image_org}/{image_name}@{candidate_digest}"
    run_cmd(
        [
            "cosign",
            "verify",
            "--new-bundle-format=false",
            "--key",
            verification_key,
            digest_ref,
        ]
    )
    print(f"Verified candidate signature before promotion: {digest_ref}")


def _copy_and_verify_digest(*, source_digest: str, source_ref: str, destination_ref: str, creds: str) -> None:
    """
    Copy `source_ref` to `destination_ref`, then confirm the copy landed at the
    exact source digest.

    `--preserve-digests` and `--multi-arch=all` should already keep the digest
    unchanged, but this check keeps the promotion fail-closed if skopeo's
    conversion behavior ever changes: `latest` must never silently point at
    something other than what was signed.
    """
    skopeo_copy(source_ref, destination_ref, creds=creds, preserve_digests=True, multi_arch="all")
    destination_digest = skopeo_inspect_digest(destination_ref, creds=creds)
    if destination_digest != source_digest:
        raise CiToolError(
            f"Promoted digest mismatch: copying {source_ref} to {destination_ref} "
            f"produced {destination_digest}, expected {source_digest}"
        )


def main() -> None:
    # Inputs from workflow context and job env.
    image_org = normalize_owner(require_env("GITHUB_REPOSITORY_OWNER"))
    registry_actor = require_env("REGISTRY_ACTOR")
    registry_token = require_env("REGISTRY_TOKEN")
    fedora_version = require_env("FEDORA_VERSION")
    image_name = require_env("IMAGE_NAME")
    run_number = require_env("GITHUB_RUN_NUMBER")
    github_sha = require_env("GITHUB_SHA")
    sha_short = github_sha[:7]

    # Candidate tags are built and pushed earlier in the workflow under the same
    # repository path. Promotion only moves stable-facing tags after that build passes.
    candidate_tag = build_candidate_tag(github_sha=github_sha, fedora_version=fedora_version)
    candidate_by_tag = f"docker://ghcr.io/{image_org}/{image_name}:{candidate_tag}"
    creds = f"{registry_actor}:{registry_token}"
    candidate_digest = skopeo_inspect_digest(candidate_by_tag, creds=creds)
    candidate_ref = f"docker://ghcr.io/{image_org}/{image_name}@{candidate_digest}"

    # Fail closed before any tag moves: never promote a digest we cannot prove
    # is signed by this repo's committed key.
    verify_candidate_signature(
        image_org=image_org, image_name=image_name, candidate_digest=candidate_digest
    )

    # Fail closed before any tag moves: never move `latest` backwards to an
    # image older than the one a newer run already promoted.
    refuse_older_than_published(
        run_number=run_number,
        tags=list_repository_tags(f"docker://ghcr.io/{image_org}/{image_name}", creds=creds),
    )

    stable_ref = f"docker://ghcr.io/{image_org}/{image_name}:latest"
    audit_ref = f"docker://ghcr.io/{image_org}/{image_name}:stable-{run_number}-{sha_short}"

    # Publish the immutable audit tag before moving the user-facing `latest`
    # tag. `build.yml` cancels in-progress runs on a newer push, so if this job
    # is cancelled between the two copies, it is safer to have an audit record
    # with no `latest` move yet than a moved `latest` with no audit record.
    _copy_and_verify_digest(
        source_digest=candidate_digest, source_ref=candidate_ref, destination_ref=audit_ref, creds=creds
    )
    _copy_and_verify_digest(
        source_digest=candidate_digest, source_ref=candidate_ref, destination_ref=stable_ref, creds=creds
    )

    print(f"Resolved candidate source {candidate_by_tag} -> {candidate_ref}")
    print(f"Published audit tag {audit_ref}")
    print(f"Promoted candidate image {candidate_ref} -> stable {stable_ref}")


if __name__ == "__main__":
    main()
