"""
Script: tests/test_install_zfs_from_akmods_cache.py
What: Tests the helper that installs cached ZFS RPMs into the build root.
Doing: Exercises the primary-kernel planning rules without invoking `dnf5` or mutating the host.
Why: The old inline Containerfile shell block was hard to reason about and almost impossible to unit test.
Goal: Keep the simplified primary-kernel contract explicit and reviewable.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_helper_module():
    helper_path = (
        Path(__file__).resolve().parents[1]
        / "containerfiles"
        / "zfs-akmods"
        / "install_zfs_from_akmods_cache.py"
    )
    spec = importlib.util.spec_from_file_location(
        "install_zfs_from_akmods_cache",
        helper_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


helper = _load_helper_module()

# The real step functions, captured before any test patches the module, so a
# faked main() step can delegate to the real refusal it stands in for.
REAL_REQUIRE_COMMAND = helper._require_command
REAL_RESOLVE_AKMODS_IMAGE = helper.resolve_akmods_image
REAL_IMAGE_KERNELS = helper.image_kernels_from_modules_root
REAL_DISCOVER_ZFS_RPMS = helper.discover_zfs_rpms
REAL_BUILD_INSTALL_PLAN = helper.build_install_plan
REAL_VALIDATE_INSTALLED_MODULES = helper.validate_installed_modules

PRIMARY_KERNEL = "6.18.16-200.fc43.x86_64"
OLDER_KERNEL = "6.18.13-200.fc43.x86_64"
IMAGE_REF = "ghcr.io/example/zfs-aurora-complex-akmods:main-43"
LAYER_FILES = [Path("/tmp/akmods-zfs/layer-a")]
SHARED_RPM = Path("/tmp/rpms/kmods/zfs/zfs-2.4.0-1.fc43.x86_64.rpm")
KMOD_RPM = Path("/tmp/rpms/kmods/zfs/kmod-zfs-6.18.16-2.4.0-1.fc43.x86_64.rpm")
OLDER_KMOD_RPM = Path("/tmp/rpms/kmods/zfs/kmod-zfs-6.18.13-2.4.0-1.fc43.x86_64.rpm")

REQUIRED_COMMANDS = ("python3", "rpm", "dnf5", "skopeo", "depmod")

# Every step main() runs, in order, for a single-kernel image whose cache holds
# one shared RPM and one kmod-zfs for the primary kernel.
EXPECTED_MAIN_STEPS = [
    *(("_require_command", (name,)) for name in REQUIRED_COMMANDS),
    ("resolve_akmods_image", ()),
    ("image_kernels_from_modules_root", ()),
    ("copy_oci_layout_from_registry", (IMAGE_REF,)),
    ("load_layer_files_from_oci_layout", (helper.LAYOUT_DIR,)),
    ("unpack_layer_tarballs", (LAYER_FILES, helper.EXTRACT_ROOT)),
    ("discover_zfs_rpms", ()),
    ("build_install_plan", ([PRIMARY_KERNEL], [SHARED_RPM, KMOD_RPM])),
    ("dnf5_install", ([SHARED_RPM, KMOD_RPM],)),
    ("validate_installed_modules", (PRIMARY_KERNEL,)),
]


def _fake_main_steps(
    stack: contextlib.ExitStack,
    calls: list[tuple[str, tuple]],
    *,
    kernels: tuple[str, ...] = (PRIMARY_KERNEL,),
    fail_at: int | None = None,
    overrides: dict | None = None,
) -> None:
    """
    Replace every step main() calls with a recorder.

    Each call is appended to `calls` as `(step name, positional args)`. The
    call at index `fail_at` raises instead of returning. A step named in
    `overrides` records its call and then runs the override, which is how a
    test puts one real refusal back into an otherwise faked pipeline.
    """

    plan = helper.InstallPlan(
        detected_kernel_releases=list(kernels),
        managed_rpms=[SHARED_RPM],
        supported_kernel_release=PRIMARY_KERNEL,
        supported_kmod_rpm=KMOD_RPM,
    )
    results = {
        "_require_command": None,
        "resolve_akmods_image": IMAGE_REF,
        "image_kernels_from_modules_root": list(kernels),
        "copy_oci_layout_from_registry": None,
        "load_layer_files_from_oci_layout": LAYER_FILES,
        "unpack_layer_tarballs": None,
        "discover_zfs_rpms": [SHARED_RPM, KMOD_RPM],
        "build_install_plan": plan,
        "dnf5_install": None,
        "validate_installed_modules": None,
    }
    overrides = overrides or {}

    for name, result in results.items():
        def fake(*args, _name=name, _result=result, **kwargs):
            calls.append((_name, args))
            if fail_at is not None and len(calls) - 1 == fail_at:
                raise RuntimeError(f"refused at {_name}")
            if _name in overrides:
                return overrides[_name](*args, **kwargs)
            return _result

        stack.enter_context(patch.object(helper, name, side_effect=fake))


def _fake_rpm_queries(names: dict[Path, str], payloads: dict[Path, str]):
    """Answer the two `rpm -qp` queries the planning step makes per RPM."""

    def run_cmd(args, **_kwargs):
        rpm_path = Path(args[-1])
        if args[:2] == ["rpm", "-qp"]:
            return names[rpm_path] + "\n"
        if args[:2] == ["rpm", "-qpl"]:
            return payloads.get(rpm_path, "")
        raise AssertionError(f"unexpected command: {args}")

    return run_cmd


class InstallZfsFromAkmodsCacheTests(unittest.TestCase):
    def test_resolve_akmods_image_prefers_explicit_override(self) -> None:
        image_ref = helper.resolve_akmods_image(
            environ={"AKMODS_IMAGE": "ghcr.io/example/zfs-aurora-complex-akmods:manual"},
            run_cmd=lambda _args: "43\n",
        )

        self.assertEqual(image_ref, "ghcr.io/example/zfs-aurora-complex-akmods:manual")

    def test_resolve_akmods_image_renders_template_with_detected_fedora(self) -> None:
        image_ref = helper.resolve_akmods_image(
            environ={
                "AKMODS_IMAGE_TEMPLATE": "ghcr.io/example/zfs-aurora-complex-akmods:main-{fedora}"
            },
            run_cmd=lambda _args: "43\n",
        )

        self.assertEqual(image_ref, "ghcr.io/example/zfs-aurora-complex-akmods:main-43")

    def test_resolve_akmods_image_uses_default_template_when_unset(self) -> None:
        image_ref = helper.resolve_akmods_image(
            environ={},
            run_cmd=lambda _args: "43\n",
        )

        self.assertEqual(image_ref, "ghcr.io/danathar/zfs-aurora-complex-akmods:main-43")

    def test_run_cmd_redacts_secret_args_in_failure_message(self) -> None:
        args = [
            "skopeo",
            "inspect",
            "--creds",
            "actor:token-secret",
            "--src-creds=actor:src-secret",
        ]
        result = subprocess.CompletedProcess(args, 1, stdout="", stderr="failed")
        with (
            patch.object(helper.subprocess, "run", return_value=result),
            self.assertRaises(RuntimeError) as context,
        ):
            helper._run_cmd(args)

        message = str(context.exception)
        self.assertNotIn("token-secret", message)
        self.assertNotIn("src-secret", message)
        self.assertIn("--creds ***REDACTED***", message)
        self.assertIn("--src-creds=***REDACTED***", message)

    def test_run_cmd_shares_the_ci_tools_redactor(self) -> None:
        # One flag list for both helpers: a credential flag added for the CI
        # side must reach this helper's error text redacted too.
        from ci_tools.common import redact_command_args

        self.assertIs(helper.redact_command_args, redact_command_args)

    def test_image_kernels_from_modules_root_uses_natural_sort(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            modules_root = Path(temp_dir)
            (modules_root / "6.10.0-200.fc43.x86_64").mkdir()
            (modules_root / "6.9.0-200.fc43.x86_64").mkdir()

            kernels = helper.image_kernels_from_modules_root(modules_root)

        self.assertEqual(
            kernels,
            [
                "6.9.0-200.fc43.x86_64",
                "6.10.0-200.fc43.x86_64",
            ],
        )

    def test_load_layer_files_from_oci_layout_reads_manifest_layers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            layout_dir = Path(temp_dir)
            manifest_path = layout_dir / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "layers": [
                            {"digest": "sha256:first-layer"},
                            {"digest": "sha256:second-layer"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            layer_files = helper.load_layer_files_from_oci_layout(layout_dir)

            self.assertEqual(
                layer_files,
                [
                    layout_dir / "first-layer",
                    layout_dir / "second-layer",
                ],
            )

    def test_unpack_layer_tarballs_rejects_parent_directory_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bad_layer = root / "layer.tar"
            destination = root / "extract"
            destination.mkdir()

            with tarfile.open(bad_layer, "w") as tar_handle:
                info = tarfile.TarInfo("../escape")
                info.size = 0
                tar_handle.addfile(info)

            with self.assertRaisesRegex(RuntimeError, "Unsafe tar path"):
                helper.unpack_layer_tarballs([bad_layer], destination)

    def test_unpack_layer_tarballs_rejects_symlinks_pointing_outside_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bad_layer = root / "layer.tar"
            destination = root / "extract"
            destination.mkdir()

            with tarfile.open(bad_layer, "w") as tar_handle:
                info = tarfile.TarInfo("safe_dir/link")
                info.size = 0
                info.type = tarfile.SYMTYPE
                info.linkname = "../../../etc/passwd"
                tar_handle.addfile(info)

            with self.assertRaisesRegex(RuntimeError, "Unsafe tar path"):
                helper.unpack_layer_tarballs([bad_layer], destination)

    def test_unpack_layer_tarballs_rejects_hardlinks_pointing_outside_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bad_layer = root / "layer.tar"
            destination = root / "extract"
            destination.mkdir()

            with tarfile.open(bad_layer, "w") as tar_handle:
                info = tarfile.TarInfo("safe_dir/link")
                info.size = 0
                info.type = tarfile.LNKTYPE
                info.linkname = "../../../etc/passwd"
                tar_handle.addfile(info)

            with self.assertRaisesRegex(RuntimeError, "Unsafe tar path"):
                helper.unpack_layer_tarballs([bad_layer], destination)

    def test_discover_zfs_rpms_filters_non_installable_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            rpm_root = Path(temp_dir)
            keep = rpm_root / "zfs-2.4.0-1.fc43.x86_64.rpm"
            skip_src = rpm_root / "zfs-2.4.0-1.fc43.src.rpm"
            skip_debug = rpm_root / "zfs-debug-2.4.0-1.fc43.x86_64.rpm"

            keep.touch()
            skip_src.touch()
            skip_debug.touch()

            zfs_rpms = helper.discover_zfs_rpms(rpm_root)

            self.assertEqual(zfs_rpms, [keep])

    def test_build_install_plan_selects_primary_kernel_and_splits_rpms(self) -> None:
        shared_rpm = Path("/tmp/zfs-2.4.0.rpm")
        first_kmod = Path("/tmp/kmod-zfs-6.18.13.rpm")
        second_kmod = Path("/tmp/kmod-zfs-6.18.16.rpm")

        name_by_path = {
            shared_rpm: "zfs",
            first_kmod: "kmod-zfs",
            second_kmod: "kmod-zfs",
        }
        kernel_by_path = {
            first_kmod: "6.18.13-200.fc43.x86_64",
            second_kmod: "6.18.16-200.fc43.x86_64",
        }

        plan = helper.build_install_plan(
            [
                "6.18.13-200.fc43.x86_64",
                "6.18.16-200.fc43.x86_64",
            ],
            [shared_rpm, first_kmod, second_kmod],
            rpm_name_lookup=name_by_path.__getitem__,
            kernel_release_lookup=kernel_by_path.__getitem__,
        )

        self.assertEqual(plan.managed_rpms, [shared_rpm])
        self.assertEqual(plan.supported_kernel_release, "6.18.16-200.fc43.x86_64")
        self.assertEqual(plan.supported_kmod_rpm, second_kmod)
        self.assertEqual(
            plan.detected_kernel_releases,
            ["6.18.13-200.fc43.x86_64", "6.18.16-200.fc43.x86_64"],
        )

    def test_build_install_plan_rejects_missing_primary_kernel_payload(self) -> None:
        first_kmod = Path("/tmp/kmod-zfs-6.18.13.rpm")

        with self.assertRaisesRegex(RuntimeError, "do not cover the supported kernel"):
            helper.build_install_plan(
                [
                    "6.18.13-200.fc43.x86_64",
                    "6.18.16-200.fc43.x86_64",
                ],
                [first_kmod],
                rpm_name_lookup=lambda _path: "kmod-zfs",
                kernel_release_lookup=lambda _path: "6.18.13-200.fc43.x86_64",
            )

    def test_build_install_plan_rejects_duplicate_kernel_payloads(self) -> None:
        first_kmod = Path("/tmp/kmod-zfs-a.rpm")
        second_kmod = Path("/tmp/kmod-zfs-b.rpm")

        with self.assertRaisesRegex(RuntimeError, "Multiple kmod-zfs RPMs"):
            helper.build_install_plan(
                ["6.18.16-200.fc43.x86_64"],
                [first_kmod, second_kmod],
                rpm_name_lookup=lambda _path: "kmod-zfs",
                kernel_release_lookup=lambda _path: "6.18.16-200.fc43.x86_64",
            )

    def test_validate_installed_modules_checks_only_supported_primary_kernel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            modules_root = Path(temp_dir)
            supported_kernel = modules_root / "6.18.16-200.fc43.x86_64" / "extra" / "zfs"
            supported_kernel.mkdir(parents=True, exist_ok=True)
            (supported_kernel / "zfs.ko").touch()

            depmod_calls: list[list[str]] = []

            helper.validate_installed_modules(
                "6.18.16-200.fc43.x86_64",
                modules_root=modules_root,
                run_cmd=lambda args, **_kwargs: depmod_calls.append(args) or "",
            )

        self.assertEqual(depmod_calls, [["depmod", "-a", "6.18.16-200.fc43.x86_64"]])

    def test_validate_installed_modules_accepts_compressed_module(self) -> None:
        for suffix in ("zfs.ko.xz", "zfs.ko.zst"):
            with self.subTest(module=suffix), tempfile.TemporaryDirectory() as temp_dir:
                modules_root = Path(temp_dir)
                module_dir = modules_root / "6.18.16-200.fc43.x86_64" / "extra" / "zfs"
                module_dir.mkdir(parents=True, exist_ok=True)
                (module_dir / suffix).touch()

                depmod_calls: list[list[str]] = []

                helper.validate_installed_modules(
                    "6.18.16-200.fc43.x86_64",
                    modules_root=modules_root,
                    run_cmd=lambda args, depmod_calls=depmod_calls, **_kwargs: (
                        depmod_calls.append(args) or ""
                    ),
                )

                self.assertEqual(
                    depmod_calls, [["depmod", "-a", "6.18.16-200.fc43.x86_64"]]
                )

    def test_validate_installed_modules_rejects_when_no_module_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            modules_root = Path(temp_dir)
            module_dir = modules_root / "6.18.16-200.fc43.x86_64" / "extra" / "zfs"
            module_dir.mkdir(parents=True, exist_ok=True)

            with self.assertRaisesRegex(RuntimeError, "do not cover the supported kernel"):
                helper.validate_installed_modules(
                    "6.18.16-200.fc43.x86_64",
                    modules_root=modules_root,
                    run_cmd=lambda _args, **_kwargs: "",
                )

    def test_kmod_kernel_release_accepts_compressed_payload(self) -> None:
        for payload_name in ("zfs.ko", "zfs.ko.xz", "zfs.ko.zst"):
            with self.subTest(payload=payload_name):
                payload = (
                    "/usr/share/doc/kmod-zfs-README\n"
                    f"/lib/modules/6.18.16-200.fc43.x86_64/extra/zfs/{payload_name}\n"
                )
                original_run_cmd = helper._run_cmd
                helper._run_cmd = lambda _args, payload=payload, **_kwargs: payload
                try:
                    kernel_release = helper.kmod_kernel_release(Path("/tmp/kmod-zfs.rpm"))
                finally:
                    helper._run_cmd = original_run_cmd

                self.assertEqual(kernel_release, "6.18.16-200.fc43.x86_64")


    def test_image_kernels_from_modules_root_refuses_when_no_kernel_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            modules_root = Path(temp_dir)
            # A stray file is not a kernel directory and must not count as one.
            (modules_root / "README").touch()

            with self.assertRaises(RuntimeError) as context:
                helper.image_kernels_from_modules_root(modules_root)

        self.assertEqual(
            str(context.exception), f"No kernel directories found in {modules_root}"
        )

    def test_fedora_major_version_refuses_empty_rpm_output(self) -> None:
        commands: list[list[str]] = []

        with self.assertRaises(RuntimeError) as context:
            helper.fedora_major_version(
                run_cmd=lambda args: commands.append(args) or "  \n",
            )

        self.assertEqual(
            str(context.exception),
            "Could not determine Fedora major version from rpm -E %fedora",
        )
        self.assertEqual(commands, [["rpm", "-E", "%fedora"]])

    def test_resolve_akmods_image_refuses_when_fedora_version_is_unknown(self) -> None:
        with self.assertRaises(RuntimeError) as context:
            helper.resolve_akmods_image(environ={}, run_cmd=lambda _args: "")

        self.assertEqual(
            str(context.exception),
            "Could not determine Fedora major version from rpm -E %fedora",
        )

    def test_discover_zfs_rpms_refuses_when_only_non_installable_rpms_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            rpm_root = Path(temp_dir)
            for name in (
                "zfs-2.4.0-1.fc43.src.rpm",
                "zfs-debuginfo-2.4.0-1.fc43.x86_64.rpm",
                "libzfs-devel-2.4.0-1.fc43.x86_64.rpm",
                "zfs-test-2.4.0-1.fc43.x86_64.rpm",
            ):
                (rpm_root / name).touch()

            with self.assertRaises(RuntimeError) as context:
                helper.discover_zfs_rpms(rpm_root)

        self.assertEqual(str(context.exception), f"No ZFS RPMs found in {rpm_root}")

    def test_kmod_kernel_release_refuses_payload_without_zfs_module(self) -> None:
        rpm_path = Path("/tmp/kmod-zfs.rpm")
        commands: list[list[str]] = []
        listing = (
            "/usr/share/doc/kmod-zfs/README\n"
            "/lib/modules/6.18.16-200.fc43.x86_64/extra/zfs/spl.ko.xz\n"
        )

        def fake_run_cmd(args, **_kwargs):
            commands.append(args)
            return listing

        with (
            patch.object(helper, "_run_cmd", side_effect=fake_run_cmd),
            self.assertRaises(RuntimeError) as context,
        ):
            helper.kmod_kernel_release(rpm_path)

        self.assertEqual(
            str(context.exception), f"Could not determine kernel release for {rpm_path}"
        )
        self.assertEqual(commands, [["rpm", "-qpl", str(rpm_path)]])

    def test_build_install_plan_refuses_cache_without_kmod_zfs(self) -> None:
        kernel_lookups: list[Path] = []

        with self.assertRaises(RuntimeError) as context:
            helper.build_install_plan(
                [PRIMARY_KERNEL],
                [SHARED_RPM, Path("/tmp/libzfs.rpm")],
                rpm_name_lookup=lambda _path: "zfs",
                kernel_release_lookup=lambda path: kernel_lookups.append(path) or PRIMARY_KERNEL,
            )

        self.assertEqual(str(context.exception), "No kmod-zfs RPMs found in cache image")
        self.assertEqual(kernel_lookups, [])

    def test_build_install_plan_missing_primary_kernel_message_is_exact(self) -> None:
        with self.assertRaises(RuntimeError) as context:
            helper.build_install_plan(
                [OLDER_KERNEL, PRIMARY_KERNEL],
                [OLDER_KMOD_RPM],
                rpm_name_lookup=lambda _path: "kmod-zfs",
                kernel_release_lookup=lambda _path: OLDER_KERNEL,
            )

        self.assertEqual(
            str(context.exception),
            "No kmod-zfs RPM found for the supported primary kernel "
            f"{PRIMARY_KERNEL}. Cached akmods do not cover the supported kernel; rebuild akmods.",
        )

    def test_run_cmd_returns_stdout_and_passes_subprocess_options(self) -> None:
        args = ["rpm", "-E", "%fedora"]
        result = subprocess.CompletedProcess(args, 0, stdout="43\n", stderr="")

        with patch.object(helper.subprocess, "run", return_value=result) as run:
            output = helper._run_cmd(args, cwd=Path("/tmp/work"))

        self.assertEqual(output, "43\n")
        run.assert_called_once_with(
            args,
            cwd="/tmp/work",
            check=False,
            capture_output=True,
            text=True,
        )

    def test_run_cmd_returns_empty_string_when_output_is_not_captured(self) -> None:
        args = ["depmod", "-a", PRIMARY_KERNEL]
        result = subprocess.CompletedProcess(args, 0, stdout=None, stderr=None)

        with patch.object(helper.subprocess, "run", return_value=result) as run:
            output = helper._run_cmd(args, capture_output=False)

        self.assertEqual(output, "")
        run.assert_called_once_with(
            args,
            cwd=None,
            check=False,
            capture_output=False,
            text=True,
        )

    def test_run_cmd_refuses_non_zero_exit(self) -> None:
        args = ["rpm", "-qpl", "/tmp/kmod-zfs.rpm"]
        cases = [
            ("stderr wins", "partial output", "  error: not an rpm\n", "error: not an rpm"),
            ("stdout when stderr is empty", " only stdout \n", "", "only stdout"),
            ("exit code when both are empty", None, None, "exit 7"),
        ]
        for label, stdout, stderr, detail in cases:
            with self.subTest(label):
                result = subprocess.CompletedProcess(args, 7, stdout=stdout, stderr=stderr)
                with (
                    patch.object(helper.subprocess, "run", return_value=result),
                    self.assertRaises(RuntimeError) as context,
                ):
                    helper._run_cmd(args)

                self.assertEqual(
                    str(context.exception),
                    f"Command failed: rpm -qpl /tmp/kmod-zfs.rpm: {detail}",
                )

    def test_require_command_refuses_tool_missing_from_path(self) -> None:
        with (
            patch.object(helper.shutil, "which", return_value=None) as which,
            self.assertRaises(RuntimeError) as context,
        ):
            helper._require_command("skopeo")

        self.assertEqual(str(context.exception), "Required command is not available: skopeo")
        which.assert_called_once_with("skopeo")

    def test_require_command_accepts_tool_on_path(self) -> None:
        with patch.object(helper.shutil, "which", return_value="/usr/bin/dnf5") as which:
            helper._require_command("dnf5")

        which.assert_called_once_with("dnf5")

    def test_copy_oci_layout_from_registry_removes_stale_layout_before_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            layout_dir = Path(temp_dir) / "akmods-zfs"
            layout_dir.mkdir()
            (layout_dir / "stale-layer").write_text("old cache", encoding="utf-8")
            copies: list[tuple[list[str], dict, bool]] = []

            def fake_run_cmd(args, **kwargs):
                copies.append((args, kwargs, layout_dir.exists()))
                return ""

            with patch.object(helper, "_run_cmd", side_effect=fake_run_cmd):
                helper.copy_oci_layout_from_registry(IMAGE_REF, layout_dir)

        self.assertEqual(
            copies,
            [
                (
                    [
                        "skopeo",
                        "copy",
                        "--retry-times",
                        "3",
                        f"docker://{IMAGE_REF}",
                        f"dir:{layout_dir}",
                    ],
                    {"capture_output": False},
                    False,
                )
            ],
        )

    def test_copy_oci_layout_from_registry_copies_when_no_layout_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            layout_dir = Path(temp_dir) / "akmods-zfs"
            commands: list[list[str]] = []

            with patch.object(
                helper,
                "_run_cmd",
                side_effect=lambda args, **_kwargs: commands.append(args) or "",
            ):
                helper.copy_oci_layout_from_registry(IMAGE_REF, layout_dir)

        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][-1], f"dir:{layout_dir}")

    def test_dnf5_install_passes_each_rpm_path_in_order(self) -> None:
        installs: list[tuple[list[str], dict]] = []

        with patch.object(
            helper,
            "_run_cmd",
            side_effect=lambda args, **kwargs: installs.append((args, kwargs)) or "",
        ):
            helper.dnf5_install([SHARED_RPM, KMOD_RPM])

        self.assertEqual(
            installs,
            [
                (
                    ["dnf5", "install", "-y", str(SHARED_RPM), str(KMOD_RPM)],
                    {"capture_output": False},
                )
            ],
        )


class InstallZfsMainTests(unittest.TestCase):
    """main() runs every step once, in order, and stops at the first refusal."""

    def _run_main(
        self,
        *,
        kernels: tuple[str, ...] = (PRIMARY_KERNEL,),
        fail_at: int | None = None,
        overrides: dict | None = None,
        extra_patches: tuple = (),
    ) -> tuple[list[tuple[str, tuple]], str, BaseException | None]:
        calls: list[tuple[str, tuple]] = []
        stdout = io.StringIO()
        error: BaseException | None = None
        with contextlib.ExitStack() as stack:
            _fake_main_steps(
                stack, calls, kernels=kernels, fail_at=fail_at, overrides=overrides
            )
            for extra in extra_patches:
                stack.enter_context(extra)
            stack.enter_context(contextlib.redirect_stdout(stdout))
            try:
                helper.main()
            except RuntimeError as exc:
                error = exc
        return calls, stdout.getvalue(), error

    def _step_names(self, calls: list[tuple[str, tuple]]) -> list[str]:
        return [name for name, _args in calls]

    def test_main_runs_every_step_once_in_order(self) -> None:
        calls, output, error = self._run_main()

        self.assertIsNone(error)
        self.assertEqual(calls, EXPECTED_MAIN_STEPS)
        self.assertEqual(output, "")

    def test_main_stops_at_the_first_refusing_step(self) -> None:
        for index, (name, args) in enumerate(EXPECTED_MAIN_STEPS):
            with self.subTest(step=name, args=args):
                calls, _output, error = self._run_main(fail_at=index)

                self.assertIsNotNone(error)
                self.assertEqual(str(error), f"refused at {name}")
                self.assertEqual(calls, EXPECTED_MAIN_STEPS[: index + 1])

    def test_main_with_multiple_kernels_prints_notice_and_installs_only_primary(self) -> None:
        names = {SHARED_RPM: "zfs", OLDER_KMOD_RPM: "kmod-zfs", KMOD_RPM: "kmod-zfs"}
        payloads = {
            OLDER_KMOD_RPM: f"/lib/modules/{OLDER_KERNEL}/extra/zfs/zfs.ko.xz\n",
            KMOD_RPM: f"/lib/modules/{PRIMARY_KERNEL}/extra/zfs/zfs.ko.xz\n",
        }

        calls, output, error = self._run_main(
            kernels=(OLDER_KERNEL, PRIMARY_KERNEL),
            overrides={
                "discover_zfs_rpms": lambda: [SHARED_RPM, OLDER_KMOD_RPM, KMOD_RPM],
                "build_install_plan": REAL_BUILD_INSTALL_PLAN,
            },
            extra_patches=(
                patch.object(
                    helper, "_run_cmd", side_effect=_fake_rpm_queries(names, payloads)
                ),
            ),
        )

        self.assertIsNone(error)
        self.assertEqual(
            output,
            "Detected multiple kernels in the base image: "
            f"{OLDER_KERNEL} {PRIMARY_KERNEL}. This repo intentionally supports only "
            f"the primary kernel {PRIMARY_KERNEL}; recovery from a bad image should use "
            "image rollback.\n",
        )
        installs = [args for name, args in calls if name == "dnf5_install"]
        validations = [args for name, args in calls if name == "validate_installed_modules"]
        self.assertEqual(installs, [([SHARED_RPM, KMOD_RPM],)])
        self.assertEqual(validations, [(PRIMARY_KERNEL,)])

    def test_main_refuses_missing_required_command_before_any_other_step(self) -> None:
        calls, _output, error = self._run_main(
            overrides={"_require_command": REAL_REQUIRE_COMMAND},
            extra_patches=(
                patch.object(
                    helper.shutil,
                    "which",
                    side_effect=lambda name: None if name == "skopeo" else f"/usr/bin/{name}",
                ),
            ),
        )

        self.assertEqual(str(error), "Required command is not available: skopeo")
        self.assertEqual(calls, EXPECTED_MAIN_STEPS[:4])

    def test_main_refuses_unknown_fedora_version_before_copying_cache(self) -> None:
        calls, _output, error = self._run_main(
            overrides={
                "resolve_akmods_image": lambda: REAL_RESOLVE_AKMODS_IMAGE(
                    environ={}, run_cmd=lambda _args: "\n"
                ),
            },
        )

        self.assertEqual(
            str(error), "Could not determine Fedora major version from rpm -E %fedora"
        )
        self.assertEqual(self._step_names(calls)[-1], "resolve_akmods_image")
        self.assertEqual(calls, EXPECTED_MAIN_STEPS[:6])

    def test_main_refuses_image_without_kernels_before_copying_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            modules_root = Path(temp_dir)
            calls, _output, error = self._run_main(
                overrides={
                    "image_kernels_from_modules_root": lambda: REAL_IMAGE_KERNELS(modules_root),
                },
            )

        self.assertEqual(str(error), f"No kernel directories found in {modules_root}")
        self.assertEqual(calls, EXPECTED_MAIN_STEPS[:7])

    def test_main_refuses_cache_without_zfs_rpms_before_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            rpm_root = Path(temp_dir)
            calls, _output, error = self._run_main(
                overrides={"discover_zfs_rpms": lambda: REAL_DISCOVER_ZFS_RPMS(rpm_root)},
            )

        self.assertEqual(str(error), f"No ZFS RPMs found in {rpm_root}")
        self.assertEqual(self._step_names(calls)[-1], "discover_zfs_rpms")
        self.assertNotIn("dnf5_install", self._step_names(calls))
        self.assertNotIn("validate_installed_modules", self._step_names(calls))

    def test_main_refuses_cache_without_kmod_zfs_before_install(self) -> None:
        names = {SHARED_RPM: "zfs", KMOD_RPM: "zfs-dkms"}

        calls, _output, error = self._run_main(
            overrides={"build_install_plan": REAL_BUILD_INSTALL_PLAN},
            extra_patches=(
                patch.object(helper, "_run_cmd", side_effect=_fake_rpm_queries(names, {})),
            ),
        )

        self.assertEqual(str(error), "No kmod-zfs RPMs found in cache image")
        self.assertEqual(self._step_names(calls)[-1], "build_install_plan")
        self.assertNotIn("dnf5_install", self._step_names(calls))
        self.assertNotIn("validate_installed_modules", self._step_names(calls))

    def test_main_refuses_kmod_with_unknown_kernel_release_before_install(self) -> None:
        names = {SHARED_RPM: "zfs", KMOD_RPM: "kmod-zfs"}
        payloads = {KMOD_RPM: "/usr/share/doc/kmod-zfs/README\n"}

        calls, _output, error = self._run_main(
            overrides={"build_install_plan": REAL_BUILD_INSTALL_PLAN},
            extra_patches=(
                patch.object(
                    helper, "_run_cmd", side_effect=_fake_rpm_queries(names, payloads)
                ),
            ),
        )

        self.assertEqual(str(error), f"Could not determine kernel release for {KMOD_RPM}")
        self.assertEqual(self._step_names(calls)[-1], "build_install_plan")
        self.assertNotIn("dnf5_install", self._step_names(calls))
        self.assertNotIn("validate_installed_modules", self._step_names(calls))

    def test_main_refuses_missing_installed_module_without_running_depmod(self) -> None:
        depmod_calls: list[list[str]] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            modules_root = Path(temp_dir)
            calls, _output, error = self._run_main(
                overrides={
                    "validate_installed_modules": lambda kernel_release: (
                        REAL_VALIDATE_INSTALLED_MODULES(
                            kernel_release,
                            modules_root=modules_root,
                            run_cmd=lambda args, **_kwargs: depmod_calls.append(args) or "",
                        )
                    ),
                },
            )

        self.assertEqual(
            str(error),
            f"No ZFS module for supported primary kernel {PRIMARY_KERNEL}. "
            "Cached akmods do not cover the supported kernel; rebuild akmods.",
        )
        self.assertEqual(calls, EXPECTED_MAIN_STEPS)
        self.assertEqual(depmod_calls, [])


if __name__ == "__main__":
    unittest.main()
