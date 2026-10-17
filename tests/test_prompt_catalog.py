"""
Script: tests/test_prompt_catalog.py
What: Joins `.github/prompts/*.prompt.md` to the code, workflow and agent-doc facts each runbook asserts.
Doing: Extracts every quoted guard message, lock-file field, manifest key, job name and rule citation from the prompts, and resolves each against the tracked tree.
Why: These are operator procedures followed during an incident, and every fact in them is a hand-copy of something that lives somewhere else.
Goal: Make a renamed guard message, a renumbered AGENTS.md rule, a new lock-file field or a moved signing job fail here instead of misleading whoever is holding the pager.

Nothing in this repository opened these files before, and a link resolving says
nothing about whether the guard message quoted next to it is still the string the
code raises.

The drift here is worse than ordinary doc drift because of when it is read.
`diagnose-build-failure.prompt.md` is opened when a production build is red and
its whole value is the table mapping a refusal message to "upstream problem" or
"escalate immediately, `:latest` has already moved". A message that was reworded
in `ci_tools/` does not make that row wrong-looking; it makes the row
unfindable, and the reader concludes the guard is one the runbook does not
cover. `replay-a-build.prompt.md` is worse still: it tells an operator which
fields to copy into `ci/inputs.lock.json`, and a field the table omits is a
field left at its default, which is the one outcome a replay must not have.

So each check below is a join, not a spell-check:

  * Every message in the diagnose table is matched against the `CiToolError`
    strings `ci_tools/` actually raises, reconstructed with `ast` so an f-string
    split across five source lines compares as one message.
  * Every lock field the replay table names is checked against the real keys of
    `ci/inputs.lock.json`, in both directions. The reverse direction is the
    valuable one: a field added to the lock file that the runbook never tells
    anyone to fill in silently falls back to a default.
  * Every `inputs.<name>` the same table reads from the `build-inputs` artifact
    is checked against the dict `ci_tools/write_build_inputs_manifest.py` emits.
  * Every "AGENTS.md section 0 rule N" citation is resolved against the rules
    that file numbers, with an anchor word per cited rule so a renumbering that
    keeps the count cannot pass.
  * The seven safety-critical paths `review-safety-critical-change.prompt.md`
    lists are joined to rule 2 itself, since the prompt is a copy of that list.

No PyYAML: the CI job installs pytest, pytest-cov and ruff and nothing else, so
an optional import would skip silently the day that changed. The workflow facts needed here are
job-scoped rather than deep -- which job declares an environment, which job uses
an action -- so the file scans `build.yml` with an indentation-aware reader that
attributes each line to its job, and asserts set equality rather than presence.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPT_DIR = REPO_ROOT / ".github" / "prompts"
CATALOG = PROMPT_DIR / "README.md"
DIAGNOSE = PROMPT_DIR / "diagnose-build-failure.prompt.md"
REPLAY = PROMPT_DIR / "replay-a-build.prompt.md"
REVIEW = PROMPT_DIR / "review-safety-critical-change.prompt.md"

CI_TOOLS = REPO_ROOT / "ci_tools"
BUILD_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build.yml"
LOCK_FILE = REPO_ROOT / "ci" / "inputs.lock.json"
DEFAULTS_FILE = REPO_ROOT / "ci" / "defaults.json"
PROMOTE_STABLE = CI_TOOLS / "promote_stable.py"
CHECK_AKMODS_CACHE = CI_TOOLS / "check_akmods_cache.py"
MANIFEST_WRITER = CI_TOOLS / "write_build_inputs_manifest.py"
AGENTS = REPO_ROOT / "AGENTS.md"

BACKTICKED_RE = re.compile(r"`([^`]+)`")
RULE_CITATION_RE = re.compile(r"AGENTS\.md section 0\s+rule\s+(\d+)", re.DOTALL)
# The prompts wrap, so a citation can straddle a newline: "section 0 rule\n2".
WHITESPACE_RE = re.compile(r"\s+")

# Rule numbers the prompts cite, mapped to a word that must appear in the rule
# body. Without the anchor a renumbering that preserved the count -- inserting a
# rule, pushing every later one down by one -- would still resolve.
CITED_RULES = {
    1: "weaken",
    2: "safety-critical",
    3: "drifted",
    4: "green",
    6: "promote",
}

# Section 0 is the only numbered list this file reads, and it ends where the
# next heading begins.
SECTION_0_START = "## 0. This repository is in production"


def tracked_files() -> list[str]:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.split()


def collapse(text: str) -> str:
    """Whitespace-insensitive form, so a wrapped message compares as one line."""

    return WHITESPACE_RE.sub(" ", text).strip()


def message_pattern(quoted: str) -> re.Pattern[str]:
    """A message as a prompt quotes it -> a regex for the message as code raises it.

    The prompts write the variable parts as `<ref>` or elide a run of them with
    `...`. Everything between those is literal and has to match exactly; the
    placeholders become `.*`, because `<v>` in the prompt is an interpolated
    value whose width nothing here knows.
    """

    parts = [part.strip() for part in re.split(r"<[^>]*>|\.\.\.", collapse(quoted))]
    return re.compile(".*".join(re.escape(part) for part in parts if part))


def ci_tool_error_messages() -> list[str]:
    """Every string `ci_tools/` builds, with interpolations replaced by a gap.

    Parsed rather than grepped. `check_akmods_cache.py` raises one refusal whose
    text is six adjacent literals spanning as many lines, three of them
    f-strings; read as text, no line of it contains the sentence the runbook
    quotes.
    """

    messages: list[str] = []
    for path in sorted(CI_TOOLS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                messages.append(collapse(node.value))
            elif isinstance(node, ast.JoinedStr):
                rendered = "".join(
                    part.value if isinstance(part, ast.Constant) else "\x00"
                    for part in node.values
                )
                messages.append(collapse(rendered.replace("\x00", " ")))
    return messages


def raised_ci_tool_errors(path: Path) -> list[str]:
    """The message of every `raise CiToolError(...)` in one module, gaps for values.

    Only the first argument of the raise, rendered the way
    `ci_tool_error_messages` renders a string, so the result can be matched
    with `message_pattern` against a row the runbook quotes. A raise of
    `CiToolError(str(exc))` is skipped: it passes on another error's text and
    has no message of its own for a row to quote.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    messages = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
            continue
        call = node.exc
        if not (isinstance(call.func, ast.Name) and call.func.id == "CiToolError" and call.args):
            continue
        arg = call.args[0]
        if (
            isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Name)
            and arg.func.id == "str"
            and len(arg.args) == 1
            and isinstance(arg.args[0], ast.Name)
        ):
            continue
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            messages.append(collapse(arg.value))
        elif isinstance(arg, ast.JoinedStr):
            rendered = "".join(
                part.value if isinstance(part, ast.Constant) else " " for part in arg.values
            )
            messages.append(collapse(rendered))
        else:
            raise TypeError(f"{path.name}: a CiToolError message is not a string literal")
    return messages


