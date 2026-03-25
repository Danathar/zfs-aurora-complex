"""
Script: tests/test_resolve_build_inputs.py
What: Tests for input-resolution tag selection.
Doing: Checks immutable-tag reuse, candidate-tag derivation, and failure paths.
Why: Protects the logic that pins run inputs and avoids moving-tag drift.
Goal: Keep input resolution predictable and explainable.
"""

from __future__ import annotations

import unittest

from ci_tools.common import CiToolError, sort_kernel_releases
from ci_tools.resolve_build_inputs import choose_base_image_tag


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


if __name__ == "__main__":
    unittest.main()
