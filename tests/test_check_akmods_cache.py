"""
Script: tests/test_check_akmods_cache.py
What: Tests for shared akmods cache validation helpers.
Doing: Creates temporary RPM trees and checks primary-kernel cache detection.
Why: Protects the simplified cache check that now follows only the supported primary kernel.
Goal: Keep rebuild decisions fail-closed when the required primary-kernel RPM is absent.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from ci_tools.check_akmods_cache import _has_kernel_matching_rpm, inspect_akmods_cache


class CheckAkmodsCacheTests(unittest.TestCase):
    def test_reports_missing_primary_kernel_rpm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rpm_dir = root / "rpms" / "kmods" / "zfs"
            rpm_dir.mkdir(parents=True, exist_ok=True)
            (rpm_dir / "kmod-zfs-6.18.13-200.fc43.x86_64-2.4.1-1.fc43.x86_64.rpm").touch()

            self.assertFalse(
                _has_kernel_matching_rpm(root, "6.18.16-200.fc43.x86_64")
            )

    def test_inspect_akmods_cache_reads_shared_cache_image(self) -> None:
        def fake_exists(image_ref: str) -> bool:
            return image_ref == "docker://ghcr.io/danathar/zfs-aurora-complex-akmods:main-43"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def fake_copy(_source: str, destination: str) -> None:
                image_dir = Path(destination.removeprefix("dir:"))
                image_dir.mkdir(parents=True, exist_ok=True)
                (image_dir / "manifest.json").write_text(
                    "{\"layers\": [{\"digest\": \"sha256:layer\"}]}",
                    encoding="utf-8",
                )
                (image_dir / "layer").write_text("", encoding="utf-8")

            def fake_load_layers(_image_dir: Path) -> list[Path]:
                return [root / "layer.tar"]

            def fake_unpack(_layer_files: list[Path], destination: Path) -> None:
                rpm_dir = destination / "rpms" / "kmods" / "zfs"
                rpm_dir.mkdir(parents=True, exist_ok=True)
                (
                    rpm_dir / "kmod-zfs-6.18.16-200.fc43.x86_64-2.4.1-1.fc43.x86_64.rpm"
                ).touch()

            with patch("ci_tools.check_akmods_cache.skopeo_exists", side_effect=fake_exists):
                with patch("ci_tools.check_akmods_cache.skopeo_copy", side_effect=fake_copy) as skopeo_copy:
                    with patch(
                        "ci_tools.check_akmods_cache.load_layer_files_from_oci_layout",
                        side_effect=fake_load_layers,
                    ):
                        with patch(
                            "ci_tools.check_akmods_cache.unpack_layer_tarballs",
                            side_effect=fake_unpack,
                        ):
                            status = inspect_akmods_cache(
                                image_org="danathar",
                                source_repo="zfs-aurora-complex-akmods",
                                fedora_version="43",
                                kernel_release="6.18.16-200.fc43.x86_64",
                            )

        self.assertTrue(status.reusable)
        self.assertEqual(status.inspection_method, "unpacked-image")
        skopeo_copy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
