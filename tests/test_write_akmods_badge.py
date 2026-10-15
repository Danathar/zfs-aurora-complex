"""
Script: tests/test_write_akmods_badge.py
What: Tests for the shields.io badge payload built from build workflow conclusions, and for
the `main()` that turns workflow environment into a badge file plus step outputs.
Doing: Feeds representative conclusion/failure-payload/build_ran combinations through
build_badge_payload, then drives main() with a controlled environment and reads back both
the file it wrote and the GITHUB_OUTPUT it wrote.
Why: Guards the README badge contract so it only speaks to OpenZFS/kernel compat state,
and so a gate-skipped scheduled run can never overwrite a real red state with green. The
`updated` step output is the second half of that contract: akmods-failure-triage.yml's
"Publish badges to status branch" step runs only when it is 'true', so a main() that
stopped writing it would leave a correct payload unpublished with the job still green.
Goal: Keep the badge accurate without it drifting into a general CI-health indicator.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ci_tools.write_akmods_badge as script
from ci_tools.classify_akmods_failure import FAILURE_KIND_UNKNOWN, FAILURE_KIND_UPSTREAM_COMPAT
from ci_tools.write_akmods_badge import build_badge_payload


class BuildBadgePayloadTests(unittest.TestCase):
    def test_success_conclusion_is_green_in_sync(self) -> None:
        badge = build_badge_payload(conclusion="success", failure_payload=None, build_ran=True)
        self.assertEqual(badge["message"], "in sync")
        self.assertEqual(badge["color"], "brightgreen")

    def test_gate_skipped_success_does_not_touch_badge(self) -> None:
        # This is the bug: a scheduled run the stable-signal gate skipped still
        # reports conclusion "success" but never built anything, so it must not
        # overwrite a previously red badge with "in sync".
        badge = build_badge_payload(conclusion="success", failure_payload=None, build_ran=False)
        self.assertIsNone(badge)

    def test_upstream_compat_failure_names_the_specific_versions(self) -> None:
        badge = build_badge_payload(
            conclusion="failure",
            failure_payload={
                "failure_kind": FAILURE_KIND_UPSTREAM_COMPAT,
                "kernel_release": "7.1.4-200.fc44.x86_64",
                "zfs_version": "2.4.3",
                "max_kernel": "7.0",
            },
            build_ran=True,
        )
        self.assertEqual(badge["color"], "red")
        self.assertIn("OpenZFS 2.4.3", badge["message"])
        self.assertIn("7.0", badge["message"])
        self.assertIn("7.1.4-200.fc44.x86_64", badge["message"])

    def test_upstream_compat_failure_without_parsed_versions_uses_generic_message(self) -> None:
        badge = build_badge_payload(
            conclusion="failure",
            failure_payload={"failure_kind": FAILURE_KIND_UPSTREAM_COMPAT, "kernel_release": "x"},
            build_ran=True,
        )
        self.assertEqual(badge["color"], "red")
        self.assertIn("known upstream ZFS/kernel incompatibility", badge["message"])

    def test_unclassified_failure_does_not_touch_badge(self) -> None:
        badge = build_badge_payload(
            conclusion="failure",
            failure_payload={"failure_kind": FAILURE_KIND_UNKNOWN},
            build_ran=True,
        )
        self.assertIsNone(badge)

    def test_failure_without_payload_does_not_touch_badge(self) -> None:
        badge = build_badge_payload(conclusion="failure", failure_payload=None, build_ran=True)
        self.assertIsNone(badge)

    def test_other_conclusions_do_not_touch_badge(self) -> None:
        badge = build_badge_payload(conclusion="cancelled", failure_payload=None, build_ran=True)
        self.assertIsNone(badge)


def _parse_github_outputs(path: Path) -> dict[str, str]:
    """
    Parse a GITHUB_OUTPUT file the way GitHub Actions does.

    The format is `name<<DELIMITER`, the value lines, then a line holding only
    the delimiter. Parsed properly rather than with a substring assertion:
    `assertIn("updated=", text)` passes on a file GitHub would reject, and the
    workflow step that reads `updated` is exactly what these tests are about.
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


