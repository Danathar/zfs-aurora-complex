"""
Script: tests/test_write_last_good_build_badge.py
What: Tests for the shields.io badge showing how old the live `:latest` image is.
Doing: Feeds representative Created timestamps and "now" values through build_last_good_build_badge.
Why: The badge is the only README-visible signal of image staleness during an outage; the day-count
    math and missing-data handling need to stay correct.
Goal: Keep the badge accurate without needing any tracked state of its own.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from ci_tools.write_last_good_build_badge import build_last_good_build_badge


class BuildLastGoodBuildBadgeTests(unittest.TestCase):
    def test_missing_created_timestamp_returns_none(self) -> None:
        badge = build_last_good_build_badge(
            created_iso="", now=datetime(2026, 7, 24, tzinfo=timezone.utc)
        )
        self.assertIsNone(badge)

    def test_built_today_says_today(self) -> None:
        badge = build_last_good_build_badge(
            created_iso="2026-07-24T07:42:31Z",
            now=datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc),
        )
        self.assertIn("today", badge["message"])
        self.assertEqual(badge["color"], "brightgreen")

    def test_built_yesterday_uses_singular_day(self) -> None:
        badge = build_last_good_build_badge(
            created_iso="2026-07-23T07:42:31Z",
            now=datetime(2026, 7, 24, 6, 0, tzinfo=timezone.utc),
        )
        self.assertIn("1 day ago", badge["message"])

    def test_built_seventeen_days_ago_uses_plural(self) -> None:
        badge = build_last_good_build_badge(
            created_iso="2026-07-07T07:42:31Z",
            now=datetime(2026, 7, 24, 6, 0, tzinfo=timezone.utc),
        )
        self.assertIn("2026-07-07", badge["message"])
        self.assertIn("17 days ago", badge["message"])

    def test_label_is_last_good_build(self) -> None:
        badge = build_last_good_build_badge(
            created_iso="2026-07-07T07:42:31Z",
            now=datetime(2026, 7, 24, tzinfo=timezone.utc),
        )
        self.assertEqual(badge["label"], "last good build")


if __name__ == "__main__":
    unittest.main()