def table_rows(text: str) -> list[list[str]]:
    """The cells of every markdown table row in `text`, header rows included."""

    rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(set(cell) <= set("-: ") for cell in cells):
            continue  # the `| --- |` separator
        rows.append(cells)
    return rows


def section(text: str, heading: str) -> str:
    """The body of the `## `-level section whose title starts with `heading`."""

    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"## {heading}"))
    body = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body)


def frontmatter(path: Path) -> dict[str, str]:
    """The `key: value` pairs of a prompt file's YAML frontmatter block.

    Flat `key: value` only, which is all these files carry. A nested or
    multi-line value would land in the dict as raw text and fail the assertions
    below rather than being quietly accepted.
    """

    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def workflow_jobs(path: Path) -> dict[str, list[str]]:
    """{job id: [line, ...]} for a workflow, by indentation.

    A job id is the only key at two spaces under the top-level `jobs:` mapping.
    Steps and their bodies are indented further, so everything up to the next
    two-space key belongs to the job that opened the block.
    """

    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.rstrip() == "jobs:")
    jobs: dict[str, list[str]] = {}
    current = None
    job_key = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
    for line in lines[start + 1 :]:
        if line and not line.startswith(" ") and not line.startswith("#"):
            break  # a top-level key after `jobs:` ends the mapping
        match = job_key.match(line)
        if match:
            current = match.group(1)
            jobs[current] = []
        elif current is not None:
            jobs[current].append(line)
    return jobs


def job_uses(jobs: dict[str, list[str]], action: str) -> set[str]:
    return {
        job
        for job, body in jobs.items()
        if any(line.strip().endswith(f"uses: ./.github/actions/{action}") for line in body)
    }


