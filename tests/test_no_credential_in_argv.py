"""
Script: tests/test_no_credential_in_argv.py
What: Fails when a command CI runs is handed a registry password, token or other secret as one
of its arguments.
Doing: Reads every `run:` script in `.github/workflows/*.yml` and `.github/actions/*/action.yml`,
every tracked `*.sh`, every argv list in `ci_tools/*.py`, and the akmods `Justfile` recipes
`ci_tools/` runs (at the akmods ref `ci/defaults.json` names), and reports a secret-bearing value
passed as `-p`, `--password`, `--creds` or `--registry-password`, or inside a `user:<secret>@`
URL. It also reports a `password:` input on a `redhat-actions/push-to-registry` step, which that
action turns into `podman push --creds`.
Why: `/proc/<pid>/cmdline` is mode 0444, so every uid on the runner can read a program's
arguments for as long as it runs. Gotcha 6 in `docs/signing-and-bootc.md` forbids a credential
there. That rule was broken and fixed one site at a time ten times across this repository and its
sister repositories (#283), and each fix tested only its own site, so nothing failed when the next
one appeared -- the tenth was the akmods `login` recipe this repository runs, which passed the
token to `podman login -p` until Danathar/akmods#249.
Goal: Fail the pull request that adds the next site, wherever in the tree it is written.

The rule is the one Danathar/akmods#249 added to akmods' `scripts/check-policies.mjs`
(`policies/secret-argv.json`): the same secret-reference and credential-flag patterns, so the two
repositories disagree about nothing. A value is secret-bearing when it references a variable
whose name contains TOKEN, PASSWORD, PASSWD or SECRET in any case, or an Actions expression that
does (`${{ secrets.* }}`, `${{ github.token }}`), or a `{{ ... }}` Justfile variable that does.

The one exception, as in Danathar/aurora-zfs-simple's `tests/test-no-credential-in-argv.sh`, is
`printf` writing to a pipe or a file (`printf ... |`, `printf ... > file`). `printf` is a shell
builtin, so no process is started with the value in its arguments. A `printf` whose output becomes
another command's argument, through a command substitution, still counts.

Shell is read as logical lines -- backslash-newline continuations joined, because the earlier
sites split their flags across lines -- with comment lines skipped. Workflows are read as text by
indentation rather than through PyYAML, so this check never skips for want of a parser and every
finding carries its physical line number.

The argv lists in `ci_tools/` are read with `ast`. There the credential is usually called `creds`
-- the `actor:token` pair `registry_creds_from_env` returns, which is how #154 put it on skopeo's
and cosign's command lines -- so a name containing CRED counts as secret-bearing too.

The external-recipe half reaches the network: it fetches the akmods `Justfile` from GitHub at the
ref the build resolves. Offline it skips with a message saying so. Under GitHub Actions a failed
fetch fails the test instead, so CI never passes without having read the recipes.

The detectors are run first against `tests/fixtures/credential-argv/`, which holds each earlier
site as it was before its fix, other spellings of the same mistake, and the allowed shapes. A
scan that matched nothing would pass everything, so the expected answers there are exact.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import unittest
import urllib.error
import urllib.request
from functools import cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "credential-argv"
DEFAULTS = REPO_ROOT / "ci" / "defaults.json"

# Copied from Danathar/akmods `policies/secret-argv.json` (#249) so both repositories apply one
# rule. A reference to a secret: an Actions expression, a shell variable, or a Justfile variable
# whose name says it holds one.
SECRET_REFERENCE_SOURCE = (
    r"\$\{\{[^}]*(?:secret|token|passw(?:or)?d)[^}]*\}\}"
    r"|\$\{?\w*(?:secret|token|passw(?:or)?d)\w*\}?"
    r"|\{\{\s*\w*(?:secret|token|passw(?:or)?d)\w*\s*\}\}"
)
SECRET_REFERENCE = re.compile(SECRET_REFERENCE_SOURCE, re.IGNORECASE)
# A credential flag and the one shell word after it. Group 1 is `-p`, group 2 a long flag, group 3
# the value.
CREDENTIAL_FLAG = re.compile(
    r"(?:^|[\s;|&(])"
    r"(?:(-p)\s*=?\s*|(--password|--registry-password|--(?:src-|dest-)?creds)(?:\s*=\s*|\s+))"
    r"(\"[^\"]*\"|'[^']*'|(?:\$?\{\{[^}]*\}\}|[^\s;|&)])+)"
)
# A secret as the password part of a URL: `user:$TOKEN@host`.
SECRET_URL = re.compile(rf"(?://|:)(?:{SECRET_REFERENCE_SOURCE})@", re.IGNORECASE)

# The text of the command a match sits in, before and after it, for the printf exception.
LAST_COMMAND = re.compile(r"[^|;&(]*$")
NEXT_COMMAND = re.compile(r"^([^|;&)]*)(.{0,2})", re.DOTALL)
PRINTF = re.compile(r"^\s*printf(?:\s|$)")

# The `ci_tools/` half. Each argv element is rendered as one word, with an interpolated or
# non-literal part written `{expression}`; a part whose expression names a secret is one.
PYTHON_CREDENTIAL_FLAGS = frozenset(
    {"-p", "--password", "--registry-password", "--creds", "--src-creds", "--dest-creds"}
)
PYTHON_SECRET_PART = re.compile(
    r"\{[^{}]*(?:secret|token|passw(?:or)?d|cred)[^{}]*\}", re.IGNORECASE
)
PYTHON_SECRET_URL = re.compile(
    r"(?://|:)\{[^{}]*(?:secret|token|passw(?:or)?d|cred)[^{}]*\}@", re.IGNORECASE
)

# A `run:` key, either a step's own key or the first key of a list item.
RUN_KEY = re.compile(r"^(\s*(?:-\s+)?)run\s*:\s*(.*)$")
LIST_ITEM = re.compile(r"^(\s*)-\s+(.*)$")
PUSH_TO_REGISTRY = re.compile(r"^\s*uses\s*:\s*['\"]?redhat-actions/push-to-registry@")
PASSWORD_INPUT = re.compile(r"^(\s*)password\s*:")

# A Justfile recipe header (`name params: deps`, optionally `@`-quiet), never an `:=` assignment.
RECIPE_HEADER = re.compile(r"^@?(?P<name>[A-Za-z_][\w-]*)(?:\s+[^:]*)?:(?!=)(?P<deps>.*)$")
RECIPE_DEPENDENCY = re.compile(r"\(\s*([A-Za-z_][\w-]*)[^)]*\)|([A-Za-z_][\w-]*)")
# A recipe running another one, as the akmods recipes do with `{{ just }} get-kernel-version`.
NESTED_JUST = re.compile(r"\{\{\s*just\s*\}\}\s+([A-Za-z_][\w-]*)")

ADVICE = (
    "pass it on stdin (printf '%s' \"$TOKEN\" | ... --password-stdin) or in a 0600 file, "
    "never as an argument (gotcha 6 in docs/signing-and-bootc.md)"
)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def logical_lines(entries: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Join backslash-newline continuations, keeping the first physical line's number."""

    out = []
    i = 0
    while i < len(entries):
        number, text = entries[i]
        while re.search(r"\\\s*$", text) and i + 1 < len(entries):
            i += 1
            text = re.sub(r"\\\s*$", " ", text) + entries[i][1].strip()
        out.append((number, text))
        i += 1
    return out


