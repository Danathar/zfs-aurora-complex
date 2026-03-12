"""
Script: tests/test_install_zfs_from_akmods_cache.py
What: Tests the helper that installs cached ZFS RPMs into the build root.
Doing: Exercises the multi-kernel planning rules without invoking `rpm-ostree` or mutating the host.
Why: The old inline Containerfile shell block was hard to reason about and almost impossible to unit test.
Goal: Keep fallback-kernel support intact while making the decision logic explicit and reviewable.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest


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


class InstallZfsFromAkmodsCacheTests(unittest.TestCase):
    def test_resolve_akmods_image_prefers_explicit_override(self) -> None:
        image_ref = helper.resolve_akmods_image(
            environ={"AKMODS_IMAGE": "ghcr.io/example/zfs-kinoite-containerfile-akmods:manual"},
            run_cmd=lambda _args: "43\n",
        )

        self.assertEqual(image_ref, "ghcr.io/example/zfs-kinoite-containerfile-akmods:manual")

    def test_resolve_akmods_image_renders_template_with_detected_fedora(self) -> None:
        image_ref = helper.resolve_akmods_image(
            environ={
                "AKMODS_IMAGE_TEMPLATE": "ghcr.io/example/zfs-kinoite-containerfile-akmods:main-{fedora}"
            },
            run_cmd=lambda _args: "43\n",
        )

        self.assertEqual(image_ref, "ghcr.io/example/zfs-kinoite-containerfile-akmods:main-43")

    def test_resolve_akmods_image_uses_default_template_when_unset(self) -> None:
        image_ref = helper.resolve_akmods_image(
            environ={},
            run_cmd=lambda _args: "43\n",
        )

        self.assertEqual(image_ref, "ghcr.io/danathar/zfs-kinoite-containerfile-akmods:main-43")

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

    def test_build_install_plan_selects_newest_kernel_and_splits_rpms(self) -> None:
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
        self.assertEqual(plan.primary_kernel_release, "6.18.16-200.fc43.x86_64")
        self.assertEqual(plan.primary_kmod_rpm, second_kmod)
        self.assertEqual(plan.kmod_rpm_by_kernel["6.18.13-200.fc43.x86_64"], first_kmod)

    def test_build_install_plan_rejects_missing_kernel_payload(self) -> None:
        first_kmod = Path("/tmp/kmod-zfs-6.18.13.rpm")

        with self.assertRaisesRegex(RuntimeError, "Cached akmods do not cover this kernel"):
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


if __name__ == "__main__":
    unittest.main()
