"""
Script: tests/test_agent_permission_boundary.py
What: Joins `.claude/settings.json`, `.claude/commands/`, `.cursor/rules/` and the ruff configuration in `pyproject.toml` to the things they claim.
Doing: Re-implements the command-prefix matcher, decides the commands the never-do list says are denied, executes the PostToolUse hook, and resolves every path and rule citation the agent configuration names.
Why: These files configure what an agent may do and what it is told, and until now no test opened any of them.
Goal: Make a dropped deny rule, a broken hook, a moved prompt or a stale claim fail here instead of the first time an agent acts on it.

`.claude/settings.json` is the only place the boundary is written down in a form
a tool obeys. Concretely, a suite that never opened it would accept all of these
one-line edits to the committed tree:

  * deleting `Bash(cosign sign:*)`, `Bash(skopeo copy:*)` or
    `Bash(podman push:*)` from `deny` -- the operations that move a published
    artifact become promptable;
  * moving `Bash(gh pr merge:*)` or `Bash(gh workflow run:*)` from `deny` to
    `ask` -- both are one click from moving `:latest`, which is the reason
    `settings.json`'s own `_note_deny_publishing` gives for denying them;
  * moving `Bash(gh api:*)` from `ask` to `allow`, which `_note_gh_api`
    explains is a mutation path because `gh api` switches to POST on `-f` and
    honours `-X`;
  * breaking the `PostToolUse` hook body, which is a shell one-liner that no
    test ran;
  * putting `Bash(python3 -m pytest:*)` back on `allow`, which sounds like a
    convenience and is unbounded local code execution -- a runner imports every
    module it collects, an import is not a tool call, and a module that calls
    `subprocess` reaches every command the `deny` list refuses.

The matcher is re-implemented here rather than assumed, and it has its own case
table (`PermissionRuleMatcherTests`). Without that, every assertion below could
pass because the matcher says yes to everything.

Two of the cases are deliberately the other way round: a leading `+` in a
refspec (`git push origin +main`) and a push to `main` specifically are *not*
expressible as prefix rules, and this file checks that they are still not
denied. If a future rule does catch one, that test fails and the fix is to
update the `_note_push_orderings` note -- the honesty of the note is the thing
being held, not the current rule set.

The other files are the same kind of hand-maintained copy that
`tests/test_contributor_instructions.py` and `tests/test_prompt_catalog.py`
already hold for `.github/`:

  * `.claude/commands/*.md` are thin pointers at `.github/prompts/*.prompt.md`.
    Their own README says "if a command here and its prompt disagree, the
    prompt is right", so a pointer at a renamed prompt is a command that sends
    the reader nowhere.
  * `.cursor/rules/zfs-aurora-complex.mdc` is `alwaysApply: true`, so it is in
    context for every Cursor session, and it carries a copy of the seven
    safety-critical files `AGENTS.md` section 0 rule 2 names.
  * `pyproject.toml` carries the per-file ignore and the line length the tree is
    written to; it is the only ruff configuration source.

Standard library only, and no PyYAML: the CI job installs pytest, pytest-cov
and ruff, so a third-party parser would depend on the runner image and skip
silently the day that changed. The frontmatter here is two or three flat keys,
which is parsed directly.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import ClassVar

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
COMMANDS_DIR = REPO_ROOT / ".claude" / "commands"
CURSOR_RULE = REPO_ROOT / ".cursor" / "rules" / "zfs-aurora-complex.mdc"
SECURITY_AI = REPO_ROOT / "docs" / "SECURITY-AI.md"
AGENTS = REPO_ROOT / "AGENTS.md"
PROMPTS_DIR = REPO_ROOT / ".github" / "prompts"
DIAGNOSE_PROMPT = PROMPTS_DIR / "diagnose-build-failure.prompt.md"
TEST_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test.yml"
RUFF_CONFIG = REPO_ROOT / "pyproject.toml"

BACKTICKED_RE = re.compile(r"`([^`]+)`")
BASH_RULE_RE = re.compile(r"^Bash\((?P<pattern>.*)\)$")
READ_RULE_RE = re.compile(r"^Read\((?P<pattern>.*)\)$")
RULE_CITATION_RE = re.compile(r"AGENTS\.md\s+section 0\s+rule\s+(\d+)")
MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
# A link whose visible text is itself a backticked filename, as every pointer
# in `.claude/commands/` writes it.
LABELLED_LINK_RE = re.compile(r"\[`([^`]+)`\]\(([^)]+)\)")
# Paths as prose writes them: a suffix this repository actually uses, or a
# directory written with a trailing slash.
PATHISH_RE = re.compile(
    r"[A-Za-z0-9_.][A-Za-z0-9_./-]*\.(?:py|yml|yaml|json|sh|md|mdc|toml|pub|key)\b"
)

RULE_2_MARKER = "Treat the build, promotion, and signing path as safety-critical"
NEXT_ITEM_RE = re.compile(r"^\s*(?:\d+\.|[-*])\s+\*\*")

# The `.claude/settings.json` buckets, in the order Claude Code resolves them:
# a deny rule wins over an ask rule, which wins over an allow rule.
BUCKETS = ("deny", "ask", "allow")


def tracked_files() -> list[str]:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.split()


def bash_pattern(rule: str) -> str | None:
    match = BASH_RULE_RE.match(rule)
    return match.group("pattern") if match else None


def bash_rule_regex(pattern: str) -> re.Pattern[str]:
    """The command-prefix rule subset this repository's settings file uses.

    A trailing `:*` is "this command with any arguments": it matches the
    command alone or the command followed by whitespace and anything. That
    boundary is the load-bearing part -- without it `Bash(git push:*)` would
    also match `git pushover`, and `Bash(cosign sign:*)` would be doing
    something other than what it looks like.

    A `*` anywhere else stands for one run of characters within the single
    command line, which is how the `git push * --force` entries reach an
    ordering where the remote comes before the option.
    """

    if pattern.endswith(":*"):
        body, tail = pattern[:-2], r"(?:\s.*)?\Z"
    else:
        body, tail = pattern, r"\Z"
    out: list[str] = []
    for index, literal in enumerate(body.split("*")):
        if index:
            out.append(r"[^\n]*")
        out.append(re.escape(literal))
    return re.compile("".join(out) + tail)


def bash_rule_matches(rule: str, command: str) -> bool:
    pattern = bash_pattern(rule)
    if pattern is None:
        return False
    return bash_rule_regex(pattern).match(command) is not None


def decide(command: str, permissions: dict[str, list[str]]) -> str:
    """deny / ask / allow / unlisted, for one Bash command line."""

    for bucket in BUCKETS:
        for rule in permissions.get(bucket, []):
            if bash_rule_matches(rule, command):
                return bucket
    return "unlisted"


def read_frontmatter(path: Path) -> tuple[dict[str, str], str]:
    """The flat `key: value` frontmatter block, and the body after it."""

    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise AssertionError(f"{path.name}: no frontmatter block")
    end = text.index("\n---", 4)
    fields = {}
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields, text[end:].split("\n", 2)[-1]


def markdown_tables(text: str) -> list[list[list[str]]]:
    """Every pipe table in `text`, as a list of rows of stripped cells."""

    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if all(set(cell) <= set("-: ") and cell for cell in cells):
                continue  # the --- separator row
            current.append(cells)
            continue
        if current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def plain(cell: str) -> str:
    return cell.replace("*", "").replace("`", "").strip().lower()


def rule_2_paths(doc: Path) -> list[str]:
    lines = doc.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if RULE_2_MARKER in line)
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if NEXT_ITEM_RE.match(line):
            break
        block.append(line)
    found = BACKTICKED_RE.findall("\n".join(block))
    return [token for token in found if "/" in token or token.endswith((".py", ".yml"))]


def resolve(name: str, tracked: list[str]) -> str:
    """A path as a doc writes it -> the path as the tree stores it.

    Rule 2 says `build.yml`; the tree says `.github/workflows/build.yml`. A
    second `build.yml` anywhere makes the short name ambiguous, and that is
    reported rather than guessed at.
    """

    name = name.rstrip("/")
    if (REPO_ROOT / name).exists():
        return name
    candidates = sorted({f for f in tracked if f == name or f.endswith("/" + name)})
    if not candidates:
        under = sorted({f for f in tracked if f.startswith(name + "/")})
        if under:
            return name
    if len(candidates) != 1:
        raise AssertionError(f"{name!r} names {len(candidates)} tracked paths: {candidates}")
    return candidates[0]


def section_0_rules() -> dict[int, str]:
    """{rule number: rule text} from AGENTS.md section 0."""

    lines = AGENTS.read_text(encoding="utf-8").splitlines()
    rules: dict[int, str] = {}
    current: int | None = None
    for line in lines:
        match = re.match(r"^(\d+)\.\s+\*\*(.*)", line)
        if match:
            current = int(match.group(1))
            rules[current] = match.group(2)
            continue
        if current is not None:
            if line.startswith(("---", "## ")):
                current = None
            else:
                rules[current] += "\n" + line
    return rules


class PermissionRuleMatcherTests(unittest.TestCase):
    """Guard the guard.

    Every other class here decides commands with `bash_rule_matches`. A matcher
    that said yes to everything would make all of them pass, and one that said
    no to everything would make the `ask` and not-expressible assertions pass.
    So the matcher is pinned first, with the two boundaries that carry the
    meaning: `:*` stops at a word boundary, and a bare `*` does not.
    """

    CASES: ClassVar[tuple[tuple[str, str, bool], ...]] = (
        ("Bash(git push:*)", "git push", True),
        ("Bash(git push:*)", "git push origin main", True),
        ("Bash(git push:*)", "git pushover origin main", False),
        ("Bash(git push:*)", "cd /tmp && git push", False),
        ("Bash(cosign sign:*)", "cosign sign --key env://K ghcr.io/x:latest", True),
        ("Bash(cosign sign:*)", "cosign verify ghcr.io/x:latest", False),
        ("Bash(gh label create:*)", "gh label list", False),
        ("Bash(git push --force:*)", "git push --force origin main", True),
        ("Bash(git push --force:*)", "git push --force-with-lease origin main", False),
        ("Bash(git push * --force:*)", "git push origin --force HEAD:main", True),
        ("Bash(git push * --force:*)", "git push origin +main", False),
        ("Bash(zpool:*)", "zpool status", True),
        # A rule with no trailing `:*` is the command and nothing else.
        ("Bash(git status)", "git status", True),
        ("Bash(git status)", "git status --short", False),
        # Non-Bash rules never decide a command line.
        ("Read(./cosign.key)", "cat ./cosign.key", False),
    )

    def test_the_matcher_agrees_with_the_documented_semantics(self) -> None:
        for rule, command, expected in self.CASES:
            with self.subTest(rule=rule, command=command):
                self.assertEqual(bash_rule_matches(rule, command), expected)


class SettingsFileTests(unittest.TestCase):
    """`.claude/settings.json` is well-formed and every rule in it parses."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        cls.permissions = cls.settings["permissions"]

    def test_the_scan_finds_rules_in_every_bucket(self) -> None:
        # Guard the guard: an empty bucket would make "is this command denied"
        # answerable only by the fallthrough, and several assertions below
        # would then be checking nothing.
        for bucket in BUCKETS:
            with self.subTest(bucket=bucket):
                self.assertGreaterEqual(len(self.permissions[bucket]), 5)

    def test_every_rule_names_a_tool_this_file_understands(self) -> None:
        # An unrecognised rule form fails rather than being skipped: a rule
        # shape this file cannot decide is a rule this file is not holding.
        for bucket in BUCKETS:
            for rule in self.permissions[bucket]:
                with self.subTest(bucket=bucket, rule=rule):
                    self.assertTrue(
                        BASH_RULE_RE.match(rule) or READ_RULE_RE.match(rule),
                        f"{rule!r} is neither a Bash(...) nor a Read(...) rule",
                    )

    def test_no_rule_appears_in_two_buckets(self) -> None:
        # Precedence would hide the duplicate, so the file would read as if a
        # command were promptable while it was in fact denied.
        seen: dict[str, str] = {}
        for bucket in BUCKETS:
            for rule in self.permissions[bucket]:
                with self.subTest(rule=rule):
                    self.assertNotIn(
                        rule, seen, f"{rule!r} is in both {seen.get(rule)} and {bucket}"
                    )
                    seen[rule] = bucket

    def test_no_allow_rule_re_permits_a_denied_command(self) -> None:
        # Precedence makes this harmless today, but an allow rule that overlaps
        # a deny rule is a statement that the command is routine, and the next
        # person to reorganise the file may believe it.
        for allowed in self.permissions["allow"]:
            pattern = bash_pattern(allowed)
            if pattern is None:
                continue
            sample = pattern.removesuffix(":*")
            if "*" in sample:
                continue
            with self.subTest(rule=allowed):
                self.assertNotEqual(
                    decide(sample, {"deny": self.permissions["deny"]}),
                    "deny",
                    f"{allowed!r} allows a command the deny list also matches",
                )

    def test_the_committed_public_key_stays_readable(self) -> None:
        # cosign.pub is committed on purpose -- docs/SECURITY-AI.md says
        # verification is a claim about that file -- so a Read deny rule broad
        # enough to cover it would block the one check an agent should run.
        read_denials = [
            READ_RULE_RE.match(rule).group("pattern")
            for rule in self.permissions["deny"]
            if READ_RULE_RE.match(rule)
        ]
        self.assertIn("./cosign.key", read_denials)
        self.assertNotIn("./cosign.pub", read_denials)
        for pattern in read_denials:
            with self.subTest(pattern=pattern):
                self.assertFalse(
                    Path("cosign.pub").match(pattern.removeprefix("./")),
                    f"{pattern!r} also covers the committed public key",
                )