def _printf_writes(before: str, after: str) -> bool:
    """True when the match is an argument of `printf` and printf writes to a pipe or a file."""

    head = LAST_COMMAND.search(before).group(0)
    if not PRINTF.match(head):
        return False
    tail, following = NEXT_COMMAND.match(after).groups()
    if ">" in head + tail:
        return True
    return following.startswith("|") and following != "||"


def shell_findings(text: str) -> list[tuple[str, str]]:
    """
    The `(kind, what)` sites in one logical shell command.

    `kind` is `flag` or `url` for a finding, and `printf` for a site the printf exception allows,
    so a fixture can show the exception was applied rather than the site missed.
    """

    if text.lstrip().startswith("#"):
        return []
    found = []
    for match in CREDENTIAL_FLAG.finditer(text):
        if not SECRET_REFERENCE.search(match.group(3)):
            continue
        what = f"{match.group(1) or match.group(2)} {match.group(3)}"
        allowed = _printf_writes(text[: match.start()], text[match.end() :])
        found.append(("printf" if allowed else "flag", what))
    for match in SECRET_URL.finditer(text):
        allowed = _printf_writes(text[: match.start()], text[match.end() :])
        found.append(("printf" if allowed else "url", f"{match.group(0)} in a URL"))
    return found


def shell_script_findings(text: str) -> list[tuple[int, str, str]]:
    """Every site in a whole shell script, as `(line, kind, what)`."""

    entries = list(enumerate(text.splitlines(), start=1))
    return [
        (number, kind, what)
        for number, line in logical_lines(entries)
        for kind, what in shell_findings(line)
    ]