class MainTests(unittest.TestCase):
    """
    The environment-to-file-and-outputs plumbing around build_badge_payload.

    The tests above cover the decision; none of them reaches main(), so nothing
    asserted that a decided badge is actually written where the publish step
    looks for it, or that `updated` is written on both branches.
    """

    def _run_main(self, env: dict[str, str], *, cwd: Path) -> dict[str, str]:
        """Run main() with exactly `env` and cwd, returning the parsed step outputs."""

        output_path = cwd / "github-output"
        output_path.touch()
        full_env = dict(env)
        full_env["GITHUB_OUTPUT"] = str(output_path)
        with patch.dict(os.environ, full_env, clear=True), contextlib.chdir(cwd):
            script.main()
        return _parse_github_outputs(output_path)

    def test_successful_build_writes_the_payload_and_reports_updated_true(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            outputs = self._run_main(
                {
                    "WORKFLOW_CONCLUSION": "success",
                    "BUILD_RAN": "true",
                    "BADGE_OUTPUT_PATH": "artifacts/akmods-badge.json",
                },
                cwd=work_dir,
            )

            badge_path = work_dir / "artifacts" / "akmods-badge.json"
            self.assertTrue(badge_path.is_file(), "no badge JSON was written")
            badge = json.loads(badge_path.read_text(encoding="utf-8"))
            self.assertEqual(badge["message"], "in sync")
            self.assertEqual(outputs["updated"], "true")
            # The publish step copies from this path, so it has to be the path
            # main() reports, not the one the test happened to ask for.
            self.assertEqual(outputs["badge_path"], "artifacts/akmods-badge.json")

    def test_badge_output_path_defaults_to_the_path_the_workflow_publishes(self) -> None:
        # akmods-failure-triage.yml sets BADGE_OUTPUT_PATH today, but its publish
        # step copies a fixed `artifacts/akmods-badge.json`. If the two ever
        # disagree the badge silently stops updating, so pin the default here.
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            outputs = self._run_main(
                {"WORKFLOW_CONCLUSION": "success", "BUILD_RAN": "true"},
                cwd=work_dir,
            )

            self.assertTrue((work_dir / "artifacts" / "akmods-badge.json").is_file())
            self.assertEqual(outputs["badge_path"], "artifacts/akmods-badge.json")

    def test_gate_skipped_success_writes_no_file_and_reports_updated_false(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            outputs = self._run_main(
                {
                    "WORKFLOW_CONCLUSION": "success",
                    "BUILD_RAN": "false",
                    "BADGE_OUTPUT_PATH": "artifacts/akmods-badge.json",
                },
                cwd=work_dir,
            )

            self.assertEqual(outputs["updated"], "false")
            self.assertNotIn("badge_path", outputs)
            self.assertFalse(
                (work_dir / "artifacts" / "akmods-badge.json").exists(),
                "a gate-skipped run wrote a badge file the publish step would copy",
            )

    def test_absent_build_ran_is_treated_as_not_built(self) -> None:
        # `BUILD_RAN: ${{ steps.download.outputs.build_ran }}` expands to the
        # empty string when that step did not set it, so "unset" has to mean
        # "did not build" rather than defaulting to green.
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            outputs = self._run_main({"WORKFLOW_CONCLUSION": "success"}, cwd=work_dir)

            self.assertEqual(outputs["updated"], "false")
            self.assertFalse((work_dir / "artifacts" / "akmods-badge.json").exists())

    def test_build_ran_is_read_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            outputs = self._run_main(
                {"WORKFLOW_CONCLUSION": "success", "BUILD_RAN": "True"},
                cwd=work_dir,
            )

            self.assertEqual(outputs["updated"], "true")

    def test_failure_payload_is_read_off_disk_and_named_in_the_badge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            payload_path = work_dir / "artifacts" / "akmods-failure.json"
            payload_path.parent.mkdir(parents=True)
            payload_path.write_text(
                json.dumps(
                    {
                        "failure_kind": FAILURE_KIND_UPSTREAM_COMPAT,
                        "kernel_release": "7.1.4-200.fc44.x86_64",
                        "zfs_version": "2.4.3",
                        "max_kernel": "7.0",
                    }
                ),
                encoding="utf-8",
            )

            outputs = self._run_main(
                {
                    "WORKFLOW_CONCLUSION": "failure",
                    "BUILD_RAN": "true",
                    "FAILURE_PAYLOAD_PATH": "artifacts/akmods-failure.json",
                },
                cwd=work_dir,
            )

            self.assertEqual(outputs["updated"], "true")
            badge = json.loads(
                (work_dir / "artifacts" / "akmods-badge.json").read_text(encoding="utf-8")
            )
            self.assertEqual(badge["color"], "red")
            self.assertIn("OpenZFS 2.4.3", badge["message"])
            self.assertIn("7.1.4-200.fc44.x86_64", badge["message"])

    def test_a_failure_payload_path_that_does_not_exist_leaves_the_badge_alone(self) -> None:
        # The triage workflow points FAILURE_PAYLOAD_PATH at an artifact that is
        # only downloaded on some runs. A missing file must read as "no
        # classified failure", not raise -- a crash here fails the triage job on
        # a run that had nothing to say.
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            outputs = self._run_main(
                {
                    "WORKFLOW_CONCLUSION": "failure",
                    "BUILD_RAN": "true",
                    "FAILURE_PAYLOAD_PATH": "artifacts/akmods-failure.json",
                },
                cwd=work_dir,
            )

            self.assertEqual(outputs["updated"], "false")
            self.assertFalse((work_dir / "artifacts" / "akmods-badge.json").exists())


if __name__ == "__main__":
    unittest.main()
