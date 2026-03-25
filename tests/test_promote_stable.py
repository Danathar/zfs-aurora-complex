"""
Script: tests/test_promote_stable.py
What: Tests for candidate-to-stable promotion in the single-repository flow.
Doing: Verifies the candidate tag naming rule and the exact copy destinations.
Why: Promotion is the safety gate that advances `latest`, so it should be covered directly.
Goal: Keep the promotion contract explicit while the workflow evolves.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from ci_tools.promote_stable import main


class PromoteStableTests(unittest.TestCase):
    def test_promotes_candidate_tag_to_latest_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            del temp_dir
            with patch.dict(
                os.environ,
                {
                    "GITHUB_REPOSITORY_OWNER": "Danathar",
                    "REGISTRY_ACTOR": "actor",
                    "REGISTRY_TOKEN": "token",
                    "FEDORA_VERSION": "43",
                    "IMAGE_NAME": "zfs-aurora-containerfile",
                    "GITHUB_RUN_NUMBER": "12",
                    "GITHUB_SHA": "deadbeefcafefeed",
                },
                clear=False,
            ):
                with patch("ci_tools.promote_stable.skopeo_inspect_digest", return_value="sha256:abc") as digest_lookup:
                    with patch("ci_tools.promote_stable.skopeo_copy") as skopeo_copy:
                        main()

            digest_lookup.assert_called_once_with(
                "docker://ghcr.io/danathar/zfs-aurora-containerfile:candidate-deadbee-43",
                creds="actor:token",
            )
            self.assertEqual(skopeo_copy.call_count, 2)
            self.assertEqual(
                skopeo_copy.call_args_list[0].args[:2],
                (
                    "docker://ghcr.io/danathar/zfs-aurora-containerfile@sha256:abc",
                    "docker://ghcr.io/danathar/zfs-aurora-containerfile:latest",
                ),
            )
            self.assertEqual(
                skopeo_copy.call_args_list[1].args[:2],
                (
                    "docker://ghcr.io/danathar/zfs-aurora-containerfile@sha256:abc",
                    "docker://ghcr.io/danathar/zfs-aurora-containerfile:stable-12-deadbee",
                ),
            )


if __name__ == "__main__":
    unittest.main()
