"""
Script: ci_tools/akmods_build_and_publish.py
What: Builds and publishes the ZFS akmods image from `/tmp/akmods`.
Doing: Pins the primary kernel info, writes the upstream `cache.json` file, and
builds one shared Fedora-wide cache image.
Why: Keeps the workflow logic in one tested file instead of repeated shell.
Goal: Publish the akmods cache image consumed by later build steps.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ci_tools.common import (
    CiToolError,
    optional_env,
    require_env,
    run_cmd,
    write_github_outputs,
)

AKMODS_WORKTREE = Path("/tmp/akmods")

# The architecture `just build` produces on the runner. The fork's Justfile
# tags the local image with `arch()`, which is `x86_64` on the hosted runners
# this workflow uses, and `pin_akmods_cache` checks the amd64 child of the
# pushed manifest list against the ID recorded here.
BUILT_IMAGE_ARCH = "x86_64"


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

    cache_json_path.parent.mkdir(parents=True, exist_ok=True)
    cache_json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"Pinned akmods kernel release to {kernel_release}")
    print(f"Using upstream akmods build root {upstream_build_root}")
    print(f"Seeded {cache_json_path}")


def record_built_image_id() -> str:
    """
    Record the ID of the image `just build` left in local storage, before it is pushed.

    The ID is the sha256 of the image config, and the config names every layer
    by its uncompressed digest, so it identifies the content this run built.
    `pin_akmods_cache` compares it with the config the shared tag resolves to
    after the push and refuses to pin anything else. Without it, the pin would
    record whatever the mutable tag pointed at by then, which another run
    holding a registry write token can move.
    """

    # Same name the fork's `push` recipe pushes from:
    # localhost/<akmods_name>:<kernel_flavor>-<version>-<arch>, where
    # akmods_name is the images.yaml `name` the configure step set to AKMODS_REPO.
    local_ref = (
        f"localhost/{require_env('AKMODS_REPO')}:"
        f"{require_env('AKMODS_KERNEL')}-{require_env('AKMODS_VERSION')}-{BUILT_IMAGE_ARCH}"
    )
    # A malformed or empty value is refused by `pin_akmods_cache`, which is the
    # step that acts on it.
    image_id = run_cmd(["podman", "image", "inspect", "--format", "{{.Id}}", local_ref]).strip()
    write_github_outputs({"built_image_id": image_id})
    print(f"Recorded built akmods image {local_ref} with ID {image_id}")
    return image_id


def build_and_push_kernel_release(kernel_release: str) -> None:
    """
    Build and push one akmods payload for the supported primary kernel.

    This repo intentionally supports only the kernel that the image is expected
    to boot first. Recovery from a bad image is handled by image rollback, not
    by keeping additional bundled kernels ZFS-ready inside the same image.
    """
    print(f"Building akmods for kernel release: {kernel_release}")
    write_kernel_cache_file(kernel_release=kernel_release)

    # Upstream tooling reads the cache.json file we just wrote and publishes the
    # shared Fedora-wide tag plus the architecture-specific inspection tag.
    run_cmd(["just", "build"], cwd=str(AKMODS_WORKTREE), capture_output=False)
    # Taken between build and push, so it names what this run built rather
    # than anything the registry says afterwards.
    record_built_image_id()
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


if __name__ == "__main__":
    main()
