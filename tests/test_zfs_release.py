"""
Script: tests/test_zfs_release.py
What: Tests for the OpenZFS minor-line release resolver.
Doing: Feeds synthetic release lists (including a same-batch, multi-line
scenario recreated from the real OpenZFS release feed) and checks which
version is selected.
Why: A resolver that picks the wrong line can silently downgrade ZFS on pools
that have already activated newer on-disk feature flags.
Goal: Guarantee the resolver never returns a version from a different minor
line than the one requested, regardless of publish order.
"""

from __future__ import annotations

import unittest

from ci_tools.common import CiToolError
from ci_tools.zfs_release import resolve_latest_zfs_version

# Recreates the real batch observed on 2026-06-12: three minor lines published
# within 30 seconds of each other, newest-line-first in API order.
SAME_BATCH_RELEASES = [
    {"tag_name": "zfs-2.4.3", "draft": False, "prerelease": False},
    {"tag_name": "zfs-2.3.8", "draft": False, "prerelease": False},
    {"tag_name": "zfs-2.2.10", "draft": False, "prerelease": False},
    {"tag_name": "zfs-2.4.2", "draft": False, "prerelease": False},
    {"tag_name": "zfs-2.3.7", "draft": False, "prerelease": False},
]


class ResolveLatestZfsVersionTests(unittest.TestCase):
    def test_resolves_newest_patch_on_the_requested_line(self) -> None:
        version = resolve_latest_zfs_version(
            "2.4", releases_fetcher=lambda: SAME_BATCH_RELEASES
        )
        self.assertEqual(version, "2.4.3")

    def test_same_batch_different_lines_never_cross_contaminate(self) -> None:
        # This is the exact hazard the module docstring describes: if a
        # resolver ever compared "publish order" instead of filtering to one
        # line first, requesting 2.4 here could return 2.3.8 or 2.2.10.
        self.assertEqual(
            resolve_latest_zfs_version("2.4", releases_fetcher=lambda: SAME_BATCH_RELEASES),
            "2.4.3",
        )
        self.assertEqual(
            resolve_latest_zfs_version("2.3", releases_fetcher=lambda: SAME_BATCH_RELEASES),
            "2.3.8",
        )
        self.assertEqual(
            resolve_latest_zfs_version("2.2", releases_fetcher=lambda: SAME_BATCH_RELEASES),
            "2.2.10",
        )

    def test_a_later_published_older_line_release_is_never_selected(self) -> None:
        # zfs-2.3.9 publishes after zfs-2.4.4 (API returns newest-created-first).
        # Requesting the 2.4 line must still resolve 2.4.4, not 2.3.9.
        releases = [
            {"tag_name": "zfs-2.3.9", "draft": False, "prerelease": False},
            {"tag_name": "zfs-2.4.4", "draft": False, "prerelease": False},
        ]
        self.assertEqual(
            resolve_latest_zfs_version("2.4", releases_fetcher=lambda: releases), "2.4.4"
        )

    def test_ignores_prerelease_releases(self) -> None:
        releases = [
            {"tag_name": "zfs-2.4.4", "draft": False, "prerelease": True},
            {"tag_name": "zfs-2.4.3", "draft": False, "prerelease": False},
        ]
        self.assertEqual(
            resolve_latest_zfs_version("2.4", releases_fetcher=lambda: releases), "2.4.3"
        )

    def test_ignores_draft_releases(self) -> None:
        releases = [
            {"tag_name": "zfs-2.4.4", "draft": True, "prerelease": False},
            {"tag_name": "zfs-2.4.3", "draft": False, "prerelease": False},
        ]
        self.assertEqual(
            resolve_latest_zfs_version("2.4", releases_fetcher=lambda: releases), "2.4.3"
        )

    def test_ignores_tags_that_do_not_match_the_zfs_release_pattern(self) -> None:
        releases = [
            {"tag_name": "some-other-tag", "draft": False, "prerelease": False},
            {"tag_name": "zfs-2.4.3", "draft": False, "prerelease": False},
        ]
        self.assertEqual(
            resolve_latest_zfs_version("2.4", releases_fetcher=lambda: releases), "2.4.3"
        )

    def test_raises_when_no_release_matches_the_requested_line(self) -> None:
        releases = [{"tag_name": "zfs-2.3.8", "draft": False, "prerelease": False}]
        with self.assertRaises(CiToolError):
            resolve_latest_zfs_version("2.4", releases_fetcher=lambda: releases)

    def test_raises_on_malformed_minor_version(self) -> None:
        with self.assertRaises(CiToolError):
            resolve_latest_zfs_version("2", releases_fetcher=lambda: SAME_BATCH_RELEASES)

    def test_raises_on_minor_version_with_patch_component(self) -> None:
        with self.assertRaises(CiToolError):
            resolve_latest_zfs_version("2.4.3", releases_fetcher=lambda: SAME_BATCH_RELEASES)


if __name__ == "__main__":
    unittest.main()