def run_script_lines(lines: list[str]) -> list[tuple[int, str]]:
    """
    The lines of every `run:` script in a workflow or composite action, as `(line, text)`.

    A script is the `run:` key's own value plus every following line indented deeper than the
    key, so a `run: |` block ends where the step's next key starts.
    """

    out = []
    i = 0
    while i < len(lines):
        match = RUN_KEY.match(lines[i])
        if not match:
            i += 1
            continue
        key_indent = len(match.group(1))
        out.append((i + 1, match.group(2)))
        i += 1
        while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) > key_indent):
            out.append((i + 1, lines[i]))
            i += 1
    return out


def push_to_registry_findings(lines: list[str]) -> list[tuple[int, str, str]]:
    """
    A `password:` input on a `redhat-actions/push-to-registry` step.

    The action logs nothing in and passes `username:password` to `podman push --creds`, so the
    secret lands on podman's command line although no `run:` script names it
    (Danathar/aurora-zfs-simple#463). Each list item is read by indentation: the `uses:` key has
    to be one of the item's own keys, and `password:` has to sit deeper, under its `with:`.
    """

    found = []
    for start, line in enumerate(lines):
        item = LIST_ITEM.match(line)
        if not item:
            continue
        dash = len(item.group(1))
        key_indent = len(line) - len(item.group(2))
        end = start + 1
        while end < len(lines) and (not lines[end].strip() or _indent(lines[end]) > dash):
            end += 1
        own_keys = [item.group(2)] + [
            text for text in lines[start + 1 : end] if text.strip() and _indent(text) == key_indent
        ]
        if not any(PUSH_TO_REGISTRY.match(key) for key in own_keys):
            continue
        for number in range(start + 1, end):
            password = PASSWORD_INPUT.match(lines[number])
            if password and len(password.group(1)) > key_indent:
                what = "password: input of redhat-actions/push-to-registry (podman push --creds)"
                found.append((number + 1, "input", what))
    return found


def workflow_findings(text: str) -> list[tuple[int, str, str]]:
    """Every site in a workflow or composite action, as `(line, kind, what)`, in line order."""

    lines = text.splitlines()
    found = [
        (number, kind, what)
        for number, line in logical_lines(run_script_lines(lines))
        for kind, what in shell_findings(line)
    ]
    return sorted(found + push_to_registry_findings(lines))


