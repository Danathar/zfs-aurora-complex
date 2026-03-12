#!/usr/bin/env python3
"""
Script: containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py
What: Install ZFS RPMs (Red Hat Package Manager package files) from the self-hosted akmods cache into the image build root.
Doing: Pulls the shared akmods image, maps each `kmod-zfs` RPM to a kernel release, installs one primary RPM through `rpm-ostree`, then unpacks the remaining kernel payloads directly.
Why: The multi-kernel workaround is too brittle to keep as one long inline Containerfile shell block.
Goal: Preserve the current fallback-kernel behavior while moving the hard-to-read logic into a tested helper.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile


LAYOUT_DIR = Path("/tmp/akmods-zfs")
EXTRACT_ROOT = Path("/tmp")
RPM_SEARCH_ROOT = EXTRACT_ROOT / "rpms" / "kmods" / "zfs"
MODULES_ROOT = Path("/lib/modules")
DEFAULT_AKMODS_IMAGE_TEMPLATE = "ghcr.io/danathar/zfs-kinoite-containerfile-akmods:main-{fedora}"


@dataclass(frozen=True)
class InstallPlan:
    """
    Exact RPM selection the build should apply to the image root.

    Why this object exists:
    1. The helper first computes a fail-closed plan from cache contents.
    2. Only after that plan is complete does it mutate the image root.
    3. Tests can validate the planning rules without running `rpm-ostree`.
    """

    image_kernels: list[str]
    managed_rpms: list[Path]
    kmod_rpm_by_kernel: dict[str, Path]
    primary_kernel_release: str
    primary_kmod_rpm: Path


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


def load_layer_files_from_oci_layout(layout_dir: Path) -> list[Path]:
    """Resolve manifest layer digests into local tarball paths."""

    manifest_path = layout_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    layer_files = [
        layout_dir / layer["digest"].removeprefix("sha256:")
        for layer in manifest.get("layers", [])
        if layer.get("digest")
    ]
    if not layer_files:
        raise RuntimeError(f"No layers found in OCI layout {layout_dir}")
    return layer_files


def _is_safe_tar_member(name: str) -> bool:
    """
    Reject absolute or parent-directory entries before extraction.

    Why: the cache image is expected to unpack under `/tmp`, not escape it.
    Matching the old shell guard here keeps the helper fail-closed.
    """

    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts


def unpack_layer_tarballs(layer_files: list[Path], destination: Path) -> None:
    """Extract every layer tarball after validating member paths."""

    for layer_path in layer_files:
        with tarfile.open(layer_path) as layer_tar:
            for member in layer_tar.getmembers():
                if not _is_safe_tar_member(member.name):
                    raise RuntimeError(
                        f"Unsafe tar path found in layer {layer_path}: {member.name}"
                    )
            layer_tar.extractall(destination)


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
        match = re.match(r"^/lib/modules/([^/]+)/extra/zfs/zfs\.ko$", line)
        if match:
            return match.group(1)
    raise RuntimeError(f"Could not determine kernel release for {rpm_path}")


def version_sort_key(value: str) -> list[tuple[int, object]]:
    """
    Natural-sort key for kernel release strings.

    Kernel releases mix digits and text. Splitting them keeps the "newest"
    primary-kernel choice stable without shelling out to `sort -V`.
    """

    parts = re.findall(r"\d+|[^\d]+", value)
    return [
        (0, int(part)) if part.isdigit() else (1, part)
        for part in parts
    ]


def build_install_plan(
    image_kernels: list[str],
    zfs_rpms: list[Path],
    *,
    rpm_name_lookup=rpm_name,
    kernel_release_lookup=kmod_kernel_release,
) -> InstallPlan:
    """
    Split shared userspace RPMs from kernel-specific payload RPMs.

    Why install one `kmod-zfs` through `rpm-ostree` and unpack the rest:
    1. Cache images may hold multiple `kmod-zfs-<kernel_release>` files.
    2. Those files still report the same RPM identity (`kmod-zfs`).
    3. `rpm-ostree` can manage only one of those identical RPM identities.
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

    for kernel_release in image_kernels:
        if kernel_release not in kmod_rpm_by_kernel:
            raise RuntimeError(
                "No kmod-zfs RPM found for base kernel "
                f"{kernel_release}. Cached akmods do not cover this kernel; rebuild akmods."
            )

    primary_kernel_release = sorted(image_kernels, key=version_sort_key)[-1]
    primary_kmod_rpm = kmod_rpm_by_kernel[primary_kernel_release]

    return InstallPlan(
        image_kernels=image_kernels,
        managed_rpms=managed_rpms,
        kmod_rpm_by_kernel=kmod_rpm_by_kernel,
        primary_kernel_release=primary_kernel_release,
        primary_kmod_rpm=primary_kmod_rpm,
    )


