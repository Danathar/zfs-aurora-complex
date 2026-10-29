"""
Script: tests/test_cli.py
What: Tests for the shared `ci_tools` command dispatcher.
Doing: Checks command-map entries, parser behavior, command-run paths, and how `main()` turns a
command's failure into the step's exit status.
Why: Makes sure workflow command names still point to the right modules.
Goal: Protect the main command entry surface used by workflow steps.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from ci_tools import cli
from ci_tools.cli import build_parser, command_map, run_command
from ci_tools.common import CiToolError


class CliTests(unittest.TestCase):
    def test_command_map_contains_expected_entries(self) -> None:
        commands = command_map()
        expected = {
            "resolve-build-inputs",
            "write-build-inputs-manifest",
            "check-akmods-cache",
            "check-stable-signal",
            "compute-candidate-tag",
            "export-repo-defaults",
            "export-registry-context",
            "prepare-validation-build",
            "pin-akmods-cache",
            "promote-stable",
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

    def test_main_runs_the_named_command(self) -> None:
        called: list[str] = []
        commands = {"demo": lambda: called.append("demo"), "other": lambda: called.append("other")}
        with mock.patch.object(cli, "command_map", return_value=commands):
            cli.main(["demo"])
        self.assertEqual(called, ["demo"])

    def test_main_turns_a_tool_error_into_exit_status_one(self) -> None:
        """
        A workflow step fails on the exit status, and the reader of the job log
        needs the tool's message, not a traceback. `main()` is the only place
        that turns a CiToolError into both.
        """

        def _fails() -> None:
            raise CiToolError("akmods cache image is missing kernel 6.99")

        with (
            mock.patch.object(cli, "command_map", return_value={"demo": _fails}),
            contextlib.redirect_stderr(io.StringIO()) as err,
            self.assertRaises(SystemExit) as raised,
        ):
            cli.main(["demo"])
        self.assertEqual(raised.exception.code, 1)
        self.assertIsInstance(raised.exception.__cause__, CiToolError)
        self.assertEqual(err.getvalue(), "akmods cache image is missing kernel 6.99\n")

    def test_main_lets_other_errors_through(self) -> None:
        """Only CiToolError is a reported failure; anything else is a bug and keeps its traceback."""

        def _breaks() -> None:
            raise KeyError("IMAGE_NAME")

        with (
            mock.patch.object(cli, "command_map", return_value={"demo": _breaks}),
            self.assertRaises(KeyError),
        ):
            cli.main(["demo"])

    def test_main_refuses_an_unknown_command(self) -> None:
        called: list[str] = []
        with (
            mock.patch.object(cli, "command_map", return_value={"demo": lambda: called.append("x")}),
            contextlib.redirect_stderr(io.StringIO()) as err,
            self.assertRaises(SystemExit) as raised,
        ):
            cli.main(["dmeo"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("invalid choice: 'dmeo'", err.getvalue())
        self.assertEqual(called, [])

    def test_main_refuses_arguments_after_the_command(self) -> None:
        """No command here takes options, so a stray one is an error, not silently dropped."""

        called: list[str] = []
        with (
            mock.patch.object(cli, "command_map", return_value={"demo": lambda: called.append("x")}),
            contextlib.redirect_stderr(io.StringIO()) as err,
            self.assertRaises(SystemExit) as raised,
        ):
            cli.main(["demo", "--fleet"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("unrecognized arguments: --fleet", err.getvalue())
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
