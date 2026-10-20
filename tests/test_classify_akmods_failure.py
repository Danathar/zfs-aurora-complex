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
    FAILURE_KIND_TRANSIENT_NETWORK,
    FAILURE_KIND_UNKNOWN,
    FAILURE_KIND_UPSTREAM_COMPAT,
    ZFS_MAX_KERNEL_MISMATCH_PATTERN,
    build_failure_summary,
    build_step_summary_markdown,
    build_sticky_issue_payload,
    classify_log_text,
    zfs_metadata_from_log,
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

    def test_install_helper_missing_rpm_message_is_upstream_compat(self) -> None:
        # This is the exact wording the install helper emits, so the matching
        # pattern must track that string and not drift away from it again.
        log = (
            "RuntimeError: No kmod-zfs RPM found for the supported primary kernel "
            "6.18.16-200.fc43.x86_64."
        )
        kind, matched = classify_log_text(log)
        self.assertEqual(kind, FAILURE_KIND_UPSTREAM_COMPAT)
        self.assertIn("No kmod-zfs RPM found for the supported primary kernel", matched)

    def test_openzfs_max_kernel_below_resolved_kernel_is_upstream_compat(self) -> None:
        log = (
            "ZFS_META_VERSION='2.4.1'\n"
            "ZFS_META_KVER_MAX='6.19'\n"
            "configure: exit 1\n"
        )

        kind, matched = classify_log_text(log, kernel_release="7.0.4-200.fc44.x86_64")

        self.assertEqual(kind, FAILURE_KIND_UPSTREAM_COMPAT)
        self.assertIn(ZFS_MAX_KERNEL_MISMATCH_PATTERN, matched)

    def test_openzfs_max_kernel_does_not_match_without_newer_resolved_kernel(self) -> None:
        log = (
            "ZFS_META_VERSION='2.4.1'\n"
            "ZFS_META_KVER_MAX='6.19'\n"
            "configure: exit 1\n"
        )

        kind, matched = classify_log_text(log, kernel_release="6.19.1-200.fc44.x86_64")

        self.assertEqual(kind, FAILURE_KIND_UNKNOWN)
        self.assertEqual(matched, [])

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

    def test_mirror_dns_failure_is_transient_network(self) -> None:
        log = (
            "Curl error (6): Could not resolve hostname for "
            "https://repos.eggycrew.com/rpmfusion/free/fedora/rpmfusion-free-release-44.noarch.rpm "
            "[Could not resolve host: repos.eggycrew.com]\n"
            "Failed to download files\n"
        )
        kind, matched = classify_log_text(log, kernel_release="7.2.7-200.fc44.x86_64")
        self.assertEqual(kind, FAILURE_KIND_TRANSIENT_NETWORK)
        self.assertIn(r"Curl error \((?:6|7|28|35|56)\)", matched)
        self.assertIn("Could not resolve host", matched)

    def test_copr_connection_refused_is_transient_network(self) -> None:
        log = (
            "Curl error (7): Couldn't connect to server for "
            "https://copr.fedorainfracloud.org [Connection refused]"
        )
        kind, matched = classify_log_text(log)
        self.assertEqual(kind, FAILURE_KIND_TRANSIENT_NETWORK)
        self.assertIn("Connection refused", matched)

    def test_upstream_compat_pattern_wins_over_network_pattern(self) -> None:
        log = (
            "Curl error (6): Could not resolve hostname for https://example.invalid\n"
            "module.c:123: error: implicit declaration of function 'folio_wait_writeback'\n"
        )
        kind, matched = classify_log_text(log)
        self.assertEqual(kind, FAILURE_KIND_UPSTREAM_COMPAT)
        self.assertEqual(matched, ["implicit declaration of function"])

    def test_zfs_max_kernel_mismatch_wins_over_network_pattern(self) -> None:
        log = (
            "ZFS_META_VERSION='2.4.1'\n"
            "ZFS_META_KVER_MAX='6.19'\n"
            "Connection refused\n"
        )
        kind, matched = classify_log_text(log, kernel_release="7.0.4-200.fc44.x86_64")
        self.assertEqual(kind, FAILURE_KIND_UPSTREAM_COMPAT)
        self.assertEqual(matched, [ZFS_MAX_KERNEL_MISMATCH_PATTERN])

    def test_each_network_pattern_alone_is_transient_network(self) -> None:
        # One row per pattern, each matching only that pattern, so dropping any
        # one of them turns its outage back into "unknown". The curl codes are
        # the ones dnf5/libcurl print for DNS, refused, timeout, TLS connect and
        # receive failures; the log casing varies between dnf versions.
        rows = {
            r"Curl error \((?:6|7|28|35|56)\)": "curl error (35): SSL connect error",
            "Could not resolve host": "could not resolve host: mirrors.fedoraproject.org",
            "Connection refused": "connect to 192.0.2.1 port 443: Connection refused",
            "Temporary failure in name resolution": (
                "Failed to connect: Temporary failure in name resolution"
            ),
            "Operation timed out after": (
                "Operation timed out after 30000 milliseconds with 0 bytes received"
            ),
        }
        for pattern, log in rows.items():
            with self.subTest(pattern=pattern):
                kind, matched = classify_log_text(log)
                self.assertEqual(kind, FAILURE_KIND_TRANSIENT_NETWORK)
                self.assertEqual(matched, [pattern])

    def test_unrelated_curl_error_code_is_not_transient_network(self) -> None:
        kind, matched = classify_log_text("Curl error (60): SSL certificate problem")
        self.assertEqual(kind, FAILURE_KIND_UNKNOWN)
        self.assertEqual(matched, [])


