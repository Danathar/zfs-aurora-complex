"""
Script: tests/test_editorconfig.py
What: Joins `.editorconfig` to the tree it claims to describe, and to the ruff settings in `pyproject.toml`.
Doing: Hand-rolls the EditorConfig section matcher, resolves every tracked file through it, and checks the resolved values against what the files actually contain.
Why: Nothing opened `.editorconfig` otherwise -- no test reads it, no CI job runs an EditorConfig checker, and `tests/check_coverage.py` measures Python statements, so an editor-facing claim could drift from the tree with a green build.
Goal: Make a false claim in `.editorconfig` -- a wrong line length, a dead section, an indent size the files do not use -- fail here.

`.editorconfig`'s own header states its warrant plainly:

    Encodes the conventions already in the tree rather than proposing new
    ones. Every value below was read off the files it applies to: no tracked
    file has a tab, none has trailing whitespace, and every one ends with a
    newline. So nothing here asks for a reformat, and adopting it produces no
    diff.

That is a factual claim about the tree, not a preference, and it is the only
thing that makes the file safe to adopt. If it stops being true, the file
starts proposing a reformat instead of recording one, and the first person to
open a stale file in a conforming editor gets the unrelated-noise diff that
`CONTRIBUTING.md`'s "every changed line traces to the change you set out to
make" exists to prevent. So this suite recomputes the claim rather than
trusting it.

Mutations to the committed tree this suite rejects:

  * `[*.py] max_line_length = 100` changed to `88`, contradicting
    `pyproject.toml`'s `line-length = 100` -- the one setting here that a tool
    really does enforce, and the one the comment says this file follows;
  * `[*.json] indent_size` changed from `2` to `4`, against every JSON file in
    the tree;
  * a section re-anchored to a path matching nothing, so it describes files
    that are not there;
  * `root = true` deleted, which makes EditorConfig keep searching parent
    directories outside the checkout.

The matcher is hand-rolled rather than taken from the `editorconfig` package
because the CI job installs pytest, pytest-cov, ruff and pyyaml and nothing
else. A matcher asserted only against the committed tree would be vacuous -- it
could be wrong in the same direction as the file and every downstream check
would still pass -- so it carries its own case table in
`EditorConfigMatcherTests`, including the cases this repository's own
`.editorconfig` depends on: `LICENSE` must not match a sibling such as
`LICENSE.APACHE-2.0`, and a pattern with no separator must match at any depth
while one with a separator is anchored at the repository root.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path
from typing import ClassVar

import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
EDITORCONFIG = REPO_ROOT / ".editorconfig"

# The sections whose indent_size is a claim about files a human wrote by hand,
# and whose smallest indentation is therefore the step the author used. Python
# is deliberately absent: ruff aligns continuation lines to an open bracket, so
# the smallest leading run in a .py file is not its indent step. The .py claim
# is joined to pyproject.toml instead, which is the tool that actually enforces it.
INDENT_STEP_SECTIONS = ("*.{yml,yaml}", "*.json", "*.sh", "Containerfile")


def tracked_paths() -> list[str]:
    """Every file git would carry, including files added but not yet committed.

    `git ls-files` alone lists only the index. A new file that violates the
    claims below would then be invisible here until someone committed it,
    which is the wrong half of the review to catch it in.
    """
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(line for line in out.splitlines() if line)


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile one EditorConfig section pattern to a regex over a POSIX path.

    Implements the subset `.editorconfig` uses: `*` (no separator), `**` (any,
    including separators), `?`, `[...]` character classes and `{a,b}`
    alternation. A pattern containing a separator is anchored at the directory
    holding `.editorconfig`; one without a separator matches at any depth.
    """
    anchored = "/" in pattern.rstrip("/")
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif ch == "*":
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        elif ch == "[":
            close = pattern.find("]", i + 1)
            if close == -1:
                out.append(re.escape(ch))
                i += 1
            else:
                body = pattern[i + 1 : close]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body + "]")
                i = close + 1
        elif ch == "{":
            close = pattern.find("}", i + 1)
            if close == -1:
                out.append(re.escape(ch))
                i += 1
            else:
                alts = pattern[i + 1 : close].split(",")
                out.append("(?:" + "|".join(re.escape(a) for a in alts) + ")")
                i = close + 1
        else:
            out.append(re.escape(ch))
            i += 1
    body = "".join(out)
    prefix = "" if anchored else "(?:.*/)?"
    return re.compile("\\A" + prefix + body + "\\Z")


