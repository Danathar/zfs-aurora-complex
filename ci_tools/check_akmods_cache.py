"""
Script: ci_tools/check_akmods_cache.py
What: Checks whether the shared akmods cache can be reused for the current base-image kernels.
Doing: Pulls cache image, unpacks layers, checks for matching `kmod-zfs` RPMs, then writes `exists=true|false`.
Why: Skip rebuild when safe, but rebuild when any required module set is stale.
Goal: Control rebuild decisions in main and validation workflows.
"""

from __future__ import annotations

from dataclasses import dataclass
import tempfile
from pathlib import Path

from ci_tools.common import (
    CiToolError,
    kernel_releases_from_env,
    load_layer_files_from_oci_layout,
    normalize_owner,
    require_env,
    skopeo_copy,
    skopeo_exists,
    unpack_layer_tarballs,
    write_github_outputs,
)


@dataclass(frozen=True)
class AkmodsCacheStatus:
    """
    Result of checking one shared akmods cache image against required kernels.

    `image_exists` tells us whether the source tag is present at all.
    `missing_releases` is the fail-closed list of kernels not covered by that
    image. A reusable cache must satisfy both conditions.
    """

    source_image: str
    image_exists: bool
    missing_releases: tuple[str, ...]

    @property
    def reusable(self) -> bool:
        """True only when the cache exists and covers every required kernel."""

        return self.image_exists and not self.missing_releases


def _has_kernel_matching_rpm(root_dir: Path, kernel_release: str) -> bool:
    # We only trust cache reuse when an RPM exists for this exact kernel string.
    # If the cache only has RPMs for older kernels, that cache is stale.
    rpm_dir = root_dir / "rpms" / "kmods" / "zfs"
    if not rpm_dir.exists():
        return False
    pattern = f"kmod-zfs-{kernel_release}-*.rpm"
    return any(rpm_dir.glob(pattern))


def _missing_kernel_releases(root_dir: Path, kernel_releases: list[str]) -> list[str]:
    """Return kernel releases that do not have a matching cached kmod RPM."""

    return [release for release in kernel_releases if not _has_kernel_matching_rpm(root_dir, release)]


def inspect_akmods_cache(
    *,
    image_org: str,
    source_repo: str,
    fedora_version: str,
    kernel_releases: list[str],
) -> AkmodsCacheStatus:
    """
    Inspect one shared akmods cache image and report whether it is reusable.

    This helper is shared by the main workflow and the read-only validation
    workflows so they all make the same cache-reuse decision.
    """

    source_image = f"ghcr.io/{image_org}/{source_repo}:main-{fedora_version}"
    if not skopeo_exists(f"docker://{source_image}"):
        return AkmodsCacheStatus(
            source_image=source_image,
            image_exists=False,
            missing_releases=tuple(kernel_releases),
        )

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        akmods_dir = root / "akmods"
        skopeo_copy(f"docker://{source_image}", f"dir:{akmods_dir}")

        layer_files = load_layer_files_from_oci_layout(akmods_dir)
        unpack_layer_tarballs(layer_files, root)

        missing_releases = _missing_kernel_releases(root, kernel_releases)
        return AkmodsCacheStatus(
            source_image=source_image,
            image_exists=True,
            missing_releases=tuple(missing_releases),
        )


def main() -> None:
    image_org = normalize_owner(require_env("GITHUB_REPOSITORY_OWNER"))
    fedora_version = require_env("FEDORA_VERSION")
    kernel_releases = kernel_releases_from_env()
    if not kernel_releases:
        raise CiToolError("Expected at least one kernel release from workflow env")
    source_repo = require_env("AKMODS_REPO")

    status = inspect_akmods_cache(
        image_org=image_org,
        source_repo=source_repo,
        fedora_version=fedora_version,
        kernel_releases=kernel_releases,
    )

    if not status.image_exists:
        write_github_outputs({"exists": "false"})
        print(f"No existing shared akmods cache image for Fedora {fedora_version}; rebuild is required.")
        return

    if status.reusable:
        write_github_outputs({"exists": "true"})
        print(
            f"Found matching {status.source_image} kmods for kernels {' '.join(kernel_releases)}; "
            "akmods rebuild can be skipped."
        )
        return

    write_github_outputs({"exists": "false"})
    print(
        f"Cached {status.source_image} is present but missing kmods for kernels "
        f"{' '.join(status.missing_releases)}; "
        "akmods rebuild is required."
    )


if __name__ == "__main__":
    main()
