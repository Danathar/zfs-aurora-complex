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
    StableSignalDecision,
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
    with patch("ci_tools.check_stable_signal.skopeo_inspect_json", side_effect=side_effect):
        with patch("ci_tools.common.skopeo_inspect_json", side_effect=side_effect):
            yield


def _stable_signal_inspect(digest: str = "sha256:stable") -> dict:
    return {
        "Name": "ghcr.io/ublue-os/aurora-dx-nvidia-open",
        "Digest": digest,
        "Labels": {},
    }


def _current_latest_inspect(*, signal_image: str, signal_digest: str) -> dict:
    return {
        "Name": "ghcr.io/danathar/zfs-aurora-complex",
        "Digest": "sha256:repo-latest",
        "Labels": {
            STABLE_SIGNAL_IMAGE_LABEL: signal_image,
            STABLE_SIGNAL_DIGEST_LABEL: signal_digest,
        },
    }


class EvaluateStableSignalGateTests(unittest.TestCase):
    def test_unchanged_signal_skips_schedule_build(self) -> None:
        def inspect(image_ref: str, *, creds: str | None = None) -> dict:
            if image_ref == "docker://ghcr.io/ublue-os/aurora-dx-nvidia-open:stable":
                self.assertIsNone(creds)
                return _stable_signal_inspect("sha256:same")
            if image_ref == "docker://ghcr.io/danathar/zfs-aurora-complex:latest":
                self.assertEqual(creds, "actor:token")
                return _current_latest_inspect(
                    signal_image="ghcr.io/ublue-os/aurora-dx-nvidia-open:stable",
                    signal_digest="sha256:same",
                )
            raise AssertionError(image_ref)

        with _patched_registry_inspect(inspect):
            decision = evaluate_stable_signal_gate(
                image_org="danathar",
                image_name="zfs-aurora-complex",
                stable_signal_image="ghcr.io/ublue-os/aurora-dx-nvidia-open:stable",
                creds="actor:token",
            )

        self.assertFalse(decision.should_build)
        self.assertEqual(decision.reason, "stable-signal-unchanged")
        self.assertEqual(
            decision.stable_signal_ref,
            "ghcr.io/ublue-os/aurora-dx-nvidia-open:stable",
        )
        self.assertEqual(decision.stable_signal_digest, "sha256:same")

    def test_changed_signal_builds(self) -> None:
        def inspect(image_ref: str, *, creds: str | None = None) -> dict:
            del creds
            if image_ref == "docker://ghcr.io/ublue-os/aurora-dx-nvidia-open:stable":
                return _stable_signal_inspect("sha256:new")
            if image_ref == "docker://ghcr.io/danathar/zfs-aurora-complex:latest":
                return _current_latest_inspect(
                    signal_image="ghcr.io/ublue-os/aurora-dx-nvidia-open:stable",
                    signal_digest="sha256:old",
                )
            raise AssertionError(image_ref)

        with _patched_registry_inspect(inspect):
            decision = evaluate_stable_signal_gate(
                image_org="danathar",
                image_name="zfs-aurora-complex",
                stable_signal_image="ghcr.io/ublue-os/aurora-dx-nvidia-open:stable",
                creds="actor:token",
            )

        self.assertTrue(decision.should_build)
        self.assertEqual(decision.reason, "stable-signal-advanced")

    def test_missing_previous_image_builds(self) -> None:
        def inspect(image_ref: str, *, creds: str | None = None) -> dict:
            del creds
            if image_ref == "docker://ghcr.io/ublue-os/aurora-dx-nvidia-open:stable":
                return _stable_signal_inspect("sha256:new")
            if image_ref == "docker://ghcr.io/danathar/zfs-aurora-complex:latest":
                raise CiToolError("Command failed: skopeo inspect\nmanifest unknown")
            raise AssertionError(image_ref)

        with _patched_registry_inspect(inspect):
            decision = evaluate_stable_signal_gate(
                image_org="danathar",
                image_name="zfs-aurora-complex",
                stable_signal_image="ghcr.io/ublue-os/aurora-dx-nvidia-open:stable",
                creds="actor:token",
            )

        self.assertTrue(decision.should_build)
        self.assertEqual(decision.reason, "current-latest-missing")

    def test_missing_previous_labels_builds(self) -> None:
        def inspect(image_ref: str, *, creds: str | None = None) -> dict:
            del creds
            if image_ref == "docker://ghcr.io/ublue-os/aurora-dx-nvidia-open:stable":
                return _stable_signal_inspect("sha256:new")
            if image_ref == "docker://ghcr.io/danathar/zfs-aurora-complex:latest":
                return {
                    "Name": "ghcr.io/danathar/zfs-aurora-complex",
                    "Digest": "sha256:repo-latest",
                    "Labels": {},
                }
            raise AssertionError(image_ref)

        with _patched_registry_inspect(inspect):
            decision = evaluate_stable_signal_gate(
                image_org="danathar",
                image_name="zfs-aurora-complex",
                stable_signal_image="ghcr.io/ublue-os/aurora-dx-nvidia-open:stable",
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

        with _patched_registry_inspect(inspect):
            with self.assertRaises(CiToolError) as context:
                evaluate_stable_signal_gate(
                    image_org="danathar",
                    image_name="zfs-aurora-complex",
                    stable_signal_image="ghcr.io/ublue-os/aurora-dx:stable",
                    creds="actor:token",
                )

        self.assertIn("unauthorized", str(context.exception))

    def test_upstream_stable_signal_inspect_failure_raises(self) -> None:
        def inspect(image_ref: str, *, creds: str | None = None) -> dict:
            del image_ref, creds
            raise CiToolError("upstream inspect failed")

        with _patched_registry_inspect(inspect):
            with self.assertRaises(CiToolError) as context:
                evaluate_stable_signal_gate(
                    image_org="danathar",
                    image_name="zfs-aurora-complex",
                    stable_signal_image="ghcr.io/ublue-os/aurora-dx-nvidia-open:stable",
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
                    "STABLE_SIGNAL_IMAGE": "ghcr.io/ublue-os/aurora-dx-nvidia-open:stable",
                },
                clear=False,
            ):
                with patch(
                    "ci_tools.check_stable_signal.evaluate_stable_signal_gate",
                    return_value=StableSignalDecision(
                        should_build=False,
                        reason="stable-signal-unchanged",
                        stable_signal_ref="ghcr.io/ublue-os/aurora-dx-nvidia-open:stable",
                        stable_signal_digest="sha256:same",
                    ),
                ):
                    main()

            self.assertEqual(
                parse_github_file(output_path),
                {
                    "should_build": "false",
                    "reason": "stable-signal-unchanged",
                    "stable_signal_ref": "ghcr.io/ublue-os/aurora-dx-nvidia-open:stable",
                    "stable_signal_digest": "sha256:same",
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
                    "STABLE_SIGNAL_IMAGE": "ghcr.io/ublue-os/aurora-dx-nvidia-open:stable",
                },
                clear=False,
            ):
                with patch("ci_tools.check_stable_signal.evaluate_stable_signal_gate") as evaluate:
                    main()

            evaluate.assert_not_called()
            self.assertEqual(
                parse_github_file(output_path),
                {
                    "should_build": "true",
                    "reason": "not-schedule-event",
                    "stable_signal_ref": "ghcr.io/ublue-os/aurora-dx-nvidia-open:stable",
                    "stable_signal_digest": "",
                },
            )


if __name__ == "__main__":
    unittest.main()
