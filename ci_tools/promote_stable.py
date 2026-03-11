"""
Script: ci_tools/promote_stable.py
What: Promotes the tested candidate tag to stable tags in the same image repository.
Doing: Copies the candidate digest to `latest` and to one immutable audit tag.
Why: Candidate-first promotion keeps broken builds from advancing the normal user-facing tag.
Goal: Update stable tags without rebuilding the image a second time.
"""

from __future__ import annotations

from ci_tools.common import normalize_owner, require_env, skopeo_copy, skopeo_inspect_digest


def main() -> None:
    # Inputs from workflow context and job env.
    image_org = normalize_owner(require_env("GITHUB_REPOSITORY_OWNER"))
    registry_actor = require_env("REGISTRY_ACTOR")
    registry_token = require_env("REGISTRY_TOKEN")
    fedora_version = require_env("FEDORA_VERSION")
    image_name = require_env("IMAGE_NAME")
    run_number = require_env("GITHUB_RUN_NUMBER")
    sha_short = require_env("GITHUB_SHA")[:7]

    # Candidate tags are built and pushed earlier in the workflow under the same
    # repository path. Promotion only moves stable-facing tags after that build passes.
    candidate_tag = f"candidate-{sha_short}-{fedora_version}"
    candidate_by_tag = f"docker://ghcr.io/{image_org}/{image_name}:{candidate_tag}"
    creds = f"{registry_actor}:{registry_token}"
    candidate_digest = skopeo_inspect_digest(candidate_by_tag, creds=creds)
    candidate_ref = f"docker://ghcr.io/{image_org}/{image_name}@{candidate_digest}"

    stable_ref = f"docker://ghcr.io/{image_org}/{image_name}:latest"
    audit_ref = f"docker://ghcr.io/{image_org}/{image_name}:stable-{run_number}-{sha_short}"

    skopeo_copy(candidate_ref, stable_ref, creds=creds)
    skopeo_copy(candidate_ref, audit_ref, creds=creds)

    print(f"Resolved candidate source {candidate_by_tag} -> {candidate_ref}")
    print(f"Promoted candidate image {candidate_ref} -> stable {stable_ref}")
    print(f"Published audit tag {audit_ref}")


if __name__ == "__main__":
    main()
