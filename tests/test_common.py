"""
Script: tests/test_common.py
What: Direct tests for shared CI helper behavior.
Doing: Mocks command wrappers and inspect helpers.
Why: Several workflow helpers rely on these common skopeo contracts.
Goal: Keep low-level image helper failure behavior clear.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from ci_tools.common import CiToolError, skopeo_exists, skopeo_inspect_digest


class CommonTests(unittest.TestCase):
    def test_skopeo_inspect_digest_requires_digest_field(self) -> None:
        with patch(
            "ci_tools.common.skopeo_inspect_json",
            return_value={"Name": "example"},
        ):
            with self.assertRaises(CiToolError) as context:
                skopeo_inspect_digest("docker://ghcr.io/example/image:tag")

        self.assertIn("docker://ghcr.io/example/image:tag", str(context.exception))

    def test_skopeo_exists_returns_true_when_inspect_succeeds(self) -> None:
        with patch("ci_tools.common.run_cmd", return_value="") as run_cmd:
            self.assertTrue(
                skopeo_exists("docker://ghcr.io/example/image:tag", creds="actor:token")
            )

        run_cmd.assert_called_once_with(
            [
                "skopeo",
                "inspect",
                "--creds",
                "actor:token",
                "docker://ghcr.io/example/image:tag",
            ]
        )

    def test_skopeo_exists_returns_false_when_inspect_fails(self) -> None:
        with patch("ci_tools.common.run_cmd", side_effect=CiToolError("missing image")):
            self.assertFalse(skopeo_exists("docker://ghcr.io/example/image:tag"))


if __name__ == "__main__":
    unittest.main()
