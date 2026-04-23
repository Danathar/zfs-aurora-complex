"""
Script: tests/test_kernel_release.py
What: Direct tests for shared kernel-release sorting behavior.
Doing: Verifies the single shared sort key orders Fedora kernel releases
consistently for both CI resolution and in-image install planning.
Why: Primary-kernel selection is safety-critical for this repo's build policy.
Goal: Catch future drift in the one shared kernel ordering helper.
"""

from __future__ import annotations

import unittest

from shared.kernel_release import kernel_release_sort_key


class KernelReleaseSortKeyTests(unittest.TestCase):
    def test_orders_fedora_kernel_releases_naturally(self) -> None:
        releases = [
            "6.18.10-200.fc43.x86_64",
            "6.18.9-200.fc43.x86_64",
            "6.18.12-200.fc43.x86_64",
        ]

        self.assertEqual(
            sorted(releases, key=kernel_release_sort_key),
            [
                "6.18.9-200.fc43.x86_64",
                "6.18.10-200.fc43.x86_64",
                "6.18.12-200.fc43.x86_64",
            ],
        )

    def test_orders_releases_with_numeric_suffixes_naturally(self) -> None:
        releases = [
            "6.18.16-200.fc43.10.x86_64",
            "6.18.16-200.fc43.2.x86_64",
            "6.18.16-200.fc43.1.x86_64",
        ]

        self.assertEqual(
            sorted(releases, key=kernel_release_sort_key),
            [
                "6.18.16-200.fc43.1.x86_64",
                "6.18.16-200.fc43.2.x86_64",
                "6.18.16-200.fc43.10.x86_64",
            ],
        )


if __name__ == "__main__":
    unittest.main()