class FailureSummaryTests(unittest.TestCase):
    def test_openzfs_max_kernel_summary_explains_fail_closed_behavior(self) -> None:
        log = (
            "ZFS_META_VERSION='2.4.1'\n"
            "ZFS_META_KVER_MAX='6.19'\n"
        )

        summary = build_failure_summary(
            failure_kind=FAILURE_KIND_UPSTREAM_COMPAT,
            kernel_release="7.0.4-200.fc44.x86_64",
            log_text=log,
        )

        self.assertIn("OpenZFS 2.4.1 supports Linux kernels up to 6.19", summary)
        self.assertIn("7.0.4-200.fc44.x86_64", summary)
        self.assertIn("intentionally failing closed", summary)

    def test_openzfs_max_kernel_summary_requires_newer_resolved_kernel(self) -> None:
        log = (
            "ZFS_META_VERSION='2.4.1'\n"
            "ZFS_META_KVER_MAX='6.19'\n"
        )

        summary = build_failure_summary(
            failure_kind=FAILURE_KIND_UPSTREAM_COMPAT,
            kernel_release="6.18.16-200.fc43.x86_64",
            log_text=log,
        )

        self.assertNotIn("but the resolved base image uses", summary)
        self.assertIn("known upstream ZFS/kernel compatibility pattern", summary)

    def test_transient_network_summary_says_it_is_not_a_compat_problem(self) -> None:
        summary = build_failure_summary(
            failure_kind=FAILURE_KIND_TRANSIENT_NETWORK,
            kernel_release="7.2.7-200.fc44.x86_64",
            log_text="Could not resolve host: repos.eggycrew.com",
        )

        self.assertIn("download mirror or network service was unreachable", summary)
        self.assertIn("not a kernel/ZFS compatibility problem", summary)
        self.assertIn("read the log", summary)
        self.assertIn("Re-run `build.yml`", summary)


