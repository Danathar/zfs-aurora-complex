"""
Script: tests/test_workflow_openzfs_lookup_token.py
What: Checks that every workflow or composite-action step that runs a CLI command
reaching the OpenZFS release lookup (ci_tools/zfs_release.py) gives that step a
GITHUB_TOKEN.
Doing: Reads the workflow and action files as text, finds each step whose `run:`
calls one of those commands, and asserts the same step sets `GITHUB_TOKEN:` in its env.
Why: Without a token the lookup is unauthenticated, and api.github.com allows 60 such
calls per hour per IP. Hosted runners share IPs, so the build can fail with
"HTTP Error 403: rate limit exceeded" for reasons unrelated to the change being built.
Goal: Make a new caller, or a caller that loses its token, fail here instead of as a
red build on an unrelated pull request.

Plain text matching, not PyYAML, for the same reason as test_workflow_build_container.py:
the CI test job installs only pytest and ruff.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GITHUB_DIR = REPO_ROOT / ".github"

# CLI commands whose module imports ci_tools.zfs_release and can call the API.
LOOKUP_COMMANDS = (
    "resolve-build-inputs",
    "prepare-validation-build",
    "check-stable-signal",
)

COMMAND_RE = re.compile(r"ci_tools\.cli\s+(?P<command>[a-z0-9-]+)")
STEP_START_RE = re.compile(r"^(?P<indent>\s*)- ")


def _step_containing(lines: list[str], index: int) -> list[str]:
    """Return the lines of the step that contains lines[index]."""
    start = index
    while start > 0 and not STEP_START_RE.match(lines[start]):
        start -= 1
    indent = len(STEP_START_RE.match(lines[start]).group("indent"))
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        end += 1
    return lines[start:end]


class OpenZfsLookupTokenTests(unittest.TestCase):
    def test_lookup_commands_still_exist(self) -> None:
        cli = (REPO_ROOT / "ci_tools" / "cli.py").read_text(encoding="utf-8")
        for command in LOOKUP_COMMANDS:
            self.assertIn(
                f'"{command}"', cli, f"{command} is gone from ci_tools/cli.py; update this test."
            )

    def test_every_lookup_step_sets_github_token(self) -> None:
        files = sorted(GITHUB_DIR.glob("workflows/*.yml")) + sorted(
            GITHUB_DIR.glob("actions/*/action.yml")
        )
        callers = 0
        for path in files:
            lines = path.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines):
                match = COMMAND_RE.search(line)
                if not match or match.group("command") not in LOOKUP_COMMANDS:
                    continue
                callers += 1
                step = _step_containing(lines, index)
                self.assertTrue(
                    any(re.match(r"\s*GITHUB_TOKEN:\s*\S", s) for s in step),
                    f"{path.relative_to(REPO_ROOT)}:{index + 1} runs {match.group('command')}, "
                    "which looks up OpenZFS releases on api.github.com, but its step sets no "
                    "GITHUB_TOKEN. Unauthenticated calls share a 60/hour per-IP limit.",
                )
        self.assertGreaterEqual(
            callers, len(LOOKUP_COMMANDS), "found fewer lookup callers than expected"
        )


if __name__ == "__main__":
    unittest.main()
