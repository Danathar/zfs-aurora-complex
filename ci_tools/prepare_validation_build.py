"""
Script: ci_tools/prepare_validation_build.py
What: Resolves inputs and verifies the shared akmods cache for read-only validation runs.
Doing: Pins the same build inputs as `main`, writes them to step outputs, then stops with an error if the shared akmods source is missing or no longer matches the required kernels.
Why: Branch and pull request workflows should validate the real production inputs without rebuilding or changing the shared akmods cache.
Goal: Keep one shared preparation command for non-main workflows instead of duplicating YAML logic.
"""

from __future__ import annotations

from ci_tools.akmods_clone_pinned import main as clone_pinned_akmods
from ci_tools.check_akmods_cache import inspect_akmods_cache
from ci_tools.common import CiToolError, normalize_owner, require_env
from ci_tools.resolve_build_inputs import resolve_build_inputs, write_resolved_build_outputs


def _shared_cache_failure_message(*, source_image: str, missing_release: str) -> str:
    """
    Build one readable failure message for read-only validation workflows.

    The branch and pull request paths intentionally do not rebuild the shared
    `zfs-kinoite-containerfile-akmods:main-<fedora>` tag. If that shared source
    is missing or no longer matches the required primary kernel, the correct repair
    action is to refresh it from the main workflow.
    """

    return (
        f"Shared akmods source tag {source_image} is missing or does not cover the supported primary kernel {missing_release}. "
        "Run main workflow (Build And Promote Main Image) with rebuild_akmods=true, "
        "then rerun this workflow."
    )


def main() -> None:
    image_org = normalize_owner(require_env("GITHUB_REPOSITORY_OWNER"))
    source_repo = require_env("AKMODS_REPO")

    resolution = resolve_build_inputs()
    inputs = resolution.inputs
    write_resolved_build_outputs(inputs)

    # Validation builds usually reuse the shared akmods cache, so without this
    # explicit clone they would never prove that the pinned akmods fork commit is
    # still fetchable. Running the same clone/verify step here keeps branch and
    # pull request paths honest with the main schedule/rebuild path.
    clone_pinned_akmods()

    status = inspect_akmods_cache(
        image_org=image_org,
        source_repo=source_repo,
        fedora_version=inputs.version,
        kernel_release=inputs.kernel_release,
    )
    if not status.reusable:
        raise CiToolError(
            _shared_cache_failure_message(
                source_image=status.source_image,
                missing_release=status.missing_release,
            )
        )

    print(
        f"Read-only validation will reuse {status.source_image} for primary kernel "
        f"{inputs.kernel_release}."
    )


if __name__ == "__main__":
    main()
