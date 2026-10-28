"""
Script: tests/test_classify_akmods_failure.py
What: Tests for akmods failure classification, sticky-issue payload shape, and the
`main()` that turns a failed build's environment into that payload plus step outputs.
Doing: Feeds representative log bodies through the classifier and checks the generated payload key and title forms, then drives main() with a controlled environment and reads back the payload file, the job summary and the GITHUB_OUTPUT it wrote.
Why: Guards the visibility-workflow contract so sticky issues stay deduplicated per distinct failure. main() is the other half of that contract: the classify step in .github/actions/prepare-main-akmods/action.yml runs only on a failed akmods build, and akmods-failure-triage.yml reads the file it writes out of an uploaded artifact -- so a main() that wrote the payload somewhere else, or stopped writing it, would leave the sticky issue and the README badge silently stale with every job still green.
Goal: Keep red builds informative without misclassification silently hiding real code bugs.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ci_tools.classify_akmods_failure as script
from ci_tools.classify_akmods_failure import (
    FAILURE_KIND_TRANSIENT_NETWORK,
    FAILURE_KIND_UNKNOWN,
    FAILURE_KIND_UPSTREAM_COMPAT,
    ZFS_MAX_KERNEL_MISMATCH_PATTERN,
    build_failure_summary,
    build_step_summary_markdown,
    build_sticky_issue_payload,
    classify_log_text,
    write_step_summary,
    zfs_metadata_from_log,
)


def _parse_github_outputs(path: Path) -> dict[str, str]:
    """
    Parse a GITHUB_OUTPUT file the way GitHub Actions does.

    The format is `name<<DELIMITER`, then the value lines, then a line holding
    only the delimiter. Parsing it rather than substring-matching the text is
    the point: `assertIn("failure_kind<<", text)` passes on a file with a
    missing terminator, which GitHub would reject at runtime.
    """

    values: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        header = lines[index]
        if "<<" not in header:
            raise AssertionError(f"line {index + 1} is not a heredoc header: {header!r}")
        name, delimiter = header.split("<<", 1)
        index += 1
        body: list[str] = []
        while index < len(lines) and lines[index] != delimiter:
            body.append(lines[index])
            index += 1
        if index >= len(lines):
            raise AssertionError(f"{name} is never terminated by its delimiter {delimiter!r}")
        index += 1
        values[name] = "\n".join(body)
    return values


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
        self.assertIn("Failing run: https://github.com/example/repo/actions/runs/12345", markdown)

    def test_step_summary_omits_the_optional_sections_it_has_no_values_for(self) -> None:
        # A payload built without a summary, and without the run URL main()
        # leaves empty when GITHUB_REPOSITORY or GITHUB_RUN_ID is absent, has to
        # render as a shorter summary rather than as "Failing run: " with
        # nothing after it or a stray blank section.
        markdown = build_step_summary_markdown(
            {
                "failure_kind": FAILURE_KIND_UNKNOWN,
                "kernel_release": "6.18.16-200.fc43.x86_64",
                "fedora_version": "43",
                "akmods_upstream_ref": "deadbeefdead",
                "summary": "",
                "run_url": "",
            }
        )

        self.assertNotIn("Failing run:", markdown)
        self.assertEqual(
            markdown,
            "## Akmods build failure\n"
            "\n"
            "- Failure kind: `unknown`\n"
            "- Primary kernel: `6.18.16-200.fc43.x86_64`\n"
            "- Fedora version: `43`\n"
            "- Akmods upstream ref: `deadbeefdead`\n",
        )

    def test_step_summary_is_appended_to_the_file_github_provides(self) -> None:
        # GITHUB_STEP_SUMMARY is shared by every step in the job, so this has to
        # append. Truncating it would erase what earlier steps reported.
        payload = build_sticky_issue_payload(
            failure_kind=FAILURE_KIND_UPSTREAM_COMPAT,
            kernel_release="7.0.4-200.fc44.x86_64",
            akmods_upstream_ref="28079918460b05c43422d48a2a5866aa78f1dce5",
            fedora_version="44",
            run_id="12345",
            run_url="https://github.com/example/repo/actions/runs/12345",
            matched_patterns=[ZFS_MAX_KERNEL_MISMATCH_PATTERN],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            summary_path = Path(temp_dir) / "step-summary.md"
            summary_path.write_text("## An earlier step\n", encoding="utf-8")

            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_path)}, clear=True):
                write_step_summary(payload)

            written = summary_path.read_text(encoding="utf-8")
            self.assertTrue(written.startswith("## An earlier step\n"))
            self.assertIn("## Akmods build failure", written)
            self.assertIn("Failure kind: `upstream-compat`", written)

    def test_no_step_summary_file_is_not_an_error(self) -> None:
        # The command also runs outside Actions -- a maintainer reproducing a
        # classification locally -- where GITHUB_STEP_SUMMARY is unset.
        with patch.dict(os.environ, {}, clear=True):
            write_step_summary({"failure_kind": FAILURE_KIND_UNKNOWN})


class MainTests(unittest.TestCase):
    """
    The environment-to-payload-file-and-outputs plumbing around the classifier.

    Every test above calls a pure function directly, so none of them reaches
    main(). Nothing asserted that the payload lands at the path the upload step
    collects, that the sticky key and failure kind reach later steps as outputs,
    or that a run with no log file still writes a payload instead of failing the
    step that only exists to explain a failure.
    """

    def _run_main(
        self,
        env: dict[str, str],
        *,
        cwd: Path,
        with_github_output: bool = True,
    ) -> tuple[dict[str, str], str]:
        """Run main() with exactly `env` and cwd; return step outputs and stdout."""

        full_env = dict(env)
        output_path = cwd / "github-output"
        if with_github_output:
            output_path.touch()
            full_env["GITHUB_OUTPUT"] = str(output_path)
        stdout = io.StringIO()
        with (
            patch.dict(os.environ, full_env, clear=True),
            contextlib.chdir(cwd),
            contextlib.redirect_stdout(stdout),
        ):
            script.main()
        outputs = _parse_github_outputs(output_path) if with_github_output else {}
        return outputs, stdout.getvalue()

    def _write_log(self, cwd: Path, body: str) -> str:
        log_path = cwd / "artifacts" / "akmods-build.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(body, encoding="utf-8")
        return "artifacts/akmods-build.log"

    def test_classified_failure_writes_the_payload_and_the_sticky_key_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            log = self._write_log(
                work_dir,
                "ZFS_META_VERSION='2.4.1'\nZFS_META_KVER_MAX='6.19'\nconfigure: exit 1\n",
            )

            outputs, _ = self._run_main(
                {
                    "AKMODS_FAILURE_LOG": log,
                    "KERNEL_RELEASE": "7.0.4-200.fc44.x86_64",
                    "AKMODS_UPSTREAM_REF": "28079918460b05c43422d48a2a5866aa78f1dce5",
                    "FEDORA_VERSION": "44",
                    "GITHUB_RUN_ID": "12345",
                    "GITHUB_REPOSITORY": "Danathar/zfs-aurora-complex",
                    "AKMODS_FAILURE_PAYLOAD_PATH": "artifacts/akmods-failure.json",
                },
                cwd=work_dir,
            )

            payload_path = work_dir / "artifacts" / "akmods-failure.json"
            self.assertTrue(payload_path.is_file(), "no failure payload was written")
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["failure_kind"], FAILURE_KIND_UPSTREAM_COMPAT)
            self.assertEqual(
                payload["key"],
                "upstream-compat:7.0.4-200.fc44.x86_64:28079918460b",
            )
            # write_akmods_badge reads these two off the payload rather than
            # re-parsing `summary`, so main() has to put them there.
            self.assertEqual(payload["zfs_version"], "2.4.1")
            self.assertEqual(payload["max_kernel"], "6.19")
            self.assertEqual(payload["fedora_version"], "44")
            self.assertIn(ZFS_MAX_KERNEL_MISMATCH_PATTERN, payload["body"])
            self.assertEqual(
                payload["run_url"],
                "https://github.com/Danathar/zfs-aurora-complex/actions/runs/12345",
            )

            self.assertEqual(outputs["failure_kind"], FAILURE_KIND_UPSTREAM_COMPAT)
            self.assertEqual(outputs["sticky_key"], payload["key"])
            # The upload step collects a fixed `artifacts/akmods-failure.json`,
            # so the reported path has to be where the file actually went.
            self.assertEqual(outputs["payload_path"], "artifacts/akmods-failure.json")

    def test_payload_path_defaults_to_the_path_the_upload_step_collects(self) -> None:
        # prepare-main-akmods/action.yml sets AKMODS_FAILURE_PAYLOAD_PATH today,
        # but its upload step names a fixed `artifacts/akmods-failure.json`. If
        # the default and that path ever disagree the artifact goes up empty and
        # the triage workflow finds nothing to read, so pin the default here.
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)

            outputs, _ = self._run_main({"KERNEL_RELEASE": "7.0.4"}, cwd=work_dir)

            self.assertTrue((work_dir / "artifacts" / "akmods-failure.json").is_file())
            self.assertEqual(outputs["payload_path"], "artifacts/akmods-failure.json")

    def test_a_missing_log_still_writes_an_unclassified_payload(self) -> None:
        # The classify step runs because the build already failed. Its job is to
        # explain that failure, so an absent or unreadable log must still produce
        # a payload -- an exception here would replace a real failure message
        # with a traceback from the step that was meant to describe it.
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)

            outputs, stdout = self._run_main(
                {
                    "AKMODS_FAILURE_LOG": "artifacts/does-not-exist.log",
                    "KERNEL_RELEASE": "6.18.16-200.fc43.x86_64",
                    "AKMODS_UPSTREAM_REF": "deadbeefdeadbeef",
                    "FEDORA_VERSION": "43",
                },
                cwd=work_dir,
            )

            payload = json.loads(
                (work_dir / "artifacts" / "akmods-failure.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["failure_kind"], FAILURE_KIND_UNKNOWN)
            self.assertTrue(payload["title"].startswith("Unclassified"))
            self.assertIn("no known compatibility pattern matched", payload["summary"])
            self.assertEqual(payload["zfs_version"], "")
            self.assertEqual(payload["max_kernel"], "")
            self.assertEqual(outputs["failure_kind"], FAILURE_KIND_UNKNOWN)
            self.assertIn(FAILURE_KIND_UNKNOWN, stdout)

    def test_an_undecodable_log_is_classified_instead_of_raising(self) -> None:
        # akmods build output is compiler and RPM output, which is not reliably
        # UTF-8. The read replaces undecodable bytes; the patterns around them
        # still have to match.
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            log_path = work_dir / "akmods-build.log"
            log_path.write_bytes(
                b"module.c:1: error: implicit declaration of function \xff\xfe'zfs_setattr'\n"
            )

            outputs, _ = self._run_main(
                {
                    "AKMODS_FAILURE_LOG": "akmods-build.log",
                    "KERNEL_RELEASE": "6.18.16-200.fc43.x86_64",
                },
                cwd=work_dir,
            )

            self.assertEqual(outputs["failure_kind"], FAILURE_KIND_UPSTREAM_COMPAT)

    def test_the_run_url_needs_both_the_repository_and_the_run_id(self) -> None:
        # Half a URL in the sticky issue body is worse than none: it renders as
        # a broken link a reader follows to a 404.
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)

            self._run_main(
                {"GITHUB_RUN_ID": "12345", "KERNEL_RELEASE": "7.0.4"},
                cwd=work_dir,
            )

            payload = json.loads(
                (work_dir / "artifacts" / "akmods-failure.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["run_url"], "")

    def test_a_github_enterprise_server_url_is_honoured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)

            self._run_main(
                {
                    "GITHUB_SERVER_URL": "https://ghe.example.com",
                    "GITHUB_REPOSITORY": "Danathar/zfs-aurora-complex",
                    "GITHUB_RUN_ID": "99",
                    "KERNEL_RELEASE": "7.0.4",
                },
                cwd=work_dir,
            )

            payload = json.loads(
                (work_dir / "artifacts" / "akmods-failure.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                payload["run_url"],
                "https://ghe.example.com/Danathar/zfs-aurora-complex/actions/runs/99",
            )

    def test_a_nested_payload_path_has_its_directories_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)

            outputs, _ = self._run_main(
                {
                    "AKMODS_FAILURE_PAYLOAD_PATH": "artifacts/triage/run-1/akmods-failure.json",
                    "KERNEL_RELEASE": "7.0.4",
                },
                cwd=work_dir,
            )

            self.assertTrue(
                (work_dir / "artifacts" / "triage" / "run-1" / "akmods-failure.json").is_file()
            )
            self.assertEqual(outputs["payload_path"], "artifacts/triage/run-1/akmods-failure.json")

    def test_the_job_summary_is_written_when_actions_provides_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            summary_path = work_dir / "step-summary.md"
            summary_path.touch()
            log = self._write_log(work_dir, "error: conflicting types for 'zfs_setattr'\n")

            self._run_main(
                {
                    "AKMODS_FAILURE_LOG": log,
                    "KERNEL_RELEASE": "6.18.16-200.fc43.x86_64",
                    "FEDORA_VERSION": "43",
                    "GITHUB_STEP_SUMMARY": str(summary_path),
                },
                cwd=work_dir,
            )

            written = summary_path.read_text(encoding="utf-8")
            self.assertIn("## Akmods build failure", written)
            self.assertIn("Failure kind: `upstream-compat`", written)

    def test_running_outside_actions_writes_the_payload_and_no_outputs(self) -> None:
        # A maintainer reproducing a classification locally has no GITHUB_OUTPUT.
        # write_github_outputs requires it, so main() must check before calling:
        # the guard is what keeps that reproduction from raising.
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            log = self._write_log(work_dir, "error: conflicting types for 'zfs_setattr'\n")

            _, stdout = self._run_main(
                {"AKMODS_FAILURE_LOG": log, "KERNEL_RELEASE": "6.18.16-200.fc43.x86_64"},
                cwd=work_dir,
                with_github_output=False,
            )

            self.assertTrue((work_dir / "artifacts" / "akmods-failure.json").is_file())
            self.assertFalse((work_dir / "github-output").exists())
            self.assertIn("artifacts/akmods-failure.json", stdout)


if __name__ == "__main__":
    unittest.main()
