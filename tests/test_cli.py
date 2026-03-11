"""
Script: tests/test_cli.py
What: Tests for the shared `ci_tools` command dispatcher.
Doing: Checks command-map entries, parser behavior, and command-run paths.
Why: Makes sure workflow command names still point to the right modules.
Goal: Protect the main command entry surface used by workflow steps.
"""

from __future__ import annotations

import unittest

from ci_tools.cli import build_parser, command_map, run_command


class CliTests(unittest.TestCase):
    def test_command_map_contains_expected_entries(self) -> None:
        commands = command_map()
        expected = {
            "resolve-build-inputs",
            "write-build-inputs-manifest",
            "check-akmods-cache",
            "prepare-validation-build",
            "compute-branch-metadata",
            "promote-stable",
            "sign-image",
            "akmods-clone-pinned",
            "akmods-configure-zfs-target",
            "akmods-build-and-publish",
        }
        self.assertTrue(expected.issubset(set(commands.keys())))

    def test_parser_accepts_known_command(self) -> None:
        parser = build_parser({"demo-command": lambda: None})
        args = parser.parse_args(["demo-command"])
        self.assertEqual(args.command, "demo-command")

    def test_run_command_calls_target_function(self) -> None:
        called = {"value": False}

        def _target() -> None:
            called["value"] = True

        run_command("demo", {"demo": _target})
        self.assertTrue(called["value"])


if __name__ == "__main__":
    unittest.main()
