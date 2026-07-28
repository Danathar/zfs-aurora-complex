"""
Script: tests/test_check_akmods_cache.py
What: Tests for shared akmods cache validation helpers.
Doing: Creates temporary RPM trees and checks primary-kernel cache detection,
plus the cosign signature check that gates reuse.
Why: Protects the simplified cache check that now follows only the supported primary kernel
and only trusts a cache signed by this repo's own key.
Goal: Keep rebuild decisions fail-closed when the required primary-kernel RPM is absent, or
when the cache cannot be verified as this repo's own signed output.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import ANY, patch

from ci_tools.check_akmods_cache import (
    AkmodsCacheStatus,
    _has_kernel_matching_rpm,
    inspect_akmods_cache,
    main,
)
from ci_tools.common import CiToolError


class CheckAkmodsCacheTests(unittest.TestCase):
    def test_reports_missing_primary_kernel_rpm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rpm_dir = root / "rpms" / "kmods" / "zfs"
            rpm_dir.mkdir(parents=True, exist_ok=True)
            (rpm_dir / "kmod-zfs-6.18.13-200.fc43.x86_64-2.4.1-1.fc43.x86_64.rpm").touch()

            self.assertFalse(
                _has_kernel_matching_rpm(root, "6.18.16-200.fc43.x86_64", "2.4.1")
            )

    def test_rejects_cache_built_against_a_different_zfs_minor_line(self) -> None:
        # The cache holds the right kernel but the wrong ZFS line. Reusing it
        # would publish an image whose ZFS version silently disagrees with the
        # resolved ZFS version for the run.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rpm_dir = root / "rpms" / "kmods" / "zfs"
            rpm_dir.mkdir(parents=True, exist_ok=True)
            (
                rpm_dir / "kmod-zfs-6.18.16-200.fc43.x86_64-2.3.8-1.fc43.x86_64.rpm"
            ).touch()

            self.assertFalse(
                _has_kernel_matching_rpm(root, "6.18.16-200.fc43.x86_64", "2.4.1")
            )
            self.assertTrue(
                _has_kernel_matching_rpm(root, "6.18.16-200.fc43.x86_64", "2.3.8")
            )

    def test_rejects_cache_built_against_an_older_patch_on_the_same_line(self) -> None:
        # This is the bug the exact-version match fixes: a cache holding 2.4.3
        # must MISS when the run resolved 2.4.4, even though both are on the
        # 2.4 line, so a new OpenZFS patch actually triggers a rebuild.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rpm_dir = root / "rpms" / "kmods" / "zfs"
            rpm_dir.mkdir(parents=True, exist_ok=True)
            (
                rpm_dir / "kmod-zfs-7.0.12-201.fc44.x86_64-2.4.3-1.fc44.x86_64.rpm"
            ).touch()

            self.assertFalse(
                _has_kernel_matching_rpm(root, "7.0.12-201.fc44.x86_64", "2.4.4")
            )
            self.assertTrue(
                _has_kernel_matching_rpm(root, "7.0.12-201.fc44.x86_64", "2.4.3")
            )

    def test_exact_version_match_does_not_match_a_longer_numeric_patch(self) -> None:
        # `2.4.3` must not be satisfied by a hypothetical `2.4.30` release.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rpm_dir = root / "rpms" / "kmods" / "zfs"
            rpm_dir.mkdir(parents=True, exist_ok=True)
            (
                rpm_dir / "kmod-zfs-6.18.16-200.fc43.x86_64-2.4.30-1.fc43.x86_64.rpm"
            ).touch()

            self.assertFalse(
                _has_kernel_matching_rpm(root, "6.18.16-200.fc43.x86_64", "2.4.3")
            )

    def test_inspect_akmods_cache_reads_shared_cache_image(self) -> None:
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

            with patch(
                "ci_tools.check_akmods_cache.skopeo_inspect_json_optional",
                return_value={"Digest": "sha256:abc123"},
            ) as inspect_json_optional, patch(
                "ci_tools.check_akmods_cache.skopeo_copy",
                side_effect=fake_copy,
            ) as skopeo_copy, patch(
                "ci_tools.check_akmods_cache.load_layer_files_from_oci_layout",
                side_effect=fake_load_layers,
            ), patch(
                "ci_tools.check_akmods_cache.unpack_layer_tarballs",
                side_effect=fake_unpack,
            ), patch(
                "ci_tools.check_akmods_cache.cosign_verify"
            ) as cosign_verify:
                status = inspect_akmods_cache(
                    image_org="danathar",
                    source_repo="zfs-aurora-complex-akmods",
                    fedora_version="43",
                    kernel_release="6.18.16-200.fc43.x86_64",
                    zfs_version="2.4.1",
                )

        self.assertTrue(status.reusable)
        self.assertTrue(status.signature_verified)
        self.assertEqual(
            status.source_image_pinned,
            "ghcr.io/danathar/zfs-aurora-complex-akmods@sha256:abc123",
        )
        self.assertEqual(status.inspection_method, "unpacked-image")
        inspect_json_optional.assert_called_once_with(
            "docker://ghcr.io/danathar/zfs-aurora-complex-akmods:main-43"
        )
        skopeo_copy.assert_called_once_with(
            "docker://ghcr.io/danathar/zfs-aurora-complex-akmods@sha256:abc123",
            ANY,
        )
        cosign_verify.assert_called_once_with(
            "ghcr.io/danathar/zfs-aurora-complex-akmods@sha256:abc123",
            key_path=ANY,
        )

    def test_inspect_akmods_cache_rejects_reuse_when_signature_verification_fails(self) -> None:
        # The cache has the right kmod-zfs RPM but is not signed by this
        # repo's key (or is not signed at all) -- reuse must be refused even
        # though the RPM content looks correct. This is the actual fix: a
        # matching filename alone is not proof of who produced the content.
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

            with patch(
                "ci_tools.check_akmods_cache.skopeo_inspect_json_optional",
                return_value={"Digest": "sha256:abc123"},
            ), patch(
                "ci_tools.check_akmods_cache.skopeo_copy",
                side_effect=fake_copy,
            ), patch(
                "ci_tools.check_akmods_cache.load_layer_files_from_oci_layout",
                side_effect=fake_load_layers,
            ), patch(
                "ci_tools.check_akmods_cache.unpack_layer_tarballs",
                side_effect=fake_unpack,
            ), patch(
                "ci_tools.check_akmods_cache.cosign_verify",
                side_effect=CiToolError("no signatures found"),
            ):
                status = inspect_akmods_cache(
                    image_org="danathar",
                    source_repo="zfs-aurora-complex-akmods",
                    fedora_version="43",
                    kernel_release="6.18.16-200.fc43.x86_64",
                    zfs_version="2.4.1",
                )

        self.assertFalse(status.reusable)
        self.assertFalse(status.signature_verified)
        # The RPM content itself was fine; only the signature check failed.
        self.assertEqual(status.missing_release, "")

    def test_inspect_akmods_cache_misses_when_cache_holds_an_older_patch(self) -> None:
        # End-to-end version of the same bug: a real cache image whose only
        # kmod-zfs is 2.4.3 must be rejected when the run resolved 2.4.4.
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
                    rpm_dir / "kmod-zfs-7.0.12-201.fc44.x86_64-2.4.3-1.fc44.x86_64.rpm"
                ).touch()

            with patch(
                "ci_tools.check_akmods_cache.skopeo_inspect_json_optional",
                return_value={"Digest": "sha256:abc123"},
            ), patch(
                "ci_tools.check_akmods_cache.skopeo_copy",
                side_effect=fake_copy,
            ), patch(
                "ci_tools.check_akmods_cache.load_layer_files_from_oci_layout",
                side_effect=fake_load_layers,
            ), patch(
                "ci_tools.check_akmods_cache.unpack_layer_tarballs",
                side_effect=fake_unpack,
            ):
                status = inspect_akmods_cache(
                    image_org="danathar",
                    source_repo="zfs-aurora-complex-akmods",
                    fedora_version="44",
                    kernel_release="7.0.12-201.fc44.x86_64",
                    zfs_version="2.4.4",
                )

        self.assertFalse(status.reusable)
        self.assertEqual(status.missing_release, "7.0.12-201.fc44.x86_64")
        self.assertEqual(status.required_zfs_version, "2.4.4")

    def test_inspect_akmods_cache_reports_missing_image_when_tag_does_not_exist(self) -> None:
        with patch(
            "ci_tools.check_akmods_cache.skopeo_inspect_json_optional",
            return_value=None,
        ):
            status = inspect_akmods_cache(
                image_org="danathar",
                source_repo="zfs-aurora-complex-akmods",
                fedora_version="43",
                kernel_release="6.18.16-200.fc43.x86_64",
                zfs_version="2.4.1",
            )

        self.assertFalse(status.reusable)
        self.assertFalse(status.image_exists)
        self.assertEqual(
            status.source_image,
            "ghcr.io/danathar/zfs-aurora-complex-akmods:main-43",
        )
        self.assertEqual(status.source_image_pinned, "")
        self.assertEqual(status.missing_release, "6.18.16-200.fc43.x86_64")
        self.assertEqual(status.inspection_method, "missing-image")

    def test_inspect_akmods_cache_raises_on_non_missing_registry_error(self) -> None:
        # skopeo_inspect_json_optional already re-raises everything except a
        # missing-image error; inspect_akmods_cache must not swallow it into a
        # false "image_exists=False" the way it used to.
        with patch(
            "ci_tools.check_akmods_cache.skopeo_inspect_json_optional",
            side_effect=CiToolError("unauthorized: authentication required"),
        ), self.assertRaises(CiToolError) as context:
            inspect_akmods_cache(
                image_org="danathar",
                source_repo="zfs-aurora-complex-akmods",
                fedora_version="43",
                kernel_release="6.18.16-200.fc43.x86_64",
                zfs_version="2.4.1",
            )

        self.assertIn("unauthorized", str(context.exception))

    def test_inspect_akmods_cache_raises_ci_error_when_layer_unpacking_fails(self) -> None:
        with patch(
            "ci_tools.check_akmods_cache.skopeo_inspect_json_optional",
            return_value={"Digest": "sha256:abc123"},
        ), patch("ci_tools.check_akmods_cache.skopeo_copy"), patch(
            "ci_tools.check_akmods_cache.load_layer_files_from_oci_layout",
            side_effect=RuntimeError("No layers found in OCI layout"),
        ), self.assertRaises(CiToolError) as context:
            inspect_akmods_cache(
                image_org="danathar",
                source_repo="zfs-aurora-complex-akmods",
                fedora_version="43",
                kernel_release="6.18.16-200.fc43.x86_64",
                zfs_version="2.4.1",
            )

        self.assertIn("No layers found in OCI layout", str(context.exception))


class RequireMatchModeTests(unittest.TestCase):
    """
    Covers the strict mode used to verify a cache this run just rebuilt.

    The akmods fork resolves its own OpenZFS patch version independently of
    this repo, so a rebuild can publish a cache that does not contain the
    version this run resolved and is about to record as an image label. In
    normal mode that is just "rebuild required"; after a rebuild it is a
    failure, because there is nothing left to retry and the label would lie.
    """

    _ENV: ClassVar[dict[str, str]] = {
        "GITHUB_REPOSITORY_OWNER": "Danathar",
        "FEDORA_VERSION": "43",
        "KERNEL_RELEASE": "6.18.16-200.fc43.x86_64",
        "AKMODS_REPO": "zfs-aurora-complex-akmods",
        "ZFS_VERSION": "2.4.4",
    }

    def test_require_match_raises_when_the_rebuilt_cache_does_not_match(self) -> None:
        mismatched = AkmodsCacheStatus(
            source_image="ghcr.io/danathar/zfs-aurora-complex-akmods:main-43",
            image_exists=True,
            source_image_pinned="ghcr.io/danathar/zfs-aurora-complex-akmods@sha256:abc",
            missing_release="6.18.16-200.fc43.x86_64",
            required_zfs_version="2.4.4",
        )
        env = {**self._ENV, "REQUIRE_MATCH": "true"}
        with patch.dict(os.environ, env, clear=False), patch(
            "ci_tools.check_akmods_cache.inspect_akmods_cache", return_value=mismatched
        ), self.assertRaises(CiToolError) as context:
            main()

        self.assertIn("2.4.4", str(context.exception))
        self.assertIn("even after a rebuild", str(context.exception))

    def test_require_match_is_silent_when_the_rebuilt_cache_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output"
            matched = AkmodsCacheStatus(
                source_image="ghcr.io/danathar/zfs-aurora-complex-akmods:main-43",
                image_exists=True,
                source_image_pinned="ghcr.io/danathar/zfs-aurora-complex-akmods@sha256:abc",
                missing_release="",
                required_zfs_version="2.4.4",
            )
            env = {**self._ENV, "REQUIRE_MATCH": "true", "GITHUB_OUTPUT": str(output_path)}
            with patch.dict(os.environ, env, clear=False), patch(
                "ci_tools.check_akmods_cache.inspect_akmods_cache", return_value=matched
            ):
                main()

    def test_require_match_does_not_demand_a_signature(self) -> None:
        # Regression guard for a bug that existed in neither change alone.
        # Exact-version verification runs inside the akmods job right after a
        # rebuild; cache signing runs in a *later, separate* job. So the cache
        # is always unsigned at verification time. If this check asked for
        # `reusable` (which requires a signature) instead of `content_matches`,
        # every single rebuild would fail.
        unsigned_but_correct = AkmodsCacheStatus(
            source_image="ghcr.io/danathar/zfs-aurora-complex-akmods:main-43",
            image_exists=True,
            source_image_pinned="ghcr.io/danathar/zfs-aurora-complex-akmods@sha256:abc",
            missing_release="",
            required_zfs_version="2.4.4",
            signature_verified=False,
        )
        self.assertTrue(unsigned_but_correct.content_matches)
        self.assertFalse(unsigned_but_correct.reusable)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output"
            env = {**self._ENV, "REQUIRE_MATCH": "true", "GITHUB_OUTPUT": str(output_path)}
            with patch.dict(os.environ, env, clear=False), patch(
                "ci_tools.check_akmods_cache.inspect_akmods_cache",
                return_value=unsigned_but_correct,
            ) as inspect_cache:
                main()

            # It must also skip the cosign call entirely, rather than making a
            # request that is guaranteed to fail against an unsigned image.
            self.assertFalse(inspect_cache.call_args.kwargs["verify_signature"])

    def test_reuse_path_still_verifies_the_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output"
            env = {**self._ENV, "GITHUB_OUTPUT": str(output_path)}
            with patch.dict(os.environ, env, clear=False), patch(
                "ci_tools.check_akmods_cache.inspect_akmods_cache",
                return_value=AkmodsCacheStatus(
                    source_image="ghcr.io/danathar/zfs-aurora-complex-akmods:main-43",
                    image_exists=True,
                    source_image_pinned=(
                        "ghcr.io/danathar/zfs-aurora-complex-akmods@sha256:abc"
                    ),
                    missing_release="",
                    required_zfs_version="2.4.4",
                    signature_verified=True,
                ),
            ) as inspect_cache:
                main()

            self.assertTrue(inspect_cache.call_args.kwargs["verify_signature"])

    def test_default_mode_still_reports_a_mismatch_without_raising(self) -> None:
        # The pre-rebuild check must keep treating "no usable cache" as a
        # normal answer that triggers a rebuild, not as a failure.
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output"
            mismatched = AkmodsCacheStatus(
                source_image="ghcr.io/danathar/zfs-aurora-complex-akmods:main-43",
                image_exists=True,
                source_image_pinned="ghcr.io/danathar/zfs-aurora-complex-akmods@sha256:abc",
                missing_release="6.18.16-200.fc43.x86_64",
                required_zfs_version="2.4.4",
            )
            env = {**self._ENV, "GITHUB_OUTPUT": str(output_path)}
            with patch.dict(os.environ, env, clear=False), patch(
                "ci_tools.check_akmods_cache.inspect_akmods_cache", return_value=mismatched
            ):
                main()

            self.assertIn("exists<<", output_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
