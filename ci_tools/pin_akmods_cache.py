"""
Script: ci_tools/pin_akmods_cache.py
What: Resolves the shared akmods cache tag to a digest-pinned image reference.
Doing: Inspects `ghcr.io/<owner>/<repo>:main-<fedora>` and writes the mutable tag plus immutable digest ref to GitHub outputs.
Why: The final image build should consume the exact cache image that was checked or published earlier in the workflow.
Goal: Keep akmods cache reuse fail-closed while avoiding mutable-tag drift between jobs.
"""

from __future__ import annotations

from ci_tools.common import (
    normalize_owner,
    require_env,
    skopeo_inspect_digest,
    write_github_outputs,
)


def akmods_cache_image_tag(*, image_org: str, source_repo: str, fedora_version: str) -> str:
    """Return the shared akmods cache tag for one Fedora major version."""

    return f"ghcr.io/{image_org}/{source_repo}:main-{fedora_version}"


def pin_akmods_cache_image(image_tag: str) -> str:
    """Resolve a mutable akmods cache tag to an immutable digest reference."""

    digest = skopeo_inspect_digest(f"docker://{image_tag}")
    image_name = image_tag.rsplit(":", 1)[0]
    return f"{image_name}@{digest}"


def main() -> None:
    image_org = normalize_owner(require_env("GITHUB_REPOSITORY_OWNER"))
    fedora_version = require_env("FEDORA_VERSION")
    source_repo = require_env("AKMODS_REPO")

    image_tag = akmods_cache_image_tag(
        image_org=image_org,
        source_repo=source_repo,
        fedora_version=fedora_version,
    )
    image_pinned = pin_akmods_cache_image(image_tag)

    write_github_outputs(
        {
            "akmods_image": image_tag,
            "akmods_image_pinned": image_pinned,
        }
    )

    print(f"Akmods cache tag checked or built: {image_tag}")
    print(f"Digest-pinned akmods cache image for final build: {image_pinned}")


if __name__ == "__main__":
    main()
