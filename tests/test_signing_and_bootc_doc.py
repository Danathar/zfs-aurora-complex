"""
Script: tests/test_signing_and_bootc_doc.py
What: Joins docs/signing-and-bootc.md to the tree it describes -- ci_tools/sign_image.py,
ci_tools/promote_stable.py, ci_tools/common.py, files/scripts/configure_signing_policy.py,
build_files/build-image.sh, .github/actions/publish-native-image/action.yml,
.github/workflows/build.yml and the pages that cite this one, including the three files that
cite it by gotcha number.
Doing: Recomputes each claim instead of restating it. The cosign flags come out of
sign_image.py's real argv by AST; the trust-policy JSON and the registries.d YAML come out of
configure_signing_policy.py by running it; the key path is derived from ci/defaults.json's
IMAGE_NAME through the workflow input that builds it; the publication order is read off the
action's step names and promote_stable.main()'s call order; the two credential mechanisms
gotcha 6 names are each checked where they live; and `gotcha 6` is parsed out of every file
that cites it and resolved against this page's numbered list.
Why: No test at any tier opened this file. Repo-wide, `grep -rn signing-and-bootc tests/`
returned four hits, all comments or docstrings, and the end-to-end tier (tests/e2e/) runs
`python3 -m ci_tools.cli` as a subprocess and opens no docs/ file at all. The four
`--cov` paths in .github/workflows/test.yml are ci_tools, shared, containerfiles/zfs-akmods
and files/scripts, so no coverage number at any tier could have shown the hole. At 344 lines
this is the third-largest page in docs/, it is the page README.md, docs/install-and-verify.md,
docs/architecture-overview.md and docs/documentation-guide.md send a reader to for the trust
model, and three files cite one of its numbered gotchas *by number* -- renumbering that list
silently falsifies all three. The page had already drifted twice: its promotion list gave `latest`
before the audit tag, while promote_stable.main() publishes the audit tag first and carries a
comment explaining why that order is the safe one under run cancellation (as did
docs/design-deep-dive.md, which repeats the list); and it said the
publishing action builds the image, which build-native-image does in an earlier step.
Goal: Make the page fail here when the tree moves under it, in both directions.

Parses Markdown, the workflow and action YAML and the shell step bodies by hand. CI installs
pytest, pytest-cov, ruff and pyyaml and nothing else (see .github/workflows/test.yml), and
PyYAML would parse these files correctly -- but a `jobs:`/step scan that is four lines long
does not need it, and keeping the parser here means these assertions cannot be turned into a
skip by an environment. `_sections`, `_fenced_blocks`, `_numbered_items`, `_bash_words`,
`_links` and `_yaml_blocks` are the whole parser and carry their own case table in
`ParserTests` below, because a hand-rolled parser that is never wrong about a fixture is the
only thing keeping the assertions built on it honest.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "signing-and-bootc.md"
README = REPO_ROOT / "README.md"
DOC_GUIDE = REPO_ROOT / "docs" / "documentation-guide.md"
INSTALL_DOC = REPO_ROOT / "docs" / "install-and-verify.md"
ARCHITECTURE = REPO_ROOT / "docs" / "architecture-overview.md"
DESIGN_DOC = REPO_ROOT / "docs" / "design-deep-dive.md"
DEFAULTS = REPO_ROOT / "ci" / "defaults.json"
SIGN_IMAGE = REPO_ROOT / "ci_tools" / "sign_image.py"
PROMOTE_STABLE = REPO_ROOT / "ci_tools" / "promote_stable.py"
COMMON = REPO_ROOT / "ci_tools" / "common.py"
COMMAND_ARGS = REPO_ROOT / "shared" / "command_args.py"
POLICY_SCRIPT = REPO_ROOT / "files" / "scripts" / "configure_signing_policy.py"
BUILD_IMAGE_SH = REPO_ROOT / "build_files" / "build-image.sh"
PUBLISH_ACTION = REPO_ROOT / ".github" / "actions" / "publish-native-image" / "action.yml"
BUILD_ACTION = REPO_ROOT / ".github" / "actions" / "build-native-image" / "action.yml"
BUILD_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build.yml"

# The pages that send a reader here. The page is only reachable because these link it, so a
# rename or a reframing that left one behind would strand the document these tests keep honest.
ENTRY_POINTS = (README, DOC_GUIDE, INSTALL_DOC, ARCHITECTURE)

# The files that cite one of this page's gotchas *by number*. Each is parsed for the number it
# names and the number is resolved against the page, so renumbering the list fails here.
GOTCHA_CITERS = (
    REPO_ROOT / "tests" / "test_common.py",
    REPO_ROOT / "tests" / "test_action_publish_native_image.py",
    REPO_ROOT / "tests" / "test_workflow_build_container.py",
)

HEADING = re.compile(r"^(#{1,6}) (.+)$")
FENCE = re.compile(r"^```([A-Za-z0-9]*)\s*$")
NUMBERED = re.compile(r"^(\d+)\. (.*)$")
LINK = re.compile(r"\[(?:[^\]]*)\]\(([^)]+)\)")
# "gotcha 6" / "Gotcha 6" / "gotcha 6 in ...". The number is what these tests resolve; the
# surrounding wording is deliberately not constrained.
GOTCHA_CITATION = re.compile(r"[Gg]otcha (\d+)")


def _sections(text: str) -> dict[str, list[str]]:
    """Return `{heading text: body lines}` for every `##`-or-deeper heading in one page."""

    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        match = HEADING.match(line)
        if match and len(match.group(1)) >= 2:
            current = match.group(2).strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _fenced_blocks(lines: list[str]) -> list[tuple[str, str]]:
    """Return `(language, body)` for every fenced code block in the given lines."""

    blocks: list[tuple[str, str]] = []
    language: str | None = None
    body: list[str] = []
    for line in lines:
        match = FENCE.match(line)
        if match and language is None:
            language = match.group(1)
            body = []
        elif line.strip() == "```" and language is not None:
            blocks.append((language, "\n".join(body)))
            language = None
        elif language is not None:
            body.append(line)
    return blocks


def _numbered_items(lines: list[str]) -> list[tuple[int, str]]:
    """
    Return `(number, first line of the item)` for every top-level numbered list item.

    Continuation lines and nested bullets are dropped: these tests assert on what each item
    says it is, not on how far its explanation runs.
    """

    items: list[tuple[int, str]] = []
    for line in lines:
        if line[:1].isspace():
            continue
        match = NUMBERED.match(line)
        if match:
            items.append((int(match.group(1)), match.group(2).strip()))
    return items


def _bash_words(block: str) -> list[str]:
    """
    Split one shell code block into words, honoring `\\` line continuations.

    Comment lines are dropped. Quoting is not interpreted: every command in this page is
    written unquoted, and a quoted word here should fail loudly rather than be guessed at.
    """

    joined = block.replace("\\\n", " ")
    words: list[str] = []
    for line in joined.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        words.extend(stripped.split())
    return words


def _links(text: str) -> list[str]:
    """Return every Markdown link target in one page, in order."""

    return LINK.findall(text)


def _yaml_blocks(text: str, *, indent: int) -> dict[str, list[str]]:
    """
    Return `{key: body lines}` for the mapping keys at exactly `indent` spaces.

    Enough to split a workflow's `jobs:` mapping into one block per job, which is all these
    tests need from YAML. Keys inside deeper-indented bodies stay with their parent.
    """

    prefix = " " * indent
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if (
            line.startswith(prefix)
            and not line.startswith(prefix + " ")
            and stripped
            and not stripped.startswith("#")
            and stripped.endswith(":")
        ):
            current = stripped[:-1]
            blocks[current] = []
        elif current is not None:
            blocks[current].append(line)
    return blocks


def _load_module(path: Path, name: str):
    """Import one file by path, for the helper that lives outside any importable package."""

    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _command_argv_literals(source: str, function_name: str, callee: str) -> list[list[str]]:
    """
    Return the constant words of every `callee([...])` list literal inside one function.

    Read from the AST rather than by regex so a flag moved onto another line, or reordered,
    is still seen exactly as the process will receive it. Non-constant elements (a variable
    holding the image reference) are dropped: this page makes claims about flags. A call that
    passes a local name is resolved through the list literal assigned to that name in the
    same function, because `ci_tools/common.py` builds its argv that way.
    """

    tree = ast.parse(source)
    found: list[list[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        bindings: dict[str, ast.List] = {}
        for statement in ast.walk(node):
            if (
                isinstance(statement, ast.Assign)
                and isinstance(statement.value, ast.List)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
            ):
                bindings[statement.targets[0].id] = statement.value
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            name = call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", "")
            if name != callee or not call.args:
                continue
            first = call.args[0]
            if isinstance(first, ast.Name):
                first = bindings.get(first.id)
            if isinstance(first, ast.List):
                found.append(
                    [e.value for e in first.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                )
    return found


def _call_order(source: str, function_name: str, callee: str) -> list[dict[str, str]]:
    """Return the keyword arguments, as source text, of each `callee(...)` call in order."""

    tree = ast.parse(source)
    calls: list[dict[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        for statement in node.body:
            for call in ast.walk(statement):
                if not isinstance(call, ast.Call):
                    continue
                name = call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", "")
                if name == callee:
                    calls.append(
                        {kw.arg: ast.unparse(kw.value) for kw in call.keywords if kw.arg}
                    )
    return calls


def _step_name_lines(text: str) -> list[tuple[int, int, str]]:
    """
    Return `(step_line, name_line, name)` for every named step under `steps:`, in file order.

    A step may open with `- name:` or declare another key first (`- id: push` and then
    `name:` on the next line), so the name is read at the step's own indent either way.
    """

    lines = text.splitlines()
    found: list[tuple[int, int, str]] = []
    in_steps = False
    step_line = step_indent = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped == "steps:":
            in_steps = True
            continue
        if not in_steps or not stripped:
            continue
        if stripped.startswith("- ") and (step_indent is None or indent <= step_indent):
            step_line, step_indent = i, indent
            stripped = stripped[2:]
        elif step_indent is None or indent != step_indent + 2:
            continue
        if stripped.startswith("name:"):
            found.append((step_line, i, stripped.split("name:", 1)[1].strip()))
    return found


def _action_step_names(text: str) -> list[str]:
    """Return the `name:` values of a composite action's steps, in file order."""

    return [name for _, _, name in _step_name_lines(text)]


