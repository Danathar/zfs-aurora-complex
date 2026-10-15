"""
Script: tests/test_write_last_good_build_badge.py
What: Tests for the shields.io badge showing how old the live `:latest` image is.
Doing: Feeds representative Created timestamps and "now" values through build_last_good_build_badge,
    then drives main() with a stubbed skopeo inspect and reads back the file and step outputs.
Why: The badge is the only README-visible signal of image staleness during an outage; the day-count
    math and missing-data handling need to stay correct. main() decides which image is inspected and
    writes the `updated` output that akmods-failure-triage.yml's "Publish badges to status branch"
    step gates on, so a break there strands a correct payload unpublished on a green job.
Goal: Keep the badge accurate without needing any tracked state of its own.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import ci_tools.write_last_good_build_badge as script
from ci_tools.common import CiToolError, load_repo_defaults
from ci_tools.write_last_good_build_badge import build_last_good_build_badge


class BuildLastGoodBuildBadgeTests(unittest.TestCase):
    def test_missing_created_timestamp_returns_none(self) -> None:
        badge = build_last_good_build_badge(
            created_iso="", now=datetime(2026, 7, 24, tzinfo=UTC)
        )
        self.assertIsNone(badge)

    def test_built_today_says_today(self) -> None:
        badge = build_last_good_build_badge(
            created_iso="2026-07-24T07:42:31Z",
            now=datetime(2026, 7, 24, 20, 0, tzinfo=UTC),
        )
        self.assertIn("today", badge["message"])
        self.assertEqual(badge["color"], "brightgreen")

    def test_built_yesterday_uses_singular_day(self) -> None:
        badge = build_last_good_build_badge(
            created_iso="2026-07-23T07:42:31Z",
            now=datetime(2026, 7, 24, 6, 0, tzinfo=UTC),
        )
        self.assertIn("1 day ago", badge["message"])

    def test_built_seventeen_days_ago_uses_plural(self) -> None:
        badge = build_last_good_build_badge(
            created_iso="2026-07-07T07:42:31Z",
            now=datetime(2026, 7, 24, 6, 0, tzinfo=UTC),
        )
        self.assertIn("2026-07-07", badge["message"])
        self.assertIn("17 days ago", badge["message"])

    def test_a_naive_now_is_read_as_utc_rather_than_local_time(self) -> None:
        # main() always passes an aware datetime, but the day-count subtracts a
        # date from a date: a caller in a UTC-behind timezone passing a naive
        # "now" would otherwise report an image built today as built yesterday.
        badge = build_last_good_build_badge(
            created_iso="2026-07-24T23:30:00Z",
            # Deliberately naive: this test exists because a naive datetime can
            # still reach production code, so the function has to handle it.
            # Stripping tzinfo (rather than omitting it) keeps DTZ001 quiet on
            # every ruff release without a noqa that newer ruff calls unused.
            now=datetime(2026, 7, 24, 23, 45, tzinfo=UTC).replace(tzinfo=None),
        )
        self.assertIn("today", badge["message"])

    def test_label_is_last_good_build(self) -> None:
        badge = build_last_good_build_badge(
            created_iso="2026-07-07T07:42:31Z",
            now=datetime(2026, 7, 24, tzinfo=UTC),
        )
        self.assertEqual(badge["label"], "last good build")


def _parse_github_outputs(path: Path) -> dict[str, str]:
    """
    Parse a GITHUB_OUTPUT file the way GitHub Actions does.

    The format is `name<<DELIMITER`, the value lines, then a line holding only
    the delimiter. Parsed properly rather than with a substring assertion:
    `assertIn("updated=", text)` passes on a file GitHub would reject, and the
    workflow step that reads `updated` is what these tests are about.
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
    Which image main() inspects, and what it reports to the publish step.

    The tests above only exercise the pure payload builder, so nothing asserted
    the registry reference main() assembles, that the run's credentials are
    passed to skopeo, or that `updated` is written on the unknown-timestamp
    branch. `skopeo_inspect_json_optional` is stubbed: this tier mocks every
    external call, so no test here reaches a registry.
    """

    def _run_main(
        self, env: dict[str, str], *, cwd: Path, inspect_result: dict | None
    ) -> tuple[dict[str, str], list]:
        """
        Run main() with exactly `env` and cwd against a stubbed skopeo inspect.

        Returns the parsed step outputs and the recorded inspect calls, so a
        test can assert on the reference and credentials main() chose.
        """

        output_path = cwd / "github-output"
        output_path.touch()
        full_env = dict(env)
        full_env["GITHUB_OUTPUT"] = str(output_path)

        calls: list = []

        def fake_inspect(image_ref: str, *, creds: str | None = None) -> dict | None:
            calls.append((image_ref, creds))
            return inspect_result

        with (
            patch.object(script, "skopeo_inspect_json_optional", fake_inspect),
            patch.dict(os.environ, full_env, clear=True),
            contextlib.chdir(cwd),
        ):
            script.main()
        return _parse_github_outputs(output_path), calls

    def test_inspects_the_latest_tag_of_the_repo_image_with_the_run_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            outputs, calls = self._run_main(
                {
                    "IMAGE_NAME": "zfs-aurora-complex",
                    "GITHUB_REPOSITORY_OWNER": "Danathar",
                    "REGISTRY_ACTOR": "github-actions[bot]",
                    "REGISTRY_TOKEN": "ghs-token",
                    "BADGE_OUTPUT_PATH": "artifacts/last-good-build-badge.json",
                },
                cwd=work_dir,
                inspect_result={"Created": "2026-07-07T07:42:31Z"},
            )

            # `latest` is the point of this badge: it only moves on a real
            # promotion, so inspecting any other tag would report the age of
            # something a user was never offered.
            self.assertEqual(
                calls,
                [
                    (
                        "docker://ghcr.io/danathar/zfs-aurora-complex:latest",
                        "github-actions[bot]:ghs-token",
                    )
                ],
            )
            self.assertEqual(outputs["updated"], "true")
            self.assertEqual(outputs["badge_path"], "artifacts/last-good-build-badge.json")
            badge = json.loads(
                (work_dir / "artifacts" / "last-good-build-badge.json").read_text(encoding="utf-8")
            )
            self.assertEqual(badge["label"], "last good build")
            self.assertIn("2026-07-07", badge["message"])

    def test_image_name_falls_back_to_the_checked_in_repo_default(self) -> None:
        expected = load_repo_defaults()["IMAGE_NAME"]

        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            _, calls = self._run_main(
                {"GITHUB_REPOSITORY_OWNER": "Danathar"},
                cwd=work_dir,
                inspect_result={"Created": "2026-07-07T07:42:31Z"},
            )

            self.assertEqual(calls[0][0], f"docker://ghcr.io/danathar/{expected}:latest")

    def test_missing_credentials_inspect_anonymously_rather_than_sending_a_half_pair(self) -> None:
        # main() builds the pair itself, so each half has to be dropped on its
        # own: a check on only one of them would send "actor:" or ":token".
        half_pairs = {
            "actor only": {"REGISTRY_ACTOR": "github-actions[bot]"},
            "token only": {"REGISTRY_TOKEN": "ghs-token"},
        }
        for case, creds_env in half_pairs.items():
            with self.subTest(case), tempfile.TemporaryDirectory() as temp_dir:
                work_dir = Path(temp_dir)
                _, calls = self._run_main(
                    {
                        "IMAGE_NAME": "zfs-aurora-complex",
                        "GITHUB_REPOSITORY_OWNER": "Danathar",
                        **creds_env,
                    },
                    cwd=work_dir,
                    inspect_result={"Created": "2026-07-07T07:42:31Z"},
                )

                self.assertIsNone(calls[0][1])

    def test_badge_output_path_defaults_to_the_path_the_workflow_publishes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            outputs, _ = self._run_main(
                {"IMAGE_NAME": "zfs-aurora-complex", "GITHUB_REPOSITORY_OWNER": "Danathar"},
                cwd=work_dir,
                inspect_result={"Created": "2026-07-07T07:42:31Z"},
            )

            self.assertTrue((work_dir / "artifacts" / "last-good-build-badge.json").is_file())
            self.assertEqual(outputs["badge_path"], "artifacts/last-good-build-badge.json")

    def test_an_image_that_does_not_exist_leaves_the_previous_badge_in_place(self) -> None:
        # skopeo_inspect_json_optional returns None for an absent image. The
        # badge on the status branch is then the last known good one, which is
        # still true; overwriting it with an error state would be worse.
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            outputs, _ = self._run_main(
                {"IMAGE_NAME": "zfs-aurora-complex", "GITHUB_REPOSITORY_OWNER": "Danathar"},
                cwd=work_dir,
                inspect_result=None,
            )

            self.assertEqual(outputs["updated"], "false")
            self.assertNotIn("badge_path", outputs)
            self.assertFalse(
                (work_dir / "artifacts" / "last-good-build-badge.json").exists(),
                "a badge file was written for an image whose age is unknown",
            )

    def test_inspect_output_without_a_created_field_reports_not_updated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            outputs, _ = self._run_main(
                {"IMAGE_NAME": "zfs-aurora-complex", "GITHUB_REPOSITORY_OWNER": "Danathar"},
                cwd=work_dir,
                inspect_result={"Digest": "sha256:" + "a" * 64},
            )

            self.assertEqual(outputs["updated"], "false")
            self.assertFalse((work_dir / "artifacts" / "last-good-build-badge.json").exists())

    def test_a_missing_repository_owner_names_the_variable_it_needs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            with self.assertRaises(CiToolError) as caught:
                self._run_main(
                    {"IMAGE_NAME": "zfs-aurora-complex"},
                    cwd=work_dir,
                    inspect_result={"Created": "2026-07-07T07:42:31Z"},
                )

            # Pinned to the variable, not just the exception type: any missing
            # environment variable raises CiToolError, so assertRaises alone
            # would pass on a run that failed for an unrelated reason.
            self.assertEqual(
                str(caught.exception),
                "Missing required environment variable: GITHUB_REPOSITORY_OWNER",
            )


if __name__ == "__main__":
    unittest.main()
