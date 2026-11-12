"""
Script: tests/test_akmods_fork_maintenance_doc.py
What: Joins the "Validate The Sync" step of docs/akmods-fork-maintenance.md to the workflows,
defaults file and module it describes.
Doing: Reads the step's after-push instruction and checks each claim in it against the tree: the
workflow and dispatch input it names, that the pull request build runs only
`prepare-validation-build` and that module imports a clone and a cache check but no build, the
promotion opt-out input and the promote job's guard on it, and that the floating tracking ref
it relies on is still what ci/defaults.json sets.
Why: This step is what a maintainer follows after syncing the akmods fork. It said a manual
rebuild validated the fork "before `main` floats onto it", but `main` tracks the fork's `main`
branch, so every run after the push already resolves the pushed commit. No test opened the page,
so nothing noticed.
Goal: Make the step fail here when a workflow, input, guard or default it describes moves.

Parses Markdown and the workflow by hand, matching the other doc tests here, so the parser
carries its own case table in `ParserTests`.
"""

from __future__ import annotations

import ast
import re
import textwrap
import unittest
from pathlib import Path

from ci_tools.common import load_repo_defaults

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "akmods-fork-maintenance.md"
BUILD_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build.yml"
PR_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-pr.yml"
VALIDATION_MODULE = REPO_ROOT / "ci_tools" / "prepare_validation_build.py"

DOC_TEXT = DOC.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> list[str]:
    """
    Return the lines under `heading`, up to the next heading of the same or higher level.

    Raises when the heading is absent or the section is empty, so a renamed section fails
    here instead of handing the assertions below nothing to check.
    """

    lines = text.splitlines()
    try:
        start = lines.index(heading)
    except ValueError as exc:
        raise AssertionError(f"{DOC.name} has no heading {heading!r}") from exc

    level = len(heading) - len(heading.lstrip("#"))
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("#") and len(line) - len(line.lstrip("#")) <= level:
            break
        body.append(line)

    if not any(line.strip() for line in body):
        raise AssertionError(f"{DOC.name} section {heading!r} is empty")
    return body


def _numbered_items(lines: list[str]) -> list[str]:
    """
    Return the first top-level ordered list in `lines`, each item flattened to one string.

    Indented lines continue the current item, because the page hard-wraps its steps. A blank
    line ends the list. Numbering must run 1..n.
    """

    items: list[str] = []
    open_item = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if items:
                break
            open_item = False
            continue
        match = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if match and not line.startswith("   "):
            expected = len(items) + 1
            if int(match.group(1)) != expected:
                raise AssertionError(f"ordered list is not sequential at item {match.group(1)!r}")
            items.append(match.group(2))
            open_item = True
        elif open_item:
            items[-1] = f"{items[-1]} {stripped}"
    return items


def _after_push_step() -> str:
    items = _numbered_items(_section(DOC_TEXT, "### Validate The Sync"))
    return next(item for item in items if "after pushing" in item)


def _dispatch_inputs(workflow_text: str) -> dict[str, dict[str, str]]:
    """Map each `workflow_dispatch` input of a workflow to its scalar fields (default, type...)."""

    lines = workflow_text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "workflow_dispatch:")
    inputs: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith("    "):
            break
        name = re.match(r"^      ([A-Za-z0-9_]+):\s*$", line)
        if name:
            current = inputs.setdefault(name.group(1), {})
            continue
        field = re.match(r"^        ([A-Za-z0-9_]+):\s*(.*?)\s*$", line)
        if field and current is not None:
            current[field.group(1)] = field.group(2)
    return inputs


def _job_if(workflow_text: str, job: str) -> str:
    """Return a job's `if:` expression, folded `>-` block included, as one line."""

    lines = workflow_text.splitlines()
    start = lines.index(f"  {job}:")
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if re.match(r"^  \S", line):
            break
        if line.startswith("    if:"):
            value = line.split(":", 1)[1].strip()
            if value not in (">-", ">", "|"):
                return value
            block = []
            for entry in lines[index + 1 :]:
                if entry.strip() and not entry.startswith("      "):
                    break
                block.append(entry.strip())
            return " ".join(part for part in block if part)
    raise AssertionError(f"job {job!r} has no `if:`")