class EnforcementDecisionTests(unittest.TestCase):
    """The never-do list, decided against the rules in `.claude/settings.json`.

    Each key of ROW_CASES is a row of the enforcement table the maintainers
    keep for this boundary, mapped to concrete command lines. The table itself
    lives in docs/SECURITY-AI.md and is joined to these cases separately.
    """

    # key substring of the row's first cell -> (expected decision, commands)
    ROW_CASES: ClassVar[dict[str, tuple[str, tuple[str, ...]]]] = {
        "merge, dispatch": (
            "deny",
            (
                "gh pr merge 42 --squash",
                "gh workflow run build.yml",
                "gh release create v1.0.0",
            ),
        ),
        # Its own row rather than a tail of the one above, because it is
        # enforced differently: the flag spellings are denied and the
        # colon-refspec spelling is not. See
        # `test_the_delete_refspec_hole_is_still_a_hole`.
        "delete a branch or tag": (
            "deny",
            (
                "git push --delete origin feature",
                "git push origin --delete feature",
                "git tag -d v1.0.0",
            ),
        ),
        "force-push": (
            "deny",
            (
                "git push --force origin main",
                "git push -f origin main",
                "git push origin --force HEAD:main",
                "git push origin -f HEAD:main",
                "git push --force-with-lease origin main",
            ),
        ),
        "move or delete a registry artifact": (
            "deny",
            (
                "skopeo copy docker://a docker://b",
                "skopeo delete docker://ghcr.io/danathar/zfs-aurora-complex:latest",
                "podman push ghcr.io/danathar/zfs-aurora-complex:latest",
                "buildah push ghcr.io/danathar/zfs-aurora-complex:latest",
                "cosign sign --key env://SIGNING_SECRET ghcr.io/x@sha256:0",
            ),
        ),
        "create, edit, or delete a label": (
            "deny",
            (
                "gh label create quality",
                "gh label edit quality --color ff0000",
                "gh label delete quality",
                "gh label clone Danathar/arch-bootc",
            ),
        ),
        "ordinary": (
            "ask",
            (
                "git push origin quality/test-branch",
                "gh pr edit 42 --add-label testing",
                "gh pr review 42 --comment --body hi",
            ),
        ),
        # The doc says this one is not expressible as a prefix rule. The
        # assertion is therefore that it is still NOT denied: if a rule ever
        # catches it, this fails and the fix is to update the
        # `_note_push_orderings` note in settings.json, which says the
        # boundary is best-effort.
        "push to": (
            "ask",
            ("git push origin HEAD:main",),
        ),
        # Also not a denial, and the expectation is deliberately `allow`: the
        # row's whole claim is that the permission layer waves these through
        # and a PreToolUse hook is what refuses them. If a rule ever does catch
        # one, this fails and the fix is to say so in the row rather than leave
        # the doc crediting the hook for a rule's work. The refusals themselves
        # are exercised in tests/test_git_diff_gate.py.
        "read or write an arbitrary file": (
            "allow",
            (
                "git diff /dev/null ./cosign.key",
                "git diff --no-index /etc/passwd /dev/null",
                "git diff /etc/shadow -",
                "git diff -- /dev/null ./cosign.key",
                "git log -p --output=cosign.pub -1",
                "git show --output=.claude/settings.json HEAD",
            ),
        ),
        # Also not a denial. The row's claim is the opposite of the git one:
        # this write primitive *is* expressible as a rule, because every ruff
        # invocation the repository documents is a fixed string. So the two
        # allowed spellings must still decide `allow` -- a rule that stopped
        # matching them would make the documented lint command prompt -- and
        # the `--output-file` spellings must not, which is asserted separately
        # in `test_the_linter_cannot_write_a_file_it_names`.
        "allow-listed linter": (
            "allow",
            (
                "ruff check",
                "ruff check ci_tools/ shared/ tests/ files/ containerfiles/",
            ),
        ),
        # The same claim as the git row, for the allow rows that are not git's:
        # the permission layer approves every one of these -- a rule ending in
        # `:*` matches a command prefix, and a redirection is the rest of the
        # string -- and the hook is what refuses them. The refusals are
        # exercised in tests/test_git_diff_gate.py.
        "allow-listed command that is not git": (
            "allow",
            (
                "python3 tests/run_tests.py >cosign.pub",
                "gh run view 1 --log >.claude/settings.json",
                "skopeo inspect docker://ghcr.io/danathar/zfs-aurora-complex:latest >cosign.pub",
                "cosign verify --output-file cosign.pub --key cosign.pub ghcr.io/x:latest",
            ),
        ),
        # The same shape for a read: the gh view and list rows allow a --jq
        # filter, and gojq's `env` builtin is the process environment. The
        # rows allow the string and the hook refuses it; the refusals are
        # exercised in tests/test_git_diff_gate.py.
        "read the environment": (
            "allow",
            (
                "gh pr view 1 --json number --jq env",
                "gh run list --json name -q '$ENV.GH_TOKEN'",
            ),
        ),
        # Not a denial at all, which is why it carries its own branch in
        # `test_the_denied_rows_really_say_denied`. The claim is that the one
        # test command an agent may run unattended is the wrapper, because a
        # runner imports what it collects and an import is not a tool call.
        "run a test suite": (
            "allow",
            (
                "python3 tests/run_tests.py",
                "python3 tests/run_tests.py tests/test_agent_permission_boundary.py -v",
            ),
        ),
    }

    @classmethod
    def setUpClass(cls) -> None:
        cls.permissions = json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]

    def test_each_command_is_decided_the_way_its_row_claims(self) -> None:
        for key, (expected, commands) in self.ROW_CASES.items():
            for command in commands:
                with self.subTest(rule=key, command=command):
                    self.assertEqual(
                        decide(command, self.permissions),
                        expected,
                        f"{key!r} is expected to decide {expected}",
                    )

    def test_the_refspec_hole_the_doc_admits_is_still_a_hole(self) -> None:
        # `git push origin +main` forces an update with no option at all. The
        # doc and `_note_push_orderings` both say no prefix rule can see it. If
        # that stops being true, say so there rather than leaving the doc
        # understating the boundary.
        self.assertNotEqual(decide("git push origin +main", self.permissions), "deny")

    def test_the_delete_refspec_hole_is_still_a_hole(self) -> None:
        # The same mechanism one line down: a refspec with an empty source side
        # deletes the remote ref, with no option for a prefix rule to match. The
        # short form is not even expressible here -- a trailing `:*` means "this
        # command with any arguments", so a pattern ending in a literal
        # colon-glob collides with that idiom. If a rule ever does catch these,
        # this fails and the fix is to say so in docs/SECURITY-AI.md and in
        # `_note_push_orderings`, both of which call the deletion row
        # best-effort.
        for command in (
            "git push origin :main",
            "git push origin :feature",
            "git push origin :refs/heads/feature",
            "git push origin :refs/tags/v1.0.0",
        ):
            with self.subTest(command=command):
                self.assertNotEqual(decide(command, self.permissions), "deny")

    def test_the_unrestricted_test_runners_are_not_on_the_allow_list(self) -> None:
        # The load-bearing half of the "run a test suite" row. A runner imports
        # every module it collects, and an import is not a tool call, so an
        # allow-listed `python3 -m pytest <anything>` reaches every command the
        # deny list above refuses -- via a module that calls subprocess, which
        # no rule here ever sees. They decide `unlisted` rather than `ask`:
        # there is no rule for them in any bucket, so Claude Code prompts.
        for command in (
            "python3 -m pytest tests/",
            "python3 -m pytest /tmp/anywhere/test_x.py",
            "python3 -m unittest discover -s tests",
            "python3 -m unittest discover -s /tmp/anywhere",
        ):
            with self.subTest(command=command):
                self.assertNotEqual(decide(command, self.permissions), "allow")

    def test_the_allowed_runner_is_the_file_that_does_the_narrowing(self) -> None:
        # The other half: the row is worth nothing if the allowed command is a
        # path that does not exist, or one that no longer refuses anything. The
        # refusals themselves are exercised in tests/test_run_tests.py; this
        # holds the join between the rule and that file.
        rule = "Bash(python3 tests/run_tests.py:*)"
        self.assertIn(rule, self.permissions["allow"])
        self.assertTrue((REPO_ROOT / "tests" / "run_tests.py").is_file())

    def test_no_allow_rule_runs_a_python_module_from_the_checkout(self) -> None:
        # `python3 -m <module>` puts the working directory first on sys.path, so
        # an untracked root `argparse.py` runs before the module's own first
        # line, and nothing inside the module can check for it in time. That is
        # how `python3 -m ci_tools.cli --help:*` ran untracked code unprompted.
        for rule in self.permissions["allow"]:
            with self.subTest(rule=rule):
                self.assertNotRegex(rule, r"^Bash\(python3? -m ")

    def test_the_linter_cannot_write_a_file_it_names(self) -> None:
        # `ruff check -o PATH` writes the report to PATH instead of stdout and
        # creates the file even when the lint is clean, so `Bash(ruff check:*)`
        # -- the linter with any arguments -- was a way to overwrite the trust
        # anchor with no prompt. Both spellings of the flag are checked: a
        # refusal that caught only the long one is one a short option defeats.
        for command in (
            "ruff check --output-file=cosign.pub ci_tools",
            "ruff check --output-file cosign.pub ci_tools",
            "ruff check -o cosign.pub ci_tools",
            "ruff check -ocosign.pub ci_tools",
        ):
            with self.subTest(command=command):
                self.assertNotEqual(decide(command, self.permissions), "allow")

    def test_no_ruff_rule_takes_arbitrary_arguments(self) -> None:
        # The other half: the row is worth nothing if some later edit puts the
        # `:*` back. A rule ending in `:*` means "this command with any
        # arguments", and any argument is what carries `--output-file`.
        for rule in self.permissions["allow"]:
            pattern = bash_pattern(rule)
            if pattern is not None and pattern.startswith("ruff "):
                with self.subTest(rule=rule):
                    self.assertFalse(
                        pattern.endswith(":*"),
                        f"{rule!r} takes arbitrary arguments, so it allows --output-file",
                    )

    def test_the_documented_lint_command_still_runs_unprompted(self) -> None:
        # Narrowing is only correct if it did not narrow past what the project
        # tells people to run. This is the command in test.yml, CONTRIBUTING,
        # the pull request template and copilot-instructions.
        self.assertEqual(
            decide("ruff check ci_tools/ shared/ tests/ files/ containerfiles/", self.permissions),
            "allow",
        )

    def test_reading_labels_is_still_permitted(self) -> None:
        # `_note_labels`: minting or renaming a label manufactures an approval
        # signal, but reading them is how you check what one means first, so
        # the deny list must stay narrower than `gh label`. It decides
        # `unlisted` today rather than `allow` -- there is no allow rule for
        # it, so Claude Code prompts -- which is why this asserts "not denied"
        # rather than the note's word.
        self.assertNotEqual(decide("gh label list", self.permissions), "deny")

    def test_gh_api_is_promptable_and_never_allowed(self) -> None:
        # `_note_gh_api`: prefix rules cannot tell a read from a mutation,
        # because `gh api` POSTs as soon as -f/-F appears and honours -X.
        for command in (
            "gh api repos/Danathar/zfs-aurora-complex",
            "gh api -X POST repos/Danathar/zfs-aurora-complex/actions/workflows/build.yml/dispatches",
            "gh api -f ref=main repos/Danathar/zfs-aurora-complex/actions/workflows/build.yml/dispatches",
        ):
            with self.subTest(command=command):
                self.assertEqual(decide(command, self.permissions), "ask")