def parse_editorconfig(text: str) -> tuple[dict[str, str], list[tuple[str, dict[str, str]]]]:
    """Return the preamble properties and the ordered (pattern, properties) sections."""
    preamble: dict[str, str] = {}
    sections: list[tuple[str, dict[str, str]]] = []
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = {}
            sections.append((line[1:-1], current))
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        target = preamble if current is None else current
        target[key.strip().lower()] = value.strip()
    return preamble, sections


def section_comment(text: str, pattern: str) -> str:
    """The comment block immediately above `[pattern]`, comment markers stripped."""
    lines = text.splitlines()
    header = "[" + pattern + "]"
    try:
        index = lines.index(header)
    except ValueError as exc:  # pragma: no cover - guarded by a test below
        raise AssertionError(f".editorconfig has no section {header}") from exc
    block: list[str] = []
    i = index - 1
    while i >= 0 and lines[i].lstrip().startswith("#"):
        block.append(lines[i].lstrip().lstrip("#").strip())
        i -= 1
    return " ".join(reversed(block))


class Resolver:
    """Last-match-wins resolution of EditorConfig properties for a path."""

    def __init__(self, text: str) -> None:
        self.preamble, raw_sections = parse_editorconfig(text)
        self.patterns = [pattern for pattern, _ in raw_sections]
        self.sections = [(glob_to_regex(p), p, props) for p, props in raw_sections]

    def matches(self, pattern: str, path: str) -> bool:
        for regex, name, _ in self.sections:
            if name == pattern:
                return bool(regex.match(path))
        raise AssertionError(f"no section {pattern!r} in .editorconfig")

    def resolve(self, path: str) -> dict[str, str]:
        props: dict[str, str] = {}
        for regex, _, section_props in self.sections:
            if regex.match(path):
                props.update(section_props)
        return props

    def matching(self, pattern: str, paths: list[str]) -> list[str]:
        return [p for p in paths if self.matches(pattern, p)]


class EditorConfigMatcherTests(unittest.TestCase):
    """The matcher, checked against its own case table.

    Everything else in this file reads the tree through `glob_to_regex`. If it
    were only ever exercised against the committed tree it could agree with a
    wrong `.editorconfig` and every other assertion here would be vacuous.
    """

    CASES: ClassVar[list[tuple[str, str, bool]]] = [
        # (pattern, path, expected)
        ("*", "README.md", True),
        ("*", "docs/glossary.md", True),
        ("*.py", "ci_tools/cli.py", True),
        ("*.py", "cli.py", True),
        ("*.py", "ci_tools/cli.pyi", False),
        ("*.py", "ci_tools/py", False),
        ("*.{yml,yaml}", "ci/x.yml", True),
        ("*.{yml,yaml}", "ci/x.yaml", True),
        ("*.{yml,yaml}", "ci/x.yl", False),
        ("*.{yml,yaml}", "ci/x.yml.bak", False),
        ("*.json", "ci/defaults.json", True),
        ("*.json", "ci/defaults.json5", False),
        ("*.sh", "build_files/build-image.sh", True),
        ("*.sh", "files/scripts/helper.sh", True),
        ("*.sh", "build_files/build-image.bash", False),
        # No separator in the pattern, so it matches at any depth -- the
        # property EditorConfig gives bare names, and the reason [LICENSE]
        # below needs its exactness checked rather than assumed.
        ("Containerfile", "Containerfile", True),
        ("Containerfile", "containerfiles/zfs-akmods/Containerfile", True),
        ("Containerfile", "Containerfile.dev", False),
        ("*.md", "docs/reflections/README.md", True),
        ("*.md", "README.markdown", False),
        # A separator anchors the pattern at the .editorconfig's directory.
        ("files/usr/lib/**", "files/usr/lib/tmpfiles.d/zfs.conf", True),
        ("files/usr/lib/**", "files/usr/lib/zfs.conf", True),
        ("files/usr/lib/**", "files/scripts/configure_signing_policy.py", False),
        ("files/usr/lib/**", "vendor/files/usr/lib/zfs.conf", False),
        ("LICENSE", "LICENSE", True),
        ("LICENSE", "LICENSE.APACHE-2.0", False),
    ]

    def test_the_matcher_agrees_with_its_case_table(self) -> None:
        for pattern, path, expected in self.CASES:
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(bool(glob_to_regex(pattern).match(path)), expected)

    def test_a_character_class_and_a_single_character_wildcard_work(self) -> None:
        self.assertTrue(glob_to_regex("*.[ch]").match("src/a.c"))
        self.assertFalse(glob_to_regex("*.[ch]").match("src/a.o"))
        self.assertTrue(glob_to_regex("a?.py").match("ab.py"))
        self.assertFalse(glob_to_regex("a?.py").match("abc.py"))

    def test_a_single_star_does_not_cross_a_separator(self) -> None:
        self.assertFalse(glob_to_regex("docs/*").match("docs/reflections/README.md"))
        self.assertTrue(glob_to_regex("docs/**").match("docs/reflections/README.md"))


class EditorConfigStructureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = EDITORCONFIG.read_text(encoding="utf-8")
        self.resolver = Resolver(self.text)

    def test_root_is_declared_before_any_section(self) -> None:
        """Without `root = true` EditorConfig walks up out of the checkout.

        It would then pick up whatever `.editorconfig` happens to sit in a
        contributor's home directory, which is the one outcome this file's
        "adopting it produces no diff" promise cannot survive.
        """
        self.assertEqual(self.resolver.preamble.get("root"), "true")

    def test_every_section_matches_at_least_one_tracked_file(self) -> None:
        """A section matching nothing is a claim about a tree that moved on."""
        paths = tracked_paths()
        for pattern in self.resolver.patterns:
            with self.subTest(pattern=pattern):
                self.assertTrue(
                    self.resolver.matching(pattern, paths),
                    f"[{pattern}] matches no tracked file",
                )

    def test_the_baseline_section_is_the_bare_star(self) -> None:
        """Later sections only override; the floor has to apply to everything."""
        self.assertEqual(self.resolver.patterns[0], "*")
        baseline = dict(self.resolver.sections[0][2])
        self.assertEqual(baseline.get("charset"), "utf-8")
        self.assertEqual(baseline.get("end_of_line"), "lf")
        self.assertEqual(baseline.get("insert_final_newline"), "true")
        self.assertEqual(baseline.get("trim_trailing_whitespace"), "true")
        self.assertEqual(baseline.get("indent_style"), "space")


class TreeMatchesTheClaimsTests(unittest.TestCase):
    """The header's factual claims, recomputed over the tree."""

    def setUp(self) -> None:
        self.resolver = Resolver(EDITORCONFIG.read_text(encoding="utf-8"))
        self.paths = tracked_paths()

    def read(self, path: str) -> bytes:
        return (REPO_ROOT / path).read_bytes()

    def test_no_tracked_file_contains_a_tab(self) -> None:
        """`indent_style = space`, and the header says the tree already obeys it."""
        offenders = []
        for path in self.paths:
            if self.resolver.resolve(path).get("indent_style") != "space":
                continue
            if b"\t" in self.read(path):
                offenders.append(path)
        self.assertEqual(offenders, [], "indent_style = space, but these carry a tab")

    def test_no_tracked_file_has_trailing_whitespace(self) -> None:
        offenders = []
        for path in self.paths:
            if self.resolver.resolve(path).get("trim_trailing_whitespace") != "true":
                continue
            data = self.read(path)
            if any(line.rstrip(b"\r") != line.rstrip() for line in data.split(b"\n")):
                offenders.append(path)
        self.assertEqual(offenders, [], "trim_trailing_whitespace = true, but these trail")

    def test_every_tracked_file_ends_with_a_newline(self) -> None:
        offenders = []
        for path in self.paths:
            if self.resolver.resolve(path).get("insert_final_newline") != "true":
                continue
            data = self.read(path)
            if data and not data.endswith(b"\n"):
                offenders.append(path)
        self.assertEqual(offenders, [], "insert_final_newline = true, but these do not")

    def test_every_tracked_file_uses_lf_and_decodes_as_utf_8(self) -> None:
        crlf, undecodable = [], []
        for path in self.paths:
            props = self.resolver.resolve(path)
            data = self.read(path)
            if props.get("end_of_line") == "lf" and b"\r" in data:
                crlf.append(path)
            if props.get("charset") == "utf-8":
                try:
                    data.decode("utf-8")
                except UnicodeDecodeError:
                    undecodable.append(path)
        self.assertEqual(crlf, [], "end_of_line = lf, but these carry a CR")
        self.assertEqual(undecodable, [], "charset = utf-8, but these do not decode")