def _step_run_body(text: str, step_name: str) -> str:
    """
    Return the `run:` block body of one named step, with its YAML comments stripped.

    Scoped to the step so an assertion about what a command does is made against the command
    and not against a comment elsewhere in the file that quotes the flag being forbidden.
    """

    lines = text.splitlines()
    start = next(step for step, _, name in _step_name_lines(text) if name == step_name)
    step_indent = len(lines[start]) - len(lines[start].lstrip())
    body: list[str] = []
    in_run = False
    for line in lines[start + 1 :]:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped and indent <= step_indent:
            break
        if stripped.startswith("run:"):
            in_run = True
            continue
        if in_run and stripped and not stripped.startswith("#"):
            body.append(line)
    return "\n".join(body)


DOC_TEXT = DOC.read_text(encoding="utf-8")
DOC_SECTIONS = _sections(DOC_TEXT)
IMAGE_NAME = json.loads(DEFAULTS.read_text(encoding="utf-8"))["IMAGE_NAME"]


class DocumentShapeTests(unittest.TestCase):
    """The headings the rest of this module resolves claims against."""

    def test_the_page_carries_the_sections_these_tests_resolve_claims_against(self) -> None:
        """
        Every section name used below is asserted here once.

        A renamed heading would otherwise turn a real assertion into a silent lookup of an
        empty list, which is the failure mode this module exists to prevent elsewhere.
        """

        for heading in (
            "Short Version",
            "What Gets Signed",
            "Publication Order",
            "In-Image Trust Policy",
            "Cosign V3 Compatibility",
            "Incident Note: What Went Wrong In April 2026",
            "Operational Checks",
            "Key Rotation",
            "Common Gotchas",
            "References",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, DOC_SECTIONS)

    def test_every_relative_link_in_the_page_resolves_to_a_file(self) -> None:
        """A link this page offers as the next step must land on something that exists."""

        for target in _links(DOC_TEXT):
            if target.startswith(("http://", "https://", "#")):
                continue
            with self.subTest(target=target):
                self.assertTrue((DOC.parent / target.split("#", 1)[0]).is_file())

    def test_the_pages_that_send_a_reader_here_still_link_this_one(self) -> None:
        """
        The page is only reachable through these four.

        Asserted on the link target rather than on prose, so a page that mentions signing
        without linking here does not count.
        """

        for page in ENTRY_POINTS:
            with self.subTest(page=page.name):
                targets = [t.split("#", 1)[0] for t in _links(page.read_text(encoding="utf-8"))]
                self.assertIn(
                    "signing-and-bootc.md",
                    [Path(t).name for t in targets],
                )

    def test_the_documentation_map_lists_this_page_in_its_tree(self) -> None:
        """docs/documentation-guide.md calls itself the map; a map missing a road is wrong."""

        self.assertRegex(
            DOC_GUIDE.read_text(encoding="utf-8"),
            r"(?m)^\s*signing-and-bootc\.md\s+<-",
        )