class SecurityDocBoundaryTests(unittest.TestCase):
    """The "agent permission boundary" table in docs/SECURITY-AI.md, decided against the rules.

    The doc is the only place the boundary is written down in words and the settings file the
    only place it is written down in a form a tool obeys. Each phrase the doc's settings row
    uses is mapped here to commands, so a dropped deny rule or a row reworded without the rule
    changing fails. A phrase the mapping does not know is a failure, not a skip.
    """

    # phrase in the `.claude/settings.json` row -> commands that must decide `deny`
    DENIED: ClassVar[dict[str, tuple[str, ...]]] = {
        "`cosign sign`": ("cosign sign --key env://K ghcr.io/x@sha256:0",),
        "`skopeo copy`/`delete`": ("skopeo copy docker://a docker://b", "skopeo delete docker://a"),
        "`podman push`": ("podman push ghcr.io/x:latest",),
        "`buildah push`": ("buildah push ghcr.io/x:latest",),
        "merging": ("gh pr merge 1 --squash",),
        "workflow dispatch": ("gh workflow run build.yml",),
        "releases": ("gh release create v1",),
        "secrets": ("gh secret set X", "gh secret delete X"),
        "labels": ("gh label create x", "gh label edit x", "gh label delete x"),
        "force-push": ("git push --force origin main", "git push origin -f HEAD:main"),
        "`git reset --hard`": ("git reset --hard HEAD~1",),
        "`git clean`": ("git clean -fdx",),
        "`zpool`": ("zpool destroy tank", "zpool status"),
        "`zfs`": ("zfs destroy tank/x", "zfs rollback tank/x@s"),
    }
    DENIED_READS: ClassVar[dict[str, str]] = {
        "`cosign.key`": "Read(./cosign.key)",
        "`.env` files": "Read(./.env)",
        "`.pem`": "Read(./**/*.pem)",
        "`.p12`": "Read(./**/*.p12)",
        "`id_rsa`": "Read(./**/id_rsa)",
        "`id_ed25519`": "Read(./**/id_ed25519)",
    }

    @classmethod
    def setUpClass(cls) -> None:
        cls.permissions = json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]
        text = SECURITY_AI.read_text(encoding="utf-8")
        section = text.split("## The agent permission boundary", 1)[1].split("\n## ", 1)[0]
        rows = [line for line in section.splitlines() if line.startswith("| [`.claude/settings.json`]")]
        assert len(rows) == 1, "docs/SECURITY-AI.md no longer has one `.claude/settings.json` row"
        cls.row = " ".join(rows[0].split())
        cls.section = section

    def test_every_phrase_the_row_denies_is_denied(self) -> None:
        denies = self.row.split("`deny` refuses", 1)[1].split("`ask` covers", 1)[0]
        for phrase, commands in self.DENIED.items():
            if phrase in ("`zpool`", "`zfs`"):
                continue  # named below as "destructive `zpool` and `zfs` commands"
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, denies, f"the doc row no longer says {phrase}")
                for command in commands:
                    self.assertEqual(decide(command, self.permissions), "deny", command)

    def test_the_destructive_zpool_and_zfs_commands_are_denied(self) -> None:
        self.assertIn("destructive `zpool` and `zfs` commands", self.row)
        for command in self.DENIED["`zpool`"] + self.DENIED["`zfs`"]:
            with self.subTest(command=command):
                self.assertEqual(decide(command, self.permissions), "deny")

    def test_the_reads_the_row_names_are_denied(self) -> None:
        for phrase, rule in self.DENIED_READS.items():
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.row)
                self.assertIn(rule, self.permissions["deny"])

    def test_the_row_says_what_ask_covers_and_the_rules_agree(self) -> None:
        asks = self.row.split("`ask` covers", 1)[1].split("`allow`", 1)[0]
        self.assertIn("an ordinary push, commit, pull request and `gh api`", asks)
        for command in (
            "git push origin feature",
            "git commit -m x",
            "gh pr create --title x",
            "gh api repos/Danathar/zfs-aurora-complex",
        ):
            with self.subTest(command=command):
                self.assertEqual(decide(command, self.permissions), "ask")

    def test_the_row_says_the_settings_file_registers_the_hook(self) -> None:
        self.assertIn("It also registers the hook", self.row)
        hooks = json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        commands = [hook["command"] for entry in hooks for hook in entry["hooks"]]
        self.assertTrue(any("gate-git-diff.sh" in command for command in commands))

    def test_the_four_files_the_table_lists_exist(self) -> None:
        for token in re.findall(r"\[`([^`]+)`\]\(\.\./", self.section):
            with self.subTest(path=token):
                self.assertTrue((REPO_ROOT / token).exists(), f"{token} is listed but missing")

    def test_push_to_main_is_still_not_expressible_as_a_rule(self) -> None:
        # The doc says "Push to `main`" is not a prefix rule and rests on `ask` and the ruleset.
        self.assertIn("is not expressible as a prefix rule", self.section)
        self.assertEqual(decide("git push origin HEAD:main", self.permissions), "ask")
        self.assertNotEqual(decide("git push origin +main", self.permissions), "deny")

    def test_the_never_list_dispatch_and_merge_are_denied_not_asked(self) -> None:
        # "Never ... merge ... dispatch build.yml": a prompt would let the maintainer click
        # through while distracted, so these must be deny rows.
        for command in ("gh pr merge 1", "gh workflow run build.yml -f promote_to_stable=true"):
            with self.subTest(command=command):
                self.assertEqual(decide(command, self.permissions), "deny")

    def test_the_one_allowed_test_command_is_the_runner(self) -> None:
        self.assertIn("The one allow-listed test command", self.section)
        for rule in self.permissions["allow"]:
            pattern = bash_pattern(rule) or ""
            self.assertFalse(re.search(r"\b(pytest|unittest)\b", pattern), rule)
        self.assertIn("Bash(python3 tests/run_tests.py:*)", self.permissions["allow"])


