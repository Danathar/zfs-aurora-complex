"""
Script: ci_tools/prepare_validation_build.py
What: Resolves inputs and verifies shared akmods cache for read-only validation runs.
Doing: Pins the same build inputs as `main`, writes them to step outputs, then fail-closes if the shared akmods source is missing or stale.
Why: Branch and PR workflows should validate the real production inputs without rebuilding or mutating the shared akmods cache.
Goal: Keep one shared preparation command for non-main workflows instead of duplicating YAML logic.
"""

from __future__ import annotations

from ci_tools.check_akmods_cache import inspect_akmods_cache
from ci_tools.common import CiToolError, normalize_owner, require_env
from ci_tools.resolve_build_inputs import resolve_build_inputs, write_resolved_build_outputs


def _shared_cache_failure_message(*, source_image: str, missing_releases: tuple[str, ...]) -> str:
    """
    Build one readable failure message for read-only validation workflows.

    The branch and PR paths intentionally do not rebuild the shared
    `zfs-kinoite-containerfile-akmods:main-<fedora>` tag. If that shared source
    is missing or stale, the correct repair action is to refresh it from the
    main workflow.
    """

    kernels_text = " ".join(missing_releases)
    return (
        f"Shared akmods source tag {source_image} is missing or stale for kernels {kernels_text}. "
        "Run main workflow (Build And Promote Main Image) with rebuild_akmods=true, "
        "then rerun this workflow."
    )


def main() -> None:
    image_org = normalize_owner(require_env("GITHUB_REPOSITORY_OWNER"))
    source_repo = require_env("AKMODS_REPO")

    resolution = resolve_build_inputs()
    inputs = resolution.inputs
    write_resolved_build_outputs(inputs)

    status = inspect_akmods_cache(
        image_org=image_org,
        source_repo=source_repo,
        fedora_version=inputs.version,
        kernel_releases=list(inputs.kernel_releases),
    )
    if not status.reusable:
        raise CiToolError(
            _shared_cache_failure_message(
                source_image=status.source_image,
                missing_releases=status.missing_releases,
            )
        )

    print(
        f"Read-only validation will reuse {status.source_image} for kernels "
        f"{' '.join(inputs.kernel_releases)}."
    )


if __name__ == "__main__":
    main()
