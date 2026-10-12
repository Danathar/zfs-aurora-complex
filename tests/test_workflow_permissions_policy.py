"""
Script: tests/test_workflow_permissions_policy.py
What: Holds every workflow's token permissions to .github/policies/workflow-permissions.json.
Doing: Reads each workflow's top-level and job-level `permissions:` blocks and compares them
with the policy file in both directions: every workflow is listed and every listed workflow
exists; each declared block matches the policy exactly; the jobs that declare a block are
exactly the jobs the policy lists; and no job is left on the repository's default token by
declaring nothing under a workflow that declares nothing.
Why: A workflow's permissions decide what its GITHUB_TOKEN can do -- push an image to GHCR,
push a branch, open a pull request, mint an OIDC token. The only record of what each one is
meant to hold was the workflow itself, so widening a token was a one-line edit inside a file a
reviewer may be reading for the step that changed. docs/SECURITY-AI.md lists widening a
`permissions:` block as something that needs a human decision first; this makes that widening
a second, separate edit to a Tier 3 file.
Goal: A workflow that asks for one more scope fails here until the policy changes in the same
pull request.

The comparison uses a small indentation parser, not PyYAML, so it runs with nothing installed:
third-party imports under tests/ are kept to a guarded, skip-when-absent form, and the check this file exists for should never be the one that skips.
The parser is checked against block, inline, quoted and job-level shapes below, so a block it
cannot read fails as a mismatch rather than passing as "no permissions". Where PyYAML is
installed -- CI installs it by name -- one more test holds the parser to what YAML itself reads
from every real workflow.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only where PyYAML is absent
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
POLICY_PATH = REPO_ROOT / ".github" / "policies" / "workflow-permissions.json"
POLICY = json.loads(POLICY_PATH.read_text(encoding="utf-8"))["workflows"]

# The scopes GitHub accepts in a permissions block. Actions ignores a misspelt
# scope without complaint, so the policy is refused one here instead.
SCOPES = {
    "actions",
    "attestations",
    "checks",
    "contents",
    "deployments",
    "discussions",
    "id-token",
    "issues",
    "models",
    "packages",
    "pages",
    "pull-requests",
    "repository-projects",
    "security-events",
    "statuses",
}
LEVELS = {"read", "write", "none"}
# The inline values GitHub accepts in place of a mapping.
INLINE = {"read-all", "write-all", "{}"}

# A mapping key, bare or quoted: YAML reads `"permissions":` and
# `'permissions':` as the same key as `permissions:`, and so does Actions.
KEY = re.compile(
    r"""^(?P<indent>\ *)(?P<q>["']?)(?P<key>[A-Za-z0-9_-]+)(?P=q):\s*(?P<value>[^#]*?)\s*(?:\#.*)?$""",
    re.VERBOSE,
)


def permissions_block(lines: list[str], indent: int) -> dict[str, str] | str | None:
    """
    The `permissions:` value written at *indent* spaces in *lines*.

    A mapping for a block, the raw string for an inline value (`read-all`,
    `{}`), or None when no such key sits at that indent. Callers pass a whole
    file for indent 0 and one job's body for that file's job-key indent.
    """

    for i, line in enumerate(lines):
        match = KEY.match(line)
        if not match or len(match["indent"]) != indent or match["key"] != "permissions":
            continue
        if match["value"]:
            return match["value"]
        block: dict[str, str] = {}
        for entry in lines[i + 1 :]:
            if not entry.strip() or entry.lstrip().startswith("#"):
                continue
            inner = KEY.match(entry)
            if not inner or len(inner["indent"]) <= indent:
                break
            block[inner["key"]] = inner["value"]
        return block
    return None


def job_lines(lines: list[str]) -> tuple[dict[str, list[str]], int]:
    """
    Each job under the top-level `jobs:` key, and the indent of its keys.

    The indent is read off the file rather than assumed: the first job id sets
    the step, and a job's own keys sit one step further in. A workflow indented
    by four spaces is as valid as one indented by two, and assuming two would
    read none of its job blocks.
    """

    starts = [
        i
        for i, line in enumerate(lines)
        if (match := KEY.match(line)) and match["key"] == "jobs" and not match["indent"]
    ]
    if len(starts) != 1:
        raise AssertionError("expected exactly one top-level jobs: key")
    body = lines[starts[0] + 1 :]
    first = next((line for line in body if line.strip() and not line.lstrip().startswith("#")), "")
    step = len(first) - len(first.lstrip(" "))
    if not step:
        raise AssertionError("jobs: has no indented job under it")
    jobs: dict[str, list[str]] = {}
    current = None
    for line in body:
        if line.strip() and not line.startswith(" "):
            break
        match = KEY.match(line)
        if match and len(match["indent"]) == step and not match["value"]:
            current = match["key"]
            jobs[current] = []
            continue
        if current is not None:
            jobs[current].append(line)
    return jobs, 2 * step


def declared(text: str) -> dict[str, object]:
    """What a workflow declares: its top-level block and each job's own block."""

    lines = text.splitlines()
    bodies, key_indent = job_lines(lines)
    jobs = {}
    for job, body in bodies.items():
        block = permissions_block(body, key_indent)
        if block is not None:
            jobs[job] = block
    return {"workflow": permissions_block(lines, 0), "jobs": jobs}


def default_token_jobs(text: str) -> list[str]:
    """
    Jobs that get the repository's default token, because neither they nor
    the workflow declare a block.

    The policy can only hold what a workflow writes down. A job like that
    gets whatever the repository or organisation default is, which can be
    broader than any block here, and it would add nothing to the policy file
    for a reviewer to see.
    """

    lines = text.splitlines()
    if permissions_block(lines, 0) is not None:
        return []
    bodies, _ = job_lines(lines)
    return sorted(set(bodies) - set(declared(text)["jobs"]))


def workflow_files() -> list[Path]:
    """Every workflow file, both suffixes GitHub accepts."""

    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


class PolicyMatchesWorkflowsTests(unittest.TestCase):
    def test_every_workflow_is_in_the_policy_and_every_entry_is_a_workflow(self) -> None:
        present = {p.name for p in workflow_files()}
        self.assertTrue(present, f"no workflow files under {WORKFLOWS}")
        self.assertEqual(
            present,
            set(POLICY),
            f"not in the policy: {sorted(present - set(POLICY))}; "
            f"in the policy but no such workflow: {sorted(set(POLICY) - present)}",
        )

    def test_each_workflow_declares_exactly_what_the_policy_allows(self) -> None:
        for path in workflow_files():
            with self.subTest(workflow=path.name):
                found = declared(path.read_text(encoding="utf-8"))
                expected = POLICY[path.name]
                self.assertEqual(
                    found["workflow"],
                    expected["workflow"],
                    f"{path.name}'s top-level permissions are {found['workflow']}; "
                    f"{POLICY_PATH.name} allows {expected['workflow']}. Change both, or neither.",
                )
                self.assertEqual(
                    found["jobs"],
                    expected["jobs"],
                    f"{path.name}'s job-level permissions are {found['jobs']}; "
                    f"{POLICY_PATH.name} allows {expected['jobs']}. Change both, or neither.",
                )

    def test_the_policy_names_only_real_scopes_and_levels(self) -> None:
        for name, entry in POLICY.items():
            for block in [entry["workflow"], *entry["jobs"].values()]:
                if block is None:
                    continue
                if isinstance(block, str):
                    # The inline forms the parser returns as written.
                    with self.subTest(workflow=name, inline=block):
                        self.assertIn(block, INLINE, f"{name}: {block!r} is not an inline permissions value")
                    continue
                for scope, level in block.items():
                    with self.subTest(workflow=name, scope=scope):
                        self.assertIn(scope, SCOPES, f"{name}: {scope!r} is not a GitHub token scope")
                        self.assertIn(level, LEVELS, f"{name}: {scope} has level {level!r}")

    def test_every_workflow_declares_permissions_somewhere(self) -> None:
        # If the parser stopped seeing blocks, both sides could read as
        # "nothing declared" and agree. Every workflow here declares at least
        # one block today, at the top or on a job.
        for name, entry in POLICY.items():
            with self.subTest(workflow=name):
                self.assertTrue(entry["workflow"] or entry["jobs"], f"{name} declares no permissions")

    def test_no_job_falls_back_to_the_default_token(self) -> None:
        # A job with no block, in a workflow with no top-level block, gets the
        # repository default token. The policy cannot record that, so adding
        # such a job would widen a token without touching the policy file.
        for path in workflow_files():
            with self.subTest(workflow=path.name):
                self.assertEqual(
                    default_token_jobs(path.read_text(encoding="utf-8")),
                    [],
                    f"{path.name} has jobs that declare no permissions under a workflow that "
                    "declares none, so they get the repository's default token. Give each "
                    "one a permissions block and list it in the policy.",
                )


@unittest.skipIf(yaml is None, "PyYAML not installed")
class ParserAgreesWithYamlTests(unittest.TestCase):
    def test_the_parser_reads_every_real_workflow_the_way_yaml_does(self) -> None:
        # The indentation parser is the one the policy check trusts. This holds
        # it to a real YAML reading of every workflow in the tree, so a shape it
        # misreads cannot make both sides of the policy check agree on the
        # wrong answer.
        def normal(block: object) -> object:
            return "{}" if block == {} else block

        for path in workflow_files():
            with self.subTest(workflow=path.name):
                text = path.read_text(encoding="utf-8")
                loaded = yaml.safe_load(text)
                expected = {
                    "workflow": normal(loaded.get("permissions")),
                    "jobs": {
                        job: normal(body["permissions"])
                        for job, body in loaded["jobs"].items()
                        if "permissions" in body
                    },
                }
                self.assertEqual(declared(text), expected)
                # The default-token check counts jobs; a job the parser missed
                # would be a job it could not flag.
                self.assertEqual(sorted(job_lines(text.splitlines())[0]), sorted(loaded["jobs"]))


class ParserTests(unittest.TestCase):
    def test_a_top_level_block_with_a_trailing_comment(self) -> None:
        text = "permissions:\n  contents: read\n  issues: write  # why\n\njobs:\n  a:\n    runs-on: x\n"
        self.assertEqual(declared(text), {"workflow": {"contents": "read", "issues": "write"}, "jobs": {}})

    def test_an_inline_top_level_value_and_a_job_block(self) -> None:
        text = "permissions: read-all\njobs:\n  a:\n    permissions:\n      contents: write\n    steps: []\n"
        self.assertEqual(declared(text), {"workflow": "read-all", "jobs": {"a": {"contents": "write"}}})

    def test_an_empty_job_block_and_a_job_without_one(self) -> None:
        text = "on: push\njobs:\n  a:\n    permissions: {}\n  b:\n    runs-on: x\n"
        self.assertEqual(declared(text), {"workflow": None, "jobs": {"a": "{}"}})

    def test_a_step_input_named_permissions_is_not_a_block(self) -> None:
        text = "jobs:\n  a:\n    steps:\n      - with:\n          permissions: write\n"
        self.assertEqual(declared(text), {"workflow": None, "jobs": {}})

    def test_quoted_keys_are_the_same_keys(self) -> None:
        # YAML and Actions read `"permissions":` as `permissions:`. Missing the
        # quoted spelling would let a job gain a block this module never sees.
        text = (
            "'permissions':\n  \"contents\": read\n"
            "jobs:\n  a:\n    \"permissions\":\n      'issues': write\n    steps: []\n"
        )
        self.assertEqual(declared(text), {"workflow": {"contents": "read"}, "jobs": {"a": {"issues": "write"}}})

    def test_a_four_space_indented_workflow_is_read(self) -> None:
        # Nothing requires two-space indentation. Assuming it would read no
        # job blocks in a file that uses four.
        text = "jobs:\n    a:\n        permissions:\n            contents: write\n        steps: []\n"
        self.assertEqual(declared(text), {"workflow": None, "jobs": {"a": {"contents": "write"}}})

    def test_a_widened_job_is_caught(self) -> None:
        # The regression this module exists for: one more scope on a job.
        text = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
        widened = text.replace(
            "    permissions:\n      contents: read\n",
            "    permissions:\n      contents: read\n      packages: write\n",
            1,
        )
        self.assertNotEqual(widened, text, "test.yml no longer has the job block this case widens")
        self.assertNotEqual(declared(widened)["jobs"], POLICY["test.yml"]["jobs"])

    def test_a_job_on_the_default_token_is_caught(self) -> None:
        # test.yml declares no top-level block, so a
        # new job with no block of its own matched the policy unchanged.
        text = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
        added = text + "  extra:\n    runs-on: ubuntu-26.04\n    steps:\n      - run: echo hi\n"
        self.assertEqual(default_token_jobs(text), [])
        self.assertEqual(default_token_jobs(added), ["extra"])

    def test_a_top_level_block_covers_jobs_without_one(self) -> None:
        text = "permissions:\n  contents: read\njobs:\n  a:\n    runs-on: x\n"
        self.assertEqual(default_token_jobs(text), [])


if __name__ == "__main__":
    unittest.main()