class GotchaCitationTests(unittest.TestCase):
    """
    Three files cite one of this page's gotchas by number.

    The number is parsed out of each citing file and resolved here, so renumbering the list --
    inserting a gotcha above it, or dropping one -- fails instead of silently retargeting
    three comments at a different rule.
    """

    def setUp(self) -> None:
        self.items = dict(_numbered_items(DOC_SECTIONS["Common Gotchas"]))

    def test_the_gotcha_list_is_contiguous_from_one(self) -> None:
        """A gap in the numbering would make "gotcha N" ambiguous for every citer."""

        self.assertEqual(sorted(self.items), list(range(1, len(self.items) + 1)))

    def test_each_citing_file_names_a_gotcha_that_exists(self) -> None:
        """The number each file cites must be a real item on the list."""

        for citer in GOTCHA_CITERS:
            text = citer.read_text(encoding="utf-8")
            numbers = {
                int(n)
                for n in GOTCHA_CITATION.findall(text)
                if "signing-and-bootc.md" in text
            }
            with self.subTest(citer=citer.name):
                self.assertTrue(numbers, f"{citer.name} cites this page but names no gotcha")
                for number in numbers:
                    self.assertIn(number, self.items)

    def test_every_citation_resolves_to_the_credential_in_argv_rule(self) -> None:
        """
        All three citers are talking about the same rule.

        Each of them exists to keep a secret out of a command line, so the item their number
        resolves to must be the argv rule and not, say, the key-rotation note.
        """

        for citer in GOTCHA_CITERS:
            text = citer.read_text(encoding="utf-8")
            for number in {int(n) for n in GOTCHA_CITATION.findall(text)}:
                with self.subTest(citer=citer.name, gotcha=number):
                    item = self._item_body(number)
                    self.assertIn("credential", item)
                    self.assertIn("argv", item)
                    self.assertIn("/proc/<pid>/cmdline", item)

    def test_the_argv_rule_still_names_the_two_flags_it_forbids(self) -> None:
        """The rule is only enforceable because it names the shapes a token arrives in."""

        item = self._item_body(self._argv_gotcha_number())
        for flag in ("--creds", "--registry-password"):
            with self.subTest(flag=flag):
                self.assertIn(flag, item)

    def test_the_rule_states_the_hole_it_does_not_close(self) -> None:
        """
        The page is explicit that a file-mode fix is a cross-uid fix only.

        Dropping that sentence would turn an accurate boundary into an overclaim, which is
        worse than no sentence: the next reader would believe a same-uid process is shut out.
        """

        item = self._item_body(self._argv_gotcha_number())
        squashed = re.sub(r"\s+", " ", item)
        self.assertIn("cross-uid", squashed)
        self.assertIn("/proc/<pid>/environ", squashed)
        self.assertIn("0444", squashed)
        self.assertIn("0600", squashed)

    def _argv_gotcha_number(self) -> int:
        matches = [n for n, body in self.items.items() if "argv" in body]
        self.assertEqual(len(matches), 1, "exactly one gotcha should be the argv rule")
        return matches[0]

    def _item_body(self, number: int) -> str:
        """Return one numbered item with its continuation lines, which `_numbered_items` drops."""

        lines = DOC_SECTIONS["Common Gotchas"]
        start = next(i for i, line in enumerate(lines) if NUMBERED.match(line) and int(NUMBERED.match(line).group(1)) == number)
        body = [lines[start]]
        for line in lines[start + 1 :]:
            if NUMBERED.match(line) and not line[:1].isspace():
                break
            body.append(line)
        return "\n".join(body)


