"""
Script: ci_tools/sign_image.py
What: Signs and verifies one published image tag in GitHub Container Registry (GHCR).
Doing: Resolves the tag to a digest, signs that digest with cosign, then verifies it immediately.
Why: Signing by digest is the reliable way to keep bootc/rpm-ostree trust tied to exact image content.
Goal: Provide one reusable signing helper for candidate, branch, and stable tags.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from ci_tools.common import (
    REPO_ROOT,
    CiToolError,
    normalize_owner,
    optional_env,
    require_env,
    run_cmd,
    skopeo_inspect_digest,
)


def image_tag_ref(image_org: str, image_name: str, image_tag: str) -> str:
    """Return the registry ref used to resolve one tag to a digest."""

    return f"docker://ghcr.io/{image_org}/{image_name}:{image_tag}"


def image_digest_ref(image_org: str, image_name: str, digest: str) -> str:
    """Return the digest-pinned image ref used for signing and verification."""

    return f"ghcr.io/{image_org}/{image_name}@{digest}"


def sign_published_image(
    *,
    image_org: str,
    image_name: str,
    image_tag: str,
    cosign_private_key: str,
    image_digest: str = "",
    digest_lookup: Callable[[str], str] = skopeo_inspect_digest,
    command_runner: Callable[..., str] = run_cmd,
) -> str:
    """
    Sign and verify one published image digest.

    The helper signs the digest rather than the tag text. That keeps signature
    verification tied to immutable content instead of a movable label. Cosign
    and skopeo both rely on the registry login the calling workflow step
    already performed (see `docker/login-action` in
    `.github/actions/publish-native-image/action.yml`); no registry
    credentials are threaded through here.

    Pass `image_digest` when the caller already knows the exact digest it wants
    signed; `image_tag` is then used only for log output. Resolving the tag here
    instead would reintroduce a race for any tag more than one workflow can
    publish: the shared akmods cache tag (`main-<fedora>`) is rebuilt and
    republished by `build-branch.yml` as well as `build.yml`, so a concurrent
    run can move that tag between the moment a caller pins a digest and the
    moment this function looks it up. Signing the re-resolved tag would then
    sign the other run's image and leave the digest this run actually consumed
    unsigned forever -- which a later cache-reuse check treats as untrusted.
    """

    if not cosign_private_key:
        raise CiToolError("SIGNING_SECRET is empty; cannot sign published image.")
    cosign_public_key_path = os.environ.get("COSIGN_PUBLIC_KEY_PATH", "").strip()
    if cosign_public_key_path:
        verification_key = cosign_public_key_path
    else:
        verification_key = str(REPO_ROOT / "cosign.pub")
    if not os.path.exists(verification_key):
        raise CiToolError(f"Missing required verification key file: {verification_key}")

    if image_digest:
        digest = image_digest
    else:
        tag_ref = image_tag_ref(image_org, image_name, image_tag)
        digest = digest_lookup(tag_ref)
        if not digest or digest == "null":
            raise CiToolError(f"Failed to resolve digest for {tag_ref}")

    digest_ref = image_digest_ref(image_org, image_name, digest)

    command_runner(
        [
            "cosign",
            "sign",
            "--yes",
            "--new-bundle-format=false",
            "--use-signing-config=false",
            "--registry-referrers-mode=legacy",
            "--key",
            "env://COSIGN_PRIVATE_KEY",
            digest_ref,
        ],
        capture_output=False,
        env={
            "COSIGN_PASSWORD": os.environ.get("COSIGN_PASSWORD", ""),
            "COSIGN_PRIVATE_KEY": cosign_private_key,
        },
    )
    command_runner(
        [
            "cosign",
            "verify",
            "--new-bundle-format=false",
            "--key",
            verification_key,
            digest_ref,
        ]
    )

    print(f"Signed published image digest: {digest_ref}")
    return digest_ref


def main() -> None:
    image_org = normalize_owner(require_env("IMAGE_ORG"))
    image_name = require_env("IMAGE_NAME")
    image_tag = require_env("IMAGE_TAG")
    cosign_private_key = require_env("COSIGN_PRIVATE_KEY")
    # Optional: callers that already pinned a digest pass it so this never
    # re-resolves a tag another workflow could have moved in the meantime.
    image_digest = optional_env("IMAGE_DIGEST").strip()

    sign_published_image(
        image_org=image_org,
        image_name=image_name,
        image_tag=image_tag,
        cosign_private_key=cosign_private_key,
        image_digest=image_digest,
    )


if __name__ == "__main__":
    main()
