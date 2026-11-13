"""
Script: tests/test_claude_settings_git_env.py
What: Checks the `env` block in `.claude/settings.json` and that it stops a committed bare repository from running a program.
Doing: Plants a bare repository as ordinary files in a clone, runs the pre-approved `git show` and `git log -p` inside it with and without the settings `env`, and asserts the program runs only without it.
Why: `.claude/settings.json` allows `git log` and `git show` with no prompt, and a branch can carry a bare repository whose config names a diff program; the guard hook never sees that config.
Goal: Make a removed or edited `env` block fail here, rather than the first time a hostile branch is checked out and its history is read.

Git treats a directory holding `HEAD`, `objects/` and `refs/` as a bare repository
wherever it sits. A branch can commit one as ordinary files (`evil/HEAD`,
`evil/config`, `evil/info/attributes`, ...). When the working directory is inside it
(`git -C evil show`, or `cd evil` and then `git show`), git uses it instead of the
checkout's `.git/`, and its `info/attributes` (`* diff=x`) plus config
(`diff.x.textconv`) name a program git runs to render the diff. The `env` block sets
`safe.bareRepository=explicit` for every command an agent runs, so git refuses such a
directory unless it is named with `--git-dir`. The checkout's own `.git/` is not bare
and is unaffected.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS = REPO_ROOT / ".claude" / "settings.json"

EXPECTED_ENV = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "safe.bareRepository",
    "GIT_CONFIG_VALUE_0": "explicit",
}


def _base_env(home: Path) -> dict[str, str]:
    """A clean git environment: no user or system config, no inherited GIT_* overrides."""
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }


def _git(
    cwd: Path, env: dict[str, str], *args: str, input: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=False, input=input
    )


def _git_ok(cwd: Path, env: dict[str, str], *args: str) -> None:
    result = _git(cwd, env, *args)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")


class SettingsEnvTests(unittest.TestCase):
    """The `env` block exists, is exactly what the protection needs, and works."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.settings_env = json.loads(SETTINGS.read_text(encoding="utf-8")).get("env", {})

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="bare-repo-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.marker = self.dir / "ran"
        self.victim = self._planted_clone()

    def _planted_clone(self) -> Path:
        """A clone whose tree carries a committed bare repository `evil/` with a textconv."""
        env = _base_env(self.dir)
        ident = ["-c", "user.name=t", "-c", "user.email=t@t"]
        src = self.dir / "src"
        evil = src / "evil"
        _git_ok(self.dir, env, "init", "-q", str(src))
        _git_ok(src, env, "init", "-q", "--bare", "--initial-branch=main", "evil")
        # Give the bare repository one commit so `git show` and `git log -p` have a diff
        # to render (plumbing only, so no work tree is needed), then name the program
        # that renders it.
        blob = _git(evil, env, "hash-object", "-w", "--stdin", input="hello\n").stdout.strip()
        tree = _git(evil, env, "mktree", input=f"100644 blob {blob}\tf.txt\n").stdout.strip()
        commit = _git(evil, env, *ident, "commit-tree", tree, "-m", "planted").stdout.strip()
        _git_ok(evil, env, "update-ref", "refs/heads/main", commit)
        (evil / "info").mkdir(exist_ok=True)
        (evil / "info" / "attributes").write_text("* diff=x\n", encoding="utf-8")
        _git_ok(
            evil,
            env,
            "config",
            "diff.x.textconv",
            f"touch '{self.marker}'; cat",
        )
        _git_ok(src, env, "add", "evil")
        _git_ok(src, env, *ident, "commit", "-qm", "x")
        _git_ok(self.dir, env, "clone", "-q", str(src), "victim")
        return self.dir / "victim"

    def test_settings_set_safe_bare_repository_explicit_through_git_config_env(self) -> None:
        self.assertEqual(self.settings_env, EXPECTED_ENV)

    def test_planted_program_runs_without_the_setting(self) -> None:
        # Guard the guard: if this does not run, the refusal checks below prove nothing.
        for cwd, args in self._routes():
            with self.subTest(cwd=cwd.name, args=args):
                self.marker.unlink(missing_ok=True)
                _git(cwd, _base_env(self.dir), *args)
                self.assertTrue(
                    self.marker.exists(),
                    f"git {' '.join(args)} did not run the planted textconv program",
                )

    def test_planted_program_is_refused_with_the_setting(self) -> None:
        agent_env = {**_base_env(self.dir), **self.settings_env}
        for cwd, args in self._routes():
            with self.subTest(cwd=cwd.name, args=args):
                self.marker.unlink(missing_ok=True)
                result = _git(cwd, agent_env, *args)
                self.assertNotEqual(result.returncode, 0, "git should refuse the bare repository")
                self.assertIn("safe.bareRepository", result.stderr)
                self.assertFalse(self.marker.exists(), "the planted program ran")

    def test_checkouts_own_repository_still_works_with_the_setting(self) -> None:
        agent_env = {**_base_env(self.dir), **self.settings_env}
        self.assertEqual(_git(self.victim, agent_env, "status", "--short").returncode, 0)
        log = _git(self.victim, agent_env, "log", "-1", "--format=%s")
        self.assertEqual(log.returncode, 0)
        self.assertEqual(log.stdout.strip(), "x")

    def _routes(self) -> list[tuple[Path, list[str]]]:
        """The pre-approved commands that reach the planted repository, and where from."""
        evil = self.victim / "evil"
        return [
            (self.victim, ["-C", "evil", "show"]),
            (self.victim, ["-C", "evil", "log", "-p", "-1"]),
            (evil, ["show"]),
            (evil, ["log", "-p", "-1"]),
        ]


if __name__ == "__main__":
    unittest.main()
