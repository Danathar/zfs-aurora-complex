"""
Script: tests/test_promote_stable.py
What: Tests for candidate-to-stable promotion in the single-repository flow.
Doing: Verifies the candidate tag naming rule and the exact copy destinations.
Why: Promotion is the safety gate that advances `latest`, so it should be covered directly.
Goal: Keep the promotion contract explicit while the workflow evolves.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ci_tools.common import CiToolError
from ci_tools.promote_stable import main


def _env() -> dict[str, str]:
    return {
        "GITHUB_REPOSITORY_OWNER": "Danathar",
        "REGISTRY_ACTOR": "actor",
        "REGISTRY_TOKEN": "token",
        "FEDORA_VERSION": "43",
        "IMAGE_NAME": "zfs-aurora-complex",
        "GITHUB_RUN_NUMBER": "12",
        "GITHUB_SHA": "deadbeefcafefeed",
    }


class PromoteStableTests(unittest.TestCase):
    def test_promotes_candidate_tag_to_latest_and_audit(self) -> None:
        with patch.dict(os.environ, _env(), clear=True):
            with patch("ci_tools.promote_stable.skopeo_inspect_digest", return_value="sha256:abc") as digest_lookup:
                with patch("ci_tools.promote_stable.skopeo_copy") as skopeo_copy:
                    main()

            digest_lookup.assert_called_once_with(
                "docker://ghcr.io/danathar/zfs-aurora-complex:candidate-deadbee-43",
                creds="actor:token",
            )
            self.assertEqual(skopeo_copy.call_count, 2)
            self.assertEqual(
                skopeo_copy.call_args_list[0].args[:2],
                (
                    "docker://ghcr.io/danathar/zfs-aurora-complex@sha256:abc",
                    "docker://ghcr.io/danathar/zfs-aurora-complex:latest",
                ),
            )
            self.assertEqual(
                skopeo_copy.call_args_list[1].args[:2],
                (
                    "docker://ghcr.io/danathar/zfs-aurora-complex@sha256:abc",
                    "docker://ghcr.io/danathar/zfs-aurora-complex:stable-12-deadbee",
                ),
            )

    def test_fails_before_copy_when_candidate_digest_lookup_fails(self) -> None:
        def fail_digest_lookup(image_ref: str, *, creds: str) -> str:
            del creds
            raise CiToolError(f"Missing digest in skopeo inspect output for {image_ref}")

        with patch.dict(os.environ, _env(), clear=True):
            with patch(
                "ci_tools.promote_stable.skopeo_inspect_digest",
                side_effect=fail_digest_lookup,
            ):
                with patch("ci_tools.promote_stable.skopeo_copy") as skopeo_copy:
                    with self.assertRaises(CiToolError) as context:
                        main()

        self.assertIn("candidate-deadbee-43", str(context.exception))
        skopeo_copy.assert_not_called()

    def test_fails_when_latest_copy_fails_before_audit_copy(self) -> None:
        with patch.dict(os.environ, _env(), clear=True):
            with patch("ci_tools.promote_stable.skopeo_inspect_digest", return_value="sha256:abc"):
                with patch(
                    "ci_tools.promote_stable.skopeo_copy",
                    side_effect=CiToolError("copy latest failed"),
                ) as skopeo_copy:
                    with self.assertRaises(CiToolError) as context:
                        main()

        self.assertIn("copy latest failed", str(context.exception))
        self.assertEqual(skopeo_copy.call_count, 1)
        self.assertEqual(
            skopeo_copy.call_args.args[:2],
            (
                "docker://ghcr.io/danathar/zfs-aurora-complex@sha256:abc",
                "docker://ghcr.io/danathar/zfs-aurora-complex:latest",
            ),
        )

    def test_fails_when_audit_copy_fails_after_latest_copy(self) -> None:
        with patch.dict(os.environ, _env(), clear=True):
            with patch("ci_tools.promote_stable.skopeo_inspect_digest", return_value="sha256:abc"):
                with patch(
                    "ci_tools.promote_stable.skopeo_copy",
                    side_effect=[None, CiToolError("copy audit failed")],
                ) as skopeo_copy:
                    with self.assertRaises(CiToolError) as context:
                        main()

        self.assertIn("copy audit failed", str(context.exception))
        self.assertEqual(skopeo_copy.call_count, 2)
        self.assertEqual(
            skopeo_copy.call_args_list[1].args[:2],
            (
                "docker://ghcr.io/danathar/zfs-aurora-complex@sha256:abc",
                "docker://ghcr.io/danathar/zfs-aurora-complex:stable-12-deadbee",
            ),
        )


if __name__ == "__main__":
    unittest.main()
