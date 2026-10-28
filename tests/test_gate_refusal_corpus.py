"""Run the fleet's shared refusal corpus against this repository's Bash gate.

Six repositories each carry their own copy of the PreToolUse hook that keeps
allow-listed commands such as `git diff` and `gh pr view` from reading `.env`
or writing a file. The copies are separate code, so a bypass fixed in one
repository says nothing about the other five
(Danathar/atomic-image-builder#609).
`tests/fixtures/gate-refusal-corpus.json` is the one table they share: each
row is a command, the verdict every gate has to reach, and the command
prefixes the row depends on. This repository runs the rows its own allow list
makes reachable, so a newly found bypass is one new row, and every repository
that allows the command fails until its gate refuses it.

A row with a `wraps` field puts a wrapper no allow rule covers (`flock`,
`watch`, `find -exec`) in front of the command it requires. It runs wherever
that inner command is allowed, because every gate refuses the protected action
behind any wrapper, whether or not Claude Code would prompt first: bypass and
auto permission modes prompt for nothing.

The canonical copy lives in Danathar/atomic-image-builder, whose
docs/gate-refusal-corpus.md documents the row format. The copy here is pinned
by SHA-256: change a row there, then copy the file here and update the pin.

The hook is run the way Claude Code runs it, through the command
`.claude/settings.json` registers, so a registration that stops pointing at
the gate fails here as well.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/fixtures/gate-refusal-corpus.json"
SETTINGS = ROOT / ".claude/settings.json"

REFUSE = "refuse"
ALLOW = "allow"
ROW_FIELDS = {"id", "class", "verdict", "requires", "command", "why"}
OPTIONAL_FIELDS = {"wraps"}

# SHA-256 of the canonical tests/fixtures/gate-refusal-corpus.json in
# Danathar/atomic-image-builder. Copy the file from there; never edit it here.
CANONICAL_SHA256 = "4478c969cb74cadb73fddf71d56cb90b0b1c986a20efae5adf8720f134caf565"


def allow_prefixes(settings: dict) -> list[str]:
    """The command prefix each wildcard `Bash(...)` allow rule covers.

    The fleet spells a prefix rule three ways (`git diff:*`, `git diff *`,
    `git diff*`); all three cover `git diff` and what follows it. A rule with
    no wildcard allows one exact command and covers no prefix.
    """
    prefixes = []
    for rule in settings.get("permissions", {}).get("allow", []):
        if not (rule.startswith("Bash(") and rule.endswith(")")):
            continue
        body = rule[len("Bash(") : -1]
        for suffix in (":*", " *", "*"):
            if body.endswith(suffix):
                prefixes.append(body[: -len(suffix)])
                break
    return prefixes


def covered(prefix: str, prefixes: list[str]) -> bool:
    return any(prefix == rule or prefix.startswith(rule + " ") for rule in prefixes)


def applies(row: dict, prefixes: list[str]) -> bool:
    return all(covered(prefix, prefixes) for prefix in row["requires"])


# The one-letter escapes bash decodes inside `$'...'`.
ANSI_C_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "?": "?",
}
# `\xHH`, `\uHHHH` and `\UHHHHHHHH`: the most hex digits each one reads.
ANSI_C_HEX = {"x": 2, "u": 4, "U": 8}
GLOB_CHARS = re.compile(r"[*?[]")
# An innermost extglob group, `@(...)`, `?(...)`, `*(...)`, `+(...)` or
# `!(...)`, which matches no more than `*` does.
EXTGLOB_GROUP = re.compile(r"[@?*+!]\([^()]*\)")
MAX_BRACE_WORDS = 32


def ansi_c(text: str, start: int) -> tuple[str, int]:
    """Decode the `$'...'` body that begins at `start`; return it and the index past its quote.

    `\'` does not end the string, as it does not in bash. A backslash
    before anything bash does not decode is kept.
    """
    out, i = [], start
    while i < len(text) and text[i] != "'":
        if text[i] != "\\" or i + 1 == len(text):
            out.append(text[i])
            i += 1
            continue
        escape = text[i + 1]
        if escape in ANSI_C_ESCAPES:
            out.append(ANSI_C_ESCAPES[escape])
            i += 2
        elif escape in ANSI_C_HEX and (
            digits := re.match(rf"[0-9a-fA-F]{{1,{ANSI_C_HEX[escape]}}}", text[i + 2 :])
        ):
            out.append(chr(int(digits.group(), 16)))
            i += 2 + len(digits.group())
        elif digits := re.match(r"[0-7]{1,3}", text[i + 1 :]):
            out.append(chr(int(digits.group(), 8) & 0xFF))
            i += 1 + len(digits.group())
        else:
            out.append("\\")
            i += 1
    return "".join(out), i + 1


def shell_words(text: str) -> list[tuple[str, bool]]:
    """Split `text` into words the way bash reads them, before expansion.

    Each word comes back with its quotes and backslashes removed, `$'...'`
    decoded and `$"..."` read as `"..."`, plus whether any of it was quoted.
    An unterminated quote runs to the end of the text. A backslash-newline
    outside single quotes is dropped, as bash drops it before it reads a
    word, so `gi\\<newline>t` is `git`.
    """
    words: list[tuple[str, bool]] = []
    word, quoted, started, i = "", False, False, 0
    while i < len(text):
        char = text[i]
        if char.isspace():
            if started:
                words.append((word, quoted))
            word, quoted, started = "", False, False
            i += 1
            continue
        if text.startswith("\\\n", i):
            i += 2
            continue
        started = True
        if text.startswith("$'", i):
            body, i = ansi_c(text, i + 2)
            word, quoted = word + body, True
        elif text.startswith('$"', i):
            i += 1
        elif char == "'":
            close = text.find("'", i + 1)
            close = len(text) if close < 0 else close
            word, quoted, i = word + text[i + 1 : close], True, close + 1
        elif char == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                if text.startswith("\\\n", i):
                    i += 2
                    continue
                if text[i] == "\\" and text[i + 1 : i + 2] in ('"', "\\", "$", "`"):
                    i += 1
                word += text[i : i + 1]
                i += 1
            quoted, i = True, i + 1
        elif char == "\\":
            word, quoted, i = word + text[i + 1 : i + 2], True, i + 2
        else:
            word, i = word + char, i + 1
    if started:
        words.append((word, quoted))
    return words


def brace_words(word: str) -> list[str]:
    """The words bash expands an unquoted brace group into, dropping empty ones."""
    group = re.search(r"\{([^{}]*,[^{}]*)\}", word)
    if not group:
        return [word] if word else []
    expanded = [
        expanded_word
        for alternative in group.group(1).split(",")
        for expanded_word in brace_words(word[: group.start()] + alternative + word[group.end() :])
    ]
    return expanded[:MAX_BRACE_WORDS]


def name_patterns(text: str) -> list[str]:
    """The words of `text` as patterns a command name is matched against.

    A word is read the way the shell reads a command name: unquoted brace
    groups are expanded, and an unquoted wildcard stands for any file it can
    match, so `/usr/bin/g?t` is the pattern `g?t`, and so is a bare `g?t`,
    which bash expands to `git` beside a file of that name and then looks
    up on PATH. An extglob group (`/usr/bin/g@(i)t`, under `shopt -s
    extglob`) is read as the `*` it can match no more than. Every other
    word matches only itself.
    """
    patterns = []
    for word, quoted in shell_words(text):
        if quoted:
            patterns.append(glob.escape(word))
            continue
        while EXTGLOB_GROUP.search(word):
            word = EXTGLOB_GROUP.sub("*", word)
        for expanded in brace_words(word):
            if GLOB_CHARS.search(expanded):
                patterns.append(PurePosixPath(expanded).name)
            else:
                patterns.append(glob.escape(expanded))
    return patterns


def inner_words(row: dict) -> list[str]:
    """The words a row's command is checked against for what it requires.

    A row without `wraps` is read the way the shell reads its command line,
    so `{git,} diff`, `/usr/bin/g?t diff`, `$'git' diff` and `$'\\x67it' diff`
    all name `git diff`, as they all run it. A quoted string stays one word,
    so `env -S 'git diff ...'` names nothing without `wraps`. A wrapped row is
    read the way the shell hands it to the wrapper, and each of those words
    is read again as a command line, so `env -S 'git diff ...'` names
    `git diff` as well, and so do `script -qc 'g""it diff ...'` and
    `script -qc "$'git' diff ..."`, as the shell that runs the string joins
    the name back together.

    Each word is a pattern (see `name_patterns`); `names()` matches a
    required prefix against them.
    """
    if "wraps" not in row:
        return name_patterns(row["command"])
    return [pattern for word, _ in shell_words(row["command"]) for pattern in name_patterns(word)]


def names(words: list[str], prefix: str) -> bool:
    """Whether consecutive `words` (from `inner_words`) spell out `prefix`."""
    wanted = prefix.split()
    return any(
        all(fnmatchcase(name, pattern) for name, pattern in zip(wanted, words[i : i + len(wanted)]))
        for i in range(len(words) - len(wanted) + 1)
    )


def hook_command(settings: dict) -> str:
    for entry in settings["hooks"]["PreToolUse"]:
        if entry.get("matcher") == "Bash":
            return entry["hooks"][0]["command"]
    raise AssertionError(".claude/settings.json registers no PreToolUse hook for Bash")


def scratch_project(directory: Path) -> Path:
    """Copy .claude/ into a fresh repository with two commits.

    CI checks this repository out at depth 1, where `HEAD~1` names no commit,
    so the gate reads `git diff HEAD~1 HEAD` as two plain-file operands and
    refuses it -- correctly for that checkout, but it fails the
    allow-diff-range row, whose verdict assumes the history an agent's working
    clone has. The rows run here instead.
    """
    shutil.copytree(ROOT / ".claude", directory / ".claude")
    git = [
        "git",
        "-c",
        "user.email=t@example.invalid",
        "-c",
        "user.name=t",
        "-c",
        "commit.gpgsign=false",
    ]
    subprocess.run(["git", "init", "-q", "."], cwd=directory, check=True)
    for message in ("first", "second"):
        subprocess.run(
            [*git, "commit", "-q", "--allow-empty", "-m", message], cwd=directory, check=True
        )
    return directory


def decide(command: str, hook: str, project: Path) -> tuple[str, subprocess.CompletedProcess]:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    result = subprocess.run(
        ["bash", "-c", hook],
        input=payload,
        capture_output=True,
        text=True,
        cwd=project,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(project)},
        check=False,
    )
    refused = result.returncode == 2 or '"deny"' in result.stdout
    return (REFUSE if refused else ALLOW), result


class CorpusShapeTests(unittest.TestCase):
    """The table is read by gates in two languages, so its shape is pinned."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.corpus = json.loads(CORPUS.read_text(encoding="utf-8"))

    def test_the_copy_matches_the_canonical_corpus(self) -> None:
        digest = hashlib.sha256(CORPUS.read_bytes()).hexdigest()
        self.assertEqual(
            digest,
            CANONICAL_SHA256,
            "tests/fixtures/gate-refusal-corpus.json differs from the pinned "
            "canonical copy; change rows in Danathar/atomic-image-builder and "
            "copy the file here",
        )

    def test_the_schema_version_is_one_this_file_reads(self) -> None:
        self.assertEqual(self.corpus["schema"], 1)

    def test_every_row_has_exactly_the_documented_fields(self) -> None:
        for row in self.corpus["rows"]:
            with self.subTest(row=row.get("id")):
                self.assertLessEqual(ROW_FIELDS, set(row))
                self.assertLessEqual(set(row), ROW_FIELDS | OPTIONAL_FIELDS)
                self.assertIn(row["verdict"], (REFUSE, ALLOW))
                self.assertTrue(row["requires"], "a row has to name what it depends on")
                for prefix in row["requires"]:
                    self.assertIsInstance(prefix, str)
                    self.assertEqual(prefix, prefix.strip())
                self.assertTrue(row["command"].strip())
                self.assertTrue(row["why"].strip())

    def test_row_ids_are_unique(self) -> None:
        ids = [row["id"] for row in self.corpus["rows"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_a_row_requires_the_command_it_starts_with(self) -> None:
        # A row whose command is outside every prefix it requires would run
        # in a repository that never allows the command, and fail there for
        # a gate that has nothing to do with it.
        for row in self.corpus["rows"]:
            with self.subTest(row=row["id"]):
                words = inner_words(row)
                self.assertTrue(
                    any(names(words, prefix) for prefix in row["requires"]),
                    f"{row['command']!r} names none of {row['requires']}",
                )

    def test_a_wrapped_row_starts_with_the_wrapper_it_names(self) -> None:
        # `wraps` is what tells a reader the row is not reachable through an
        # allow rule alone. It names the first word, and is never one of the
        # prefixes the row requires, or the row would run only where the
        # wrapper itself is allowed.
        wrapped = [row for row in self.corpus["rows"] if "wraps" in row]
        self.assertTrue(wrapped, "the corpus has no wrapped row for this test to read")
        for row in wrapped:
            with self.subTest(row=row["id"]):
                wrapper = row["wraps"]
                self.assertIsInstance(wrapper, str)
                self.assertTrue(wrapper and wrapper == wrapper.strip() and " " not in wrapper)
                self.assertEqual(shlex.split(row["command"])[0], wrapper)
                self.assertFalse(any(prefix.split()[0] == wrapper for prefix in row["requires"]))

    def test_both_verdicts_are_present(self) -> None:
        # Refusals alone would pass a gate that refuses everything, and a
        # gate that refuses everything gets switched off.
        verdicts = {row["verdict"] for row in self.corpus["rows"]}
        self.assertEqual(verdicts, {REFUSE, ALLOW})


class PrefixMatchTests(unittest.TestCase):
    def test_each_fleet_spelling_of_a_prefix_rule_covers_the_prefix(self) -> None:
        for rule in ("Bash(git diff:*)", "Bash(git diff *)", "Bash(git diff*)"):
            with self.subTest(rule=rule):
                prefixes = allow_prefixes({"permissions": {"allow": [rule]}})
                self.assertTrue(covered("git diff", prefixes))

    def test_an_exact_rule_covers_no_prefix(self) -> None:
        prefixes = allow_prefixes({"permissions": {"allow": ["Bash(ruff check)"]}})
        self.assertFalse(covered("ruff check", prefixes))

    def test_a_prefix_is_covered_only_at_a_word_boundary(self) -> None:
        prefixes = allow_prefixes({"permissions": {"allow": ["Bash(gh pr:*)"]}})
        self.assertTrue(covered("gh pr view", prefixes))
        self.assertFalse(covered("gh prx view", prefixes))
        prefixes = allow_prefixes({"permissions": {"allow": ["Bash(gh pr view:*)"]}})
        self.assertFalse(covered("gh pr", prefixes))


SAMPLE_ROW = {
    "id": "r",
    "class": "c",
    "verdict": REFUSE,
    "requires": ["git diff"],
    "command": "git diff /dev/null ./.env",
    "why": "w",
}


class WrappedRowTests(unittest.TestCase):
    def test_a_wrapped_row_runs_where_its_inner_command_is_allowed(self) -> None:
        # No allow rule covers `flock`; the row still runs wherever `git diff`
        # is allowed, which is the point of the field.
        row = {**SAMPLE_ROW, "wraps": "flock", "command": "flock /tmp/l git diff /dev/null ./.env"}
        self.assertTrue(applies(row, ["git diff"]))
        self.assertFalse(applies(row, ["git status"]))

    def test_a_wrapped_row_names_what_it_requires_inside_a_quoted_word(self) -> None:
        row = {**SAMPLE_ROW, "wraps": "env", "command": "env -S 'git diff /dev/null ./.env'"}
        self.assertEqual(inner_words(row)[2:4], ["git", "diff"])

    def test_a_row_without_wraps_is_read_as_written(self) -> None:
        row = {**SAMPLE_ROW, "command": "env -S 'git diff /dev/null ./.env'"}
        self.assertNotIn("git", inner_words(row))


class NameSpellingTests(unittest.TestCase):
    """A row names what it requires in every spelling the shell runs as that name."""

    def test_a_spelling_the_shell_runs_as_the_name_names_it(self) -> None:
        for command in (
            "{git,} diff /dev/null ./.env",
            "/usr/bin/g?t diff /dev/null ./.env",
            "/usr/bin/g[i]t diff /dev/null ./.env",
            "$'git' diff /dev/null ./.env",
            '$"git" diff /dev/null ./.env',
            "$'\\x67it' diff /dev/null ./.env",
            "$'\\147it' diff /dev/null ./.env",
            "$'\\u0067it' diff /dev/null ./.env",
            "touch git; g?t diff /dev/null ./.env",
            "shopt -s extglob\n/usr/bin/g@(i)t diff /dev/null ./.env",
            "shopt -s extglob\n/usr/bin/@(git) diff /dev/null ./.env",
            "shopt -s extglob\n/usr/bin/g@(i)@(t) diff /dev/null ./.env",
            "gi\\\nt diff /dev/null ./.env",
            '"gi\\\nt" diff /dev/null ./.env',
        ):
            with self.subTest(command=command):
                row = {**SAMPLE_ROW, "command": command}
                self.assertTrue(names(inner_words(row), "git diff"))

    def test_a_spelling_the_shell_does_not_run_as_the_name_names_nothing(self) -> None:
        # A quoted wildcard, brace or extglob group is literal, and a brace
        # group that builds another word builds no name.
        for command in (
            "'/usr/bin/g?t' diff /dev/null ./.env",
            "'/usr/bin/g@(i)t' diff /dev/null ./.env",
            "'gi\\\nt' diff /dev/null ./.env",
            "/usr/bin/g@(i)x diff /dev/null ./.env",
            "'{git,}' diff /dev/null ./.env",
            "g{i,x}t diff /dev/null ./.env",
            "/usr/bin/g?t status",
            "$'\\x67itx' diff /dev/null ./.env",
        ):
            with self.subTest(command=command):
                row = {**SAMPLE_ROW, "command": command}
                self.assertFalse(names(inner_words(row), "git diff"))

    def test_a_command_string_is_read_the_way_its_shell_joins_the_name(self) -> None:
        for command in (
            "script -qc 'g\"\"it diff /dev/null ./.env' /dev/null",
            "script -qc 'gi\\t diff /dev/null ./.env' /dev/null",
            "script -qc \"$'git' diff /dev/null ./.env\" /dev/null",
            "script -qc 'gi\\\nt diff /dev/null ./.env' /dev/null",
        ):
            with self.subTest(command=command):
                row = {**SAMPLE_ROW, "wraps": "script", "command": command}
                self.assertTrue(names(inner_words(row), "git diff"))


class CorpusVerdictTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        cls.prefixes = allow_prefixes(settings)
        cls.hook = hook_command(settings)
        cls.rows = [row for row in cls.corpus["rows"] if applies(row, cls.prefixes)]
        scratch = tempfile.TemporaryDirectory()
        cls.addClassCleanup(scratch.cleanup)
        cls.project = scratch_project(Path(scratch.name))

    def test_the_scratch_repository_has_a_parent_commit(self) -> None:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "HEAD~1^{commit}"],
            cwd=self.project,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)

    def test_enough_rows_apply_here_to_mean_something(self) -> None:
        # If the allow list stopped covering `git diff`, every row would be
        # skipped and the verdict test would pass on nothing.
        self.assertGreaterEqual(len(self.rows), 20)

    def test_every_row_this_repository_allows_is_decided_the_way_it_says(self) -> None:
        for row in self.rows:
            with self.subTest(row=row["id"], command=row["command"]):
                verdict, result = decide(row["command"], self.hook, self.project)
                self.assertIn(result.returncode, (0, 2), result.stderr)
                self.assertEqual(
                    verdict,
                    row["verdict"],
                    f"the corpus says {row['verdict']} {row['command']!r}: {row['why']}",
                )


if __name__ == "__main__":
    unittest.main()
