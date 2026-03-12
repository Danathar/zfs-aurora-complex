"""
Script: ci_tools/akmods_build_and_publish.py
What: Builds and publishes the ZFS akmods image from `/tmp/akmods`.
Doing: Pins the primary kernel info, builds one shared Fedora-wide cache image,
and publishes metadata describing that supported kernel.
Why: Keeps the workflow logic in one tested file instead of repeated shell.
Goal: Publish the akmods cache image consumed by later build steps.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ci_tools.common import (
    CiToolError,
    normalize_owner,
    optional_env,
    require_env,
    run_cmd,
)
from ci_tools.akmods_cache_metadata import publish_shared_cache_metadata, shared_cache_tag


AKMODS_WORKTREE = Path("/tmp/akmods")


def kernel_name_for_flavor(kernel_flavor: str) -> str:
    """
    Map a kernel flavor name to the package base name expected by akmods tooling.

    Current rule in upstream scripts:
    - flavors starting with `longterm` use `kernel-longterm`
    - all others use `kernel`
    """
    if kernel_flavor.startswith("longterm"):
        return "kernel-longterm"
    return "kernel"


def kernel_major_minor_patch(kernel_release: str) -> str:
    """Keep only the first three dot-separated parts of the kernel release."""
    return ".".join(kernel_release.split(".")[:3])


def build_kernel_cache_document(
    *,
    kernel_release: str,
    kernel_flavor: str,
    akmods_version: str,
    build_root: Path,
    kcpath_override: str,
) -> tuple[dict[str, str], Path, Path]:
    """
    Build the cache JSON payload and destination path used by akmods tooling.

    Return value is a tuple:
    1. `payload` (dict): JSON fields that upstream scripts read.
    2. `cache_json_path` (Path): where that JSON should be written.
    3. `upstream_build_root` (Path): directory to export as `AKMODS_BUILDDIR`.
    """
    # Upstream Justfile derives `KCWD` and `KCPATH` from `AKMODS_BUILDDIR`.
    # In the primary-kernel-only model, one build root per workflow run is
    # enough because we no longer iterate over several kernel payloads here.
    upstream_build_root = build_root
    build_id = f"{kernel_flavor}-{akmods_version}"
    # KCWD/KCPATH names are expected by upstream akmods scripts.
    kcwd = upstream_build_root / build_id / "KCWD"
    kcpath = Path(kcpath_override) if kcpath_override else (kcwd / "rpms")
    cache_json_path = kcpath / "cache.json"

    # This object becomes cache.json.
    # Keeping it as a dict makes the structure explicit and easy to test.
    payload = {
        "kernel_build_tag": "",
        "kernel_flavor": kernel_flavor,
        "kernel_major_minor_patch": kernel_major_minor_patch(kernel_release),
        "kernel_release": kernel_release,
        "kernel_name": kernel_name_for_flavor(kernel_flavor),
        "KCWD": str(kcwd),
        "KCPATH": str(kcpath),
    }
    return payload, cache_json_path, upstream_build_root


def write_kernel_cache_file(*, kernel_release: str) -> None:
    # When kernel pinning is enabled, these values must also be set.
    kernel_flavor = require_env("AKMODS_KERNEL")
    akmods_version = require_env("AKMODS_VERSION")

    # Allow override paths from env, but keep a stable default layout.
    build_root_default = str(AKMODS_WORKTREE / "build")
    build_root = Path(optional_env("AKMODS_BUILDDIR", build_root_default))
    kcpath_override = optional_env("KCPATH")

    # Build both the JSON object and output file path from one helper function.
    payload, cache_json_path, upstream_build_root = build_kernel_cache_document(
        kernel_release=kernel_release,
        kernel_flavor=kernel_flavor,
        akmods_version=akmods_version,
        build_root=build_root,
        kcpath_override=kcpath_override,
    )

    # Upstream Justfile computes `KCWD`/`KCPATH` from `AKMODS_BUILDDIR`.
    # Export the per-kernel build root so the later `just build`/`just push`
    # commands really use the isolated path we just calculated.
    os.environ["AKMODS_BUILDDIR"] = str(upstream_build_root)
    if kcpath_override:
        os.environ["KCPATH"] = kcpath_override
    else:
        os.environ.pop("KCPATH", None)

    cache_json_path.parent.mkdir(parents=True, exist_ok=True)
    cache_json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"Pinned akmods kernel release to {kernel_release}")
    print(f"Using upstream akmods build root {upstream_build_root}")
    print(f"Seeded {cache_json_path}")


def build_and_push_kernel_release(kernel_release: str) -> None:
    """
    Build and push one akmods payload for the supported primary kernel.

    This repo intentionally supports only the kernel that the image is expected
    to boot first. Recovery from a bad image is handled by image rollback, not
    by keeping additional bundled kernels ZFS-ready inside the same image.
    """
    print(f"Building akmods for kernel release: {kernel_release}")
    write_kernel_cache_file(kernel_release=kernel_release)

    # Upstream tooling reads the cache metadata we just wrote and publishes the
    # shared Fedora-wide tag plus the architecture-specific inspection tag.
    run_cmd(["just", "build"], cwd=str(AKMODS_WORKTREE), capture_output=False)
    run_cmd(["just", "push"], cwd=str(AKMODS_WORKTREE), capture_output=False)


def main() -> None:
    # All akmods commands run from /tmp/akmods after the clone step.
    if not AKMODS_WORKTREE.exists():
        raise CiToolError(f"Expected akmods checkout at {AKMODS_WORKTREE}")

    kernel_release = optional_env("KERNEL_RELEASE")
    if not kernel_release:
        # If no explicit kernel is provided, keep default upstream behavior.
        run_cmd(["just", "build"], cwd=str(AKMODS_WORKTREE), capture_output=False)
        run_cmd(["just", "login"], cwd=str(AKMODS_WORKTREE), capture_output=False)
        run_cmd(["just", "push"], cwd=str(AKMODS_WORKTREE), capture_output=False)
        run_cmd(["just", "manifest"], cwd=str(AKMODS_WORKTREE), capture_output=False)
        return

    print(
        "Building akmods only for the supported primary kernel. "
        "Recovery from a bad image is handled by image rollback."
    )
    run_cmd(["just", "login"], cwd=str(AKMODS_WORKTREE), capture_output=False)
    build_and_push_kernel_release(kernel_release)
    run_cmd(["just", "manifest"], cwd=str(AKMODS_WORKTREE), capture_output=False)
    publish_shared_cache_metadata(
        image_org=normalize_owner(require_env("GITHUB_REPOSITORY_OWNER")),
        akmods_repo=require_env("AKMODS_REPO"),
        kernel_flavor=require_env("AKMODS_KERNEL"),
        akmods_version=require_env("AKMODS_VERSION"),
        kernel_releases=[kernel_release],
    )


if __name__ == "__main__":
    main()
