"""
Script: tests/test_pin_akmods_cache.py
What: Tests for digest-pinning the shared akmods cache image.
Doing: Checks tag construction, conversion from a mutable tag to an immutable digest reference, and
the refusal to pin a digest whose config is not the image this run built.
Why: The final image build must consume the exact akmods cache image verified or published earlier in the workflow.
Goal: Prevent regressions back to mutable akmods cache tags in downstream jobs.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_tools.common import REGISTRY_METADATA_TIMEOUT, CiToolError
from ci_tools.pin_akmods_cache import (
    akmods_cache_image_tag,
    main,
    pin_akmods_cache_image,
    pushed_config_hex,
)

IMAGE_TAG = "ghcr.io/danathar/zfs-aurora-complex-akmods:main-43"
DIGEST = "sha256:" + "1" * 64
PINNED = "ghcr.io/danathar/zfs-aurora-complex-akmods@" + DIGEST

# What `skopeo inspect --raw --config` prints for the image this run built, and the ID
# `podman image inspect --format '{{.Id}}'` reports for it: the sha256 of those bytes.
BUILT_CONFIG = '{"architecture":"amd64","os":"linux","rootfs":{"type":"layers","diff_ids":[]}}'
BUILT_IMAGE_ID = hashlib.sha256(BUILT_CONFIG.encode("utf-8")).hexdigest()
OTHER_CONFIG = '{"architecture":"amd64","os":"linux","rootfs":{"type":"layers","diff_ids":["x"]}}'


class PinAkmodsCacheTests(unittest.TestCase):
    def test_builds_shared_cache_tag_for_fedora_version(self) -> None:
        image_tag = akmods_cache_image_tag(
            image_org="danathar",
            source_repo="zfs-aurora-complex-akmods",
            fedora_version="43",
        )

        self.assertEqual(image_tag, "ghcr.io/danathar/zfs-aurora-complex-akmods:main-43")

    def test_pins_tag_to_digest_reference(self) -> None:
        with (
            patch(
                "ci_tools.pin_akmods_cache.skopeo_inspect_digest",
                return_value=DIGEST,
            ) as inspect_digest,
            patch(
                "ci_tools.pin_akmods_cache.run_cmd", return_value=BUILT_CONFIG
            ) as run_cmd,
        ):
            image_pinned, image_digest = pin_akmods_cache_image(IMAGE_TAG, BUILT_IMAGE_ID)

        self.assertEqual(image_pinned, PINNED)
        # The bare digest is returned separately so the signing job can sign
        # exactly this image instead of re-resolving the shared tag.
        self.assertEqual(image_digest, DIGEST)
        inspect_digest.assert_called_once_with(f"docker://{IMAGE_TAG}")
        # The config is read from the digest just resolved, never from the tag
        # a second time, which could have moved again in between.
        run_cmd.assert_called_once_with(
            [
                "skopeo",
                "--override-os",
                "linux",
                "--override-arch",
                "amd64",
                "inspect",
                "--raw",
                "--config",
                f"docker://{PINNED}",
            ],
            timeout=REGISTRY_METADATA_TIMEOUT,
        )

    def test_pushed_config_hex_hashes_the_raw_config_skopeo_prints(self) -> None:
        with patch("ci_tools.pin_akmods_cache.run_cmd", return_value=BUILT_CONFIG):
            self.assertEqual(pushed_config_hex(PINNED), BUILT_IMAGE_ID)

    def test_accepts_a_sha256_prefixed_built_image_id(self) -> None:
        with (
            patch("ci_tools.pin_akmods_cache.skopeo_inspect_digest", return_value=DIGEST),
            patch("ci_tools.pin_akmods_cache.run_cmd", return_value=BUILT_CONFIG),
        ):
            image_pinned, _ = pin_akmods_cache_image(IMAGE_TAG, f"sha256:{BUILT_IMAGE_ID}\n")

        self.assertEqual(image_pinned, PINNED)

    def test_refuses_a_digest_whose_config_is_not_the_image_this_run_built(self) -> None:
        # The shared tag moved between this run's push and the read-back: a
        # different run pushed its own image there. Pinning it would get it signed.
        with (
            patch("ci_tools.pin_akmods_cache.skopeo_inspect_digest", return_value=DIGEST),
            patch("ci_tools.pin_akmods_cache.run_cmd", return_value=OTHER_CONFIG),
            self.assertRaisesRegex(
                CiToolError,
                rf"Refusing to pin akmods cache {PINNED}: .* image this run built is "
                rf"sha256:{BUILT_IMAGE_ID}",
            ),
        ):
            pin_akmods_cache_image(IMAGE_TAG, BUILT_IMAGE_ID)

    def test_refuses_without_a_built_image_id_before_reading_the_registry(self) -> None:
        for value in ("", "   "):
            with (
                self.subTest(value=value),
                patch("ci_tools.pin_akmods_cache.skopeo_inspect_digest") as inspect_digest,
                patch("ci_tools.pin_akmods_cache.run_cmd") as run_cmd,
                self.assertRaisesRegex(CiToolError, "AKMODS_BUILT_IMAGE_ID"),
            ):
                pin_akmods_cache_image(IMAGE_TAG, value)
            # No fallback to the tag: nothing is resolved at all.
            inspect_digest.assert_not_called()
            run_cmd.assert_not_called()

    def test_refuses_a_malformed_built_image_id(self) -> None:
        for value in (
            BUILT_IMAGE_ID[:-1],  # truncated
            BUILT_IMAGE_ID.upper(),  # podman prints lowercase hex
            f"sha512:{BUILT_IMAGE_ID}",
            f"{BUILT_IMAGE_ID} {BUILT_IMAGE_ID}",
            "localhost/zfs-aurora-complex-akmods:main-43-x86_64",
        ):
            with (
                self.subTest(value=value),
                patch("ci_tools.pin_akmods_cache.skopeo_inspect_digest") as inspect_digest,
                self.assertRaisesRegex(CiToolError, "is not the sha256 image ID"),
            ):
                pin_akmods_cache_image(IMAGE_TAG, value)
            inspect_digest.assert_not_called()

    def test_main_writes_mutable_and_pinned_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output"
            env = {
                "GITHUB_REPOSITORY_OWNER": "Danathar",
                "FEDORA_VERSION": "43",
                "AKMODS_REPO": "zfs-aurora-complex-akmods",
                "AKMODS_BUILT_IMAGE_ID": BUILT_IMAGE_ID,
                "GITHUB_OUTPUT": str(output_path),
            }
            with (
                patch.dict(os.environ, env, clear=False),
                patch(
                    "ci_tools.pin_akmods_cache.skopeo_inspect_digest",
                    return_value=DIGEST,
                ),
                patch("ci_tools.pin_akmods_cache.run_cmd", return_value=BUILT_CONFIG),
            ):
                main()

            output = output_path.read_text(encoding="utf-8")

        self.assertIn("akmods_image<<", output)
        self.assertIn(IMAGE_TAG, output)
        self.assertIn("akmods_image_pinned<<", output)
        self.assertIn(PINNED, output)
        # The signing job consumes this one directly as IMAGE_DIGEST.
        self.assertIn("akmods_image_digest<<", output)

    def test_main_without_a_built_image_id_publishes_no_pin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output"
            env = {
                "GITHUB_REPOSITORY_OWNER": "Danathar",
                "FEDORA_VERSION": "43",
                "AKMODS_REPO": "zfs-aurora-complex-akmods",
                "GITHUB_OUTPUT": str(output_path),
            }
            with (
                patch.dict(os.environ, env, clear=False),
                patch.dict(os.environ, {"AKMODS_BUILT_IMAGE_ID": ""}),
                patch("ci_tools.pin_akmods_cache.skopeo_inspect_digest") as inspect_digest,
                self.assertRaisesRegex(CiToolError, "AKMODS_BUILT_IMAGE_ID"),
            ):
                main()

            inspect_digest.assert_not_called()
            self.assertFalse(output_path.exists())

    def test_main_refuses_a_moved_tag_and_publishes_no_pin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output"
            env = {
                "GITHUB_REPOSITORY_OWNER": "Danathar",
                "FEDORA_VERSION": "43",
                "AKMODS_REPO": "zfs-aurora-complex-akmods",
                "AKMODS_BUILT_IMAGE_ID": BUILT_IMAGE_ID,
                "GITHUB_OUTPUT": str(output_path),
            }
            with (
                patch.dict(os.environ, env, clear=False),
                patch("ci_tools.pin_akmods_cache.skopeo_inspect_digest", return_value=DIGEST),
                patch("ci_tools.pin_akmods_cache.run_cmd", return_value=OTHER_CONFIG),
                self.assertRaisesRegex(CiToolError, "Refusing to pin akmods cache"),
            ):
                main()

            # Nothing for the signing job or the candidate build to consume.
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
