"""
Script: ci_tools/publish_akmods_cache_metadata.py
What: Publishes the metadata tag for an existing or newly built shared akmods cache.
Doing: Optionally logs in to GitHub Container Registry (GHCR), then builds and pushes a tiny metadata image that records the covered kernel releases.
Why: Later cache checks can inspect this metadata tag instead of unpacking the full shared cache image.
Goal: Keep shared-cache reuse checks fast while remaining backward-compatible with older tags.
"""

from __future__ import annotations

from ci_tools.akmods_cache_metadata import publish_shared_cache_metadata
from ci_tools.common import normalize_owner, optional_env, require_env, require_env_or_default, run_cmd


def main() -> None:
    kernel_releases = require_env("KERNEL_RELEASES").split()
    image_org = normalize_owner(require_env("GITHUB_REPOSITORY_OWNER"))
    akmods_repo = optional_env("AKMODS_REPO") or require_env_or_default("AKMODS_REPO")
    kernel_flavor = optional_env("AKMODS_KERNEL", "main")
    akmods_version = require_env("AKMODS_VERSION")

    # Main rebuilds are already logged in through upstream akmods tooling, but a
    # metadata-backfill step may run on a reused cache image. In that case we do
    # one explicit registry login here before pushing the metadata tag.
    registry_actor = optional_env("REGISTRY_ACTOR")
    registry_token = optional_env("REGISTRY_TOKEN")
    if registry_actor and registry_token:
        run_cmd(
            [
                "podman",
                "login",
                "ghcr.io",
                "--username",
                registry_actor,
                "--password",
                registry_token,
            ],
            capture_output=False,
        )

    publish_shared_cache_metadata(
        image_org=image_org,
        akmods_repo=akmods_repo,
        kernel_flavor=kernel_flavor,
        akmods_version=akmods_version,
        kernel_releases=kernel_releases,
    )


if __name__ == "__main__":
    main()
