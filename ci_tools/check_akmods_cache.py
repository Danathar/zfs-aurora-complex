"""
Script: ci_tools/check_akmods_cache.py
What: Checks whether the shared akmods cache can be reused for the current primary base-image kernel.
Doing: Pins and pulls the cache image, checks for a matching `kmod-zfs` RPM, then writes cache state outputs.
Why: Skip rebuild when safe, but rebuild when the required primary-kernel module set is missing or older than the current target kernel.
Goal: Control rebuild decisions in main and validation workflows.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from ci_tools.common import (
    CiToolError,
    normalize_owner,
    require_env,
    skopeo_copy,
    skopeo_inspect_json_optional,
    write_github_outputs,
)
from shared.oci_layout import load_layer_files_from_oci_layout, unpack_layer_tarballs


@dataclass(frozen=True)
class AkmodsCacheStatus:
    """
    Result of checking one shared akmods cache image against the required kernel.

    `image_exists` tells us whether the source tag is present at all.
    `source_image_pinned` is the exact image digest that was inspected.
    `missing_release` is the fail-closed kernel not covered by that image at
    the required ZFS line, and `required_zfs_minor_version` records which line
    that was so a rebuild reason can say why the cache was rejected.
    A reusable cache must satisfy both conditions.
    """

    source_image: str
    image_exists: bool
    source_image_pinned: str = ""
    missing_release: str = ""
    required_zfs_minor_version: str = ""
    inspection_method: str = "unpacked-image"

    @property
    def reusable(self) -> bool:
        """True only when the cache exists and covers the required kernel."""

        return self.image_exists and not self.missing_release


def _has_kernel_matching_rpm(root_dir: Path, kernel_release: str, zfs_minor_version: str) -> bool:
    # We only trust cache reuse when an RPM exists for this exact kernel string
    # *and* the ZFS minor line this run is configured to ship. If the cache only
    # has RPMs for older kernels, that cache is out of date; if it has the right
    # kernel but a different ZFS line, reusing it would silently publish an
    # image whose ZFS version disagrees with the resolved build inputs.
    #
    # Cached payloads are named
    # `kmod-zfs-<kernel_release>-<zfs_version>-<rel>.<dist>.<arch>.rpm`, for
    # example `kmod-zfs-7.1.4-204.fc44.x86_64-2.4.3-1.fc44.x86_64.rpm`. The
    # trailing dot after the minor line keeps `2.4` from matching a future
    # `2.41`; it does assume upstream keeps publishing `<minor>.<patch>`
    # releases rather than a bare `<minor>`, which OpenZFS has always done.
    rpm_dir = root_dir / "rpms" / "kmods" / "zfs"
    if not rpm_dir.exists():
        return False
    pattern = f"kmod-zfs-{kernel_release}-{zfs_minor_version}.*.rpm"
    return any(rpm_dir.glob(pattern))


def inspect_akmods_cache(
    *,
    image_org: str,
    source_repo: str,
    fedora_version: str,
    kernel_release: str,
    zfs_minor_version: str,
) -> AkmodsCacheStatus:
    """
    Inspect one shared akmods cache image and report whether it is reusable.

    This helper is shared by the main workflow and the read-only validation
    workflows so they all make the same cache-reuse decision.
    """

    source_image = f"ghcr.io/{image_org}/{source_repo}:main-{fedora_version}"
    inspect_json = skopeo_inspect_json_optional(f"docker://{source_image}")
    if inspect_json is None:
        return AkmodsCacheStatus(
            source_image=source_image,
            image_exists=False,
            missing_release=kernel_release,
            required_zfs_minor_version=zfs_minor_version,
            inspection_method="missing-image",
        )

    source_digest = str(inspect_json.get("Digest") or "")
    if not source_digest:
        raise CiToolError(f"Missing digest in skopeo inspect output for docker://{source_image}")

    source_image_pinned = f"ghcr.io/{image_org}/{source_repo}@{source_digest}"
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        akmods_dir = root / "akmods"
        skopeo_copy(f"docker://{source_image_pinned}", f"dir:{akmods_dir}")

        try:
            layer_files = load_layer_files_from_oci_layout(akmods_dir)
            unpack_layer_tarballs(layer_files, root)
        except RuntimeError as exc:
            raise CiToolError(str(exc)) from exc

        has_match = _has_kernel_matching_rpm(root, kernel_release, zfs_minor_version)
        return AkmodsCacheStatus(
            source_image=source_image,
            image_exists=True,
            source_image_pinned=source_image_pinned,
            missing_release="" if has_match else kernel_release,
            required_zfs_minor_version=zfs_minor_version,
            inspection_method="unpacked-image",
        )


def main() -> None:
    image_org = normalize_owner(require_env("GITHUB_REPOSITORY_OWNER"))
    fedora_version = require_env("FEDORA_VERSION")
    kernel_release = require_env("KERNEL_RELEASE")
    source_repo = require_env("AKMODS_REPO")
    zfs_minor_version = require_env("ZFS_MINOR_VERSION")

    status = inspect_akmods_cache(
        image_org=image_org,
        source_repo=source_repo,
        fedora_version=fedora_version,
        kernel_release=kernel_release,
        zfs_minor_version=zfs_minor_version,
    )

    if not status.image_exists:
        write_github_outputs({"exists": "false"})
        print(f"No existing shared akmods cache image for Fedora {fedora_version}; rebuild is required.")
        return

    if status.reusable:
        write_github_outputs(
            {
                "exists": "true",
                "akmods_image": status.source_image,
                "akmods_image_pinned": status.source_image_pinned,
            }
        )
        print(
            f"Found matching {status.source_image} kmods for primary kernel {kernel_release} "
            f"on the ZFS {zfs_minor_version} line; "
            f"akmods rebuild can be skipped. Inspection method: {status.inspection_method}."
        )
        print(f"Checked akmods cache digest: {status.source_image_pinned}")
        return

    write_github_outputs({"exists": "false"})
    print(
        f"Cached {status.source_image} is present but has no kmod-zfs for primary kernel "
        f"{status.missing_release} on the ZFS {zfs_minor_version} line; "
        "akmods rebuild is required."
    )


if __name__ == "__main__":
    main()