class ParserTests(unittest.TestCase):
    """The parsers above, against fixtures whose right answer is written out by hand."""

    FIXTURE = textwrap.dedent(
        """\
        # Title

        ## Alpha

        1. first item
        2. second item that
           wraps onto another line

        prose after the list

        ### Alpha Nested

        nested body

        ## Beta

        beta body
        """
    )

    WORKFLOW = textwrap.dedent(
        """\
        on:
          workflow_dispatch:
            inputs:
              first:
                default: false
                type: boolean
              second:
                default: true
        jobs:
          plain:
            if: always()
            runs-on: x
          folded:
            needs: plain
            if: >-
              !cancelled()
              && inputs.second == 'true'
            runs-on: x
          none:
            runs-on: x
        """
    )

    def test_section_stops_at_next_same_level_heading(self) -> None:
        body = "\n".join(_section(self.FIXTURE, "## Alpha"))
        self.assertIn("nested body", body)
        self.assertNotIn("beta body", body)

    def test_section_rejects_missing_or_empty_heading(self) -> None:
        with self.assertRaises(AssertionError):
            _section(self.FIXTURE, "## Gamma")
        with self.assertRaises(AssertionError):
            _section("## Alpha\n\n## Beta\n\nbody\n", "## Alpha")

    def test_numbered_items_join_wrapped_lines_and_stop_at_blank(self) -> None:
        items = _numbered_items(_section(self.FIXTURE, "## Alpha"))
        self.assertEqual(items, ["first item", "second item that wraps onto another line"])

    def test_numbered_items_reject_a_gap_in_the_numbering(self) -> None:
        with self.assertRaises(AssertionError):
            _numbered_items(["1. one", "3. three"])

    def test_dispatch_inputs_reads_each_input_and_stops_at_jobs(self) -> None:
        self.assertEqual(
            _dispatch_inputs(self.WORKFLOW),
            {"first": {"default": "false", "type": "boolean"}, "second": {"default": "true"}},
        )

    def test_job_if_reads_plain_and_folded_forms(self) -> None:
        self.assertEqual(_job_if(self.WORKFLOW, "plain"), "always()")
        self.assertEqual(_job_if(self.WORKFLOW, "folded"), "!cancelled() && inputs.second == 'true'")
        with self.assertRaises(AssertionError):
            _job_if(self.WORKFLOW, "none")


