"""
Script: tests/test_check_stable_signal.py
What: Tests for the scheduled stable-signal build gate helper.
Doing: Mocks registry inspection results and checks the gate outputs without network access.
Why: The gate must skip only unchanged schedule runs and fail closed on unknown upstream state.
Goal: Keep the schedule-only cadence signal explicit and testable.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_tools.check_stable_signal import (
    STABLE_SIGNAL_DIGEST_LABEL,
    STABLE_SIGNAL_IMAGE_LABEL,
    ZFS_VERSION_LABEL,
    StableSignalDecision,
    _bypass_decision,
    evaluate_stable_signal_gate,
    main,
)
from ci_tools.common import CiToolError
from tests.test_common import parse_github_file


@contextlib.contextmanager
def _patched_registry_inspect(side_effect):
    """
    Patch every path `evaluate_stable_signal_gate` uses to reach `skopeo_inspect_json`.

    The stable-signal-image call goes through the name imported directly into
    `ci_tools.check_stable_signal`. The current-`:latest` call goes through the
    real (unmocked) `skopeo_inspect_json_optional`, which is defined in
    `ci_tools.common` and looks up `skopeo_inspect_json` in that module's own
    namespace. One side effect needs to be installed in both places so a
    single dispatcher function can answer both calls.
    """
    with (
        patch("ci_tools.check_stable_signal.skopeo_inspect_json", side_effect=side_effect),
        patch("ci_tools.common.skopeo_inspect_json", side_effect=side_effect),
    ):
        yield


def _stable_signal_inspect(digest: str = "sha256:stable") -> dict:
    return {
        "Name": "ghcr.io/ublue-os/aurora-dx",
        "Digest": digest,
        "Labels": {},
    }


def _current_latest_inspect(
    *, signal_image: str, signal_digest: str, zfs_version: str = "2.4.3"
) -> dict:
    labels = {
        STABLE_SIGNAL_IMAGE_LABEL: signal_image,
        STABLE_SIGNAL_DIGEST_LABEL: signal_digest,
    }
    if zfs_version:
        labels[ZFS_VERSION_LABEL] = zfs_version
    return {
        "Name": "ghcr.io/danathar/zfs-aurora-complex",
        "Digest": "sha256:repo-latest",
        "Labels": labels,
    }


class EvaluateStableSignalGateTests(unittest.TestCase):
    def test_unchanged_signal_skips_schedule_build(self) -> None:
        def inspect(image_ref: str, *, creds: str | None = None) -> dict:
            if image_ref == "docker://ghcr.io/ublue-os/aurora-dx:stable":
                self.assertIsNone(creds)
                return _stable_signal_inspect("sha256:same")
            if image_ref == "docker://ghcr.io/danathar/zfs-aurora-complex:latest":
                self.assertEqual(creds, "actor:token")
                return _current_latest_inspect(
                    signal_image="ghcr.io/ublue-os/aurora-dx:stable",
                    signal_digest="sha256:same",
                    zfs_version="2.4.3",
                )
            raise AssertionError(image_ref)

        with _patched_registry_inspect(inspect), patch(
            "ci_tools.check_stable_signal.resolve_latest_zfs_version",
            return_value="2.4.3",
        ):
            decision = evaluate_stable_signal_gate(
                image_org="danathar",
                image_name="zfs-aurora-complex",
                stable_signal_image="ghcr.io/ublue-os/aurora-dx:stable",
                zfs_minor_version="2.4",
                creds="actor:token",
            )

        self.assertFalse(decision.should_build)
        self.assertEqual(decision.reason, "stable-signal-unchanged")
        self.assertEqual(
            decision.stable_signal_ref,
            "ghcr.io/ublue-os/aurora-dx:stable",
        )
        self.assertEqual(decision.stable_signal_digest, "sha256:same")
        self.assertEqual(decision.zfs_version, "2.4.3")

    def test_changed_signal_builds(self) -> None:
        def inspect(image_ref: str, *, creds: str | None = None) -> dict:
            del creds
            if image_ref == "docker://ghcr.io/ublue-os/aurora-dx:stable":
                return _stable_signal_inspect("sha256:new")
            if image_ref == "docker://ghcr.io/danathar/zfs-aurora-complex:latest":
                return _current_latest_inspect(
                    signal_image="ghcr.io/ublue-os/aurora-dx:stable",
                    signal_digest="sha256:old",
                    zfs_version="2.4.3",
                )
            raise AssertionError(image_ref)

        with _patched_registry_inspect(inspect), patch(
            "ci_tools.check_stable_signal.resolve_latest_zfs_version",
            return_value="2.4.3",
        ):
            decision = evaluate_stable_signal_gate(
                image_org="danathar",
                image_name="zfs-aurora-complex",
                stable_signal_image="ghcr.io/ublue-os/aurora-dx:stable",
                zfs_minor_version="2.4",
                creds="actor:token",
            )

        self.assertTrue(decision.should_build)
        self.assertEqual(decision.reason, "stable-signal-advanced")

    def test_zfs_version_advanced_builds_even_when_stable_signal_unchanged(self) -> None:
        # This is the fix: a new OpenZFS patch on the configured line must
        # force a build even when Aurora stable has not moved at all.
        def inspect(image_ref: str, *, creds: str | None = None) -> dict:
            del creds
            if image_ref == "docker://ghcr.io/ublue-os/aurora-dx:stable":
                return _stable_signal_inspect("sha256:same")
            if image_ref == "docker://ghcr.io/danathar/zfs-aurora-complex:latest":
                return _current_latest_inspect(
                    signal_image="ghcr.io/ublue-os/aurora-dx:stable",
                    signal_digest="sha256:same",
                    zfs_version="2.4.3",
                )
            raise AssertionError(image_ref)

        with _patched_registry_inspect(inspect), patch(
            "ci_tools.check_stable_signal.resolve_latest_zfs_version",
            return_value="2.4.4",
        ):
            decision = evaluate_stable_signal_gate(
                image_org="danathar",
                image_name="zfs-aurora-complex",
                stable_signal_image="ghcr.io/ublue-os/aurora-dx:stable",
                zfs_minor_version="2.4",
                creds="actor:token",
            )

        self.assertTrue(decision.should_build)
        self.assertEqual(decision.reason, "zfs-version-advanced")
        self.assertEqual(decision.zfs_version, "2.4.4")

    def test_missing_zfs_version_label_builds(self) -> None:
        # :latest predates this label (or was built by a run before this
        # feature existed). Treat that the same as "we don't know", not "no
        # change".
        def inspect(image_ref: str, *, creds: str | None = None) -> dict:
            del creds
            if image_ref == "docker://ghcr.io/ublue-os/aurora-dx:stable":
                return _stable_signal_inspect("sha256:same")
            if image_ref == "docker://ghcr.io/danathar/zfs-aurora-complex:latest":
                return _current_latest_inspect(
                    signal_image="ghcr.io/ublue-os/aurora-dx:stable",
                    signal_digest="sha256:same",
                    zfs_version="",
                )
            raise AssertionError(image_ref)

        with _patched_registry_inspect(inspect), patch(
            "ci_tools.check_stable_signal.resolve_latest_zfs_version",
            return_value="2.4.3",
        ):
            decision = evaluate_stable_signal_gate(
                image_org="danathar",
                image_name="zfs-aurora-complex",
                stable_signal_image="ghcr.io/ublue-os/aurora-dx:stable",
                zfs_minor_version="2.4",
                creds="actor:token",
            )

        self.assertTrue(decision.should_build)
        self.assertEqual(decision.reason, "current-latest-missing-zfs-version-label")
        self.assertEqual(decision.zfs_version, "2.4.3")

    def test_missing_previous_image_builds(self) -> None:
        def inspect(image_ref: str, *, creds: str | None = None) -> dict:
            del creds
            if image_ref == "docker://ghcr.io/ublue-os/aurora-dx:stable":
                return _stable_signal_inspect("sha256:new")
            if image_ref == "docker://ghcr.io/danathar/zfs-aurora-complex:latest":
                raise CiToolError("Command failed: skopeo inspect\nmanifest unknown")
            raise AssertionError(image_ref)

        with _patched_registry_inspect(inspect), patch(
            "ci_tools.check_stable_signal.resolve_latest_zfs_version",
            return_value="2.4.3",
        ):
            decision = evaluate_stable_signal_gate(
                image_org="danathar",
                image_name="zfs-aurora-complex",
                stable_signal_image="ghcr.io/ublue-os/aurora-dx:stable",
                zfs_minor_version="2.4",
                creds="actor:token",
            )

        self.assertTrue(decision.should_build)
        self.assertEqual(decision.reason, "current-latest-missing")

    def test_missing_previous_labels_builds(self) -> None:
        def inspect(image_ref: str, *, creds: str | None = None) -> dict:
            del creds
            if image_ref == "docker://ghcr.io/ublue-os/aurora-dx:stable":
                return _stable_signal_inspect("sha256:new")
            if image_ref == "docker://ghcr.io/danathar/zfs-aurora-complex:latest":
                return {
                    "Name": "ghcr.io/danathar/zfs-aurora-complex",
                    "Digest": "sha256:repo-latest",
                    "Labels": {},
                }
            raise AssertionError(image_ref)

        with _patched_registry_inspect(inspect), patch(
            "ci_tools.check_stable_signal.resolve_latest_zfs_version",
            return_value="2.4.3",
        ):
            decision = evaluate_stable_signal_gate(
                image_org="danathar",
                image_name="zfs-aurora-complex",
                stable_signal_image="ghcr.io/ublue-os/aurora-dx:stable",
                zfs_minor_version="2.4",
                creds="actor:token",
            )

        self.assertTrue(decision.should_build)
        self.assertEqual(decision.reason, "current-latest-missing-stable-signal-labels")

    def test_current_latest_registry_error_raises_instead_of_building(self) -> None:
        # An auth/rate-limit/network failure on the current-`:latest` lookup is
        # not the same as "no previous image yet" and must not be swallowed
        # into a build decision from unknown state.
        def inspect(image_ref: str, *, creds: str | None = None) -> dict:
            del creds
            if image_ref == "docker://ghcr.io/ublue-os/aurora-dx:stable":
                return _stable_signal_inspect("sha256:new")
            if image_ref == "docker://ghcr.io/danathar/zfs-aurora-complex:latest":
                raise CiToolError("unauthorized: authentication required")
            raise AssertionError(image_ref)

        with _patched_registry_inspect(inspect), patch(
            "ci_tools.check_stable_signal.resolve_latest_zfs_version",
            return_value="2.4.3",
        ), self.assertRaises(CiToolError) as context:
            evaluate_stable_signal_gate(
                image_org="danathar",
                image_name="zfs-aurora-complex",
                stable_signal_image="ghcr.io/ublue-os/aurora-dx:stable",
                zfs_minor_version="2.4",
                creds="actor:token",
            )

        self.assertIn("unauthorized", str(context.exception))

    def test_upstream_stable_signal_inspect_failure_raises(self) -> None:
        def inspect(image_ref: str, *, creds: str | None = None) -> dict:
            del image_ref, creds
            raise CiToolError("upstream inspect failed")

        with _patched_registry_inspect(inspect), patch(
            "ci_tools.check_stable_signal.resolve_latest_zfs_version",
            return_value="2.4.3",
        ), self.assertRaises(CiToolError) as context:
            evaluate_stable_signal_gate(
                image_org="danathar",
                image_name="zfs-aurora-complex",
                stable_signal_image="ghcr.io/ublue-os/aurora-dx:stable",
                zfs_minor_version="2.4",
                creds="actor:token",
            )

        self.assertIn("upstream inspect failed", str(context.exception))


class CheckStableSignalMainTests(unittest.TestCase):
    def test_main_writes_github_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output.txt"
            with patch.dict(
                os.environ,
                {
                    "GITHUB_OUTPUT": str(output_path),
                    "GITHUB_REPOSITORY_OWNER": "Danathar",
                    "GITHUB_EVENT_NAME": "schedule",
                    "REGISTRY_ACTOR": "actor",
                    "REGISTRY_TOKEN": "token",
                    "IMAGE_NAME": "zfs-aurora-complex",
                    "STABLE_SIGNAL_IMAGE": "ghcr.io/ublue-os/aurora-dx:stable",
                    "DEFAULT_ZFS_MINOR_VERSION": "2.4",
                },
                clear=False,
            ), patch(
                "ci_tools.check_stable_signal.evaluate_stable_signal_gate",
                return_value=StableSignalDecision(
                    should_build=False,
                    reason="stable-signal-unchanged",
                    stable_signal_ref="ghcr.io/ublue-os/aurora-dx:stable",
                    stable_signal_digest="sha256:same",
                    zfs_version="2.4.3",
                ),
            ) as evaluate:
                main()

            evaluate.assert_called_once_with(
                image_org="danathar",
                image_name="zfs-aurora-complex",
                stable_signal_image="ghcr.io/ublue-os/aurora-dx:stable",
                zfs_minor_version="2.4",
                creds="actor:token",
            )
            self.assertEqual(
                parse_github_file(output_path),
                {
                    "should_build": "false",
                    "reason": "stable-signal-unchanged",
                    "stable_signal_ref": "ghcr.io/ublue-os/aurora-dx:stable",
                    "stable_signal_digest": "sha256:same",
                    "zfs_version": "2.4.3",
                },
            )

    def test_main_bypasses_gate_for_non_schedule_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output.txt"
            with patch.dict(
                os.environ,
                {
                    "GITHUB_OUTPUT": str(output_path),
                    "GITHUB_EVENT_NAME": "workflow_dispatch",
                    "IMAGE_NAME": "zfs-aurora-complex",
                    "STABLE_SIGNAL_IMAGE": "ghcr.io/ublue-os/aurora-dx:stable",
                },
                clear=False,
            ), patch("ci_tools.check_stable_signal.evaluate_stable_signal_gate") as evaluate, patch(
                "ci_tools.check_stable_signal.skopeo_inspect_json_optional",
                return_value={"Digest": "sha256:push-time"},
            ):
                main()

            evaluate.assert_not_called()
            self.assertEqual(
                parse_github_file(output_path),
                {
                    "should_build": "true",
                    "reason": "not-schedule-event",
                    "stable_signal_ref": "ghcr.io/ublue-os/aurora-dx:stable",
                    "stable_signal_digest": "sha256:push-time",
                    "zfs_version": "",
                },
            )


class BypassDecisionTests(unittest.TestCase):
    def test_fills_stable_signal_digest_from_registry(self) -> None:
        with patch(
            "ci_tools.check_stable_signal.skopeo_inspect_json_optional",
            return_value={"Digest": "sha256:push-time"},
        ) as inspect_optional:
            decision = _bypass_decision("ghcr.io/ublue-os/aurora-dx:stable")

        inspect_optional.assert_called_once_with("docker://ghcr.io/ublue-os/aurora-dx:stable")
        self.assertTrue(decision.should_build)
        self.assertEqual(decision.reason, "not-schedule-event")
        self.assertEqual(decision.stable_signal_ref, "ghcr.io/ublue-os/aurora-dx:stable")
        self.assertEqual(decision.stable_signal_digest, "sha256:push-time")
        self.assertEqual(decision.zfs_version, "")

    def test_leaves_digest_empty_when_signal_image_missing(self) -> None:
        with patch(
            "ci_tools.check_stable_signal.skopeo_inspect_json_optional",
            return_value=None,
        ):
            decision = _bypass_decision("ghcr.io/ublue-os/aurora-dx:stable")

        self.assertTrue(decision.should_build)
        self.assertEqual(decision.stable_signal_digest, "")

    def test_swallows_registry_error_and_leaves_digest_empty(self) -> None:
        with patch(
            "ci_tools.check_stable_signal.skopeo_inspect_json_optional",
            side_effect=CiToolError("unauthorized: authentication required"),
        ):
            decision = _bypass_decision("ghcr.io/ublue-os/aurora-dx:stable")

        self.assertTrue(decision.should_build)
        self.assertEqual(decision.reason, "not-schedule-event")
        self.assertEqual(decision.stable_signal_digest, "")

    def test_never_resolves_zfs_version_for_non_schedule_events(self) -> None:
        # The actual `zfs-version` image label for push/manual builds is
        # sourced from the real build's own resolution in `build-zfs-akmods`,
        # not from this gate, so the bypass path must not make a redundant
        # network call to the OpenZFS releases API for a value nothing reads.
        with patch(
            "ci_tools.check_stable_signal.skopeo_inspect_json_optional",
            return_value={"Digest": "sha256:push-time"},
        ), patch(
            "ci_tools.check_stable_signal.resolve_latest_zfs_version"
        ) as resolve_zfs:
            _bypass_decision("ghcr.io/ublue-os/aurora-dx:stable")

        resolve_zfs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
