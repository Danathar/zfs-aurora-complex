"""
Script: tests/test_compute_branch_metadata.py
What: Unit tests for branch metadata helper logic.
Doing: Checks branch-name cleanup, fallback behavior, and output length limits.
Why: Prevents invalid branch tag text from reaching registry steps.
Goal: Keep branch-scoped naming safe and stable.
"""

from __future__ import annotations

import unittest

from ci_tools.compute_branch_metadata import build_branch_metadata, sanitize_branch_name


class BranchMetadataTests(unittest.TestCase):
    def test_sanitizes_branch_name(self) -> None:
        self.assertEqual(sanitize_branch_name("Feature/My Branch!"), "feature-my-branch")

    def test_uses_fallback_when_branch_sanitizes_to_empty(self) -> None:
        self.assertEqual(sanitize_branch_name("!!!"), "branch")

    def test_clamps_long_names(self) -> None:
        long_branch = "a" * 300
        branch_tag = build_branch_metadata(long_branch)
        self.assertLessEqual(len(branch_tag), 120)
        self.assertTrue(branch_tag.startswith("br-"))


if __name__ == "__main__":
    unittest.main()
