"""
Script: ci_tools/promote_stable.py
What: Promotes the tested candidate tag to stable tags in the same image repository.
Doing: Copies the candidate digest to one immutable audit tag, then to `latest`.
Why: Candidate-first promotion keeps broken builds from advancing the normal user-facing tag.
Goal: Update stable tags without rebuilding the image a second time.
"""

from __future__ import annotations

from ci_tools.common import (
    CiToolError,
    normalize_owner,
    require_env,
    skopeo_copy,
    skopeo_inspect_digest,
)
from ci_tools.tagging_context import build_candidate_tag


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
