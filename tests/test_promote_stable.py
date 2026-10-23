"""
Script: tests/test_promote_stable.py
What: Tests for candidate-to-stable promotion in the single-repository flow.
Doing: Verifies the candidate tag naming rule, copy order, digest-preserving flags, and the
refusal to move `latest` backwards.
Why: Promotion is the safety gate that advances `latest`, so it should be covered directly.
Goal: Keep the promotion contract explicit while the workflow evolves.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ci_tools.common import CiToolError
from ci_tools.promote_stable import list_repository_tags, main, refuse_older_than_published


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
    def setUp(self) -> None:
        # Run 12 is promoting; the newest published audit tag is from run 11.
        tags = patch(
            "ci_tools.promote_stable.list_repository_tags",
            return_value=["latest", "stable-11-c0ffee1", "candidate-deadbee-43"],
        )
        self.list_tags = tags.start()
        self.addCleanup(tags.stop)

    def test_promotes_audit_tag_before_latest_with_digest_preserving_flags(self) -> None:
        with patch.dict(os.environ, _env(), clear=True):
            with patch(
                "ci_tools.promote_stable.skopeo_inspect_digest",
                return_value="sha256:abc",
            ) as digest_lookup, patch(
                "ci_tools.promote_stable.skopeo_copy"
            ) as skopeo_copy, patch("ci_tools.promote_stable.run_cmd"):
                main()

            digest_lookup.assert_any_call(
                "docker://ghcr.io/danathar/zfs-aurora-complex:candidate-deadbee-43",
                creds="actor:token",
            )
            self.assertEqual(skopeo_copy.call_count, 2)

            # Audit tag is copied first.
            self.assertEqual(
                skopeo_copy.call_args_list[0].args[:2],
                (
                    "docker://ghcr.io/danathar/zfs-aurora-complex@sha256:abc",
                    "docker://ghcr.io/danathar/zfs-aurora-complex:stable-12-deadbee",
                ),
            )
            # `latest` is copied second.
            self.assertEqual(
                skopeo_copy.call_args_list[1].args[:2],
                (
                    "docker://ghcr.io/danathar/zfs-aurora-complex@sha256:abc",
                    "docker://ghcr.io/danathar/zfs-aurora-complex:latest",
                ),
            )
            for call in skopeo_copy.call_args_list:
                self.assertEqual(call.kwargs.get("creds"), "actor:token")
                self.assertTrue(call.kwargs.get("preserve_digests"))
                self.assertEqual(call.kwargs.get("multi_arch"), "all")

    def test_fails_before_copy_when_candidate_digest_lookup_fails(self) -> None:
        def fail_digest_lookup(image_ref: str, *, creds: str) -> str:
            del creds
            raise CiToolError(f"Missing digest in skopeo inspect output for {image_ref}")

        with (
            patch.dict(os.environ, _env(), clear=True),
            patch(
                "ci_tools.promote_stable.skopeo_inspect_digest",
                side_effect=fail_digest_lookup,
            ),
            patch("ci_tools.promote_stable.skopeo_copy") as skopeo_copy,
            self.assertRaises(CiToolError) as context,
        ):
            main()

        self.assertIn("candidate-deadbee-43", str(context.exception))
        skopeo_copy.assert_not_called()

    def test_fails_when_audit_copy_fails_before_latest_copy(self) -> None:
        with (
            patch.dict(os.environ, _env(), clear=True),
            patch("ci_tools.promote_stable.skopeo_inspect_digest", return_value="sha256:abc"),
            patch("ci_tools.promote_stable.run_cmd"),
            patch(
                "ci_tools.promote_stable.skopeo_copy",
                side_effect=CiToolError("copy audit failed"),
            ) as skopeo_copy,
            self.assertRaises(CiToolError) as context,
        ):
            main()

        self.assertIn("copy audit failed", str(context.exception))
        self.assertEqual(skopeo_copy.call_count, 1)
        self.assertEqual(
            skopeo_copy.call_args.args[:2],
            (
                "docker://ghcr.io/danathar/zfs-aurora-complex@sha256:abc",
                "docker://ghcr.io/danathar/zfs-aurora-complex:stable-12-deadbee",
            ),
        )

    def test_fails_when_latest_copy_fails_after_audit_copy(self) -> None:
        with (
            patch.dict(os.environ, _env(), clear=True),
            patch("ci_tools.promote_stable.skopeo_inspect_digest", return_value="sha256:abc"),
            patch("ci_tools.promote_stable.run_cmd"),
            patch(
                "ci_tools.promote_stable.skopeo_copy",
                side_effect=[None, CiToolError("copy latest failed")],
            ) as skopeo_copy,
            self.assertRaises(CiToolError) as context,
        ):
            main()

        self.assertIn("copy latest failed", str(context.exception))
        self.assertEqual(skopeo_copy.call_count, 2)
        self.assertEqual(
            skopeo_copy.call_args_list[1].args[:2],
            (
                "docker://ghcr.io/danathar/zfs-aurora-complex@sha256:abc",
                "docker://ghcr.io/danathar/zfs-aurora-complex:latest",
            ),
        )

    def test_fails_when_destination_digest_does_not_match_candidate(self) -> None:
        with (
            patch.dict(os.environ, _env(), clear=True),
            patch(
                "ci_tools.promote_stable.skopeo_inspect_digest",
                # First call resolves the candidate digest; second call (the
                # post-audit-copy verification) returns a different digest.
                side_effect=["sha256:abc", "sha256:different"],
            ),
            patch("ci_tools.promote_stable.run_cmd"),
            patch("ci_tools.promote_stable.skopeo_copy") as skopeo_copy,
            self.assertRaises(CiToolError) as context,
        ):
            main()

        self.assertIn("Promoted digest mismatch", str(context.exception))
        self.assertIn("sha256:different", str(context.exception))
        self.assertIn("sha256:abc", str(context.exception))
        skopeo_copy.assert_called_once()

    def test_verifies_candidate_signature_by_digest_before_any_tag_moves(self) -> None:
        with (
            patch.dict(os.environ, _env(), clear=True),
            patch("ci_tools.promote_stable.skopeo_inspect_digest", return_value="sha256:abc"),
            patch("ci_tools.promote_stable.skopeo_copy") as skopeo_copy,
            patch("ci_tools.promote_stable.run_cmd") as run_cmd,
        ):
            main()

        run_cmd.assert_called_once()
        verify_args = run_cmd.call_args.args[0]
        self.assertEqual(verify_args[0], "cosign")
        self.assertEqual(verify_args[1], "verify")
        # Verification must name the immutable digest, never a movable tag.
        self.assertEqual(
            verify_args[-1], "ghcr.io/danathar/zfs-aurora-complex@sha256:abc"
        )
        self.assertIn("--new-bundle-format=false", verify_args)
        self.assertEqual(skopeo_copy.call_count, 2)

    def test_unsigned_candidate_is_never_promoted(self) -> None:
        # If the candidate digest cannot be verified against the committed
        # public key, `latest` and the audit tag must both stay where they are.
        with (
            patch.dict(os.environ, _env(), clear=True),
            patch("ci_tools.promote_stable.skopeo_inspect_digest", return_value="sha256:abc"),
            patch("ci_tools.promote_stable.skopeo_copy") as skopeo_copy,
            patch(
                "ci_tools.promote_stable.run_cmd",
                side_effect=CiToolError("no matching signatures"),
            ),
            self.assertRaises(CiToolError) as context,
        ):
            main()

        self.assertIn("no matching signatures", str(context.exception))
        skopeo_copy.assert_not_called()

    def test_lists_tags_of_the_image_repository_with_credentials(self) -> None:
        with (
            patch.dict(os.environ, _env(), clear=True),
            patch("ci_tools.promote_stable.skopeo_inspect_digest", return_value="sha256:abc"),
            patch("ci_tools.promote_stable.skopeo_copy"),
            patch("ci_tools.promote_stable.run_cmd"),
        ):
            main()

        self.list_tags.assert_called_once_with(
            "docker://ghcr.io/danathar/zfs-aurora-complex", creds="actor:token"
        )

    def test_rerun_of_older_build_never_moves_any_tag(self) -> None:
        # Run 12 re-run after run 13 promoted: `latest` would move backwards.
        self.list_tags.return_value = ["latest", "stable-11-c0ffee1", "stable-13-feedfac"]
        with (
            patch.dict(os.environ, _env(), clear=True),
            patch("ci_tools.promote_stable.skopeo_inspect_digest", return_value="sha256:abc"),
            patch("ci_tools.promote_stable.skopeo_copy") as skopeo_copy,
            patch("ci_tools.promote_stable.run_cmd"),
            self.assertRaises(CiToolError) as context,
        ):
            main()

        self.assertIn("run 13", str(context.exception))
        skopeo_copy.assert_not_called()

    def test_tag_listing_failure_never_moves_any_tag(self) -> None:
        self.list_tags.side_effect = CiToolError("toomanyrequests")
        with (
            patch.dict(os.environ, _env(), clear=True),
            patch("ci_tools.promote_stable.skopeo_inspect_digest", return_value="sha256:abc"),
            patch("ci_tools.promote_stable.skopeo_copy") as skopeo_copy,
            patch("ci_tools.promote_stable.run_cmd"),
            self.assertRaises(CiToolError),
        ):
            main()

        skopeo_copy.assert_not_called()

    def test_non_numeric_run_number_is_refused(self) -> None:
        env = _env() | {"GITHUB_RUN_NUMBER": "12a"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch("ci_tools.promote_stable.skopeo_inspect_digest", return_value="sha256:abc"),
            patch("ci_tools.promote_stable.skopeo_copy") as skopeo_copy,
            patch("ci_tools.promote_stable.run_cmd"),
            self.assertRaises(CiToolError) as context,
        ):
            main()

        self.assertIn("not a run number", str(context.exception))
        skopeo_copy.assert_not_called()


# Two consecutive audit tags in the shape `main()` writes.
PUBLISHED = ["stable-220-e6f14f7", "stable-221-c6d1dee"]


class RefuseOlderThanPublishedTests(unittest.TestCase):
    def test_allows_first_promotion_with_no_audit_tags(self) -> None:
        refuse_older_than_published(run_number="1", tags=["latest", "candidate-deadbee-43"])

    def test_allows_newer_run(self) -> None:
        refuse_older_than_published(run_number="222", tags=PUBLISHED)

    def test_allows_rerun_of_the_newest_promoted_run(self) -> None:
        # A promotion that failed between the audit copy and the `latest` copy,
        # re-run before any newer build promoted, must still finish.
        refuse_older_than_published(run_number="221", tags=PUBLISHED)

    def test_refuses_older_run(self) -> None:
        with self.assertRaises(CiToolError) as context:
            refuse_older_than_published(run_number="220", tags=PUBLISHED)
        self.assertIn("Refusing to promote run 220", str(context.exception))

    def test_compares_run_numbers_numerically_not_as_text(self) -> None:
        with self.assertRaises(CiToolError):
            refuse_older_than_published(run_number="99", tags=["stable-100-e6f14f7"])

    def test_ignores_tags_outside_the_audit_family(self) -> None:
        refuse_older_than_published(
            run_number="5", tags=["stable-999", "stable-999-NOTHEX", "br-stable-999-abc", "999"]
        )


class ListRepositoryTagsTests(unittest.TestCase):
    def test_passes_credentials_as_authfile_and_returns_tags(self) -> None:
        with patch(
            "ci_tools.promote_stable.run_json_cmd",
            return_value={"Repository": "ghcr.io/o/i", "Tags": ["latest", "stable-1-abc"]},
        ) as run_json_cmd:
            tags = list_repository_tags("docker://ghcr.io/o/i", creds="actor:token")

        self.assertEqual(tags, ["latest", "stable-1-abc"])
        command = run_json_cmd.call_args.args[0]
        self.assertEqual(command[:2], ["skopeo", "list-tags"])
        self.assertIn("--authfile", command)
        self.assertEqual(command[-1], "docker://ghcr.io/o/i")
        self.assertNotIn("actor:token", " ".join(command))

    def test_missing_tag_list_fails_closed(self) -> None:
        with (
            patch("ci_tools.promote_stable.run_json_cmd", return_value={"Repository": "x"}),
            self.assertRaises(CiToolError),
        ):
            list_repository_tags("docker://ghcr.io/o/i", creds=None)


if __name__ == "__main__":
    unittest.main()