def _python_word(node: ast.expr) -> str:
    """One argv element as text: literal parts as written, everything else as `{expression}`."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_python_word(part) for part in node.values)
    if isinstance(node, ast.FormattedValue):
        return "{" + ast.unparse(node.value) + "}"
    return "{" + ast.unparse(node) + "}"


def python_argv_findings(source: str) -> list[tuple[int, str, str]]:
    """
    Every site in the list and tuple literals of one Python module, as `(line, kind, what)`.

    A credential flag counts as an element of its own followed by the value
    (`["--creds", creds]`) or joined to it with `=` (`f"--creds={creds}"`); a URL counts in any
    f-string.
    """

    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.JoinedStr):
            for match in PYTHON_SECRET_URL.finditer(_python_word(node)):
                found.append((node.lineno, "url", f"{match.group(0)} in a URL"))
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        words = [_python_word(element) for element in node.elts]
        for index, word in enumerate(words):
            flag, joined, value = word.partition("=")
            if flag not in PYTHON_CREDENTIAL_FLAGS:
                continue
            if not joined:
                if index + 1 == len(words):
                    continue
                value = words[index + 1]
            if PYTHON_SECRET_PART.search(value):
                found.append((node.elts[index].lineno, "flag", f"{flag} {value}"))
    return sorted(found)


def just_recipes_run_by(source: str) -> set[str]:
    """The recipes a Python module runs as `["just", "<recipe>", ...]`."""

    recipes = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.List, ast.Tuple)) or len(node.elts) < 2:
            continue
        program, recipe = node.elts[0], node.elts[1]
        if (
            isinstance(program, ast.Constant)
            and program.value == "just"
            and isinstance(recipe, ast.Constant)
            and isinstance(recipe.value, str)
            and not recipe.value.startswith("-")
        ):
            recipes.add(recipe.value)
    return recipes


def justfile_recipes(text: str) -> dict[str, tuple[int, list[str], list[tuple[int, str]]]]:
    """`{name: (header line, dependencies, body lines)}` for every recipe in a Justfile."""

    lines = text.splitlines()
    recipes = {}
    i = 0
    while i < len(lines):
        header = RECIPE_HEADER.match(lines[i])
        if not header:
            i += 1
            continue
        deps_text = header.group("deps").split("#", 1)[0]
        dependencies = [
            paren or bare for paren, bare in RECIPE_DEPENDENCY.findall(deps_text) if paren or bare
        ]
        start = i + 1
        i += 1
        while i < len(lines) and (not lines[i].strip() or lines[i][:1].isspace()):
            i += 1
        body = [(number + 1, lines[number]) for number in range(start, i)]
        recipes[header.group("name")] = (start, dependencies, body)
    return recipes


def justfile_findings(text: str, roots: list[str]) -> list[tuple[int, str, str, str]]:
    """
    Every site in the recipes `just <root>` runs, as `(line, recipe, kind, what)`.

    `just build` runs `build`'s dependencies and every recipe its body runs through `{{ just }}`
    as well, so those are followed. A recipe that cannot be found fails rather than being
    skipped: a check that silently read less than the build runs would pass anything.
    """

    recipes = justfile_recipes(text)
    pending = list(roots)
    seen: set[str] = set()
    found = []
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        if name not in recipes:
            raise AssertionError(f"the Justfile has no recipe {name!r}, which the build runs")
        _, dependencies, body = recipes[name]
        pending.extend(dependencies)
        for number, line in logical_lines(body):
            pending.extend(NESTED_JUST.findall(line))
            found.extend((number, name, kind, what) for kind, what in shell_findings(line))
    return sorted(found)


def tracked(*patterns: str) -> list[str]:
    listing = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(REPO_ROOT), "ls-files", "-z", "--"]
        + list(patterns),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout
    return sorted(name for name in listing.split("\0") if name)


def _verdicts(found: list[tuple]) -> list[tuple[int, str]]:
    """`(line, kind)` per site, the part of a finding the fixtures pin."""

    return [(site[0], site[-2]) for site in found]


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class ShellDetectorTests(unittest.TestCase):
    """The shell rule against the earlier sites, as they were before each fix."""

    def test_the_podman_push_creds_of_issue_155_is_flagged(self) -> None:
        found = workflow_findings(_fixture("159-publish-native-image.yml"))
        self.assertEqual(_verdicts(found), [(16, "flag")])

    def test_the_git_remote_url_of_issue_266_is_flagged(self) -> None:
        found = workflow_findings(_fixture("271-akmods-failure-triage.yml"))
        self.assertEqual(_verdicts(found), [(18, "url")])

    def test_both_skopeo_creds_of_the_aurora_zfs_simple_nightly_job_are_flagged(self) -> None:
        found = workflow_findings(_fixture("150-nightly-compliance.yml"))
        self.assertEqual(_verdicts(found), [(15, "flag"), (22, "flag")])

    def test_the_creds_array_of_aurora_zfs_simple_write_badges_is_flagged(self) -> None:
        found = shell_script_findings(_fixture("150-write-badges.sh.txt"))
        self.assertEqual(_verdicts(found), [(8, "flag")])

    def test_the_other_spellings_are_each_flagged(self) -> None:
        found = shell_script_findings(_fixture("other-shapes.sh.txt"))
        flags = [(line, "flag") for line in (6, 8, 10, 12, 14)]
        self.assertEqual(_verdicts(found), flags + [(16, "url"), (19, "url")])

    def test_a_password_input_to_push_to_registry_is_flagged(self) -> None:
        found = workflow_findings(_fixture("463-push-to-registry.yml"))
        self.assertEqual(_verdicts(found), [(19, "input")])

    def test_stdin_files_logins_and_printf_into_a_pipe_or_a_file_pass(self) -> None:
        """
        Both printf writes of a credentialed URL are seen and allowed, not missed.

        The same file has a `docker/login-action` `password:` and a push-to-registry step with
        no password, which must not be reported either.
        """

        found = workflow_findings(_fixture("allowed.yml"))
        self.assertEqual(_verdicts(found), [(29, "printf"), (30, "printf")])


class PythonArgvDetectorTests(unittest.TestCase):
    """The `ci_tools/` rule against #154's helpers and other spellings."""

    def test_the_helpers_of_issue_154_are_each_flagged(self) -> None:
        found = python_argv_findings(_fixture("154-common.py.txt"))
        self.assertEqual(_verdicts(found), [(10, "flag"), (18, "flag"), (18, "flag"), (27, "flag")])

    def test_the_other_spellings_are_each_flagged(self) -> None:
        found = python_argv_findings(_fixture("other-shapes.py.txt"))
        self.assertEqual(_verdicts(found), [(7, "flag"), (8, "flag"), (9, "url"), (10, "flag")])

    def test_an_auth_file_stdin_and_a_header_pass(self) -> None:
        self.assertEqual(python_argv_findings(_fixture("allowed.py.txt")), [])


