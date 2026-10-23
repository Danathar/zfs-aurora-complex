"""
Script: ci_tools/pin_akmods_cache.py
What: Resolves the shared akmods cache tag to a digest-pinned image reference.
Doing: Inspects `ghcr.io/<owner>/<repo>:main-<fedora>`, proves the digest it resolves to is the image this run built, and writes the mutable tag plus immutable digest ref to GitHub outputs.
Why: The final image build and the signing job should consume the exact cache image this run published, not whatever the shared tag points at by the time it is read back.
Goal: Keep akmods cache publishing fail-closed while avoiding mutable-tag drift between jobs.
"""

from __future__ import annotations

import hashlib
import re

from ci_tools.common import (
    REGISTRY_METADATA_TIMEOUT,
    CiToolError,
    normalize_owner,
    optional_env,
    require_env,
    run_cmd,
    skopeo_inspect_digest,
    write_github_outputs,
)

# `podman image inspect --format '{{.Id}}'` prints 64 lowercase hex digits; a
# `sha256:`-prefixed spelling of the same value is accepted too.
_IMAGE_ID_RE = re.compile(r"(?:sha256:)?([0-9a-f]{64})")


def akmods_cache_image_tag(*, image_org: str, source_repo: str, fedora_version: str) -> str:
    """Return the shared akmods cache tag for one Fedora major version."""

    return f"ghcr.io/{image_org}/{source_repo}:main-{fedora_version}"


def built_image_config_hex(built_image_id: str) -> str:
    """Return the bare hex config digest of the image the build step recorded, or refuse."""

    match = _IMAGE_ID_RE.fullmatch(built_image_id.strip())
    if not match:
        raise CiToolError(
            "Refusing to pin the akmods cache: AKMODS_BUILT_IMAGE_ID "
            f"{built_image_id!r} is not the sha256 image ID the build step records."
        )
    return match.group(1)


def pushed_config_hex(pinned_ref: str) -> str:
    """
    Return the sha256 of the amd64 image config behind one digest-pinned ref.

    `--raw --config` prints the config blob itself rather than skopeo's
    summary of it. The override selects the amd64 child when the ref is a
    manifest list, which `just manifest` publishes `main-<fedora>` as.
    """

    config = run_cmd(
        [
            "skopeo",
            "--override-os",
            "linux",
            "--override-arch",
            "amd64",
            "inspect",
            "--raw",
            "--config",
            f"docker://{pinned_ref}",
        ],
        timeout=REGISTRY_METADATA_TIMEOUT,
    )
    # `run_cmd` decodes stdout as text. The config is JSON, which is UTF-8 and
    # carries no raw carriage return, so encoding it back yields the bytes
    # skopeo printed. Should that ever not hold, the hash differs and the pin
    # is refused, so the failure mode is a red build, not a wrong pin.
    return hashlib.sha256(config.encode("utf-8")).hexdigest()


def pin_akmods_cache_image(image_tag: str, built_image_id: str) -> tuple[str, str]:
    """
    Resolve a mutable akmods cache tag to an immutable digest reference.

    Returns `(pinned_ref, digest)`. The bare digest is returned alongside the
    full ref because the signing job needs to sign this exact digest rather
    than re-resolve the tag, which another run can move.

    The tag is read after this run pushed it, so on its own it says only what
    the tag points at now. The digest is pinned only when its amd64 config is
    the one `built_image_id` names: the image this run built. Anything else is
    refused rather than pinned and signed.
    """

    expected = built_image_config_hex(built_image_id)
    digest = skopeo_inspect_digest(f"docker://{image_tag}")
    image_name = image_tag.rsplit(":", 1)[0]
    pinned_ref = f"{image_name}@{digest}"

    pushed = pushed_config_hex(pinned_ref)
    if pushed != expected:
        raise CiToolError(
            f"Refusing to pin akmods cache {pinned_ref}: its amd64 config is "
            f"sha256:{pushed}, but the image this run built is sha256:{expected}."
        )
    return pinned_ref, digest


def main() -> None:
    image_org = normalize_owner(require_env("GITHUB_REPOSITORY_OWNER"))
    fedora_version = require_env("FEDORA_VERSION")
    source_repo = require_env("AKMODS_REPO")

    image_tag = akmods_cache_image_tag(
        image_org=image_org,
        source_repo=source_repo,
        fedora_version=fedora_version,
    )
    # Read with optional_env so an unset or empty value reaches the refusal in
    # built_image_config_hex, which says what the value should have been.
    image_pinned, image_digest = pin_akmods_cache_image(
        image_tag, optional_env("AKMODS_BUILT_IMAGE_ID")
    )

    write_github_outputs(
        {
            "akmods_image": image_tag,
            "akmods_image_pinned": image_pinned,
            "akmods_image_digest": image_digest,
        }
    )

    print(f"Akmods cache tag checked or built: {image_tag}")
    print(f"Digest-pinned akmods cache image for final build: {image_pinned}")


if __name__ == "__main__":
    main()