class ZfsMetadataFromLogTests(unittest.TestCase):
    """
    The two captured values are published, so what they may contain matters.

    `build_failure_summary` puts them in the sticky issue body
    akmods-failure-triage.yml opens with `issues: write`, and
    `ci_tools/write_akmods_badge.py` puts them in the badge message that same
    workflow commits to the `status` branch for shields.io. The log they come
    out of is not written by this repository: it is the akmods fork's
    `just build` output plus the OpenZFS release tarball's own `configure`.
    """

    # A real configure line, and the release-candidate form, which carries the
    # two characters most likely to be dropped by an over-tight pattern.
    def test_ordinary_metadata_still_parses(self) -> None:
        version, max_kernel = zfs_metadata_from_log(
            "ZFS_META_VERSION='2.4.0-rc1'\nZFS_META_KVER_MAX='6.19'\n"
        )

        self.assertEqual(version, "2.4.0-rc1")
        self.assertEqual(max_kernel, "6.19")

    def test_capture_does_not_run_past_the_end_of_its_own_line(self) -> None:
        # An unterminated `ZFS_META_VERSION='` used to capture every line up to
        # the next quote anywhere later in the log, because a character class
        # matches newlines. Pin the boundary rather than the old behaviour.
        version, max_kernel = zfs_metadata_from_log(
            "checking ZFS_META_VERSION='2.4.4\n"
            "some other output line\n"
            "ZFS_META_KVER_MAX='6.99'\n"
        )

        self.assertEqual(version, "")
        self.assertEqual(max_kernel, "6.99")

    def test_markdown_in_the_log_is_not_captured_as_a_version(self) -> None:
        # Markdown in an issue body renders as markdown, and an image fetches a
        # remote URL when a maintainer opens the issue. A version string has no
        # business containing any of these characters.
        version, _ = zfs_metadata_from_log(
            "ZFS_META_VERSION='[x](https://example.invalid)'\n"
        )

        self.assertEqual(version, "")

    def test_capture_is_bounded_to_a_version_shaped_value(self) -> None:
        # 32 version characters is the most the pattern takes; one more, or a
        # space or `*` (prose and emphasis markdown), takes the no-metadata path.
        longest = "2." + "4" * 30
        rows = {
            longest: longest,
            longest + "4": "",
            "2.4.4 see the log": "",
            "*2.4.4*": "",
        }
        for value, expected in rows.items():
            with self.subTest(value=value):
                version, max_kernel = zfs_metadata_from_log(
                    f"ZFS_META_VERSION='{value}'\nZFS_META_KVER_MAX='{value}'\n"
                )
                self.assertEqual(version, expected)
                self.assertEqual(max_kernel, expected)

    def test_summary_names_the_placeholder_rather_than_quoting_the_log(self) -> None:
        # The unparseable half degrades to the placeholder this function
        # already used for a log that names a max kernel but no version, so
        # the summary still says which kernel is too new. What it must not do
        # is carry the log lines that sat between the two metadata markers.
        summary = build_failure_summary(
            failure_kind=FAILURE_KIND_UPSTREAM_COMPAT,
            kernel_release="7.1.4-204.fc44.x86_64",
            log_text=(
                "checking ZFS_META_VERSION='2.4.4\n"
                "[x](https://example.invalid) all this text\n"
                "ZFS_META_KVER_MAX='6.99'\n"
            ),
        )

        self.assertNotIn("example.invalid", summary)
        self.assertIn("the selected OpenZFS release", summary)
        self.assertIn("up to 6.99", summary)



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

    def test_transient_network_kind_has_its_own_title_and_key(self) -> None:
        payload = build_sticky_issue_payload(
            failure_kind=FAILURE_KIND_TRANSIENT_NETWORK,
            kernel_release="7.2.7-200.fc44.x86_64",
            akmods_upstream_ref="d73abd6aacb2ffffffffffffffffffffffffffff",
            fedora_version="44",
            run_id="12345",
            run_url="https://github.com/example/repo/actions/runs/12345",
            matched_patterns=["Could not resolve host"],
        )
        self.assertEqual(
            payload["title"],
            "Transient network failure in akmods build: 7.2.7-200.fc44.x86_64 + akmods@d73abd6aacb2",
        )
        self.assertEqual(payload["key"], "transient-network:7.2.7-200.fc44.x86_64:d73abd6aacb2")
        self.assertEqual(payload["failure_kind"], FAILURE_KIND_TRANSIENT_NETWORK)

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

    def test_payload_body_includes_summary_when_provided(self) -> None:
        payload = build_sticky_issue_payload(
            failure_kind=FAILURE_KIND_UPSTREAM_COMPAT,
            kernel_release="7.0.4-200.fc44.x86_64",
            akmods_upstream_ref="28079918460b05c43422d48a2a5866aa78f1dce5",
            fedora_version="44",
            run_id="12345",
            run_url="https://github.com/example/repo/actions/runs/12345",
            matched_patterns=[ZFS_MAX_KERNEL_MISMATCH_PATTERN],
            summary="OpenZFS 2.4.1 supports Linux kernels up to 6.19.",
        )

        self.assertIn("**Summary:** OpenZFS 2.4.1 supports Linux kernels up to 6.19.", payload["body"])
        self.assertIn("summary", payload)


class StepSummaryTests(unittest.TestCase):
    def test_step_summary_shows_actionable_failure_reason(self) -> None:
        markdown = build_step_summary_markdown(
            {
                "failure_kind": FAILURE_KIND_UPSTREAM_COMPAT,
                "kernel_release": "7.0.4-200.fc44.x86_64",
                "fedora_version": "44",
                "akmods_upstream_ref": "28079918460b05c43422d48a2a5866aa78f1dce5",
                "summary": "OpenZFS 2.4.1 supports Linux kernels up to 6.19.",
                "run_url": "https://github.com/example/repo/actions/runs/12345",
            }
        )

        self.assertIn("Failure kind: `upstream-compat`", markdown)
        self.assertIn("Primary kernel: `7.0.4-200.fc44.x86_64`", markdown)
        self.assertIn("OpenZFS 2.4.1 supports Linux kernels up to 6.19.", markdown)


if __name__ == "__main__":
    unittest.main()
