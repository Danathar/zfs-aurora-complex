"""
Script: ci_tools/akmods_build_and_publish.py
What: Builds and publishes the ZFS akmods image from `/tmp/akmods`.
Doing: Optionally pins kernel info, publishes per-kernel akmods payloads, and
merges them into one shared Fedora-wide cache image when the base carries more
than one installed kernel.
Why: Keeps the workflow logic in one tested file instead of repeated shell.
Goal: Publish the akmods cache images consumed by later build steps.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from ci_tools.common import (
    CiToolError,
    kernel_releases_from_env,
    load_layer_files_from_oci_layout,
    normalize_owner,
    optional_env,
    require_env,
    run_cmd,
    skopeo_copy,
    sort_kernel_releases,
    unpack_layer_tarballs,
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
    shared_cache_path: bool,
) -> tuple[dict[str, str], Path, Path]:
    """
    Build the cache JSON payload and destination path used by akmods tooling.

    Return value is a tuple:
    1. `payload` (dict): JSON fields that upstream scripts read.
    2. `cache_json_path` (Path): where that JSON should be written.
    3. `upstream_build_root` (Path): directory to export as `AKMODS_BUILDDIR`.
    """
    # Upstream Justfile derives `KCWD` and `KCPATH` from `AKMODS_BUILDDIR`.
    # To isolate multi-kernel runs, we must change that upstream build root, not
    # only the JSON file location we seed here.
    if shared_cache_path:
        upstream_build_root = build_root
    else:
        upstream_build_root = build_root / kernel_release
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


def write_kernel_cache_file(*, kernel_release: str, shared_cache_path: bool) -> None:
    # When kernel pinning is enabled, these values must also be set.
    kernel_flavor = require_env("AKMODS_KERNEL")
    akmods_version = require_env("AKMODS_VERSION")

    # Allow override paths from env, but keep a stable default layout.
    build_root_default = str(AKMODS_WORKTREE / "build")
    build_root = Path(optional_env("AKMODS_BUILDDIR", build_root_default))
    kcpath_override = optional_env("KCPATH")
    if kcpath_override and not shared_cache_path:
        raise CiToolError(
            "Multi-kernel akmods rebuild cannot reuse a fixed KCPATH override. "
            "Unset KCPATH so each kernel gets its own upstream build root."
        )

    # Build both the JSON object and output file path from one helper function.
    payload, cache_json_path, upstream_build_root = build_kernel_cache_document(
        kernel_release=kernel_release,
        kernel_flavor=kernel_flavor,
        akmods_version=akmods_version,
        build_root=build_root,
        kcpath_override=kcpath_override,
        shared_cache_path=shared_cache_path,
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


def build_and_push_kernel_release(kernel_release: str, *, shared_cache_path: bool) -> None:
    """
    Build and push one kernel-specific akmods payload.

    Multi-kernel rebuilds use isolated cache paths per kernel. Upstream akmods
    expects one kernel set per cache directory; mixing two kernels in one cache
    path causes package collisions during the later build.
    """
    print(f"Building akmods for kernel release: {kernel_release}")
    write_kernel_cache_file(
        kernel_release=kernel_release,
        shared_cache_path=shared_cache_path,
    )

    # Upstream tooling reads the cache metadata we just wrote and publishes the
    # kernel-specific tag plus an architecture tag. In the multi-kernel path we
    # later assemble the shared Fedora-wide tag ourselves from those per-kernel
    # images, because upstream's shared-cache flow assumes one kernel per build.
    run_cmd(["just", "build"], cwd=str(AKMODS_WORKTREE), capture_output=False)
    run_cmd(["just", "push"], cwd=str(AKMODS_WORKTREE), capture_output=False)


def merged_cache_missing_kernel_releases(
    *,
    merged_root: Path,
    kernel_releases: list[str],
) -> list[str]:
    """
    Return kernel releases whose `kmod-zfs` RPM is missing from the merged root.

    The merged shared cache image must carry a `kmod-zfs-<kernel_release>-...`
    RPM for every kernel shipped in the base image. This check keeps the custom
    merge step fail-closed before we publish a broken shared cache tag.
    """
    rpm_dir = merged_root / "rpms" / "kmods" / "zfs"
    if not rpm_dir.exists():
        return list(kernel_releases)

    present_names = {path.name for path in rpm_dir.glob("kmod-zfs-*.rpm")}
    missing: list[str] = []
    for kernel_release in kernel_releases:
        expected_prefix = f"kmod-zfs-{kernel_release}-"
        if not any(name.startswith(expected_prefix) for name in present_names):
            missing.append(kernel_release)
    return missing


def merge_and_push_shared_cache_image(*, kernel_releases: list[str]) -> None:
    """
    Build and push one shared cache image that contains RPMs for every kernel.

    Upstream akmods can publish correct per-kernel images, but its shared-cache
    layout assumes one kernel per cache directory. We therefore merge the local
    per-kernel images into one scratch image ourselves and publish that as the
    Fedora-wide `main-<fedora>` tag consumed by later workflow steps.
    """
    kernel_flavor = require_env("AKMODS_KERNEL")
    akmods_version = require_env("AKMODS_VERSION")
    akmods_repo = require_env("AKMODS_REPO")
    image_org = normalize_owner(require_env("GITHUB_REPOSITORY_OWNER"))
    arch = run_cmd(["uname", "-m"]).strip()

    shared_tag = shared_cache_tag(kernel_flavor=kernel_flavor, akmods_version=akmods_version)
    local_shared_ref = f"localhost/{akmods_repo}:{shared_tag}"
    local_shared_arch_ref = f"{local_shared_ref}-{arch}"

    with TemporaryDirectory(prefix="akmods-merge-") as tempdir:
        build_context = Path(tempdir)

        for kernel_release in kernel_releases:
            image_dir = build_context / f"image-{kernel_release}"
            source_ref = (
                f"containers-storage:localhost/{akmods_repo}:"
                f"{kernel_flavor}-{akmods_version}-{kernel_release}"
            )
            # `containers-storage:` reads the image we just built locally.
            # We unpack those local images and then republish one merged result.
            skopeo_copy(source_ref, f"dir:{image_dir}")
            layer_files = load_layer_files_from_oci_layout(image_dir)
            unpack_layer_tarballs(layer_files, build_context)

        missing = merged_cache_missing_kernel_releases(
            merged_root=build_context,
            kernel_releases=kernel_releases,
        )
        if missing:
            raise CiToolError(
                "Merged shared akmods cache is missing kernel RPMs for: "
                + ", ".join(missing)
            )

        containerfile = build_context / "Containerfile"
        containerfile.write_text(
            "FROM scratch\n"
            "COPY kernel-rpms /kernel-rpms\n"
            "COPY rpms /rpms\n",
            encoding="utf-8",
        )

        # Tag both the shared Fedora-wide ref and the architecture-specific ref.
        # The workflow consumes `main-<fedora>`, while `main-<fedora>-x86_64`
        # stays available for direct inspection and parity with upstream naming.
        run_cmd(
            [
                "podman",
                "build",
                "-f",
                str(containerfile),
                "-t",
                local_shared_ref,
                "-t",
                local_shared_arch_ref,
                str(build_context),
            ],
            capture_output=False,
        )
        run_cmd(
            [
                "podman",
                "push",
                local_shared_arch_ref,
                f"docker://ghcr.io/{image_org}/{akmods_repo}:{shared_tag}-{arch}",
            ],
            capture_output=False,
        )
        run_cmd(
            [
                "podman",
                "push",
                local_shared_ref,
                f"docker://ghcr.io/{image_org}/{akmods_repo}:{shared_tag}",
            ],
            capture_output=False,
        )

    print(
        "Published merged shared akmods cache: "
        f"ghcr.io/{image_org}/{akmods_repo}:{shared_tag}"
    )
    publish_shared_cache_metadata(
        image_org=image_org,
        akmods_repo=akmods_repo,
        kernel_flavor=kernel_flavor,
        akmods_version=akmods_version,
        kernel_releases=kernel_releases,
    )


def main() -> None:
    # All akmods commands run from /tmp/akmods after the clone step.
    if not AKMODS_WORKTREE.exists():
        raise CiToolError(f"Expected akmods checkout at {AKMODS_WORKTREE}")

    # Keep a stable, de-duplicated order even if env input is messy.
    # This avoids redundant builds and keeps logs deterministic.
    kernel_releases = sort_kernel_releases(kernel_releases_from_env())
    if not kernel_releases:
        # If no explicit kernel list is provided, keep default upstream behavior.
        run_cmd(["just", "build"], cwd=str(AKMODS_WORKTREE), capture_output=False)
        run_cmd(["just", "login"], cwd=str(AKMODS_WORKTREE), capture_output=False)
        run_cmd(["just", "push"], cwd=str(AKMODS_WORKTREE), capture_output=False)
        run_cmd(["just", "manifest"], cwd=str(AKMODS_WORKTREE), capture_output=False)
        return

    if len(kernel_releases) == 1:
        run_cmd(["just", "login"], cwd=str(AKMODS_WORKTREE), capture_output=False)
        build_and_push_kernel_release(
            kernel_releases[0],
            shared_cache_path=True,
        )
        run_cmd(["just", "manifest"], cwd=str(AKMODS_WORKTREE), capture_output=False)
        publish_shared_cache_metadata(
            image_org=normalize_owner(require_env("GITHUB_REPOSITORY_OWNER")),
            akmods_repo=require_env("AKMODS_REPO"),
            kernel_flavor=require_env("AKMODS_KERNEL"),
            akmods_version=require_env("AKMODS_VERSION"),
            kernel_releases=kernel_releases,
        )
        return

    # Authenticate once, then publish one kernel-specific payload at a time.
    # This keeps the loop readable in logs and avoids repeated login churn.
    #
    # Disable Buildah layer caching for the multi-kernel loop. Each iteration
    # binds in a different host-side cache directory, and reusing image layers
    # across those builds can stamp newer labels onto older earlier-kernel RPMs.
    os.environ["BUILDAH_LAYERS"] = "false"
    print("Disabled Buildah layer cache for multi-kernel akmods rebuild.")
    run_cmd(["just", "login"], cwd=str(AKMODS_WORKTREE), capture_output=False)
    for kernel_release in kernel_releases:
        build_and_push_kernel_release(
            kernel_release,
            shared_cache_path=False,
        )

    merge_and_push_shared_cache_image(kernel_releases=kernel_releases)


if __name__ == "__main__":
    main()