class CatalogTests(unittest.TestCase):
    """README.md is the index Copilot users read; it is hand-maintained."""

    def setUp(self) -> None:
        self.text = CATALOG.read_text(encoding="utf-8")
        self.prompts = sorted(PROMPT_DIR.glob("*.prompt.md"))

    def test_the_catalog_finds_prompts_to_check(self) -> None:
        # Guards every other test in this class: a rename of the suffix would
        # otherwise empty the set and pass everything vacuously.
        self.assertGreaterEqual(len(self.prompts), 3, "no *.prompt.md files were found")

    def test_every_prompt_file_is_listed_in_the_catalog_table(self) -> None:
        listed = {
            name
            for row in table_rows(self.text)
            for cell in row[:1]
            for name in BACKTICKED_RE.findall(cell)
        }
        self.assertEqual(
            {path.name for path in self.prompts},
            listed,
            "the catalog table and .github/prompts/ disagree about which prompts exist",
        )

    def test_every_catalog_link_names_a_file_that_exists(self) -> None:
        for target in re.findall(r"\]\(([^)\s#]+)\)", self.text):
            with self.subTest(target=target):
                self.assertTrue(
                    (CATALOG.parent / target).resolve().exists(),
                    f"catalog links to {target}, which does not exist",
                )

    def test_no_prompt_hides_behind_a_plain_md_suffix(self) -> None:
        # The catalog states the reason: Copilot discovers prompts by the
        # `.prompt.md` extension, so a plain `.md` here is invisible to it.
        strays = [
            path.name
            for path in PROMPT_DIR.glob("*.md")
            if path.name != "README.md" and not path.name.endswith(".prompt.md")
        ]
        self.assertEqual([], strays, f"prompt files Copilot cannot discover: {strays}")

    def test_every_prompt_carries_the_frontmatter_copilot_needs(self) -> None:
        for path in self.prompts:
            with self.subTest(prompt=path.name):
                fields = frontmatter(path)
                self.assertIn("description", fields, f"{path.name} has no description")
                self.assertTrue(fields["description"], f"{path.name} description is empty")
                self.assertEqual(
                    "agent",
                    fields.get("mode"),
                    f"{path.name} is not in agent mode; the catalog says every prompt is",
                )


class AgentDocCitationTests(unittest.TestCase):
    """The prompts defer to AGENTS.md section 0 by rule number."""

    def setUp(self) -> None:
        text = AGENTS.read_text(encoding="utf-8")
        body = text.split(SECTION_0_START, 1)[1]
        body = body.split("\n## ", 1)[0]
        self.rules: dict[int, str] = {}
        current = None
        for line in body.splitlines():
            match = re.match(r"^(\d+)\. (.*)$", line)
            if match:
                current = int(match.group(1))
                self.rules[current] = match.group(2)
            elif current is not None and line.startswith("   "):
                self.rules[current] += " " + line.strip()
            elif current is not None and not line.strip():
                continue
            else:
                current = None

    def test_section_0_parses_into_a_contiguous_rule_list(self) -> None:
        self.assertEqual(
            list(range(1, len(self.rules) + 1)),
            sorted(self.rules),
            f"AGENTS.md section 0 rule numbers are not 1..N: {sorted(self.rules)}",
        )

    def test_every_rule_the_prompts_cite_exists_and_still_says_what_they_assume(self) -> None:
        cited = set()
        for path in (DIAGNOSE, REPLAY, REVIEW):
            text = collapse(path.read_text(encoding="utf-8"))
            cited.update(int(number) for number in RULE_CITATION_RE.findall(text))
        self.assertTrue(cited, "no AGENTS.md rule citations were found in the prompts")
        for number in sorted(cited):
            with self.subTest(rule=number):
                self.assertIn(number, self.rules, f"prompts cite rule {number}, which does not exist")
                anchor = CITED_RULES.get(number)
                self.assertIsNotNone(
                    anchor,
                    f"prompts cite rule {number}; add its anchor word to CITED_RULES",
                )
                self.assertIn(
                    anchor,
                    self.rules[number].lower(),
                    f"AGENTS.md rule {number} no longer discusses {anchor!r}; "
                    "the prompts citing it by number are now pointing at a different rule",
                )


