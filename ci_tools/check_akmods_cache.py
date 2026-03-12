"""
Script: ci_tools/check_akmods_cache.py
What: Checks whether the shared akmods cache can be reused for the current primary base-image kernel.
Doing: Pulls the cache image, unpacks layers when needed, checks for a matching `kmod-zfs` RPM, then writes `exists=true|false`.
Why: Skip rebuild when safe, but rebuild when the required primary-kernel module set is missing or older than the current target kernel.
Goal: Control rebuild decisions in main and validation workflows.
"""

from __future__ import annotations

from dataclasses import dataclass
import tempfile
from pathlib import Path

from ci_tools.common import (
    CiToolError,
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
    Result of checking one shared akmods cache image against the required kernel.

    `image_exists` tells us whether the source tag is present at all.
    `missing_release` is the fail-closed kernel not covered by that image.
    A reusable cache must satisfy both conditions.
    """

    source_image: str
    image_exists: bool
    missing_release: str = ""
    inspection_method: str = "unpacked-image"

    @property
    def reusable(self) -> bool:
        """True only when the cache exists and covers the required kernel."""

        return self.image_exists and not self.missing_release


def _has_kernel_matching_rpm(root_dir: Path, kernel_release: str) -> bool:
    # We only trust cache reuse when an RPM exists for this exact kernel string.
    # If the cache only has RPMs for older kernels, that cache is out of date.
    rpm_dir = root_dir / "rpms" / "kmods" / "zfs"
    if not rpm_dir.exists():
        return False
    pattern = f"kmod-zfs-{kernel_release}-*.rpm"
    return any(rpm_dir.glob(pattern))


def inspect_akmods_cache(
    *,
    image_org: str,
    source_repo: str,
    fedora_version: str,
    kernel_release: str,
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
            missing_release=kernel_release,
            inspection_method="missing-image",
        )

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        akmods_dir = root / "akmods"
        skopeo_copy(f"docker://{source_image}", f"dir:{akmods_dir}")

        try:
            layer_files = load_layer_files_from_oci_layout(akmods_dir)
            unpack_layer_tarballs(layer_files, root)
        except RuntimeError as exc:
            raise CiToolError(str(exc)) from exc

        missing_release = "" if _has_kernel_matching_rpm(root, kernel_release) else kernel_release
        return AkmodsCacheStatus(
            source_image=source_image,
            image_exists=True,
            missing_release=missing_release,
            inspection_method="unpacked-image",
        )


def main() -> None:
    image_org = normalize_owner(require_env("GITHUB_REPOSITORY_OWNER"))
    fedora_version = require_env("FEDORA_VERSION")
    kernel_release = require_env("KERNEL_RELEASE")
    source_repo = require_env("AKMODS_REPO")

    status = inspect_akmods_cache(
        image_org=image_org,
        source_repo=source_repo,
        fedora_version=fedora_version,
        kernel_release=kernel_release,
    )

    if not status.image_exists:
        write_github_outputs({"exists": "false"})
        print(f"No existing shared akmods cache image for Fedora {fedora_version}; rebuild is required.")
        return

    if status.reusable:
        write_github_outputs({"exists": "true"})
        print(
            f"Found matching {status.source_image} kmods for primary kernel {kernel_release}; "
            f"akmods rebuild can be skipped. Inspection method: {status.inspection_method}."
        )
        return

    write_github_outputs({"exists": "false"})
    print(
        f"Cached {status.source_image} is present but missing kmods for primary kernel "
        f"{status.missing_release}; "
        "akmods rebuild is required."
    )


if __name__ == "__main__":
    main()