class JustfileDetectorTests(unittest.TestCase):
    """Recipe resolution and the shell rule, against Justfile recipes in the akmods shapes."""

    def setUp(self) -> None:
        self.text = _fixture("akmods-login.just")

    def test_the_login_recipe_before_akmods_249_is_flagged(self) -> None:
        found = justfile_findings(self.text, ["login"])
        self.assertEqual(
            [(line, recipe, kind) for line, recipe, kind, _ in found], [(13, "login", "flag")]
        )

    def test_the_login_recipe_after_akmods_249_passes(self) -> None:
        self.assertEqual(justfile_findings(self.text, ["login-stdin"]), [])

    def test_dependencies_and_nested_just_calls_are_followed(self) -> None:
        """`build` reaches `get-kernel-version` only through a dependency's `{{ just }}` call."""

        found = justfile_findings(self.text, ["build"])
        self.assertEqual(
            [(line, recipe, kind) for line, recipe, kind, _ in found],
            [(29, "get-kernel-version", "flag")],
        )

    def test_a_recipe_nothing_runs_is_not_reported(self) -> None:
        found = justfile_findings(self.text, ["build", "login", "login-stdin"])
        self.assertEqual([line for line, *_ in found], [13, 29])

    def test_a_recipe_that_is_not_there_fails(self) -> None:
        with self.assertRaisesRegex(AssertionError, "no recipe 'publish'"):
            justfile_findings(self.text, ["publish"])


class TreeTests(unittest.TestCase):
    """The rule against this repository's own workflows, actions, scripts and `ci_tools/`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.workflows = tracked(
            ".github/workflows/*.yml",
            ".github/workflows/*.yaml",
            ".github/actions/*/action.yml",
            ".github/actions/*/action.yaml",
        )
        cls.scripts = tracked("*.sh")
        cls.modules = tracked("ci_tools/*.py")

    def test_the_scan_reads_the_files_the_earlier_sites_were_in(self) -> None:
        """A scan that read nothing would pass everything."""

        scanned = set(self.workflows + self.scripts + self.modules)
        for path in (
            ".github/actions/publish-native-image/action.yml",
            ".github/actions/prepare-main-akmods/action.yml",
            ".github/workflows/akmods-failure-triage.yml",
            ".github/workflows/build.yml",
            "build_files/build-image.sh",
            "ci_tools/common.py",
            "ci_tools/akmods_build_and_publish.py",
        ):
            with self.subTest(path=path):
                self.assertIn(path, scanned)
        self.assertFalse([path for path in scanned if path.startswith("tests/fixtures/")])

    def test_the_scan_reads_the_run_scripts_themselves(self) -> None:
        """The push that #155 fixed is read from its `run:` script, not skipped as YAML."""

        action = REPO_ROOT / ".github/actions/publish-native-image/action.yml"
        lines = action.read_text(encoding="utf-8").splitlines()
        script = logical_lines(run_script_lines(lines))
        pushes = [text for _, text in script if "podman push" in text]
        self.assertTrue([text for text in pushes if "--authfile" in text], pushes)

    def test_no_workflow_or_action_puts_a_secret_on_a_command_line(self) -> None:
        found = []
        for path in self.workflows:
            text = (REPO_ROOT / path).read_text(encoding="utf-8")
            found += [
                f"{path}:{line}: {what}"
                for line, kind, what in workflow_findings(text)
                if kind != "printf"
            ]
        self.assertEqual(found, [], ADVICE)

    def test_no_shell_script_puts_a_secret_on_a_command_line(self) -> None:
        found = []
        for path in self.scripts:
            text = (REPO_ROOT / path).read_text(encoding="utf-8")
            found += [
                f"{path}:{line}: {what}"
                for line, kind, what in shell_script_findings(text)
                if kind != "printf"
            ]
        self.assertEqual(found, [], ADVICE)

    def test_no_ci_tools_argv_list_carries_a_secret(self) -> None:
        found = []
        for path in self.modules:
            source = (REPO_ROOT / path).read_text(encoding="utf-8")
            found += [f"{path}:{line}: {what}" for line, _, what in python_argv_findings(source)]
        self.assertEqual(found, [], ADVICE)