def rpm_ostree_install(rpms: list[Path]) -> None:
    """Install shared RPMs plus the primary kernel module through rpm-ostree."""

    install_args = ["rpm-ostree", "install", *(str(rpm) for rpm in rpms)]
    _run_cmd(install_args, capture_output=False)


def _require_command(name: str) -> None:
    """Fail clearly if one external command used by the helper is missing."""

    if shutil.which(name) is None:
        raise RuntimeError(f"Required command is not available: {name}")


def unpack_rpm_payload(rpm_path: Path, destination_root: Path = Path("/")) -> None:
    """
    Expand one kernel-module RPM payload directly into the image root.

    This keeps fallback-kernel module files present even though `rpm-ostree`
    cannot keep multiple identical `kmod-zfs` RPM identities side by side.
    """

    rpm2cpio = subprocess.Popen(
        ["rpm2cpio", str(rpm_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    assert rpm2cpio.stdout is not None
    assert rpm2cpio.stderr is not None
    cpio = subprocess.run(
        ["cpio", "-idmu", "--quiet"],
        cwd=str(destination_root),
        stdin=rpm2cpio.stdout,
        capture_output=True,
        text=True,
        check=False,
    )
    rpm2cpio.stdout.close()
    rpm2cpio_stderr = rpm2cpio.stderr.read().decode("utf-8", errors="replace")
    rpm2cpio_returncode = rpm2cpio.wait()
    rpm2cpio.stderr.close()

    if rpm2cpio_returncode != 0:
        detail = rpm2cpio_stderr.strip() or f"exit {rpm2cpio_returncode}"
        raise RuntimeError(f"rpm2cpio failed for {rpm_path}: {detail}")
    if cpio.returncode != 0:
        detail = cpio.stderr.strip() or cpio.stdout.strip() or f"exit {cpio.returncode}"
        raise RuntimeError(f"cpio failed for {rpm_path}: {detail}")


def apply_extra_kmod_payloads(plan: InstallPlan) -> None:
    """Unpack all non-primary kernel-module RPM payloads into the image root."""

    if len(plan.image_kernels) <= 1:
        return

    _require_command("rpm2cpio")
    _require_command("cpio")

    for kernel_release in plan.image_kernels:
        kmod_rpm = plan.kmod_rpm_by_kernel[kernel_release]
        if kmod_rpm == plan.primary_kmod_rpm:
            continue
        unpack_rpm_payload(kmod_rpm)


def validate_installed_modules(
    image_kernels: list[str],
    *,
    modules_root: Path = MODULES_ROOT,
) -> None:
    """
    Verify ZFS modules exist for every base-image kernel and refresh depmod.

    During image builds `uname -r` usually points at the builder kernel, not the
    target image kernel, so we must run `depmod` manually for each release.
    """

    for kernel_release in image_kernels:
        module_path = modules_root / kernel_release / "extra" / "zfs" / "zfs.ko"
        if not module_path.is_file():
            raise RuntimeError(
                "No ZFS module for base kernel "
                f"{kernel_release}. Cached akmods do not cover this kernel; rebuild akmods."
            )
        _run_cmd(["depmod", "-a", kernel_release], capture_output=False)


def main() -> None:
    """Apply the cached akmods image to the build root."""

    _require_command("python3")
    _require_command("rpm")
    _require_command("rpm-ostree")
    _require_command("skopeo")
    _require_command("depmod")

    image_ref = resolve_akmods_image()
    image_kernels = image_kernels_from_modules_root()
    copy_oci_layout_from_registry(image_ref)
    layer_files = load_layer_files_from_oci_layout(LAYOUT_DIR)
    unpack_layer_tarballs(layer_files, EXTRACT_ROOT)
    zfs_rpms = discover_zfs_rpms()
    install_plan = build_install_plan(image_kernels, zfs_rpms)

    rpm_ostree_install([*install_plan.managed_rpms, install_plan.primary_kmod_rpm])
    apply_extra_kmod_payloads(install_plan)
    validate_installed_modules(install_plan.image_kernels)


if __name__ == "__main__":
    main()