class IndentClaimsTests(unittest.TestCase):
    """`indent_size` per section, against the indentation those files really use."""

    def setUp(self) -> None:
        self.text = EDITORCONFIG.read_text(encoding="utf-8")
        self.resolver = Resolver(self.text)
        self.paths = tracked_paths()

    def smallest_indent(self, path: str) -> int | None:
        widths = []
        for line in (REPO_ROOT / path).read_text(encoding="utf-8").splitlines():
            stripped = line.lstrip(" ")
            if stripped and stripped != line:
                widths.append(len(line) - len(stripped))
        return min(widths) if widths else None

    def test_declared_indent_size_is_the_step_those_files_use(self) -> None:
        """The smallest indentation in a hand-written file is its indent step.

        A continuation line can only ever be deeper, so the minimum is the
        author's unit. Changing a declared `indent_size` away from it makes
        `.editorconfig` propose a reformat, which its header says it does not.
        """
        for pattern in INDENT_STEP_SECTIONS:
            declared = None
            for _, name, props in self.resolver.sections:
                if name == pattern and "indent_size" in props:
                    declared = int(props["indent_size"])
            self.assertIsNotNone(declared, f"[{pattern}] declares no indent_size")
            observed = [
                width
                for path in self.resolver.matching(pattern, self.paths)
                if (width := self.smallest_indent(path)) is not None
            ]
            with self.subTest(pattern=pattern):
                self.assertTrue(observed, f"[{pattern}] matches nothing indented")
                self.assertEqual(min(observed), declared)

    def test_the_python_line_length_follows_ruff(self) -> None:
        """`.editorconfig`'s comment says ruff wins where the two overlap.

        `ruff check` is the step `.github/workflows/test.yml` really runs, so
        a disagreement here is `.editorconfig` telling an editor to wrap at a
        column CI does not use.
        """
        pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        ruff = pyproject["tool"]["ruff"]
        declared = None
        for _, name, props in self.resolver.sections:
            if name == "*.py" and "max_line_length" in props:
                declared = int(props["max_line_length"])
        self.assertIsNotNone(declared, "[*.py] declares no max_line_length")
        self.assertEqual(declared, ruff["line-length"])
        self.assertIn("pyproject.toml", section_comment(self.text, "*.py"))

    def test_the_markdown_section_really_relaxes_the_baseline(self) -> None:
        """Two trailing spaces are a hard line break, so `[*]` must not win here."""
        baseline = dict(self.resolver.sections[0][2])
        self.assertEqual(baseline["trim_trailing_whitespace"], "true")
        self.assertEqual(self.resolver.resolve("docs/glossary.md")["trim_trailing_whitespace"], "false")

    def test_the_license_carve_out_is_exactly_the_license(self) -> None:
        """A bare name matches at any depth, so the sibling file needs checking."""
        self.assertEqual(self.resolver.matching("LICENSE", self.paths), ["LICENSE"])
        self.assertEqual(self.resolver.resolve("LICENSE")["indent_style"], "unset")


class ShellSectionCommentTests(unittest.TestCase):
    """`[*.sh]`'s comment names a script; the tree decides whether it still exists."""

    def setUp(self) -> None:
        self.text = EDITORCONFIG.read_text(encoding="utf-8")
        self.resolver = Resolver(self.text)
        self.paths = tracked_paths()

    def test_the_sh_comment_names_no_path_that_is_gone(self) -> None:
        comment = section_comment(self.text, "*.sh")
        for token in re.findall(r"[\w./-]+\.sh", comment):
            with self.subTest(token=token):
                self.assertIn(token, self.paths)

    def test_every_shell_script_indents_at_the_declared_size(self) -> None:
        declared = int(self.resolver.resolve("build_files/build-image.sh")["indent_size"])
        for path in self.resolver.matching("*.sh", self.paths):
            widths = set()
            for line in (REPO_ROOT / path).read_text(encoding="utf-8").splitlines():
                stripped = line.lstrip(" ")
                if stripped and stripped != line:
                    widths.add(len(line) - len(stripped))
            with self.subTest(path=path):
                self.assertTrue(widths, f"{path} has no indented line")
                self.assertEqual(
                    sorted(w for w in widths if w % declared),
                    [],
                    f"{path} indents off the declared {declared}-space step",
                )


if __name__ == "__main__":
    unittest.main()
