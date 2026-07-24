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
    extract_source_tag,
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

    def test_rejects_date_stamped_source_tag_when_digest_moved(self) -> None:
        with self.assertRaises(CiToolError):
            choose_base_image_tag(
                source_tag="latest-20260227",
                version_label="43.20260227.1",
                fedora_version="43",
                expected_digest="sha256:abc",
                digest_lookup=lambda _tag: "sha256:moved",
            )

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
        self.assertEqual(checked, ["43.20260227.1", "latest-20260227.1", "43-20260227.1"])

    def test_derives_tag_from_bare_version_label_when_only_it_matches(self) -> None:
        # Some ublue images publish a tag equal to org.opencontainers.image.version
        # verbatim, with none of the other derived candidate forms present.
        digests = {"43.20260610.3": "sha256:match"}

        tag, checked = choose_base_image_tag(
            source_tag="latest",
            version_label="43.20260610.3",
            fedora_version="43",
            expected_digest="sha256:match",
            digest_lookup=lambda t: digests.get(t, ""),
        )
        self.assertEqual(tag, "43.20260610.3")
        self.assertEqual(checked[0], "43.20260610.3")

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


class ExtractSourceTagTests(unittest.TestCase):
    def test_extract_source_tag_from_standard_tagged_ref(self) -> None:
        self.assertEqual(extract_source_tag("ghcr.io/x/y:latest"), "latest")

    def test_extract_source_tag_returns_empty_for_untagged_ref(self) -> None:
        self.assertEqual(extract_source_tag("ghcr.io/x/y"), "")

    def test_extract_source_tag_rejects_host_port_only_ref(self) -> None:
        self.assertEqual(extract_source_tag("localhost:5000/x/y"), "")

    def test_extract_source_tag_accepts_tag_after_host_port(self) -> None:
        self.assertEqual(extract_source_tag("localhost:5000/x/y:latest"), "latest")

    def test_extract_source_tag_rejects_digest_ref(self) -> None:
        self.assertEqual(extract_source_tag("ghcr.io/x/y@sha256:abc"), "")


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

    def test_env_sha_ref_wins_over_everything(self) -> None:
        defaults = {
            "AKMODS_UPSTREAM_REF": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            "AKMODS_UPSTREAM_TRACK": "main",
            "AKMODS_UPSTREAM_REPO": "https://example.invalid/akmods.git",
        }
        with (
            patch.dict(os.environ, self._env(AKMODS_UPSTREAM_REF="cafef00d" * 5), clear=False),
            patch("ci_tools.resolve_build_inputs.load_repo_defaults", return_value=defaults),
            patch("ci_tools.resolve_build_inputs.git_ls_remote_resolve") as ls_remote,
        ):
            resolved = _resolve_default_akmods_ref()
        self.assertEqual(resolved, "cafef00d" * 5)
        ls_remote.assert_not_called()

    def test_env_branch_ref_resolves_with_ls_remote(self) -> None:
        defaults = {
            "AKMODS_UPSTREAM_REF": "",
            "AKMODS_UPSTREAM_TRACK": "main",
            "AKMODS_UPSTREAM_REPO": "https://example.invalid/akmods.git",
        }
        with (
            patch.dict(os.environ, self._env(AKMODS_UPSTREAM_REF="main"), clear=False),
            patch("ci_tools.resolve_build_inputs.load_repo_defaults", return_value=defaults),
            patch(
                "ci_tools.resolve_build_inputs.git_ls_remote_resolve",
                return_value="b" * 40,
            ) as ls_remote,
        ):
            resolved = _resolve_default_akmods_ref()
        self.assertEqual(resolved, "b" * 40)
        ls_remote.assert_called_once_with("https://example.invalid/akmods.git", "main")

    def test_env_tag_ref_resolves_with_ls_remote(self) -> None:
        defaults = {
            "AKMODS_UPSTREAM_REF": "",
            "AKMODS_UPSTREAM_TRACK": "main",
            "AKMODS_UPSTREAM_REPO": "https://example.invalid/akmods.git",
        }
        with (
            patch.dict(os.environ, self._env(AKMODS_UPSTREAM_REF="v2.4.0"), clear=False),
            patch("ci_tools.resolve_build_inputs.load_repo_defaults", return_value=defaults),
            patch(
                "ci_tools.resolve_build_inputs.git_ls_remote_resolve",
                return_value="c" * 40,
            ) as ls_remote,
        ):
            resolved = _resolve_default_akmods_ref()
        self.assertEqual(resolved, "c" * 40)
        ls_remote.assert_called_once_with("https://example.invalid/akmods.git", "v2.4.0")

    def test_defaults_file_sha_pin_used_when_env_empty(self) -> None:
        defaults = {
            "AKMODS_UPSTREAM_REF": "0e06cd70879aa5063c4193710d8c7e37bbc2ab57",
            "AKMODS_UPSTREAM_TRACK": "main",
            "AKMODS_UPSTREAM_REPO": "https://example.invalid/akmods.git",
        }
        with (
            patch.dict(os.environ, self._env(), clear=False),
            patch("ci_tools.resolve_build_inputs.load_repo_defaults", return_value=defaults),
            patch("ci_tools.resolve_build_inputs.git_ls_remote_resolve") as ls_remote,
        ):
            resolved = _resolve_default_akmods_ref()
        self.assertEqual(resolved, "0e06cd70879aa5063c4193710d8c7e37bbc2ab57")
        ls_remote.assert_not_called()

    def test_defaults_file_branch_pin_resolves_with_ls_remote(self) -> None:
        defaults = {
            "AKMODS_UPSTREAM_REF": "main",
            "AKMODS_UPSTREAM_TRACK": "stable",
            "AKMODS_UPSTREAM_REPO": "https://example.invalid/akmods.git",
        }
        with (
            patch.dict(os.environ, self._env(), clear=False),
            patch("ci_tools.resolve_build_inputs.load_repo_defaults", return_value=defaults),
            patch(
                "ci_tools.resolve_build_inputs.git_ls_remote_resolve",
                return_value="d" * 40,
            ) as ls_remote,
        ):
            resolved = _resolve_default_akmods_ref()
        self.assertEqual(resolved, "d" * 40)
        ls_remote.assert_called_once_with("https://example.invalid/akmods.git", "main")

    def test_floats_to_tracking_ref_when_nothing_pinned(self) -> None:
        defaults = {
            "AKMODS_UPSTREAM_REF": "",
            "AKMODS_UPSTREAM_TRACK": "main",
            "AKMODS_UPSTREAM_REPO": "https://example.invalid/akmods.git",
        }
        with (
            patch.dict(os.environ, self._env(), clear=False),
            patch("ci_tools.resolve_build_inputs.load_repo_defaults", return_value=defaults),
            patch(
                "ci_tools.resolve_build_inputs.git_ls_remote_resolve",
                return_value="a" * 40,
            ) as ls_remote,
        ):
            resolved = _resolve_default_akmods_ref()
        self.assertEqual(resolved, "a" * 40)
        ls_remote.assert_called_once_with("https://example.invalid/akmods.git", "main")

    def test_raises_when_nothing_is_configured(self) -> None:
        defaults = {"AKMODS_UPSTREAM_REF": "", "AKMODS_UPSTREAM_TRACK": "", "AKMODS_UPSTREAM_REPO": ""}
        with (
            patch.dict(os.environ, self._env(), clear=False),
            patch("ci_tools.resolve_build_inputs.load_repo_defaults", return_value=defaults),
            self.assertRaises(CiToolError),
        ):
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
                "DEFAULT_AKMODS_REF": "a" * 40,
            }
            with patch.dict(os.environ, env, clear=False):
                configured = resolve_configured_inputs()

        self.assertTrue(configured.use_input_lock)
        self.assertEqual(configured.base_image_ref, "ghcr.io/example/base@sha256:deadbeef")
        self.assertEqual(configured.zfs_minor_version, "2.4")
        self.assertEqual(configured.akmods_upstream_ref, "a" * 40)


if __name__ == "__main__":
    unittest.main()