@cache
def akmods_justfile() -> tuple[str, str]:
    """
    The akmods `Justfile` at the ref the build checks out, and a label naming that ref.

    `ci/defaults.json` pins `AKMODS_UPSTREAM_REF` when it is set and otherwise tracks
    `AKMODS_UPSTREAM_TRACK`, the order `ci_tools/resolve_build_inputs.py` resolves them in.
    """

    defaults = json.loads(DEFAULTS.read_text(encoding="utf-8"))
    repo_url = defaults["AKMODS_UPSTREAM_REPO"].strip()
    ref = (
        defaults.get("AKMODS_UPSTREAM_REF", "").strip()
        or defaults.get("AKMODS_UPSTREAM_TRACK", "").strip()
    )
    repo = re.fullmatch(r"https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?", repo_url)
    if not repo or not ref:
        raise AssertionError(
            f"cannot locate the akmods Justfile from ci/defaults.json: {repo_url!r} @ {ref!r}"
        )
    owner, name = repo.groups()
    url = f"https://raw.githubusercontent.com/{owner}/{name}/{ref}/Justfile"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.read().decode("utf-8"), f"{owner}/{name}@{ref}"
    except urllib.error.HTTPError as error:
        # The server answered: the ref or the file is wrong, which no retry offline would fix.
        raise AssertionError(f"{url}: HTTP {error.code}") from error
    except OSError as error:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise AssertionError(f"{url} could not be fetched in CI: {error}") from error
        raise unittest.SkipTest(
            f"offline: could not fetch {url} ({error}); the akmods recipes this repository runs "
            "are checked for secrets on a command line in CI only"
        ) from error


class AkmodsRecipeTests(unittest.TestCase):
    """
    The akmods recipes `ci_tools/` runs in its akmods checkout, under the same rule.

    Those recipes are code this repository runs with its registry token in the environment, and
    #283's tenth site was one of them. They live in Danathar/akmods, so they are read at the ref
    the build would check out.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.recipes = set()
        for path in tracked("ci_tools/*.py"):
            cls.recipes |= just_recipes_run_by((REPO_ROOT / path).read_text(encoding="utf-8"))

    def test_ci_tools_runs_the_akmods_login_recipe(self) -> None:
        """The recipe list is read from `ci_tools/`, so it must still find the one that broke."""

        self.assertIn("login", self.recipes)

    def test_the_recipes_ci_tools_runs_put_no_secret_on_a_command_line(self) -> None:
        text, where = akmods_justfile()
        found = [
            f"{where} Justfile:{line} (recipe {recipe}): {what}"
            for line, recipe, kind, what in justfile_findings(text, sorted(self.recipes))
            if kind != "printf"
        ]
        self.assertEqual(found, [], ADVICE)

    def test_the_login_recipe_pipes_the_token_to_password_stdin(self) -> None:
        """
        Gotcha 6's fourth mechanism, checked where it lives.

        `docs/signing-and-bootc.md` says the akmods `login` recipe hands the token to
        `podman login --password-stdin` through the `printf` builtin.
        """

        text, where = akmods_justfile()
        _, _, body = justfile_recipes(text)["login"]
        script = "\n".join(line for _, line in body)
        self.assertRegex(script, r"printf '%s' \"\$REGISTRY_TOKEN\" \|", where)
        self.assertIn("--password-stdin", script, where)


if __name__ == "__main__":
    unittest.main()