class SettingsNotesTests(unittest.TestCase):
    """The `_note_*` keys explain why each denial is there. They must still be true."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        cls.permissions = cls.settings["permissions"]
        cls.notes = {key: value for key, value in cls.settings.items() if key.startswith("_note_")}
        cls.tracked = tracked_files()

    def test_the_scan_finds_notes(self) -> None:
        self.assertGreaterEqual(len(self.notes), 4)

    def test_every_path_a_note_names_exists(self) -> None:
        for key, note in self.notes.items():
            for token in sorted(set(PATHISH_RE.findall(note))):
                with self.subTest(note=key, path=token):
                    resolve(token, self.tracked)

    def test_every_rule_citation_in_a_note_still_says_what_it_assumes(self) -> None:
        rules = section_0_rules()
        for key, note in self.notes.items():
            for number in RULE_CITATION_RE.findall(note):
                with self.subTest(note=key, rule=number):
                    self.assertIn(int(number), rules)
                    # Rule 6 is the one every note leans on: registry tags and
                    # git history are outward-facing, propose rather than do.
                    if int(number) == 6:
                        text = rules[6].lower()
                        for word in ("push", "promote", "tag", "delete"):
                            self.assertIn(word, text)

    def test_every_command_a_note_calls_denied_is_denied(self) -> None:
        # The notes name their subjects in prose ("cosign sign, skopeo
        # copy/delete, podman/buildah push, gh release"). Rather than parse
        # that, each is pinned to a command line here.
        claimed = {
            "_note_deny_publishing": (
                "cosign sign --key env://K ghcr.io/x@sha256:0",
                "skopeo copy docker://a docker://b",
                "skopeo delete docker://a",
                "podman push ghcr.io/x:latest",
                "buildah push ghcr.io/x:latest",
                "gh release create v1",
                "gh pr merge 1",
                "gh workflow run build.yml",
                "git push --force origin main",
                "git tag -d v1",
            ),
            "_note_labels": (
                "gh label create quality",
                "gh label edit quality",
                "gh label delete quality",
                "gh label clone other/repo",
            ),
            "_note_zpool": ("zpool status", "zpool destroy tank"),
        }
        for key, commands in claimed.items():
            self.assertIn(key, self.notes)
            for command in commands:
                with self.subTest(note=key, command=command):
                    self.assertEqual(decide(command, self.permissions), "deny")

    def test_the_hook_note_points_at_the_job_that_really_enforces_lint(self) -> None:
        # `_note_hook` says the hook no-ops without ruff because CI is where
        # lint is enforced, per .github/workflows/test.yml.
        self.assertIn("test.yml", self.notes["_note_hook"])
        self.assertIn("ruff check", TEST_WORKFLOW.read_text(encoding="utf-8"))


HOOK_INPUT = '{"tool_input": {"file_path": "%s"}}'
BASH = shutil.which("bash")


@unittest.skipUnless(
    shutil.which("jq") and BASH, "the hook body is a bash one-liner written in terms of jq"
)
class PostToolUseHookTests(unittest.TestCase):
    """The `PostToolUse` hook is a shell one-liner. Run it.

    It is the only part of `.claude/settings.json` with behaviour rather than
    policy, and behaviour that was read by no test. Its three claims --
    lint only `.py`, no-op when ruff is absent, exit 2 so the finding is fed
    back rather than printed -- are each a separate way for it to silently stop
    working.
    """

    @classmethod
    def setUpClass(cls) -> None:
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        entries = settings["hooks"]["PostToolUse"]
        if len(entries) != 1:
            raise AssertionError(f"expected one PostToolUse entry, got {len(entries)}")
        cls.entry = entries[0]
        hooks = cls.entry["hooks"]
        if len(hooks) != 1 or hooks[0]["type"] != "command":
            raise AssertionError("expected one command hook")
        cls.command = hooks[0]["command"]

    def setUp(self) -> None:
        self.bindir = Path(tempfile.mkdtemp(prefix="hook-path-"))
        self.addCleanup(shutil.rmtree, self.bindir, True)
        self.calls = self.bindir / "ruff-calls"
        os.symlink(shutil.which("jq"), self.bindir / "jq")

    def _install_ruff(self, exit_code: int, message: str = "") -> None:
        script = self.bindir / "ruff"
        script.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> {self.calls}\n'
            f'[ -n "{message}" ] && printf "%s\\n" "{message}"\n'
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

    def _run(self, file_path: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [BASH, "-c", self.command],
            input=HOOK_INPUT % file_path,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env={
                "PATH": str(self.bindir),
                "CLAUDE_PROJECT_DIR": str(REPO_ROOT),
                "HOME": str(self.bindir),
            },
        )

    def test_it_fires_on_the_tools_that_write_files(self) -> None:
        for tool in ("Edit", "Write", "MultiEdit"):
            with self.subTest(tool=tool):
                self.assertIn(tool, self.entry["matcher"])

    def test_a_lint_finding_exits_2_and_reaches_stderr(self) -> None:
        # Exit 2 is what makes Claude Code feed the output back to the model.
        # Exit 1 would print it and move on, which looks identical in a diff.
        self._install_ruff(1, "ci_tools/cli.py:1:1: F401 unused import")
        result = self._run("/tmp/example.py")
        self.assertEqual(result.returncode, 2)
        self.assertIn("F401 unused import", result.stderr)

    def test_a_clean_file_exits_0(self) -> None:
        self._install_ruff(0)
        result = self._run("/tmp/example.py")
        self.assertEqual(result.returncode, 0)
        self.assertIn("/tmp/example.py", self.calls.read_text(encoding="utf-8"))

    def test_a_non_python_file_never_reaches_ruff(self) -> None:
        self._install_ruff(1, "should not run")
        result = self._run(str(REPO_ROOT / "README.md"))
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())

    def test_it_no_ops_when_ruff_is_not_installed(self) -> None:
        # The claim in `_note_hook`: it never blocks work on a machine without
        # ruff. Without this the hook would fail every edit on such a machine.
        result = self._run("/tmp/example.py")
        self.assertEqual(result.returncode, 0)

    def test_it_survives_input_with_no_file_path(self) -> None:
        self._install_ruff(1, "should not run")
        result = subprocess.run(
            [BASH, "-c", self.command],
            input="{}",
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env={"PATH": str(self.bindir), "CLAUDE_PROJECT_DIR": str(REPO_ROOT)},
        )
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())


class ClaudeCommandTests(unittest.TestCase):
    """`.claude/commands/*.md` are pointers. A pointer at nothing is the defect."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.commands = sorted(p for p in COMMANDS_DIR.glob("*.md") if p.name != "README.md")
        cls.readme = COMMANDS_DIR / "README.md"
        cls.tracked = tracked_files()

    def test_the_scan_finds_commands(self) -> None:
        self.assertGreaterEqual(len(self.commands), 3)

    def test_every_command_carries_a_description(self) -> None:
        # Claude Code lists slash commands by this field; without it the
        # command still runs but is unfindable.
        for path in self.commands:
            with self.subTest(command=path.name):
                fields, _ = read_frontmatter(path)
                self.assertTrue(fields.get("description"))

    def test_every_command_takes_the_arguments_it_is_given(self) -> None:
        # Each of these ends by handing the run number, PR or diff through.
        # Drop the placeholder and the command silently ignores its argument.
        for path in self.commands:
            with self.subTest(command=path.name):
                self.assertIn("$ARGUMENTS", path.read_text(encoding="utf-8"))

    def test_every_command_points_at_exactly_one_prompt_that_exists(self) -> None:
        for path in self.commands:
            with self.subTest(command=path.name):
                targets = {
                    link
                    for link in MD_LINK_RE.findall(path.read_text(encoding="utf-8"))
                    if link.endswith(".prompt.md")
                }
                self.assertEqual(len(targets), 1, f"{path.name} points at {sorted(targets)}")
                target = (path.parent / targets.pop()).resolve()
                self.assertTrue(target.exists(), f"{path.name} points at a missing prompt")
                self.assertEqual(target.parent, PROMPTS_DIR.resolve())

    def test_no_link_shows_a_filename_other_than_the_one_it_opens(self) -> None:
        # Renaming a prompt and updating only the visible text leaves a link
        # that reads correctly and opens the old file -- or the reverse. Either
        # way the reader is told one thing and sent somewhere else.
        for path in self.commands + [self.readme]:
            for label, target in LABELLED_LINK_RE.findall(path.read_text(encoding="utf-8")):
                if not label.endswith((".md", "/")):
                    continue
                with self.subTest(command=path.name, label=label):
                    self.assertEqual(Path(label.rstrip("/")).name, Path(target.rstrip("/")).name)

    def test_no_two_commands_point_at_the_same_prompt(self) -> None:
        targets = []
        for path in self.commands:
            for link in MD_LINK_RE.findall(path.read_text(encoding="utf-8")):
                if link.endswith(".prompt.md"):
                    targets.append(Path(link).name)
        self.assertEqual(sorted(targets), sorted(set(targets)))

    def test_the_readme_table_lists_exactly_the_commands_that_exist(self) -> None:
        tables = markdown_tables(self.readme.read_text(encoding="utf-8"))
        self.assertEqual(len(tables), 1)
        header, *rows = tables[0]
        self.assertEqual(header[0].lower(), "command")
        listed = {plain(row[0]).lstrip("/") for row in rows}
        self.assertEqual(listed, {path.stem for path in self.commands})
        for row in rows:
            with self.subTest(command=row[0]):
                self.assertTrue(row[1].strip(), "no description in the table")

    def test_every_path_a_command_names_exists(self) -> None:
        for path in self.commands:
            text = path.read_text(encoding="utf-8")
            # Skip the pointer link itself: it is relative, and the assertion
            # above resolves it properly.
            for token in sorted(set(PATHISH_RE.findall(text))):
                if token.endswith(".prompt.md"):
                    continue
                with self.subTest(command=path.name, path=token):
                    resolve(token, self.tracked)

    def test_every_rule_a_command_cites_exists_and_still_says_what_it_assumes(self) -> None:
        rules = section_0_rules()
        citations = 0
        for path in self.commands:
            for number in RULE_CITATION_RE.findall(path.read_text(encoding="utf-8")):
                citations += 1
                with self.subTest(command=path.name, rule=number):
                    self.assertIn(int(number), rules)
                    if int(number) == 6:
                        # replay-build.md leans on rule 6 for "registry tags
                        # are not an agent's to move".
                        self.assertIn("tag", rules[6].lower())
        self.assertGreaterEqual(citations, 1)

    def test_the_promote_default_the_replay_command_warns_about_is_real(self) -> None:
        # replay-build.md's second hazard is that `promote_to_stable` defaults
        # to true, so a diagnostic replay left at the default moves `:latest`.
        # If the default ever changes, the warning becomes a false alarm and
        # the reader learns to discount the file.
        replay = COMMANDS_DIR / "replay-build.md"
        text = replay.read_text(encoding="utf-8")
        self.assertIn("promote_to_stable", text)
        build = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        start = build.index("promote_to_stable:")
        block = build[start : start + 400]
        default = re.search(r"default:\s*(\S+)", block)
        self.assertIsNotNone(default, "promote_to_stable declares no default")
        self.assertEqual(default.group(1).strip("'\""), "true")


