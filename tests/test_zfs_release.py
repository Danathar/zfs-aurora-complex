"""
Script: tests/test_zfs_release.py
What: Tests for the OpenZFS minor-line release resolver.
Doing: Feeds synthetic release lists (including a same-batch, multi-line
scenario recreated from the real OpenZFS release feed) and checks which
version is selected. Also covers the fetcher's pagination and authentication
without making a live API call.
Why: A resolver that picks the wrong line can silently downgrade ZFS on pools
that have already activated newer on-disk feature flags.
Goal: Guarantee the resolver never returns a version from a different minor
line than the one requested, regardless of publish order.
"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch
from urllib.request import Request

from ci_tools.common import CiToolError
from ci_tools.zfs_release import (
    MAX_RELEASE_PAGES,
    RELEASES_PER_PAGE,
    fetch_openzfs_releases,
    resolve_latest_zfs_version,
)

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


class FetchOpenzfsReleasesTests(unittest.TestCase):
    """Covers the HTTP behaviour of the fetcher itself, without a live call."""

    def _fake_urlopen(self, pages: list[list[dict]], seen: list[Request]):
        """Return a urlopen stand-in serving `pages` in order and recording requests."""

        class FakeResponse:
            def __init__(self, payload: bytes) -> None:
                self._payload = payload

            def read(self) -> bytes:
                return self._payload

            def __enter__(self):
                return self

            def __exit__(self, *_exc) -> bool:
                return False

        def fake_urlopen(request, timeout=None):
            del timeout
            seen.append(request)
            index = len(seen) - 1
            payload = pages[index] if index < len(pages) else []
            return FakeResponse(json.dumps(payload).encode("utf-8"))

        return fake_urlopen

    def test_follows_pagination_until_a_short_page(self) -> None:
        # A line that is not the newest can have its latest release pushed off
        # the API's first page by newer releases on other lines.
        first_page = [
            {"tag_name": f"zfs-2.9.{n}", "draft": False, "prerelease": False}
            for n in range(RELEASES_PER_PAGE)
        ]
        second_page = [{"tag_name": "zfs-2.4.9", "draft": False, "prerelease": False}]
        seen: list[Request] = []
        with patch(
            "ci_tools.zfs_release.urllib.request.urlopen",
            self._fake_urlopen([first_page, second_page], seen),
        ):
            releases = fetch_openzfs_releases()

        self.assertEqual(len(seen), 2)
        self.assertIn("page=1", seen[0].full_url)
        self.assertIn("page=2", seen[1].full_url)
        self.assertEqual(
            resolve_latest_zfs_version("2.4", releases_fetcher=lambda: releases), "2.4.9"
        )

    def test_stops_at_the_page_cap(self) -> None:
        full_page = [
            {"tag_name": f"zfs-2.9.{n}", "draft": False, "prerelease": False}
            for n in range(RELEASES_PER_PAGE)
        ]
        seen: list[Request] = []
        with patch(
            "ci_tools.zfs_release.urllib.request.urlopen",
            self._fake_urlopen([full_page] * (MAX_RELEASE_PAGES + 3), seen),
        ):
            fetch_openzfs_releases()

        self.assertEqual(len(seen), MAX_RELEASE_PAGES)

    def test_sends_the_github_token_when_one_is_available(self) -> None:
        # Unauthenticated api.github.com calls are rate limited per IP, and
        # hosted runners share IPs, so the token must actually be sent.
        seen: list[Request] = []
        with patch.dict(os.environ, {"GITHUB_TOKEN": "secret-token"}, clear=False), patch(
            "ci_tools.zfs_release.urllib.request.urlopen", self._fake_urlopen([[]], seen)
        ):
            fetch_openzfs_releases()

        self.assertEqual(seen[0].get_header("Authorization"), "Bearer secret-token")

    def test_omits_the_authorization_header_when_no_token_is_set(self) -> None:
        seen: list[Request] = []
        env_without_token = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GH_TOKEN")}
        with patch.dict(os.environ, env_without_token, clear=True), patch(
            "ci_tools.zfs_release.urllib.request.urlopen", self._fake_urlopen([[]], seen)
        ):
            fetch_openzfs_releases()

        self.assertIsNone(seen[0].get_header("Authorization"))

    def test_wraps_network_failures_as_ci_tool_error(self) -> None:
        with patch(
            "ci_tools.zfs_release.urllib.request.urlopen",
            side_effect=OSError("HTTP Error 403: rate limit exceeded"),
        ), self.assertRaises(CiToolError) as context:
            fetch_openzfs_releases()

        self.assertIn("rate limit exceeded", str(context.exception))


if __name__ == "__main__":
    unittest.main()
