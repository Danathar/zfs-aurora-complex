"""
Script: ci_tools/prepare_validation_build.py
What: Resolves inputs and verifies the shared akmods cache for read-only validation runs.
Doing: Pins the same build inputs as `main`, writes them to step outputs, then stops with an error if the shared akmods source is missing or no longer matches the required kernels.
Why: Branch and pull request workflows should validate the real production inputs without rebuilding or changing the shared akmods cache.
Goal: Keep one shared preparation command for non-main workflows instead of duplicating YAML logic.
"""

from __future__ import annotations

from ci_tools.akmods_clone_pinned import clone_pinned
from ci_tools.check_akmods_cache import inspect_akmods_cache
from ci_tools.common import (
    CiToolError,
    normalize_owner,
    require_env,
    require_env_or_default,
    write_github_outputs,
)
from ci_tools.resolve_build_inputs import resolve_build_inputs, write_resolved_build_outputs


def main() -> None:
    image_org = normalize_owner(require_env("GITHUB_REPOSITORY_OWNER"))
    source_repo = require_env("AKMODS_REPO")

    resolution = resolve_build_inputs()
    inputs = resolution.inputs
    write_resolved_build_outputs(inputs)

    # Validation builds usually reuse the shared akmods cache, so without this
    # explicit clone they would never prove that the resolved akmods source
    # commit is still fetchable. Running the same clone/verify step here keeps
    # branch and pull request paths honest with the main schedule/rebuild path.
    upstream_repo = require_env_or_default("AKMODS_UPSTREAM_REPO")
    clone_pinned(upstream_repo, inputs.akmods_upstream_ref)

    status = inspect_akmods_cache(
        image_org=image_org,
        source_repo=source_repo,
        fedora_version=inputs.version,
        kernel_release=inputs.kernel_release,
        zfs_version=inputs.zfs_version,
    )
    if not status.reusable:
        # Branch/PR paths intentionally do not rebuild the shared akmods tag;
        # the repair action is a main-workflow rebuild_akmods run.
        #
        # Say which of the two checks actually rejected the cache. Reporting
        # the kernel unconditionally is misleading when the content was fine
        # and only the signature failed: `missing_release` is empty in that
        # case, so the message read "does not cover the supported primary
        # kernel <blank>" and pointed at the wrong problem entirely.
        if not status.image_exists:
            reason = "is missing from the registry"
        elif status.missing_release:
            reason = (
                f"does not cover the supported primary kernel {status.missing_release} "
                f"at ZFS version {inputs.zfs_version}"
            )
        else:
            reason = (
                f"({status.source_image_pinned}) has the required kmod-zfs but its cosign "
                "signature could not be verified against cosign.pub. Expected the first "
                "time this check runs after cache signing was introduced, because the "
                "already-published cache predates it"
            )
        raise CiToolError(
            f"Shared akmods source tag {status.source_image} {reason}. "
            "Run main workflow (Build And Promote Main Image) with rebuild_akmods=true, "
            "then rerun this workflow."
        )

    write_github_outputs(
        {
            "akmods_image": status.source_image,
            "akmods_image_pinned": status.source_image_pinned,
        }
    )
    print(
        f"Read-only validation will reuse {status.source_image} for primary kernel "
        f"{inputs.kernel_release}."
    )
    print(f"Digest-pinned akmods cache image for validation build: {status.source_image_pinned}")


if __name__ == "__main__":
    main()
