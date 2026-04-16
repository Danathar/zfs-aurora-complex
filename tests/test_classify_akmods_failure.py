"""
Script: tests/test_classify_akmods_failure.py
What: Tests for akmods failure classification and sticky-issue payload shape.
Doing: Feeds representative log bodies through the classifier and checks the generated payload key and title forms.
Why: Guards the visibility-workflow contract so sticky issues stay deduplicated per distinct failure.
Goal: Keep red builds informative without misclassification silently hiding real code bugs.
"""

from __future__ import annotations

import unittest

from ci_tools.classify_akmods_failure import (
    FAILURE_KIND_UNKNOWN,
    FAILURE_KIND_UPSTREAM_COMPAT,
    build_sticky_issue_payload,
    classify_log_text,
)


class ClassifyLogTextTests(unittest.TestCase):
    def test_empty_log_is_unknown(self) -> None:
        kind, matched = classify_log_text("")
        self.assertEqual(kind, FAILURE_KIND_UNKNOWN)
        self.assertEqual(matched, [])

    def test_implicit_declaration_is_upstream_compat(self) -> None:
        log = "error: implicit declaration of function 'kthread_create_on_node'"
        kind, matched = classify_log_text(log)
        self.assertEqual(kind, FAILURE_KIND_UPSTREAM_COMPAT)
        self.assertTrue(any("implicit" in pat for pat in matched))

    def test_unknown_struct_type_is_upstream_compat(self) -> None:
        log = "error: unknown type name 'struct bio_set'"
        kind, _ = classify_log_text(log)
        self.assertEqual(kind, FAILURE_KIND_UPSTREAM_COMPAT)

    def test_cached_akmods_do_not_cover_is_upstream_compat(self) -> None:
        log = "RuntimeError: Cached akmods do not cover the supported kernel; rebuild akmods."
        kind, _ = classify_log_text(log)
        self.assertEqual(kind, FAILURE_KIND_UPSTREAM_COMPAT)

    def test_unrelated_python_traceback_is_unknown(self) -> None:
        log = (
            "Traceback (most recent call last):\n"
            "  File 'foo.py', line 1, in <module>\n"
            "TypeError: unhashable type: 'list'"
        )
        kind, matched = classify_log_text(log)
        self.assertEqual(kind, FAILURE_KIND_UNKNOWN)
        self.assertEqual(matched, [])

    def test_multi_pattern_log_returns_all_matches_in_declaration_order(self) -> None:
        # A realistic kernel-API-drift failure hits several patterns at once.
        # The classifier returns every match in declaration order so future
        # readers can see which surfaces of the failure tripped the allowlist.
        log = (
            "module.c:123: error: implicit declaration of function 'folio_wait_writeback'\n"
            "module.c:456: error: 'struct bio' has no member named 'bi_disk'\n"
            "module.c:789: error: conflicting types for 'zfs_setattr'\n"
        )
        kind, matched = classify_log_text(log)

        self.assertEqual(kind, FAILURE_KIND_UPSTREAM_COMPAT)
        self.assertEqual(
            matched,
            [
                "implicit declaration of function",
                "has no member named",
                "conflicting types for",
            ],
        )


class BuildStickyIssuePayloadTests(unittest.TestCase):
    def test_payload_key_is_stable_per_kernel_and_ref(self) -> None:
        payload = build_sticky_issue_payload(
            failure_kind=FAILURE_KIND_UPSTREAM_COMPAT,
            kernel_release="6.18.16-200.fc43.x86_64",
            akmods_upstream_ref="0e06cd70879aa5063c4193710d8c7e37bbc2ab57",
            fedora_version="43",
            run_id="12345",
            run_url="https://github.com/example/repo/actions/runs/12345",
            matched_patterns=["implicit declaration of function"],
        )
        self.assertEqual(
            payload["key"],
            "upstream-compat:6.18.16-200.fc43.x86_64:0e06cd70879a",
        )
        self.assertIn("6.18.16-200.fc43.x86_64", payload["title"])
        self.assertIn("akmods@0e06cd70879a", payload["title"])
        self.assertIn("`implicit declaration of function`", payload["body"])

    def test_unknown_kind_uses_different_title_prefix(self) -> None:
        payload = build_sticky_issue_payload(
            failure_kind=FAILURE_KIND_UNKNOWN,
            kernel_release="6.18.16-200.fc43.x86_64",
            akmods_upstream_ref="deadbeefdead",
            fedora_version="43",
            run_id="12345",
            run_url="https://github.com/example/repo/actions/runs/12345",
            matched_patterns=[],
        )
        self.assertTrue(payload["title"].startswith("Unclassified"))
        self.assertEqual(payload["failure_kind"], FAILURE_KIND_UNKNOWN)

    def test_missing_ref_uses_placeholder(self) -> None:
        payload = build_sticky_issue_payload(
            failure_kind=FAILURE_KIND_UPSTREAM_COMPAT,
            kernel_release="6.18.16-200.fc43.x86_64",
            akmods_upstream_ref="",
            fedora_version="43",
            run_id="12345",
            run_url="https://github.com/example/repo/actions/runs/12345",
            matched_patterns=["unknown type name 'struct"],
        )
        self.assertIn("unknown-ref", payload["key"])


if __name__ == "__main__":
    unittest.main()
