"""
Script: ci_tools/akmods_cache_metadata.py
What: Shared helpers for the akmods cache metadata image.
Doing: Defines metadata labels, metadata tag names, label parsing, and metadata-image publishing.
Why: Main, branch, and pull request cache checks should not need to unpack the full shared cache image on the fast path.
Goal: Keep cache-reuse decisions cheap and explicit while preserving a backward-compatible fallback path.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Mapping

from ci_tools.common import CiToolError, run_cmd, sort_kernel_releases


AKMODS_CACHE_KERNEL_RELEASES_LABEL = "org.danathar.zfs-kinoite.akmods.kernel-releases"
AKMODS_CACHE_FEDORA_VERSION_LABEL = "org.danathar.zfs-kinoite.akmods.fedora-version"
AKMODS_CACHE_SOURCE_TAG_LABEL = "org.danathar.zfs-kinoite.akmods.source-tag"


def shared_cache_tag(*, kernel_flavor: str, akmods_version: str) -> str:
    """Return the shared cache tag consumed by later image-build steps."""
    return f"{kernel_flavor}-{akmods_version}"


def shared_cache_metadata_tag(*, kernel_flavor: str, akmods_version: str) -> str:
    """Return the metadata tag paired with one shared cache tag."""
    return f"{shared_cache_tag(kernel_flavor=kernel_flavor, akmods_version=akmods_version)}-metadata"


def metadata_labels(*, kernel_flavor: str, akmods_version: str, kernel_releases: list[str]) -> dict[str, str]:
    """
    Return image labels written onto the metadata tag image.

    We sort and de-duplicate the kernel list so every publisher writes the same
    label string for the same logical kernel set.
    """
    normalized_kernel_releases = sort_kernel_releases(kernel_releases)
    if not normalized_kernel_releases:
        raise CiToolError("Cannot publish akmods cache metadata without any kernel releases")

    return {
        AKMODS_CACHE_KERNEL_RELEASES_LABEL: " ".join(normalized_kernel_releases),
        AKMODS_CACHE_FEDORA_VERSION_LABEL: akmods_version,
        AKMODS_CACHE_SOURCE_TAG_LABEL: shared_cache_tag(
            kernel_flavor=kernel_flavor,
            akmods_version=akmods_version,
        ),
    }


def parse_kernel_releases_from_labels(labels: Mapping[str, object]) -> tuple[str, ...]:
    """
    Parse the cached kernel list from metadata image labels.

    The metadata image exists purely so `skopeo inspect` can answer whether the
    shared akmods cache covers the current base-kernel set. Missing or malformed
    labels should therefore stop the fast path and trigger the slower fallback path.
    """
    kernel_release_string = str(labels.get(AKMODS_CACHE_KERNEL_RELEASES_LABEL) or "").strip()
    if not kernel_release_string:
        raise CiToolError(
            f"Metadata labels missing required key: {AKMODS_CACHE_KERNEL_RELEASES_LABEL}"
        )
    return tuple(sort_kernel_releases(kernel_release_string.split()))


def publish_shared_cache_metadata(
    *,
    image_org: str,
    akmods_repo: str,
    kernel_flavor: str,
    akmods_version: str,
    kernel_releases: list[str],
) -> None:
    """
    Build and push the metadata image for one shared akmods cache tag.

    This metadata image is intentionally tiny. Its only job is to carry explicit
    labels describing which kernel releases the matching shared cache image
    covers, so future cache checks can read those labels first instead of
    unpacking the whole cache image every time.
    """
    labels = metadata_labels(
        kernel_flavor=kernel_flavor,
        akmods_version=akmods_version,
        kernel_releases=kernel_releases,
    )
    metadata_tag = shared_cache_metadata_tag(
        kernel_flavor=kernel_flavor,
        akmods_version=akmods_version,
    )
    local_ref = f"localhost/{akmods_repo}:{metadata_tag}"
    remote_ref = f"docker://ghcr.io/{image_org}/{akmods_repo}:{metadata_tag}"

    with TemporaryDirectory(prefix="akmods-cache-metadata-") as tempdir:
        build_context = Path(tempdir)
        metadata_json = build_context / "metadata.json"
        metadata_json.write_text(
            json.dumps(
                {
                    "kernel_flavor": kernel_flavor,
                    "fedora_version": akmods_version,
                    "kernel_releases": sort_kernel_releases(kernel_releases),
                    "source_tag": shared_cache_tag(
                        kernel_flavor=kernel_flavor,
                        akmods_version=akmods_version,
                    ),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        containerfile = build_context / "Containerfile"
        containerfile.write_text(
            "FROM scratch\n"
            "COPY metadata.json /metadata.json\n",
            encoding="utf-8",
        )

        build_command = [
            "podman",
            "build",
            "-f",
            str(containerfile),
            "-t",
            local_ref,
        ]
        for key, value in labels.items():
            build_command.extend(["--label", f"{key}={value}"])
        build_command.append(str(build_context))
        run_cmd(build_command, capture_output=False)
        run_cmd(["podman", "push", local_ref, remote_ref], capture_output=False)

    print(
        "Published akmods cache metadata tag: "
        f"ghcr.io/{image_org}/{akmods_repo}:{metadata_tag}"
    )