class SafetyCriticalListTests(unittest.TestCase):
    """The review prompt retypes rule 2's seven paths."""

    def setUp(self) -> None:
        self.text = REVIEW.read_text(encoding="utf-8")
        self.tracked = tracked_files()

    def prompt_paths(self) -> list[str]:
        """The bullet list in the preamble, before the first numbered step.

        Only the preamble: step 1 lists `|| true` and `continue-on-error` as
        bullets too, and those are not paths. Reading the whole file and
        filtering for path-shaped tokens would pass either way, which is the
        weaker test -- a path moved out of the preamble into a later section
        would stop being the list rule 2 is compared against.
        """

        preamble = self.text.split("\n## ", 1)[0]
        listed = []
        for line in preamble.splitlines():
            if line.startswith("- `"):
                listed.extend(BACKTICKED_RE.findall(line))
        return listed

    def rule_2_paths(self) -> list[str]:
        text = AGENTS.read_text(encoding="utf-8")
        body = text.split(SECTION_0_START, 1)[1]
        start = body.index("2. **Treat the build, promotion, and signing path as safety-critical")
        rule = body[start : body.index("\n3. ", start)]
        return [
            token
            for token in BACKTICKED_RE.findall(rule)
            if "/" in token or token.endswith((".py", ".yml"))
        ]

    def resolve(self, name: str) -> str:
        """A path as a doc writes it -> the path as the tree stores it.

        Rule 2 says `build.yml` and the prompt says
        `.github/workflows/build.yml`; both have to land on the same tracked
        file for the comparison to mean anything. A directory (the composite
        action) resolves through its `action.yml`.
        """

        if name in self.tracked:
            return name
        matches = {
            path for path in self.tracked if path == name or path.startswith(f"{name}/")
        }
        matches |= {path for path in self.tracked if path.endswith(f"/{name}")}
        self.assertTrue(matches, f"{name} names nothing in the tracked tree")
        prefixes = {path.split("/action.yml")[0] for path in matches}
        self.assertEqual(
            1,
            len(prefixes),
            f"{name} is ambiguous in the tracked tree: {sorted(prefixes)}",
        )
        return prefixes.pop()

    def test_the_prompt_lists_the_same_seven_files_as_rule_2(self) -> None:
        prompt = self.prompt_paths()
        self.assertEqual(7, len(prompt), f"the prompt lists {len(prompt)} paths: {prompt}")
        self.assertEqual(
            sorted(self.resolve(name) for name in self.rule_2_paths()),
            sorted(self.resolve(name) for name in prompt),
            "review-safety-critical-change.prompt.md and AGENTS.md rule 2 "
            "disagree about which files are safety-critical",
        )


class DiagnoseGuardTableTests(unittest.TestCase):
    """Step 3's table maps a refusal message to what to do about it."""

    def setUp(self) -> None:
        self.text = DIAGNOSE.read_text(encoding="utf-8")
        self.messages = ci_tool_error_messages()

    def quoted_guard_messages(self) -> list[str]:
        rows = table_rows(section(self.text, "3. Match the message to the guard"))
        quoted = []
        for row in rows[1:]:  # row 0 is the header
            quoted.extend(BACKTICKED_RE.findall(row[0]))
        return quoted

    def test_the_guard_table_has_rows_to_check(self) -> None:
        self.assertGreaterEqual(len(self.quoted_guard_messages()), 6)

    def test_every_guard_message_in_the_table_is_one_the_code_raises(self) -> None:
        for quoted in self.quoted_guard_messages():
            with self.subTest(message=quoted):
                pattern = message_pattern(quoted)
                self.assertTrue(
                    any(pattern.search(message) for message in self.messages),
                    f"no string in ci_tools/ matches the runbook's {quoted!r}; "
                    "a reworded guard leaves this row unfindable during an incident",
                )

    def test_the_zfs_label_the_table_names_is_the_one_the_guard_writes(self) -> None:
        self.assertTrue(
            any("org.zfs-aurora-complex.zfs-version=" in message for message in self.messages),
            "the kmod-zfs row explains the refusal in terms of a label the code no longer names",
        )


class DiagnosePromotionRefusalTests(unittest.TestCase):
    """The reverse join, for the one module that moves `:latest`.

    `DiagnoseGuardTableTests` checks that every row the runbook quotes is a
    message the code still raises. Nothing checked the other direction, so a
    new refusal in `promote_stable.py` could ship with no row at all. That is
    the module whose refusals carry the most consequence: a digest mismatch on
    `:latest` means the tag has already moved. A reader who finds no row for the
    message on a red promote job has to guess which.
    """

    def setUp(self) -> None:
        text = DIAGNOSE.read_text(encoding="utf-8")
        rows = table_rows(section(text, "3. Match the message to the guard"))
        self.patterns = [
            message_pattern(quoted) for row in rows[1:] for quoted in BACKTICKED_RE.findall(row[0])
        ]
        self.refusals = raised_ci_tool_errors(PROMOTE_STABLE)

    def test_the_promote_module_has_refusals_to_check(self) -> None:
        # The digest mismatch and the missing verification key.
        self.assertGreaterEqual(len(self.refusals), 2)
        self.assertTrue(any(r.startswith("Promoted digest mismatch") for r in self.refusals))

    def test_every_promotion_refusal_has_a_row_in_the_guard_table(self) -> None:
        for refusal in self.refusals:
            with self.subTest(refusal=refusal):
                self.assertTrue(
                    any(pattern.search(refusal) for pattern in self.patterns),
                    f"promote_stable.py raises {refusal!r} but step 3 of "
                    "diagnose-build-failure.prompt.md has no row for it",
                )