class ShortVersionTests(unittest.TestCase):
    """The five-step summary at the top, recomputed from the tree it summarizes."""

    def test_the_committed_public_key_is_tracked_and_the_private_key_is_not(self) -> None:
        """Step 1 and gotcha 7 are the same claim, checked against git rather than the disk."""

        tracked = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        self.assertIn("cosign.pub", tracked)
        self.assertNotIn("cosign.key", tracked)
        self.assertIn("cosign.key", (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").split())

    def test_the_secret_the_page_names_is_the_secret_the_workflow_reads(self) -> None:
        """Step 2 names `SIGNING_SECRET`; the publishing workflow must read that same name."""

        workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Actions secret\n   `SIGNING_SECRET`", DOC_TEXT)
        self.assertIn("secrets.SIGNING_SECRET", workflow)
        self.assertIn("COSIGN_PRIVATE_KEY: ${{ secrets.SIGNING_SECRET }}", workflow)

    def test_the_build_installs_the_committed_key_where_the_page_says(self) -> None:
        """Step 4: the key lands under the directory the in-image policy later points at."""

        script = BUILD_IMAGE_SH.read_text(encoding="utf-8")
        self.assertIn('install -m 0644 /ctx/cosign.pub "/etc/pki/containers/${SIGNING_KEY_FILENAME}"', script)
        self.assertIn("/etc/pki/containers/", DOC_SECTIONS["Short Version"][0] + DOC_TEXT)

    def test_the_build_calls_the_policy_helper_the_page_names(self) -> None:
        """Step 5 attributes the two policy files to configure_signing_policy.py."""

        self.assertIn("files/scripts/configure_signing_policy.py", BUILD_IMAGE_SH.read_text(encoding="utf-8"))
        self.assertIn("`files/scripts/configure_signing_policy.py`", DOC_TEXT)

    def test_the_first_switch_command_enforces_the_container_signature_policy(self) -> None:
        """The switch command is the one place the operator opts into enforcement."""

        blocks = _fenced_blocks(DOC_SECTIONS["Short Version"])
        switch = next(body for _, body in blocks if "bootc switch" in body)
        words = _bash_words(switch)
        self.assertIn("--enforce-container-sigpolicy", words)
        self.assertIn(f"ghcr.io/danathar/{IMAGE_NAME}:latest", words)


class TrustPolicyTests(unittest.TestCase):
    """The In-Image Trust Policy section, recomputed by running the helper it describes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.policy_module = _load_module(POLICY_SCRIPT, "configure_signing_policy_under_test")
        cls.image_repo = f"ghcr.io/danathar/{IMAGE_NAME}"
        cls.key_path = Path("/etc/pki/containers") / f"{IMAGE_NAME}.pub"

    def test_the_policy_rule_in_the_page_is_the_rule_the_helper_writes(self) -> None:
        """
        The JSON block is compared to the helper's real output, not to a copy of itself.

        Run through `update_policy` so a changed `signedIdentity` type, or a keyPath that
        stopped being the installed key, fails here.
        """

        written = self.policy_module.update_policy(
            policy_data={},
            image_repo=self.image_repo,
            key_path=self.key_path,
        )
        rule = written["transports"]["docker"][self.image_repo]
        block = next(
            body for language, body in _fenced_blocks(DOC_SECTIONS["In-Image Trust Policy"])
            if language == "json"
        )
        self.assertEqual([json.loads(block)], rule)

    def test_the_discovery_file_in_the_page_is_the_file_the_helper_writes(self) -> None:
        """
        The YAML block is checked against the bytes the helper actually writes.

        The page omits the file's leading comment line on purpose, so containment is the
        assertion; `use-sigstore-attachments: true` is asserted separately because that one
        line is why bootc can find the signature at all.
        """

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "registry.yaml"
            self.policy_module.write_registry_discovery_file(
                image_repo=self.image_repo, registry_file=target
            )
            written = target.read_text(encoding="utf-8")
        block = next(
            body for language, body in _fenced_blocks(DOC_SECTIONS["In-Image Trust Policy"])
            if language == "yaml"
        )
        for line in block.splitlines():
            with self.subTest(line=line):
                self.assertIn(line, written)
        self.assertIn("    use-sigstore-attachments: true\n", written)

    def test_the_helper_defaults_are_the_paths_the_page_names(self) -> None:
        """The prose names three absolute paths; all three are module constants."""

        self.assertEqual(str(self.policy_module.DEFAULT_POLICY_FILE), "/etc/containers/policy.json")
        self.assertEqual(str(self.policy_module.DEFAULT_REGISTRIES_DIR), "/etc/containers/registries.d")
        self.assertEqual(str(self.policy_module.DEFAULT_KEYS_DIR), "/etc/pki/containers")
        for path in ("/etc/containers/policy.json", "/etc/containers/registries.d/", "/etc/pki/containers/"):
            with self.subTest(path=path):
                self.assertIn(path, DOC_TEXT)

    def test_the_key_path_in_the_policy_is_the_filename_the_workflows_build(self) -> None:
        """
        `/etc/pki/containers/zfs-kinoite-complex.pub` is a derived value, so derive it.

        IMAGE_NAME comes from ci/defaults.json, the `.pub` suffix from the workflow input, and
        the directory from the helper's own default. Renaming the image in defaults.json
        without touching this page fails here.
        """

        for workflow in ("build.yml", "build-pr.yml"):
            with self.subTest(workflow=workflow):
                text = (REPO_ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
                self.assertIn("signing_key_filename: ${{ steps.defaults.outputs.image_name }}.pub", text)
        resolved = self.policy_module.key_path_from_env(signing_key_filename=f"{IMAGE_NAME}.pub")
        self.assertEqual(str(resolved), f"/etc/pki/containers/{IMAGE_NAME}.pub")
        self.assertIn(f"/etc/pki/containers/{IMAGE_NAME}.pub", DOC_TEXT)

    def test_the_key_rotation_install_command_targets_the_same_path(self) -> None:
        """Key Rotation tells an operator to install over the exact file the policy reads."""

        words = _bash_words(
            next(body for _, body in _fenced_blocks(DOC_SECTIONS["Key Rotation"]) if "install" in body)
        )
        self.assertEqual(words[:4], ["sudo", "install", "-m", "0644"])
        self.assertEqual(words[-2:], ["cosign.pub", f"/etc/pki/containers/{IMAGE_NAME}.pub"])


class CosignCommandTests(unittest.TestCase):
    """The cosign invocations the page prints, against the argv sign_image.py builds."""

    @classmethod
    def setUpClass(cls) -> None:
        source = SIGN_IMAGE.read_text(encoding="utf-8")
        argvs = _command_argv_literals(source, "sign_published_image", "command_runner")
        cls.sign_argv = next(a for a in argvs if a[:2] == ["cosign", "sign"])
        cls.verify_argv = next(a for a in argvs if a[:2] == ["cosign", "verify"])

    def test_the_sign_command_in_the_page_is_the_command_the_helper_runs(self) -> None:
        """
        Compared word for word, in order, after folding `--key env://...` back together.

        The page's last word is the digest reference, which the helper holds in a variable, so
        it is dropped from both sides rather than guessed at.
        """

        block = next(
            body for _, body in _fenced_blocks(DOC_SECTIONS["Cosign V3 Compatibility"])
            if "cosign sign" in body
        )
        documented = _bash_words(block)
        self.assertTrue(documented[-1].startswith(f"ghcr.io/danathar/{IMAGE_NAME}@sha256:"))
        self.assertEqual(documented[:-1], self.sign_argv)

    def test_the_three_legacy_flags_the_incident_note_names_are_on_the_sign_command(self) -> None:
        """The April 2026 fix is quoted as three flags; all three must still be passed."""

        block = next(
            body for _, body in _fenced_blocks(DOC_SECTIONS["Incident Note: What Went Wrong In April 2026"])
            if "--new-bundle-format" in body
        )
        flags = _bash_words(block)
        self.assertEqual(
            flags,
            ["--new-bundle-format=false", "--use-signing-config=false", "--registry-referrers-mode=legacy"],
        )
        for flag in flags:
            with self.subTest(flag=flag):
                self.assertIn(flag, self.sign_argv)

    def test_the_signing_key_is_read_from_the_environment_not_from_argv(self) -> None:
        """
        `env://COSIGN_PRIVATE_KEY` is the page's own example of gotcha 6 applied to cosign.

        Asserted on the argv the helper builds, so a key path reintroduced here fails even
        though the page would still read correctly.
        """

        self.assertIn("env://COSIGN_PRIVATE_KEY", self.sign_argv)
        self.assertEqual(self.sign_argv[self.sign_argv.index("--key") + 1], "env://COSIGN_PRIVATE_KEY")

    def test_the_helpers_verify_step_carries_the_format_flag_the_page_quotes(self) -> None:
        """The matching verify in the same section is the one sign_image.py runs after signing."""

        block = next(
            body for _, body in _fenced_blocks(DOC_SECTIONS["Cosign V3 Compatibility"])
            if body.strip().startswith("cosign verify")
        )
        self.assertEqual(_bash_words(block)[:4], self.verify_argv[:4])
        self.assertIn("--new-bundle-format=false", self.verify_argv)

    def test_the_shared_verify_helper_deliberately_omits_that_flag(self) -> None:
        """
        `ci_tools/common.py`'s `cosign_verify` is not the command this page quotes.

        It runs where cosign v2.4.1 is preinstalled and does not recognize the flag, and it
        says so. Pinned here so the page's "the matching verify command also uses" is never
        read as a rule about every cosign call in the repository.
        """

        source = COMMON.read_text(encoding="utf-8")
        argvs = _command_argv_literals(source, "cosign_verify", "run_cmd")
        self.assertTrue(argvs, "cosign_verify should build its argv as a list literal")
        for argv in argvs:
            with self.subTest(argv=argv):
                self.assertNotIn("--new-bundle-format=false", argv)
        self.assertIn("Deliberately does not pass `--new-bundle-format=false`", source)

    def test_the_operational_check_verifies_in_the_format_bootc_expects(self) -> None:
        """
        The operator-facing check is compared as a flag set, not in order.

        It is typed by a human against a moving tag, so only the flags it must carry are
        asserted; the sign command above is the one pinned word for word.
        """

        block = next(
            body for _, body in _fenced_blocks(DOC_SECTIONS["Operational Checks"])
            if "cosign verify" in body
        )
        words = _bash_words(block)
        self.assertEqual(words[:2], ["cosign", "verify"])
        self.assertIn("--new-bundle-format=false", words)
        self.assertIn("--key", words)
        self.assertEqual(words[-1], f"ghcr.io/danathar/{IMAGE_NAME}:latest")

    def test_the_legacy_attachment_tag_shape_matches_the_command_that_derives_it(self) -> None:
        """
        `sha256-<digest-without-colon>.sig` is derived in the page's own shell snippet.

        The prose and the snippet are two statements of one fact, so they are joined instead
        of both being trusted.
        """

        self.assertIn("sha256-<digest-without-colon>.sig", DOC_TEXT)
        block = next(
            body for _, body in _fenced_blocks(DOC_SECTIONS["Operational Checks"])
            if "sig_tag=" in body
        )
        self.assertIn('sig_tag="sha256-${digest#sha256:}.sig"', block)


class PublicationOrderTests(unittest.TestCase):
    """The order claims, read off the action's steps and promote_stable.main()'s call order."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.action_text = PUBLISH_ACTION.read_text(encoding="utf-8")
        cls.step_names = _action_step_names(cls.action_text)
        cls.items = _numbered_items(DOC_SECTIONS["Publication Order"])

    def test_the_publishing_action_does_not_build_the_image(self) -> None:
        """
        The build happens in a different action, and the page must not attribute it here.

        `buildah tag` is the publishing action's only buildah call; `buildah build` belongs to
        `.github/actions/build-native-image/action.yml`. This is the claim the page used to
        get wrong by opening its list with "build the local image".
        """

        self.assertNotIn("buildah build", self.action_text)
        self.assertIn("buildah build", BUILD_ACTION.read_text(encoding="utf-8"))
        publish_items = [text for number, text in self.items if number <= len(self.step_names)]
        self.assertFalse(
            [text for text in publish_items if re.match(r"builds? the local image", text)],
            "the publishing action's list must not open by claiming it builds",
        )

    def test_the_listed_publication_steps_are_the_actions_steps_in_order(self) -> None:
        """
        Retag, push and promote are named steps; the page lists them in the same order.

        Matched on the step names the action declares, so renaming a step without touching
        the page fails here.
        """

        build = BUILD_WORKFLOW.read_text(encoding="utf-8")
        signing_job = build.split("\n  sign-candidate-image:\n", 1)[1]
        signing_job = re.split(r"\n  [a-z][\w-]*:\n", signing_job, maxsplit=1)[0]
        signing_steps = re.findall(r"^      - name: (.+)$", signing_job, re.MULTILINE)
        for names, earlier, later in (
            (self.step_names, "Retag local image for transient publication", "Push transient tag with podman"),
            (signing_steps, "Confirm the pushed digest", "Sign candidate image"),
            (signing_steps, "Sign candidate image", "Point the candidate tag at the signed digest"),
        ):
            with self.subTest(earlier=earlier, later=later):
                self.assertIn(earlier, names)
                self.assertIn(later, names)
                self.assertLess(names.index(earlier), names.index(later))
        documented = [text for _, text in self.items]
        retag = next(i for i, text in enumerate(documented) if text.startswith("retags it"))
        push = next(i for i, text in enumerate(documented) if text.startswith("pushes only"))
        confirm = next(i for i, text in enumerate(documented) if text.startswith("confirms the transient tag"))
        sign = next(i for i, text in enumerate(documented) if text.startswith("signs that digest"))
        promote = next(i for i, text in enumerate(documented) if text.startswith("copies the same digest"))
        self.assertEqual([retag, push, confirm, sign, promote], sorted([retag, push, confirm, sign, promote]))

    def test_the_transient_tag_shape_is_the_one_the_action_builds(self) -> None:
        """
        `candidate-<sha>-<fedora>-unsigned-<run_id>` is two derivations joined.

        The `candidate-<sha>-<fedora>` half comes from `tagging_context.build_candidate_tag`
        and the `-unsigned-<run_id>` half from the action's env, so both are recomputed.
        """

        self.assertIn("candidate-<sha>-<fedora>-unsigned-<run_id>", DOC_TEXT)
        self.assertIn("${{ inputs.image_tag }}-unsigned-${{ github.run_id }}", self.action_text)
        tagging = _load_module(REPO_ROOT / "ci_tools" / "tagging_context.py", "tagging_context_under_test")
        self.assertEqual(
            tagging.build_candidate_tag(github_sha="deadbeefcafe", fedora_version="43"),
            "candidate-deadbee-43",
        )

    def test_the_page_promotes_the_audit_tag_before_latest(self) -> None:
        """
        The order is read off promote_stable.main()'s two copy calls, not off the page.

        `main()` publishes the immutable audit tag first on purpose -- build.yml cancels
        in-progress runs, so an audit record with no `latest` move is the safe way to be
        interrupted. A page that lists `latest` first tells the next reader the opposite.
        """

        calls = _call_order(PROMOTE_STABLE.read_text(encoding="utf-8"), "main", "_copy_and_verify_digest")
        self.assertEqual([c["destination_ref"] for c in calls], ["audit_ref", "stable_ref"])
        promote_items = [text for _, text in self.items if "`" in text]
        audit = next(i for i, text in enumerate(promote_items) if "stable-<run>-<sha>" in text)
        latest = next(i for i, text in enumerate(promote_items) if text.strip("`") == "latest")
        self.assertLess(audit, latest, "the page must list the audit tag before `latest`")

    def test_the_design_page_promotes_in_the_same_order(self) -> None:
        """
        docs/design-deep-dive.md repeats the promotion list, so it is held to the same order.

        Both pages gave `latest` first while promote_stable.main() wrote the audit tag first.
        """

        section = _sections(DESIGN_DOC.read_text(encoding="utf-8"))["6. Promote Candidate To Stable"]
        items = [text for _, text in _numbered_items(section)]
        self.assertEqual(items, ["`stable-<run>-<sha>`", "`latest`"])

    def test_the_audit_tag_shape_is_the_one_promote_stable_builds(self) -> None:
        """`stable-<run>-<sha>` is a real f-string in the helper, with the sha truncated to 7."""

        source = PROMOTE_STABLE.read_text(encoding="utf-8")
        self.assertIn('stable-{run_number}-{sha_short}', source)
        self.assertIn("sha_short = github_sha[:7]", source)
        self.assertIn("stable-<run>-<sha>", DOC_TEXT)

    def test_promotion_verifies_the_candidate_signature_before_any_tag_moves(self) -> None:
        """
        "Promotion copies the already-signed candidate digest" is only true fail-closed.

        Asserted on statement order inside `main()`: the signature check must precede both
        copies, or the sentence describes a promotion that can publish unsigned content.
        """

        source = PROMOTE_STABLE.read_text(encoding="utf-8")
        verify_at = source.index("verify_candidate_signature(\n")
        first_copy_at = source.index("_copy_and_verify_digest(\n")
        self.assertLess(verify_at, first_copy_at)
        self.assertIn("promotion does not need to sign `latest` a second time", DOC_TEXT)

    def test_the_page_states_the_failure_mode_the_ordering_buys(self) -> None:
        """The reason the transient tag exists at all is the sentence that closes the section."""

        squashed = re.sub(r"\s+", " ", "\n".join(DOC_SECTIONS["Publication Order"]))
        self.assertIn("If the sign step fails, the user-facing candidate tag and `latest` do not move.", squashed)


class CredentialMechanismTests(unittest.TestCase):
    """Gotcha 6 names two ways a job satisfies it. Each is checked where it lives."""

    def setUp(self) -> None:
        self.jobs = _yaml_blocks(
            "\n".join(_yaml_blocks(BUILD_WORKFLOW.read_text(encoding="utf-8"), indent=0)["jobs"]),
            indent=2,
        )

    def test_the_three_jobs_the_rule_names_authenticate_with_a_job_level_login(self) -> None:
        """
        `sign-akmods-cache`, `sign-candidate-image` and `promote-stable` are named by the page
        as login-action jobs.

        The job bodies are split out of build.yml so a login-action step somewhere else in the
        file cannot satisfy this.
        """

        item = re.sub(r"\s+", " ", "\n".join(DOC_SECTIONS["Common Gotchas"]))
        self.assertIn("what `sign-akmods-cache`, `sign-candidate-image` and `promote-stable` do", item)
        for job in ("sign-akmods-cache", "sign-candidate-image", "promote-stable"):
            with self.subTest(job=job):
                self.assertIn(job, self.jobs)
                self.assertIn("docker/login-action", "\n".join(self.jobs[job]))

    def test_the_podman_push_job_has_no_job_level_login(self) -> None:
        """
        The page says the auth file is the push's only credential because its job never logs in.

        If `build-candidate-image` gained a `docker/login-action` step, a broken auth file would
        no longer be the only thing between the push and the registry, and the sentence would
        be false.
        """

        self.assertIn("That job has no `docker/login-action` login", re.sub(r"\s+", " ", DOC_TEXT))
        self.assertIn("./.github/actions/publish-native-image", "\n".join(self.jobs["build-candidate-image"]))
        self.assertNotIn("docker/login-action", "\n".join(self.jobs["build-candidate-image"]))

    def test_the_auth_file_helper_writes_a_private_docker_config(self) -> None:
        """
        `registry_auth_dir` is the page's second mechanism; 0600 is the whole point of it.

        Read as source rather than executed, because the assertion is about the mode the file
        is created with, which a later chmod would not reproduce.
        """

        source = COMMON.read_text(encoding="utf-8")
        self.assertIn("def registry_auth_dir(", source)
        self.assertRegex(source, r"os\.open\(\s*registry_auth_file\(auth_dir\),[^)]*0o600")

    def test_skopeo_and_cosign_read_the_credential_the_way_the_page_says(self) -> None:
        """Three skopeo flags and one cosign environment variable, each named by the page."""

        source = COMMON.read_text(encoding="utf-8")
        self.assertIn('command.extend(["--authfile", registry_auth_file(auth_dir)])', source)
        self.assertIn('["--src-authfile", auth_file, "--dest-authfile", auth_file]', source)
        self.assertIn('env={"DOCKER_CONFIG": auth_dir}', source)
        for token in ("--authfile", "--src-authfile", "--dest-authfile", "DOCKER_CONFIG"):
            with self.subTest(token=token):
                self.assertIn(token, DOC_TEXT)

    def test_the_podman_push_passes_an_auth_file_and_no_credential_in_argv(self) -> None:
        """
        The page singles out this step as the one that writes the same shape inline in bash.

        Checked on the push step's own `run:` body, comments stripped: `--authfile` present,
        `--creds` absent, and the credential handed to `base64` on stdin rather than on a
        command line. Scoped to the body because the step's comment quotes `--creds` while
        explaining why it is not used.
        """

        body = _step_run_body(PUBLISH_ACTION.read_text(encoding="utf-8"), "Push transient tag with podman")
        self.assertIn("--authfile", body)
        self.assertNotIn("--creds", body)
        self.assertNotIn("${REGISTRY_PASSWORD}\"\n", body.split("podman push", 1)[1])
        self.assertIn('encoded_credential="$(printf \'%s\' "${REGISTRY_USER}:${REGISTRY_PASSWORD}" | base64 -w0)"', body)

    def test_the_backstop_the_page_refuses_to_count_as_enforcement_exists(self) -> None:
        """
        `redact_command_args` is named as an error-text backstop, not as a way to satisfy
        the rule. It has to exist for that sentence to mean anything.
        """

        self.assertIn("def redact_command_args(", COMMAND_ARGS.read_text(encoding="utf-8"))
        self.assertIn(
            "`redact_command_args` in `shared/command_args.py` is a backstop",
            re.sub(r"\s+", " ", DOC_TEXT),
        )


class ParserTests(unittest.TestCase):
    """The hand-rolled parser every assertion above is built on, against fixtures."""

    def test_sections_splits_on_headings_of_depth_two_or_more(self) -> None:
        text = textwrap.dedent(
            """\
            # Title
            intro
            ## One
            a
            ### Two
            b
            """
        )
        self.assertEqual(_sections(text), {"One": ["a"], "Two": ["b"]})

    def test_fenced_blocks_keeps_the_language_and_drops_the_fences(self) -> None:
        lines = ["```json", "{}", "```", "text", "```", "bare", "```"]
        self.assertEqual(_fenced_blocks(lines), [("json", "{}"), ("", "bare")])

    def test_numbered_items_ignores_indented_continuations(self) -> None:
        lines = ["1. first", "   still first", "2. second", "   - nested"]
        self.assertEqual(_numbered_items(lines), [(1, "first"), (2, "second")])

    def test_bash_words_joins_continuations_and_drops_comments(self) -> None:
        block = "# note\ncosign sign \\\n  --yes \\\n  ref"
        self.assertEqual(_bash_words(block), ["cosign", "sign", "--yes", "ref"])

    def test_links_returns_targets_in_order(self) -> None:
        self.assertEqual(_links("see [a](./a.md) and [b](https://x/y)"), ["./a.md", "https://x/y"])

    def test_yaml_blocks_splits_only_on_keys_at_the_given_indent(self) -> None:
        text = "jobs:\n  one:\n    steps:\n      - uses: x\n  two:\n    steps: []\n"
        blocks = _yaml_blocks("\n".join(_yaml_blocks(text, indent=0)["jobs"]), indent=2)
        self.assertEqual(sorted(blocks), ["one", "two"])
        self.assertIn("      - uses: x", blocks["one"])

    def test_command_argv_literals_reads_only_the_named_callee(self) -> None:
        source = textwrap.dedent(
            """\
            def f():
                other(["no"])
                runner(["cosign", "sign", ref], timeout=1)
            """
        )
        self.assertEqual(_command_argv_literals(source, "f", "runner"), [["cosign", "sign"]])

    def test_call_order_preserves_statement_order(self) -> None:
        source = "def main():\n    copy(dest='a')\n    copy(dest='b')\n"
        self.assertEqual([c["dest"] for c in _call_order(source, "main", "copy")], ["'a'", "'b'"])

    def test_action_step_names_reads_names_in_file_order(self) -> None:
        text = "runs:\n  steps:\n    - name: One\n    - uses: x\n    - name: Two\n"
        self.assertEqual(_action_step_names(text), ["One", "Two"])

    def test_action_step_names_reads_a_name_declared_after_another_key(self) -> None:
        text = "runs:\n  steps:\n    - id: one\n      name: One\n      with:\n        name: nested\n    - name: Two\n"
        self.assertEqual(_action_step_names(text), ["One", "Two"])

    def test_command_argv_literals_resolves_a_local_name(self) -> None:
        source = "def f():\n    command = ['cosign', 'verify', key]\n    run(command, timeout=1)\n"
        self.assertEqual(_command_argv_literals(source, "f", "run"), [["cosign", "verify"]])

    def test_step_run_body_stops_at_the_next_step_and_drops_comments(self) -> None:
        text = textwrap.dedent(
            """\
            runs:
              steps:
                - name: One
                  shell: bash
                  run: |
                    # a comment
                    echo one
                - name: Two
                  run: |
                    echo two
            """
        )
        self.assertEqual(_step_run_body(text, "One").strip(), "echo one")
        self.assertEqual(_step_run_body(text, "Two").strip(), "echo two")

    def test_step_run_body_finds_a_step_whose_name_follows_its_id(self) -> None:
        text = textwrap.dedent(
            """\
            runs:
              steps:
                - id: one
                  name: One
                  run: |
                    echo one
                - name: Two
                  run: |
                    echo two
            """
        )
        self.assertEqual(_step_run_body(text, "One").strip(), "echo one")


if __name__ == "__main__":
    unittest.main()