class DiagnoseGuardTableTests(unittest.TestCase):
    """`.claude/commands/diagnose-build.md` counts rows in someone else's table.

    It says "five of the entries have no fix in this repository", which is a
    number about `diagnose-build-failure.prompt.md`'s guard table. Adding or
    removing a row there makes the count wrong in a file nobody would think to
    reopen.
    """

    # The guard table keyed by a distinctive fragment of its message column ->
    # whether the repository can fix that cause at all. An unrecognised row
    # fails: a new guard has to be classified deliberately, because that is the
    # decision the sentence in the command file summarises.
    FIXABLE_HERE: ClassVar[dict[str, bool]] = {
        "does not provide a kmod-zfs": False,  # upstream ZFS/kernel disagreement
        "needs AKMODS_IMAGE_PINNED": True,  # action wiring, ours to fix
        "Refusing to check akmods cache ref": True,  # the pin handed over is ours
        "Refusing to pin the akmods cache: AKMODS_BUILT_IMAGE_ID": True,  # the action's wiring
        # Escalate: a moved shared tag, or a push that rewrote the config. The guard is ours.
        "Refusing to pin akmods cache": True,
        "Promoted digest mismatch": True,  # escalate, and the guard is ours
        # Correct refusal of a re-run that would move `:latest` backwards.
        "Refusing to promote run": False,
        "Missing tag list": False,  # transient registry failure
        "GITHUB_RUN_NUMBER is not a run number": True,  # the workflow's environment
        "Missing digest in skopeo inspect output": False,  # transient registry/CDN failure
        "SIGNING_SECRET is empty": False,  # wrong trigger context, not a defect
        "Missing required verification key file": True,  # a repository problem
        "is not a lowercase ghcr.io/<owner>/<name> repository": True,  # the signing job's wiring
        "is not a sha256 digest": True,  # the push step's output, ours to fix
        "Replay lock file not found": True,  # pass the lock file
    }

    @classmethod
    def setUpClass(cls) -> None:
        text = DIAGNOSE_PROMPT.read_text(encoding="utf-8")
        start = text.index("## 3. Match the message to the guard")
        cls.rows = markdown_tables(text[start:])[0][1:]

    def test_the_table_has_rows_to_count(self) -> None:
        self.assertGreaterEqual(len(self.rows), 6)

    def test_every_row_is_classified(self) -> None:
        for row in self.rows:
            with self.subTest(message=row[0][:60]):
                matched = [key for key in self.FIXABLE_HERE if key in row[0]]
                self.assertEqual(len(matched), 1, f"unclassified guard row: {row[0][:80]}")

    def test_the_command_file_states_the_right_count(self) -> None:
        unfixable = sum(
            1
            for row in self.rows
            if not self.FIXABLE_HERE[next(k for k in self.FIXABLE_HERE if k in row[0])]
        )
        text = (COMMANDS_DIR / "diagnose-build.md").read_text(encoding="utf-8")
        match = re.search(
            r"\b(one|two|three|four|five|six)\b\s+of\s+the\s+entries", text, re.IGNORECASE
        )
        self.assertIsNotNone(match, "diagnose-build.md no longer counts the guard rows")
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
        self.assertEqual(words[match.group(1).lower()], unfixable)


