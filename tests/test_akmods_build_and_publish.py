"""
Script: tests/test_akmods_build_and_publish.py
What: Tests helper functions used by `ci_tools/akmods_build_and_publish.py`.
Doing: Checks cache-document generation and the primary-kernel-only publish flow.
Why: Catches behavior changes that could break the shared akmods cache build.
Goal: Keep the simplified akmods publish path explicit and reviewable.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from ci_tools import akmods_build_and_publish as script
from ci_tools.akmods_build_and_publish import (
    build_kernel_cache_document,
    kernel_major_minor_patch,
    kernel_name_for_flavor,
)


class AkmodsBuildAndPublishTests(unittest.TestCase):
    def test_kernel_name_for_longterm_flavor(self) -> None:
        self.assertEqual(kernel_name_for_flavor("longterm"), "kernel-longterm")
        self.assertEqual(kernel_name_for_flavor("longterm-lts"), "kernel-longterm")

    def test_kernel_name_for_standard_flavor(self) -> None:
        self.assertEqual(kernel_name_for_flavor("main"), "kernel")

    def test_kernel_major_minor_patch(self) -> None:
        value = kernel_major_minor_patch("6.18.12-200.fc43.x86_64")
        self.assertEqual(value, "6.18.12-200")

    def test_build_kernel_cache_document_default_path(self) -> None:
        payload, cache_path, upstream_build_root = build_kernel_cache_document(
            kernel_release="6.18.12-200.fc43.x86_64",
            kernel_flavor="main",
            akmods_version="43",
            build_root=Path("/tmp/akmods/build"),
            kcpath_override="",
        )

        self.assertEqual(payload["kernel_name"], "kernel")
        self.assertEqual(payload["kernel_major_minor_patch"], "6.18.12-200")
        self.assertEqual(str(upstream_build_root), "/tmp/akmods/build")
        self.assertTrue(payload["KCWD"].endswith("/main-43/KCWD"))
        self.assertTrue(payload["KCPATH"].endswith("/main-43/KCWD/rpms"))
        self.assertTrue(str(cache_path).endswith("/main-43/KCWD/rpms/cache.json"))

    def test_build_kernel_cache_document_with_kcpath_override(self) -> None:
        payload, cache_path, upstream_build_root = build_kernel_cache_document(
            kernel_release="6.18.12-200.fc43.x86_64",
            kernel_flavor="main",
            akmods_version="43",
            build_root=Path("/tmp/akmods/build"),
            kcpath_override="/custom/rpms",
        )

        self.assertEqual(str(upstream_build_root), "/tmp/akmods/build")
        self.assertEqual(payload["KCPATH"], "/custom/rpms")
        self.assertEqual(str(cache_path), "/custom/rpms/cache.json")

    def test_write_kernel_cache_file_exports_upstream_build_root(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tempdir,
            patch.object(script, "AKMODS_WORKTREE", Path(tempdir)),
            patch.dict(
                script.os.environ,
                {
                    "AKMODS_KERNEL": "main",
                    "AKMODS_VERSION": "43",
                },
                clear=True,
            ),
        ):
            script.write_kernel_cache_file(
                kernel_release="6.18.16-200.fc43.x86_64",
            )

            self.assertEqual(
                script.os.environ["AKMODS_BUILDDIR"],
                f"{tempdir}/build",
            )
            self.assertFalse("KCPATH" in script.os.environ)

    def test_write_kernel_cache_file_preserves_preexisting_kcpath_without_override(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tempdir,
            patch.object(script, "AKMODS_WORKTREE", Path(tempdir)),
            patch.dict(
                script.os.environ,
                {
                    "AKMODS_KERNEL": "main",
                    "AKMODS_VERSION": "43",
                    "KCPATH": "/preexisting",
                },
                clear=True,
            ),
        ):
            with patch.object(Path, "mkdir"), patch.object(Path, "write_text"):
                script.write_kernel_cache_file(
                    kernel_release="6.18.16-200.fc43.x86_64",
                )

            self.assertEqual(script.os.environ["KCPATH"], "/preexisting")

    def test_build_and_push_records_the_built_image_between_build_and_push(self) -> None:
        # main() patches this function out in the tests below, so the body is
        # asserted here: the cache.json upstream reads has to be on disk with
        # the pinned release in it *before* `just build` runs, the built image
        # has to be recorded between the build and the push, and `just push`
        # has to follow the build rather than race it.
        observed: list[tuple[str, bool]] = []
        local_ref = "localhost/zfs-aurora-complex-akmods:main-43-x86_64"

        with (
            tempfile.TemporaryDirectory() as tempdir,
            patch.object(script, "AKMODS_WORKTREE", Path(tempdir)),
            patch.dict(
                script.os.environ,
                {
                    "AKMODS_KERNEL": "main",
                    "AKMODS_VERSION": "43",
                    "AKMODS_REPO": "zfs-aurora-complex-akmods",
                    "GITHUB_OUTPUT": str(Path(tempdir) / "github-output"),
                },
                clear=True,
            ),
        ):
            cache_json = (
                Path(tempdir) / "build" / "main-43" / "KCWD" / "rpms" / "cache.json"
            )

            def record(args: list[str], **_kwargs: object) -> str:
                observed.append((args[-1], cache_json.exists()))
                return f"{'a' * 64}\n" if args[0] == "podman" else ""

            def record_build(args: list[str], **_kwargs: object) -> tuple[int, str]:
                record(args)
                return 0, ""

            with (
                patch.object(script, "run_cmd", side_effect=record) as run_cmd,
                patch.object(script, "run_streamed", side_effect=record_build) as run_streamed,
                contextlib.redirect_stdout(io.StringIO()) as printed,
            ):
                script.build_and_push_kernel_release("6.18.16-200.fc43.x86_64")

            self.assertEqual(observed, [("build", True), (local_ref, True), ("push", True)])
            run_streamed.assert_called_once_with(["just", "build"], cwd=str(Path(tempdir)))
            self.assertEqual(
                run_cmd.call_args_list,
                [
                    call(["podman", "image", "inspect", "--format", "{{.Id}}", local_ref]),
                    call(["just", "push"], cwd=str(Path(tempdir)), capture_output=False),
                ],
            )
            # The pin step reads this back as AKMODS_BUILT_IMAGE_ID.
            output = (Path(tempdir) / "github-output").read_text(encoding="utf-8")
            self.assertIn("built_image_id<<", output)
            self.assertIn("a" * 64 + "\n", output)
            self.assertIn(
                "Building akmods for kernel release: 6.18.16-200.fc43.x86_64",
                printed.getvalue(),
            )

            payload = json.loads(cache_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["kernel_release"], "6.18.16-200.fc43.x86_64")
            self.assertEqual(payload["kernel_flavor"], "main")

    def test_main_primary_kernel_runs_upstream_manifest_flow(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tempdir,
            patch.object(script, "AKMODS_WORKTREE", Path(tempdir)),
            patch.object(script, "build_and_push_kernel_release") as build_release,
            patch.object(script, "run_cmd") as run_cmd,
            patch.dict(
                script.os.environ,
                {
                    "KERNEL_RELEASE": "6.18.16-200.fc43.x86_64",
                    "GITHUB_REPOSITORY_OWNER": "Danathar",
                    "AKMODS_REPO": "zfs-aurora-complex-akmods",
                    "AKMODS_KERNEL": "main",
                    "AKMODS_VERSION": "43",
                },
                clear=False,
            ),
        ):
            script.main()

        build_release.assert_called_once_with("6.18.16-200.fc43.x86_64")
        self.assertEqual(
            run_cmd.call_args_list,
            [
                call(["just", "login"], cwd=str(Path(tempdir)), capture_output=False),
                call(["just", "manifest"], cwd=str(Path(tempdir)), capture_output=False),
            ],
        )

    def test_main_without_kernel_release_keeps_upstream_default_behavior(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tempdir,
            patch.object(script, "AKMODS_WORKTREE", Path(tempdir)),
            patch.object(script, "run_cmd") as run_cmd,
            patch.dict(script.os.environ, {}, clear=True),
        ):
            script.main()

        self.assertEqual(
            run_cmd.call_args_list,
            [
                call(["just", "build"], cwd=str(Path(tempdir)), capture_output=False),
                call(["just", "login"], cwd=str(Path(tempdir)), capture_output=False),
                call(["just", "push"], cwd=str(Path(tempdir)), capture_output=False),
                call(["just", "manifest"], cwd=str(Path(tempdir)), capture_output=False),
            ],
        )


# Verbatim from the failing build of run 39380642084 (#194).
MIRROR_DNS_FAILURE = (
    ">>> Curl error (6): Could not resolve hostname for "
    "https://repos.eggycrew.com/rpmfusion/free/fedora/rpmfusion-free-release-44.noarch.rpm "
    "[Could not resolve host: repos.eggycrew.com]\nFailed to download files\n"
)
KERNEL = "7.2.7-200.fc44.x86_64"


class BuildWithNetworkRetryTests(unittest.TestCase):
    def run_builds(self, results: list[tuple[int, str]]) -> str:
        """Run build_with_network_retry on scripted `just build` results; record calls and waits."""
        with (
            patch.object(script, "run_streamed", side_effect=results) as run_streamed,
            patch.object(script.time, "sleep") as sleep,
            contextlib.redirect_stdout(io.StringIO()) as printed,
        ):
            try:
                script.build_with_network_retry(KERNEL)
            finally:
                self.calls = run_streamed.call_count
                self.sleeps = [c.args[0] for c in sleep.call_args_list]
        return printed.getvalue()

    def test_network_failure_then_success_retries_once(self) -> None:
        printed = self.run_builds([(1, MIRROR_DNS_FAILURE), (0, "")])
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.sleeps, [60])
        self.assertIn("attempt 1/3", printed)

    def test_persistent_network_failure_gives_up_after_three_attempts(self) -> None:
        with self.assertRaisesRegex(
            script.CiToolError, "attempt 3/3, failure kind transient-network"
        ):
            self.run_builds([(1, MIRROR_DNS_FAILURE)] * 3)
        self.assertEqual(self.calls, 3)
        self.assertEqual(self.sleeps, [60, 180])

    def test_unclassified_failure_is_not_retried(self) -> None:
        with self.assertRaisesRegex(script.CiToolError, "attempt 1/3, failure kind unknown"):
            self.run_builds([(1, "make: *** [Makefile:12: all] Error 2\n")])
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.sleeps, [])

    def test_compat_failure_with_network_noise_is_not_retried(self) -> None:
        # A real kernel/ZFS break must fail on the first attempt even when a
        # mirror hiccup also left network lines in the same output.
        with self.assertRaisesRegex(script.CiToolError, "failure kind upstream-compat"):
            self.run_builds([(1, MIRROR_DNS_FAILURE + "configure: error: Unsupported Linux 7.2\n")])
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.sleeps, [])

    def test_only_the_failing_attempts_output_is_classified(self) -> None:
        # The second attempt's unrelated failure must not be retried just
        # because the first attempt's output had network errors.
        with self.assertRaisesRegex(script.CiToolError, "attempt 2/3, failure kind unknown"):
            self.run_builds([(1, MIRROR_DNS_FAILURE), (1, "error: some other failure\n")])
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.sleeps, [60])

    def test_run_streamed_echoes_and_returns_combined_output(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            returncode, output = script.run_streamed(
                ["sh", "-c", "echo out; echo err >&2; exit 3"], cwd="."
            )
        self.assertEqual(returncode, 3)
        self.assertEqual(output, "out\nerr\n")
        self.assertEqual(printed.getvalue(), "out\nerr\n")


if __name__ == "__main__":
    unittest.main()
