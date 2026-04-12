"""
Script: tests/test_resolve_build_inputs.py
What: Tests for input-resolution tag selection.
Doing: Checks immutable-tag reuse, candidate-tag derivation, and failure paths.
Why: Protects the logic that pins run inputs and avoids moving-tag drift.
Goal: Keep input resolution predictable and explainable.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_tools.common import CiToolError, sort_kernel_releases
from ci_tools.resolve_build_inputs import (
    _resolve_default_akmods_ref,
    choose_base_image_tag,
    resolve_configured_inputs,
)


class ChooseBaseImageTagTests(unittest.TestCase):
    def test_keeps_existing_date_stamped_source_tag(self) -> None:
        tag, checked = choose_base_image_tag(
            source_tag="latest-20260227",
            version_label="43.20260227.1",
            fedora_version="43",
            expected_digest="sha256:abc",
            digest_lookup=lambda _tag: "sha256:abc",
        )
        self.assertEqual(tag, "latest-20260227")
        self.assertEqual(checked, ["latest-20260227"])

    def test_derives_tag_from_version_label_and_digest_match(self) -> None:
        digests = {
            "latest-20260227.1": "sha256:match",
            "43-20260227.1": "sha256:other",
        }

        tag, checked = choose_base_image_tag(
            source_tag="latest",
            version_label="43.20260227.1",
            fedora_version="43",
            expected_digest="sha256:match",
            digest_lookup=lambda t: digests.get(t, ""),
        )
        self.assertEqual(tag, "latest-20260227.1")
        self.assertEqual(checked, ["latest-20260227.1", "43-20260227.1"])

    def test_derives_tag_from_prefixed_version_label_and_digest_match(self) -> None:
        digests = {
            "latest-43.20260324": "sha256:match",
            "latest-20260324.1": "sha256:other",
            "43-20260324.1": "sha256:other",
            "43-43.20260324": "sha256:other",
        }

        tag, checked = choose_base_image_tag(
            source_tag="latest",
            version_label="latest-43.20260324.1",
            fedora_version="43",
            expected_digest="sha256:match",
            digest_lookup=lambda t: digests.get(t, ""),
        )
        self.assertEqual(tag, "latest-43.20260324")
        self.assertIn("latest-43.20260324", checked)
        self.assertIn("latest-20260324", checked)
        self.assertIn("43-43.20260324", checked)

    def test_rejects_unexpected_version_label(self) -> None:
        with self.assertRaises(CiToolError):
            choose_base_image_tag(
                source_tag="latest",
                version_label="bad-version",
                fedora_version="43",
                expected_digest="sha256:abc",
                digest_lookup=lambda _tag: "",
            )


class SortKernelReleasesTests(unittest.TestCase):
    def test_sorts_kernel_releases_naturally(self) -> None:
        releases = sort_kernel_releases(
            [
                "6.18.10-200.fc43.x86_64",
                "6.18.9-200.fc43.x86_64",
                "6.18.12-200.fc43.x86_64",
            ]
        )
        self.assertEqual(
            releases,
            [
                "6.18.9-200.fc43.x86_64",
                "6.18.10-200.fc43.x86_64",
                "6.18.12-200.fc43.x86_64",
            ],
        )

    def test_deduplicates_kernel_releases_while_preserving_order(self) -> None:
        releases = sort_kernel_releases(
            [
                "6.18.12-200.fc43.x86_64",
                "6.18.10-200.fc43.x86_64",
                "6.18.12-200.fc43.x86_64",
            ]
        )
        self.assertEqual(
            releases,
            [
                "6.18.10-200.fc43.x86_64",
                "6.18.12-200.fc43.x86_64",
            ],
        )


class ResolveDefaultAkmodsRefTests(unittest.TestCase):
    """Cascade: explicit env > defaults-file pin > git ls-remote against tracking ref."""

    def _env(self, **overrides: str) -> dict:
        wipe = {
            "DEFAULT_AKMODS_REF": "",
            "AKMODS_UPSTREAM_REF": "",
            "AKMODS_UPSTREAM_TRACK": "",
            "AKMODS_UPSTREAM_REPO": "",
        }
        wipe.update(overrides)
        return wipe

    def test_env_ref_wins_over_everything(self) -> None:
        defaults = {
            "AKMODS_UPSTREAM_REF": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            "AKMODS_UPSTREAM_TRACK": "main",
            "AKMODS_UPSTREAM_REPO": "https://example.invalid/akmods.git",
        }
        with patch.dict(os.environ, self._env(AKMODS_UPSTREAM_REF="cafef00d" * 5), clear=False):
            with patch("ci_tools.resolve_build_inputs.load_repo_defaults", return_value=defaults):
                with patch("ci_tools.resolve_build_inputs.git_ls_remote_resolve") as ls_remote:
                    resolved = _resolve_default_akmods_ref()
        self.assertEqual(resolved, "cafef00d" * 5)
        ls_remote.assert_not_called()

    def test_defaults_file_pin_used_when_env_empty(self) -> None:
        defaults = {
            "AKMODS_UPSTREAM_REF": "0e06cd70879aa5063c4193710d8c7e37bbc2ab57",
            "AKMODS_UPSTREAM_TRACK": "main",
            "AKMODS_UPSTREAM_REPO": "https://example.invalid/akmods.git",
        }
        with patch.dict(os.environ, self._env(), clear=False):
            with patch("ci_tools.resolve_build_inputs.load_repo_defaults", return_value=defaults):
                with patch("ci_tools.resolve_build_inputs.git_ls_remote_resolve") as ls_remote:
                    resolved = _resolve_default_akmods_ref()
        self.assertEqual(resolved, "0e06cd70879aa5063c4193710d8c7e37bbc2ab57")
        ls_remote.assert_not_called()

    def test_floats_to_tracking_ref_when_nothing_pinned(self) -> None:
        defaults = {
            "AKMODS_UPSTREAM_REF": "",
            "AKMODS_UPSTREAM_TRACK": "main",
            "AKMODS_UPSTREAM_REPO": "https://example.invalid/akmods.git",
        }
        with patch.dict(os.environ, self._env(), clear=False):
            with patch("ci_tools.resolve_build_inputs.load_repo_defaults", return_value=defaults):
                with patch(
                    "ci_tools.resolve_build_inputs.git_ls_remote_resolve",
                    return_value="a" * 40,
                ) as ls_remote:
                    resolved = _resolve_default_akmods_ref()
        self.assertEqual(resolved, "a" * 40)
        ls_remote.assert_called_once_with("https://example.invalid/akmods.git", "main")

    def test_raises_when_nothing_is_configured(self) -> None:
        defaults = {"AKMODS_UPSTREAM_REF": "", "AKMODS_UPSTREAM_TRACK": "", "AKMODS_UPSTREAM_REPO": ""}
        with patch.dict(os.environ, self._env(), clear=False):
            with patch("ci_tools.resolve_build_inputs.load_repo_defaults", return_value=defaults):
                with self.assertRaises(CiToolError):
                    _resolve_default_akmods_ref()


class LockFileAkmodsRefInvariantTests(unittest.TestCase):
    """
    The checked-in ci/inputs.lock.json must not carry its own akmods_upstream_ref.
    ci/defaults.json is the one source of truth for the pinned akmods commit, and
    a divergent value in the lock file would silently win during replay runs.
    """

    def test_repo_lock_file_does_not_pin_akmods_upstream_ref(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        lock_path = repo_root / "ci" / "inputs.lock.json"
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertNotIn(
            "akmods_upstream_ref",
            data,
            "ci/inputs.lock.json must not pin akmods_upstream_ref; it comes from ci/defaults.json",
        )

    def test_lock_replay_without_akmods_ref_falls_back_to_defaults(self) -> None:
        lock_payload = {
            "version": 1,
            "base_image": "ghcr.io/example/base@sha256:deadbeef",
            "build_container": "ghcr.io/example/build@sha256:cafef00d",
            "zfs_minor_version": "2.4",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "inputs.lock.json"
            lock_path.write_text(json.dumps(lock_payload), encoding="utf-8")
            env = {
                "USE_INPUT_LOCK": "true",
                "LOCK_FILE": str(lock_path),
                "BUILD_CONTAINER_REF": "ghcr.io/example/build@sha256:cafef00d",
                "DEFAULT_AKMODS_REF": "abcdef1234567890",
            }
            with patch.dict(os.environ, env, clear=False):
                (
                    use_input_lock,
                    _lock_file_path,
                    _build_container_ref,
                    base_image_ref,
                    zfs_minor_version,
                    akmods_upstream_ref,
                ) = resolve_configured_inputs()

        self.assertTrue(use_input_lock)
        self.assertEqual(base_image_ref, "ghcr.io/example/base@sha256:deadbeef")
        self.assertEqual(zfs_minor_version, "2.4")
        self.assertEqual(akmods_upstream_ref, "abcdef1234567890")


if __name__ == "__main__":
    unittest.main()