class CursorRuleTests(unittest.TestCase):
    """`.cursor/rules/zfs-aurora-complex.mdc` is in context for every Cursor session."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fields, cls.body = read_frontmatter(CURSOR_RULE)
        cls.text = CURSOR_RULE.read_text(encoding="utf-8")
        cls.tracked = tracked_files()

    def test_it_is_still_always_applied(self) -> None:
        # `alwaysApply: false` would leave the file in the tree, reviewed and
        # maintained, and loaded by nothing.
        self.assertEqual(self.fields.get("alwaysApply"), "true")
        self.assertTrue(self.fields.get("description"))

    def test_it_names_exactly_the_seven_rule_2_files(self) -> None:
        # A copy of the list written in prose instead of backticks, so a scan
        # that looks for backticked paths would never match it.
        # The list runs to the end of its sentence: the paths are separated by
        # commas and line breaks, and the first period followed by whitespace
        # is the one that closes it.
        listed = re.search(r"Safety-critical files:(.*?)\.\s", self.text, re.DOTALL).group(1)
        names = [
            token.strip().rstrip(".")
            for token in listed.replace("\n", " ").replace(" and ", ", ").split(",")
            if token.strip()
        ]
        self.assertEqual(len(names), 7, f"parsed {names}")
        resolved = {resolve(name, self.tracked) for name in names}
        expected = {resolve(name, self.tracked) for name in rule_2_paths(AGENTS)}
        self.assertEqual(resolved, expected)

    def test_every_path_it_names_exists(self) -> None:
        for token in sorted(set(PATHISH_RE.findall(self.text))):
            with self.subTest(path=token):
                resolve(token, self.tracked)

    def test_the_ruff_invocation_is_the_one_ci_runs(self) -> None:
        # The rule file tells an agent which command must be clean. A command
        # missing a directory is one an agent can satisfy while CI fails.
        quoted = [
            token for token in BACKTICKED_RE.findall(self.text) if token.startswith("ruff check")
        ]
        self.assertEqual(len(quoted), 1, f"found {quoted}")
        workflow = TEST_WORKFLOW.read_text(encoding="utf-8")
        run = re.search(r"run:\s*(ruff check [^\n]+)", workflow)
        self.assertIsNotNone(run, "test.yml no longer runs ruff check")
        self.assertEqual(" ".join(quoted[0].split()), " ".join(run.group(1).split()))

    def test_the_ruff_pin_really_lives_where_it_says(self) -> None:
        self.assertIn(".github/workflows/test.yml", self.text)
        self.assertRegex(TEST_WORKFLOW.read_text(encoding="utf-8"), r"ruff==\d+\.\d+\.\d+")

    def test_the_failure_it_calls_most_likely_is_still_described_by_rule_1(self) -> None:
        # The section heading is a restatement of AGENTS.md section 0 rule 1.
        # A renumbered rule list would leave the two describing different
        # things with no link between them.
        self.assertIn("Never weaken a fail-closed check", self.text)
        self.assertIn("Never weaken a fail-closed check", section_0_rules()[1])


class RuffConfigTests(unittest.TestCase):
    """`pyproject.toml` is the repository's only ruff configuration, and carries a per-file ignore."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = tomllib.loads(RUFF_CONFIG.read_text(encoding="utf-8"))["tool"]["ruff"]

    def test_there_is_exactly_one_ruff_configuration_source(self) -> None:
        # Two sources (a ruff.toml or .ruff.toml beside pyproject.toml) would make
        # which one wins a question rather than a fact, and ruff prefers the
        # dedicated file without saying so.
        for name in ("ruff.toml", ".ruff.toml"):
            with self.subTest(name=name):
                self.assertFalse((REPO_ROOT / name).exists())

    def test_every_per_file_ignore_names_a_file_that_exists(self) -> None:
        ignores = self.config["lint"]["per-file-ignores"]
        self.assertTrue(ignores)
        for path in ignores:
            with self.subTest(path=path):
                self.assertTrue((REPO_ROOT / path).exists(), f"{path} is not in the tree")

    def test_the_e402_ignore_is_still_needed(self) -> None:
        # An ignore that stopped being needed is an ignore that silently
        # permits the next real E402 in that file.
        for path, codes in self.config["lint"]["per-file-ignores"].items():
            if "E402" not in codes:
                continue
            with self.subTest(path=path):
                source = (REPO_ROOT / path).read_text(encoding="utf-8")
                sys_path_line = source.index("sys.path")
                import_after = re.search(r"^(?:import|from) ", source[sys_path_line:], re.MULTILINE)
                self.assertIsNotNone(
                    import_after,
                    f"{path} no longer imports after touching sys.path; drop the E402 ignore",
                )

    def test_the_line_length_is_the_one_the_tree_is_written_to(self) -> None:
        # Guard the guard: this is the setting every other Python file in the
        # repository is formatted against, so a silent change would show up as
        # a diff in unrelated files rather than here.
        self.assertEqual(self.config["line-length"], 100)

    def test_renovate_bumps_every_ruff_pin(self) -> None:
        # Renovate's ruff manager rewrites only the files its patterns name. A
        # pin outside them stays behind when the others move: the 0.17.0 bump
        # in #75 changed test.yml alone, and the nightly suite's same-pin test
        # failed on it.
        renovate = json.loads((REPO_ROOT / "renovate.json").read_text(encoding="utf-8"))
        managers = [m for m in renovate["customManagers"] if m.get("depNameTemplate") == "ruff"]
        self.assertEqual(len(managers), 1)
        patterns = [re.compile(p.strip("/")) for p in managers[0]["managerFilePatterns"]]
        pinned = [
            path
            for path in tracked_files()
            if (REPO_ROOT / path).is_file()
            and re.search(
                r"ruff==\d+\.\d+\.\d+",
                (REPO_ROOT / path).read_text(encoding="utf-8", errors="ignore"),
            )
        ]
        self.assertTrue(pinned)
        for path in pinned:
            with self.subTest(path=path):
                self.assertTrue(any(p.search(path) for p in patterns), f"{path} pins ruff")


if __name__ == "__main__":
    sys.exit(unittest.main())
