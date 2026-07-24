"""
Script: tests/test_write_akmods_badge.py
What: Tests for the shields.io badge payload built from build workflow conclusions.
Doing: Feeds representative conclusion/failure-payload combinations through build_badge_payload.
Why: Guards the README badge contract so it only speaks to OpenZFS/kernel compat state.
Goal: Keep the badge accurate without it drifting into a general CI-health indicator.
"""

from __future__ import annotations

import unittest

from ci_tools.classify_akmods_failure import FAILURE_KIND_UNKNOWN, FAILURE_KIND_UPSTREAM_COMPAT
from ci_tools.write_akmods_badge import build_badge_payload


class BuildBadgePayloadTests(unittest.TestCase):
    def test_success_conclusion_is_green_in_sync(self) -> None:
        badge = build_badge_payload(conclusion="success", failure_payload=None)
        self.assertEqual(badge["message"], "in sync")
        self.assertEqual(badge["color"], "brightgreen")

    def test_upstream_compat_failure_names_the_specific_versions(self) -> None:
        badge = build_badge_payload(
            conclusion="failure",
            failure_payload={
                "failure_kind": FAILURE_KIND_UPSTREAM_COMPAT,
                "kernel_release": "7.1.4-200.fc44.x86_64",
                "zfs_version": "2.4.3",
                "max_kernel": "7.0",
            },
        )
        self.assertEqual(badge["color"], "red")
        self.assertIn("OpenZFS 2.4.3", badge["message"])
        self.assertIn("7.0", badge["message"])
        self.assertIn("7.1.4-200.fc44.x86_64", badge["message"])

    def test_upstream_compat_failure_without_parsed_versions_uses_generic_message(self) -> None:
        badge = build_badge_payload(
            conclusion="failure",
            failure_payload={"failure_kind": FAILURE_KIND_UPSTREAM_COMPAT, "kernel_release": "x"},
        )
        self.assertEqual(badge["color"], "red")
        self.assertIn("known upstream ZFS/kernel incompatibility", badge["message"])

    def test_unclassified_failure_does_not_touch_badge(self) -> None:
        badge = build_badge_payload(
            conclusion="failure",
            failure_payload={"failure_kind": FAILURE_KIND_UNKNOWN},
        )
        self.assertIsNone(badge)

    def test_failure_without_payload_does_not_touch_badge(self) -> None:
        badge = build_badge_payload(conclusion="failure", failure_payload=None)
        self.assertIsNone(badge)

    def test_other_conclusions_do_not_touch_badge(self) -> None:
        badge = build_badge_payload(conclusion="cancelled", failure_payload=None)
        self.assertIsNone(badge)


if __name__ == "__main__":
    unittest.main()