class DiagnoseAkmodsCacheRefusalTests(unittest.TestCase):
    """The same reverse join, for the check that guards the akmods cache.

    Step 3 quotes the kmod-zfs refusal from `check_akmods_cache.py`, so a
    reader matching a red "Verify the rebuilt cache matches the resolved ZFS
    version" step looks there first. A refusal added to that module without a
    row sends the reader to guess.
    """

    def setUp(self) -> None:
        text = DIAGNOSE.read_text(encoding="utf-8")
        self.rows = table_rows(section(text, "3. Match the message to the guard"))
        self.patterns = [
            message_pattern(quoted)
            for row in self.rows[1:]
            for quoted in BACKTICKED_RE.findall(row[0])
        ]
        self.refusals = raised_ci_tool_errors(CHECK_AKMODS_CACHE)

    def test_the_cache_module_has_refusals_to_check(self) -> None:
        # The missing digest and the kmod-zfs mismatch. The `str(exc)` re-raise of an
        # unpack failure carries no text of its own and is not counted.
        self.assertGreaterEqual(len(self.refusals), 2)
        self.assertTrue(any("does not provide a kmod-zfs" in r for r in self.refusals))

    def test_every_cache_refusal_has_a_row_in_the_guard_table(self) -> None:
        for refusal in self.refusals:
            with self.subTest(refusal=refusal):
                self.assertTrue(
                    any(pattern.search(refusal) for pattern in self.patterns),
                    f"check_akmods_cache.py raises {refusal!r} but step 3 of "
                    "diagnose-build-failure.prompt.md has no row for it",
                )


class DiagnoseWorkflowClaimTests(unittest.TestCase):
    """Steps 0, 1 and 4 assert how build.yml is arranged."""

    def setUp(self) -> None:
        self.text = DIAGNOSE.read_text(encoding="utf-8")
        self.workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
        self.jobs = workflow_jobs(BUILD_WORKFLOW)

    def test_the_workflow_display_name_the_prompt_quotes_is_the_real_one(self) -> None:
        # Step 1 tells the reader to pick this out of `gh run list --json name`,
        # which prints the display name, not the filename.
        name = next(
            line.partition(":")[2].strip()
            for line in self.workflow.splitlines()
            if line.startswith("name:")
        )
        self.assertIn(f"`{name}`", self.text)

    def test_the_other_build_workflow_the_prompt_contrasts_still_exists(self) -> None:
        self.assertIn("`build-pr.yml`", self.text)
        self.assertTrue((BUILD_WORKFLOW.parent / "build-pr.yml").exists())

    def test_the_cancelled_run_advice_rests_on_a_real_concurrency_setting(self) -> None:
        # Step 0's whole hypothesis -- a cancelled run renders as `failing` --
        # depends on this being set. Without it, back-to-back merges do not
        # cancel each other and the advice sends the reader down a dead end.
        self.assertIn("`cancel-in-progress: true`", self.text)
        self.assertRegex(self.workflow, r"(?m)^\s*cancel-in-progress:\s*true\s*$")

    def test_the_three_jobs_named_as_installing_cosign_are_exactly_those_jobs(self) -> None:
        # Step 4 tells the reader to work out how much of the run completed from
        # *which* job's cosign install failed. A fourth job installing it, or
        # one of these three dropping it, makes that inference wrong.
        named = {"preflight", "sign-akmods-cache", "promote-stable"}
        self.assertEqual(
            named,
            job_uses(self.jobs, "install-signing-tools"),
            "build.yml no longer installs cosign in exactly the jobs step 4 names",
        )
        for job in named:
            self.assertIn(f"`{job}`", self.text)

    def test_the_action_that_installs_cosign_after_publishing_still_does(self) -> None:
        # The same step's "and again inside publish-native-image, which runs
        # after the transient image has been pushed".
        action = REPO_ROOT / ".github" / "actions" / "publish-native-image" / "action.yml"
        self.assertIn(
            "uses: ./.github/actions/install-signing-tools",
            action.read_text(encoding="utf-8"),
        )


