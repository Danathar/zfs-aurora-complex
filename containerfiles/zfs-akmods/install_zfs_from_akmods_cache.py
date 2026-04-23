#!/usr/bin/env python3
"""
Script: containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py
What: Install ZFS RPMs (Red Hat Package Manager package files) from the self-hosted akmods cache into the image build root.
Doing: Pulls the shared akmods image, maps each `kmod-zfs` RPM to a kernel release, and installs the RPMs needed for the supported primary kernel.
Why: The repo intentionally fails closed on the kernel the image is expected to boot first, then relies on image rollback instead of keeping extra bundled kernels ZFS-ready inside the same image.
Goal: Keep the image build logic explicit while reducing the older multi-kernel fallback complexity.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


# The build copies repo helper modules into `/shared`, but Python started with
# `python3 /containerfiles/.../install_zfs_from_akmods_cache.py` only adds the
# script directory to `sys.path`. Add the image root explicitly so the shared
# helper package is importable both in CI tests and inside the built image.
IMAGE_ROOT = Path(__file__).resolve().parents[2]
if str(IMAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(IMAGE_ROOT))

from shared.kernel_release import kernel_release_sort_key
from shared.oci_layout import load_layer_files_from_oci_layout, unpack_layer_tarballs


LAYOUT_DIR = Path("/tmp/akmods-zfs")
EXTRACT_ROOT = Path("/tmp")
RPM_SEARCH_ROOT = EXTRACT_ROOT / "rpms" / "kmods" / "zfs"
MODULES_ROOT = Path("/lib/modules")
DEFAULT_AKMODS_IMAGE_TEMPLATE = "ghcr.io/danathar/zfs-aurora-complex-akmods:main-{fedora}"
# Fedora has shipped kmod payloads uncompressed (`zfs.ko`) and compressed
# (`zfs.ko.xz`, `zfs.ko.zst`) at different times. Accept any of those so a
# kernel packaging change upstream doesn't silently break this install path.
ZFS_KO_PAYLOAD_RE = re.compile(r"^/lib/modules/([^/]+)/extra/zfs/zfs\.ko(?:\.[a-z]+)?$")
ZFS_KO_DISK_GLOB = "zfs.ko*"


@dataclass(frozen=True)
class InstallPlan:
    """
    Exact RPM selection the build should apply to the image root.

    Why this object exists:
    1. The helper first computes a fail-closed plan from cache contents.
    2. Only after that plan is complete does it mutate the image root.
    3. Tests can validate the planning rules without running `rpm-ostree`.
    """

    detected_kernel_releases: list[str]
    managed_rpms: list[Path]
    supported_kernel_release: str
    supported_kmod_rpm: Path


def _run_cmd(
    args: list[str],
    *,
    cwd: Path | None = None,
    capture_output: bool = True,
) -> str:
    """
    Run one external command and return stdout as text.

    These builds depend on host tools such as `rpm`, `skopeo`, and `depmod`.
    Wrapping subprocess calls here keeps error reporting consistent.
    """

    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        capture_output=capture_output,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        stdout = result.stdout.strip() if result.stdout else ""
        detail = stderr or stdout or f"exit {result.returncode}"
        raise RuntimeError(f"Command failed: {' '.join(args)}: {detail}")
    return result.stdout if capture_output else ""


def image_kernels_from_modules_root(modules_root: Path = MODULES_ROOT) -> list[str]:
    """Return the kernel release directories already present in the base image."""

    kernels = sorted(
        entry.name
        for entry in modules_root.iterdir()
        if entry.is_dir()
    )
    if not kernels:
        raise RuntimeError(f"No kernel directories found in {modules_root}")
    return kernels


def fedora_major_version(*, run_cmd=_run_cmd) -> str:
    """
    Resolve Fedora major from the build root itself.

    Why use `rpm -E %fedora` here instead of shell glue in the Containerfile:
    1. The helper already owns the runtime decision-making for this step.
    2. This keeps the Containerfile declarative.
    3. It preserves the exact Fedora detection behavior the earlier shell wrapper used.
    """

    fedora_version = run_cmd(["rpm", "-E", "%fedora"]).strip()
    if not fedora_version:
        raise RuntimeError("Could not determine Fedora major version from rpm -E %fedora")
    return fedora_version


def resolve_akmods_image(
    *,
    environ: os._Environ[str] | dict[str, str] = os.environ,
    run_cmd=_run_cmd,
) -> str:
    """
    Compute the akmods image reference used for this compose run.

    Resolution order:
    1. `AKMODS_IMAGE` keeps a direct escape hatch for debugging or one-off runs.
    2. `AKMODS_IMAGE_TEMPLATE` lets CI declare which repo/tag-prefix to use.
    3. The helper fills in `{fedora}` itself so the Containerfile does not need
       an inline shell wrapper just to compute the Fedora-specific suffix.
    """

    explicit_image = environ.get("AKMODS_IMAGE", "").strip()
    if explicit_image:
        return explicit_image

    image_template = environ.get("AKMODS_IMAGE_TEMPLATE", "").strip()
    if not image_template:
        image_template = DEFAULT_AKMODS_IMAGE_TEMPLATE

    return image_template.format(fedora=fedora_major_version(run_cmd=run_cmd))


def copy_oci_layout_from_registry(image_ref: str, layout_dir: Path = LAYOUT_DIR) -> None:
    """Pull the akmods cache image into a local `dir:` OCI layout."""

    if layout_dir.exists():
        shutil.rmtree(layout_dir)
    _run_cmd(
        [
            "skopeo",
            "copy",
            "--retry-times",
            "3",
            f"docker://{image_ref}",
            f"dir:{layout_dir}",
        ],
        capture_output=False,
    )


def discover_zfs_rpms(rpm_root: Path = RPM_SEARCH_ROOT) -> list[Path]:
    """Return installable ZFS RPMs from the extracted akmods cache tree."""

    zfs_rpms = sorted(
        path
        for path in rpm_root.glob("*.rpm")
        if path.is_file()
        and not path.name.endswith(".src.rpm")
        and "-debug" not in path.name
        and "-devel" not in path.name
        and "-test" not in path.name
    )
    if not zfs_rpms:
        raise RuntimeError(f"No ZFS RPMs found in {rpm_root}")
    return zfs_rpms


def rpm_name(rpm_path: Path) -> str:
    """Read the RPM package name from one cached RPM file."""

    return _run_cmd(
        ["rpm", "-qp", "--qf", "%{NAME}\n", str(rpm_path)]
    ).strip()


def kmod_kernel_release(rpm_path: Path) -> str:
    """
    Identify which kernel release one `kmod-zfs` RPM was built for.

    The payload path under `/lib/modules/<kernel_release>/...` is the most
    reliable signal. File names alone would be easier to parse incorrectly.
    """

    payload_listing = _run_cmd(["rpm", "-qpl", str(rpm_path)])
    for line in payload_listing.splitlines():
        match = ZFS_KO_PAYLOAD_RE.match(line)
        if match:
            return match.group(1)
    raise RuntimeError(f"Could not determine kernel release for {rpm_path}")


def build_install_plan(
    image_kernels: list[str],
    zfs_rpms: list[Path],
    *,
    rpm_name_lookup=rpm_name,
    kernel_release_lookup=kmod_kernel_release,
) -> InstallPlan:
    """
    Split shared userspace RPMs from kernel-specific payload RPMs.

    This repo now supports only the primary base-image kernel. We still inspect
    every detected kernel directory so the build logs explain what the base
    image contains, but the fail-closed support contract is only:

    1. choose the newest detected kernel as the supported boot target
    2. require one matching `kmod-zfs` RPM for that kernel
    3. install that one `kmod-zfs` normally through `rpm-ostree`
    """

    managed_rpms: list[Path] = []
    kmod_rpms: list[Path] = []

    for rpm_path in zfs_rpms:
        if rpm_name_lookup(rpm_path) == "kmod-zfs":
            kmod_rpms.append(rpm_path)
        else:
            managed_rpms.append(rpm_path)

    if not kmod_rpms:
        raise RuntimeError("No kmod-zfs RPMs found in cache image")

    kmod_rpm_by_kernel: dict[str, Path] = {}
    for rpm_path in kmod_rpms:
        kernel_release = kernel_release_lookup(rpm_path)
        if kernel_release in kmod_rpm_by_kernel:
            raise RuntimeError(
                f"Multiple kmod-zfs RPMs found for kernel {kernel_release}"
            )
        kmod_rpm_by_kernel[kernel_release] = rpm_path

    supported_kernel_release = sorted(image_kernels, key=kernel_release_sort_key)[-1]
    supported_kmod_rpm = kmod_rpm_by_kernel.get(supported_kernel_release)
    if supported_kmod_rpm is None:
        raise RuntimeError(
            "No kmod-zfs RPM found for the supported primary kernel "
            f"{supported_kernel_release}. Cached akmods do not cover the supported kernel; rebuild akmods."
        )

    return InstallPlan(
        detected_kernel_releases=image_kernels,
        managed_rpms=managed_rpms,
        supported_kernel_release=supported_kernel_release,
        supported_kmod_rpm=supported_kmod_rpm,
    )


def rpm_ostree_install(rpms: list[Path]) -> None:
    """Install shared RPMs plus the supported primary kernel module through rpm-ostree."""

    install_args = ["rpm-ostree", "install", *(str(rpm) for rpm in rpms)]
    _run_cmd(install_args, capture_output=False)


def _require_command(name: str) -> None:
    """Fail clearly if one external command used by the helper is missing."""

    if shutil.which(name) is None:
        raise RuntimeError(f"Required command is not available: {name}")


def validate_installed_modules(
    kernel_release: str,
    *,
    modules_root: Path = MODULES_ROOT,
    run_cmd=_run_cmd,
) -> None:
    """
    Verify the ZFS module exists for the supported primary kernel and refresh depmod.

    During image builds `uname -r` usually points at the builder kernel, not the
    target image kernel, so we must run `depmod` manually for that release.
    """

    module_dir = modules_root / kernel_release / "extra" / "zfs"
    installed_modules = [
        candidate
        for candidate in module_dir.glob(ZFS_KO_DISK_GLOB)
        if candidate.is_file()
    ]
    if not installed_modules:
        raise RuntimeError(
            "No ZFS module for supported primary kernel "
            f"{kernel_release}. Cached akmods do not cover the supported kernel; rebuild akmods."
        )
    run_cmd(["depmod", "-a", kernel_release], capture_output=False)


def main() -> None:
    """Apply the cached akmods image to the build root."""

    _require_command("python3")
    _require_command("rpm")
    _require_command("rpm-ostree")
    _require_command("skopeo")
    _require_command("depmod")

    image_ref = resolve_akmods_image()
    image_kernels = image_kernels_from_modules_root()
    if len(image_kernels) > 1:
        print(
            "Detected multiple kernels in the base image: "
            + " ".join(image_kernels)
            + ". This repo intentionally supports only the primary kernel "
            f"{sorted(image_kernels, key=kernel_release_sort_key)[-1]}; recovery from a bad image should use image rollback."
        )
    copy_oci_layout_from_registry(image_ref)
    layer_files = load_layer_files_from_oci_layout(LAYOUT_DIR)
    unpack_layer_tarballs(layer_files, EXTRACT_ROOT)
    zfs_rpms = discover_zfs_rpms()
    install_plan = build_install_plan(image_kernels, zfs_rpms)

    rpm_ostree_install([*install_plan.managed_rpms, install_plan.supported_kmod_rpm])
    validate_installed_modules(install_plan.supported_kernel_release)


if __name__ == "__main__":
    main()
