"""
Script: ci_tools/sign_image.py
What: Signs and verifies one published image tag in GitHub Container Registry (GHCR).
Doing: Resolves the tag to a digest, signs that digest with cosign, then verifies it immediately.
Why: Signing by digest is the reliable way to keep bootc/rpm-ostree trust tied to exact image content.
Goal: Provide one reusable signing helper for candidate, branch, and stable tags.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ci_tools.common import CiToolError, normalize_owner, require_env, run_cmd, skopeo_inspect_digest


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
    registry_actor: str,
    registry_token: str,
    cosign_private_key: str,
    digest_lookup: Callable[[str], str] = skopeo_inspect_digest,
    command_runner: Callable[..., str] = run_cmd,
) -> str:
    """
    Sign and verify the digest currently referenced by one image tag.

    The helper signs the digest rather than the tag text. That keeps signature
    verification tied to immutable content instead of a movable label.
    """

    if not cosign_private_key:
        raise CiToolError("SIGNING_SECRET is empty; cannot sign published image.")
    if not Path("cosign.pub").exists():
        raise CiToolError("Missing required verification key file: cosign.pub")

    tag_ref = image_tag_ref(image_org, image_name, image_tag)
    digest = digest_lookup(tag_ref)
    if not digest or digest == "null":
        raise CiToolError(f"Failed to resolve digest for {tag_ref}")

    digest_ref = image_digest_ref(image_org, image_name, digest)
    registry_args = [
        "--registry-username",
        registry_actor,
        "--registry-password",
        registry_token,
    ]

    command_runner(
        [
            "cosign",
            "sign",
            "--yes",
            "--key",
            "env://COSIGN_PRIVATE_KEY",
            *registry_args,
            digest_ref,
        ],
        capture_output=False,
        env={
            "COSIGN_PASSWORD": "",
            "COSIGN_PRIVATE_KEY": cosign_private_key,
        },
    )
    command_runner(
        [
            "cosign",
            "verify",
            "--key",
            "cosign.pub",
            *registry_args,
            digest_ref,
        ]
    )

    print(f"Signed published image digest: {digest_ref}")
    return digest_ref


def main() -> None:
    image_org = normalize_owner(require_env("IMAGE_ORG"))
    image_name = require_env("IMAGE_NAME")
    image_tag = require_env("IMAGE_TAG")
    registry_actor = require_env("REGISTRY_ACTOR")
    registry_token = require_env("REGISTRY_TOKEN")
    cosign_private_key = require_env("COSIGN_PRIVATE_KEY")

    sign_published_image(
        image_org=image_org,
        image_name=image_name,
        image_tag=image_tag,
        registry_actor=registry_actor,
        registry_token=registry_token,
        cosign_private_key=cosign_private_key,
    )


if __name__ == "__main__":
    main()