class ValidationInstructionTests(unittest.TestCase):
    """The after-push step of "Validate The Sync" against what it tells a maintainer to run."""

    def test_the_named_workflow_and_dispatch_input_exist(self) -> None:
        after_push = _after_push_step()
        workflow = re.search(r"`(build\.yml)` run", after_push)
        dispatch = re.search(r"`rebuild_akmods=(true|false)`", after_push)
        self.assertIsNotNone(workflow, "the step no longer names the workflow that rebuilds akmods")
        self.assertIsNotNone(dispatch, "the step no longer names the rebuild input")
        self.assertEqual("true", dispatch.group(1))
        self.assertTrue((REPO_ROOT / ".github" / "workflows" / workflow.group(1)).exists())
        build_text = BUILD_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("rebuild_akmods", _dispatch_inputs(build_text))
        self.assertIn("github.event.inputs.rebuild_akmods == 'true'", build_text)

    def test_pull_request_build_is_not_presented_as_an_akmods_build(self) -> None:
        after_push = " ".join(_after_push_step().split())
        self.assertIn("pull request build (`build-pr.yml`) does not", after_push)
        self.assertIn("compiles nothing", after_push)
        self.assertTrue(PR_WORKFLOW.exists())

    def test_pull_request_build_checks_exactly_what_the_step_says(self) -> None:
        """
        "it only checks that the fork commit clones and that the existing shared cache still
        matches" is a claim about `prepare-validation-build`, the one akmods command
        build-pr.yml runs. Hold the module to those two checks and to nothing that builds.
        """

        self.assertIn(
            "it only checks that the fork commit clones and that the existing shared cache "
            "still matches",
            _after_push_step(),
        )
        self.assertIn(
            "python3 -m ci_tools.cli prepare-validation-build",
            PR_WORKFLOW.read_text(encoding="utf-8"),
        )
        tree = ast.parse(VALIDATION_MODULE.read_text(encoding="utf-8"))
        imported = {
            (node.module, alias.name)
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        self.assertIn(("ci_tools.akmods_clone_pinned", "clone_pinned"), imported)
        self.assertIn(("ci_tools.check_akmods_cache", "inspect_akmods_cache"), imported)
        self.assertEqual(
            {module for module, _ in imported if module and "build" in module.split(".")[-1]},
            {"ci_tools.resolve_build_inputs"},
            "prepare-validation-build now imports a build module, so 'compiles nothing' may be false",
        )

    def test_the_promotion_opt_out_names_a_real_input_that_skips_promotion(self) -> None:
        """
        The step warns that the rebuild moves `:latest` and names the input that stops it.

        So the input must be a real `build.yml` dispatch input, its default must be the
        promoting one (or "unless you also set" is backwards), and the value the step gives
        must be one the promote job's guard refuses.
        """

        opt_out = re.search(
            r"promotes `:latest` unless you also set `([A-Za-z0-9_]+)=(true|false)`", _after_push_step()
        )
        self.assertIsNotNone(opt_out, "the step no longer names the input that skips promotion")
        name, value = opt_out.groups()
        build_text = BUILD_WORKFLOW.read_text(encoding="utf-8")
        inputs = _dispatch_inputs(build_text)
        self.assertIn(name, inputs, f"build.yml has no dispatch input named {name!r}")
        self.assertEqual(
            inputs[name].get("default"),
            "true",
            f"{name} no longer defaults to promoting, so 'unless you also set' is backwards",
        )
        self.assertIn(
            f"github.event.inputs.{name} == 'true'",
            _job_if(build_text, "promote-stable"),
            "the promote job no longer reads the named input",
        )
        self.assertNotEqual(value, "true", "the step's value is the one that promotes")

    def test_the_rebuild_is_placed_on_main_after_the_push(self) -> None:
        """
        `main` follows the fork's tracking branch, so the pushed commit is live on the next
        run. The step has to say the rebuild validates after the fact, and the defaults
        file has to still be on the floating track that makes that true. While the
        temporary `AKMODS_UPSTREAM_REF` pin is set, it must also say the rebuild builds
        the pinned commit, not the pushed one.
        """

        after_push = " ".join(_after_push_step().split())
        self.assertIn("The rebuild runs on `main`", after_push)
        self.assertIn(
            "With the floating track, `main` already resolves the pushed fork commit, so this "
            "validates the sync after it lands, not before",
            after_push,
        )
        self.assertNotIn("before `main` floats onto it", after_push)
        defaults = load_repo_defaults()
        self.assertEqual(defaults.get("AKMODS_UPSTREAM_TRACK"), "main")
        if defaults.get("AKMODS_UPSTREAM_REF"):
            # While the guard pin is set, the rebuild builds the pinned commit.
            self.assertIn(
                "a `rebuild_akmods=true` run rebuilds the pinned commit, not the pushed one",
                after_push,
            )
            self.assertIn("Danathar/akmods#260", after_push)
        # Temporary guard pin (Danathar/akmods#255, removed by Danathar/akmods#260): a 40-hex SHA.
        self.assertRegex(defaults.get("AKMODS_UPSTREAM_REF", ""), r"^[0-9a-f]{40}$")

    def test_the_files_a_green_pull_request_run_says_nothing_about_are_checked_above(self) -> None:
        """The synced files the step names must be ones the pre-push steps already check."""

        items = _numbered_items(_section(DOC_TEXT, "### Validate The Sync"))
        named = re.search(r"says nothing about the synced `([^`]+)` or `([^`]+)`", _after_push_step())
        self.assertIsNotNone(named, "the step no longer names the synced files")
        earlier = " ".join(item for item in items if "after pushing" not in item)
        for name in named.groups():
            self.assertRegex(earlier, rf"[`/]{re.escape(name)}[` ]", f"no earlier step checks {name}")


if __name__ == "__main__":
    unittest.main()