class ReplayLockFileTests(unittest.TestCase):
    """The replay prompt's fill-in-the-lock table is the operative part."""

    def setUp(self) -> None:
        self.text = REPLAY.read_text(encoding="utf-8")
        self.lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
        self.defaults = json.loads(DEFAULTS_FILE.read_text(encoding="utf-8"))
        self.rows = table_rows(section(self.text, "2. Fill in the lock file"))[1:]

    def lock_fields(self) -> list[str]:
        return [BACKTICKED_RE.findall(row[0])[0] for row in self.rows]

    def manifest_keys(self) -> set[str]:
        """The keys of the `inputs` dict write_build_inputs_manifest.py emits."""

        tree = ast.parse(MANIFEST_WRITER.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = {
                key.value
                for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            if "base_image_pinned" in keys:
                return keys
        raise AssertionError("write_build_inputs_manifest.py emits no recognisable inputs dict")

    def test_the_table_names_every_field_the_lock_file_actually_holds(self) -> None:
        # Both directions, and the reverse one is why this test exists: a lock
        # field the runbook does not mention is a field the operator leaves at
        # its default, which is exactly the non-replay the guard cannot catch.
        settable = {
            key for key in self.lock if key not in {"version", "description"}
        }
        self.assertEqual(
            sorted(settable),
            sorted(self.lock_fields()),
            "ci/inputs.lock.json and the replay runbook's table disagree about "
            "which fields an operator has to fill in",
        )

    def test_every_artifact_field_the_table_copies_from_is_one_the_manifest_writes(self) -> None:
        keys = self.manifest_keys()
        for row in self.rows:
            source = BACKTICKED_RE.findall(row[1])[0]
            with self.subTest(source=source):
                self.assertTrue(
                    source.startswith("inputs."),
                    f"{source} is not read from the artifact's inputs object",
                )
                self.assertIn(
                    source.removeprefix("inputs."),
                    keys,
                    f"build-inputs.json has no {source}; the runbook sends the "
                    "operator looking for a field the manifest never writes",
                )

    def test_the_placeholders_the_prompt_promises_are_in_the_lock_file(self) -> None:
        self.assertIn("`REPLACE_ME`", self.text)
        self.assertIn("REPLACE_ME", LOCK_FILE.read_text(encoding="utf-8"))

    def test_the_akmods_ref_is_still_sourced_from_defaults_not_the_lock(self) -> None:
        # "akmods_upstream_ref is not in the checked-in lock file, and this
        # table does not ask you to add it."
        self.assertNotIn("akmods_upstream_ref", self.lock)
        self.assertIn("AKMODS_UPSTREAM_REF", self.defaults)

    def test_the_unpinned_akmods_source_warning_still_describes_the_resolver(self) -> None:
        # The prompt's headline caveat: the replay is partial because
        # AKMODS_UPSTREAM_REF is empty by default and the ref re-resolves.
        self.assertEqual("", self.defaults["AKMODS_UPSTREAM_REF"])
        self.assertIn(
            "def _resolve_default_akmods_ref(",
            (CI_TOOLS / "resolve_build_inputs.py").read_text(encoding="utf-8"),
        )

    def test_the_build_container_guard_the_prompt_expects_is_configured_here(self) -> None:
        self.assertIn("DEFAULT_BUILD_CONTAINER_IMAGE", self.defaults)


class ReplayAkmodsCaveatTests(unittest.TestCase):
    """
    The opening caveat and section 2's akmods paragraph: why a replay stays partial.

    Both explain the gap by naming code -- two resolver functions, the order they
    run in, the label that records which cache a build consumed, what the cache
    check compares, and the tier that check sits in. None of those names was read
    by any test, so a renamed function, a swapped label or a reordered resolver
    left the paragraph telling an operator something the code no longer does.
    The resolver's behaviour itself is run in
    tests/test_akmods_fork_maintenance_doc.py (ReplayClaimTests); this class
    holds the prompt's own sentences to the same code.
    """

    def setUp(self) -> None:
        text = REPLAY.read_text(encoding="utf-8")
        self.caveat = collapse(text.split("\n## 1.", 1)[0])
        self.lock_section = collapse(section(text, "2."))
        self.both = f"{self.caveat} {self.lock_section}"

    def resolver_body(self) -> ast.FunctionDef:
        tree = ast.parse((CI_TOOLS / "resolve_build_inputs.py").read_text(encoding="utf-8"))
        return next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "resolve_configured_inputs"
        )

    def test_every_function_named_is_defined_in_ci_tools(self) -> None:
        named = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)\(\)`", self.both)
        self.assertIn("resolve_configured_inputs", named)
        self.assertIn("_resolve_default_akmods_ref", named)
        defined: dict[str, str] = {}
        for path in sorted(CI_TOOLS.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.FunctionDef):
                    defined.setdefault(node.name, path.relative_to(REPO_ROOT).as_posix())
        for name in named:
            with self.subTest(function=name):
                self.assertIn(name, defined, f"the replay runbook names {name}(), which ci_tools/ does not define")
        # "(`resolve_configured_inputs()` in `ci_tools/resolve_build_inputs.py`)"
        for name, path in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)\(\)` in `(ci_tools/[a-z_]+\.py)`", self.both):
            with self.subTest(function=name, path=path):
                self.assertEqual(defined.get(name), path)

    def test_the_configured_ref_is_resolved_before_the_lock_is_opened(self) -> None:
        # "the resolver works out the configured ref (`_resolve_default_akmods_ref()`)
        # before it opens the lock, so a replay whose tracking ref has been deleted
        # ... still stops there even when the lock carries a commit."
        self.assertIn("(`_resolve_default_akmods_ref()`) before it opens the lock", self.lock_section)
        self.assertIn("still stops there even when the lock carries a commit", self.lock_section)
        calls = {
            node.func.id: node.lineno
            for node in ast.walk(self.resolver_body())
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("_resolve_default_akmods_ref", calls)
        self.assertIn("_load_lock_file", calls)
        self.assertLess(calls["_resolve_default_akmods_ref"], calls["_load_lock_file"])

    def test_a_lock_value_is_used_ahead_of_the_configured_ref(self) -> None:
        # "a lock that carries `akmods_upstream_ref` uses that commit ahead of
        # every other source": the lock's value is read first and the configured
        # ref only fills it when it is empty.
        self.assertIn(
            "a lock that carries `akmods_upstream_ref` uses that commit ahead of every other source",
            self.lock_section,
        )
        source = ast.unparse(self.resolver_body())
        self.assertIn("akmods_upstream_ref = str(lock_data.get('akmods_upstream_ref') or '')", source)
        self.assertIn("if not akmods_upstream_ref:\n            akmods_upstream_ref = default_akmods_ref", source)

    def test_the_label_named_as_the_accurate_record_carries_the_consumed_cache(self) -> None:
        match = re.search(r"The accurate record is the `([^`]+)` label", self.lock_section)
        self.assertIsNotNone(match, "the runbook no longer names the label that records the cache")
        action = (REPO_ROOT / ".github" / "actions" / "build-native-image" / "action.yml").read_text(
            encoding="utf-8"
        )
        labels = dict(re.findall(r'--label "([a-z0-9.-]+)=\$\{([A-Z_]+)\}"', action))
        self.assertEqual(
            labels.get(match.group(1)),
            "AKMODS_IMAGE",
            f"{match.group(1)} is not the label the build writes from the akmods cache image",
        )

    def test_the_cache_check_never_looks_at_the_akmods_commit(self) -> None:
        # "the cache check reuses whatever cache matches the kernel release and ZFS
        # version, whichever akmods commit built it" and "decides reuse from the
        # kernel release, the ZFS version and the signature". If the check started
        # comparing the commit, a pinned ref would make a replay exact and both
        # sentences would be telling the operator not to bother.
        self.assertIn(
            "reuses whatever cache matches the kernel release and ZFS version, whichever akmods commit built it",
            self.caveat,
        )
        self.assertIn(
            "`ci_tools/check_akmods_cache.py` decides reuse from the kernel release, the ZFS version and the signature.",
            self.lock_section,
        )
        source = CHECK_AKMODS_CACHE.read_text(encoding="utf-8")
        for name in ("akmods_upstream_ref", "AKMODS_UPSTREAM_REF", "akmods-ref"):
            with self.subTest(name=name):
                self.assertNotIn(name, source)

    def test_the_tier_cited_for_the_cache_check_is_the_one_risk_tiers_gives_it(self) -> None:
        cited = re.search(r"\[`risk-tiers\.md`\]\([^)]*\) lists as Tier (\d)", self.lock_section)
        self.assertIsNotNone(cited, "the runbook no longer cites a tier for the cache check")
        tier = None
        for line in (REPO_ROOT / "docs" / "risk-tiers.md").read_text(encoding="utf-8").splitlines():
            heading = re.match(r"### Tier (\d)\b", line)
            if heading:
                tier = heading.group(1)
            elif line.strip() == f"- `{CHECK_AKMODS_CACHE.relative_to(REPO_ROOT).as_posix()}`":
                break
        else:
            self.fail("docs/risk-tiers.md no longer lists ci_tools/check_akmods_cache.py")
        self.assertEqual(cited.group(1), tier)


class ReplayDispatchTests(unittest.TestCase):
    """Sections 4 and 5 describe what dispatching build.yml costs."""

    def setUp(self) -> None:
        self.text = REPLAY.read_text(encoding="utf-8")
        self.workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
        self.jobs = workflow_jobs(BUILD_WORKFLOW)

    def dispatch_inputs(self) -> dict[str, dict[str, str]]:
        """{input name: {key: value}} from build.yml's workflow_dispatch block."""

        lines = self.workflow.splitlines()
        start = next(i for i, line in enumerate(lines) if line.strip() == "workflow_dispatch:")
        inputs: dict[str, dict[str, str]] = {}
        current = None
        for line in lines[start + 1 :]:
            if line.strip() and not line.startswith("    "):
                break
            match = re.match(r"^      ([A-Za-z0-9_]+):\s*$", line)
            if match:
                current = match.group(1)
                inputs[current] = {}
            elif current is not None:
                field = re.match(r"^        ([A-Za-z0-9_]+):\s*(.*)$", line)
                if field:
                    inputs[current][field.group(1)] = field.group(2).strip()
        return inputs

    def test_the_dispatch_command_only_passes_inputs_the_workflow_declares(self) -> None:
        inputs = self.dispatch_inputs()
        self.assertIn("use_input_lock", inputs)
        passed = re.findall(r"-f ([A-Za-z0-9_]+)=", self.text)
        self.assertTrue(passed, "the prompt's `gh workflow run` example passes no inputs")
        for name in passed:
            with self.subTest(input=name):
                self.assertIn(
                    name,
                    inputs,
                    f"the runbook dispatches -f {name}=..., which build.yml does not declare",
                )

    def test_promote_to_stable_still_defaults_to_true(self) -> None:
        # The prompt calls `promote_to_stable=false` "not optional" precisely
        # because the default promotes. If the default flipped, the sentence
        # would be scaring operators about the wrong thing.
        self.assertEqual("true", self.dispatch_inputs()["promote_to_stable"]["default"])
        self.assertIn("The\ndefault is `true`", self.text)

    def test_the_default_lock_path_is_the_one_the_prompt_tells_you_to_pass(self) -> None:
        self.assertEqual("ci/inputs.lock.json", self.dispatch_inputs()["lock_file"]["default"])
        self.assertIn("-f lock_file=ci/inputs.lock.json", self.text)

    def test_the_two_jobs_gated_on_the_signing_environment_are_the_named_ones(self) -> None:
        # Hazard one: where production-signing is restricted to `main`, a
        # replay/* dispatch cannot reach the secret and these jobs fail.
        gated = {
            job
            for job, body in self.jobs.items()
            if any(line.strip() == "environment: production-signing" for line in body)
        }
        self.assertEqual({"sign-akmods-cache", "build-candidate-image"}, gated)
        for job in gated:
            self.assertIn(f"`{job}`", self.text)

    def test_the_shared_cache_hazard_rests_on_an_unset_override(self) -> None:
        # Hazard two: "build.yml calls prepare-main-akmods without overriding
        # allow_cache_rebuild, whose default is true". Both halves are checked --
        # an override added to build.yml, or a default flipped in the action,
        # would each make the warning false.
        cache_job = self.jobs["build-zfs-akmods"]
        self.assertIn(
            "uses: ./.github/actions/prepare-main-akmods",
            [line.strip() for line in cache_job],
        )
        self.assertNotIn(
            "allow_cache_rebuild",
            "\n".join(cache_job),
            "build.yml now sets allow_cache_rebuild; the replay runbook says it does not",
        )
        action = (REPO_ROOT / ".github" / "actions" / "prepare-main-akmods" / "action.yml").read_text(
            encoding="utf-8"
        )
        block = action.split("allow_cache_rebuild:", 1)[1].split("\n  registry_actor:", 1)[0]
        self.assertRegex(block, r"(?m)^\s*default:\s*\"?true\"?\s*$")

    def test_the_production_boundary_document_the_hazard_cites_exists(self) -> None:
        self.assertTrue((REPO_ROOT / "docs" / "production-boundary-proposal.md").exists())


if __name__ == "__main__":
    unittest.main()
