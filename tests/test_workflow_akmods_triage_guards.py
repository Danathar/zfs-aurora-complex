"""
Script: tests/test_workflow_akmods_triage_guards.py
What: Tests the `if:` conditions in .github/workflows/akmods-failure-triage.yml -- which steps
run, and which stay skipped, for each kind of finished build.
Doing: Parses the workflow with PyYAML, takes each guarded step's own `if:` text, and evaluates
it against a table of build outcomes (event, conclusion, whether a failure payload was found,
whether a real build ran, which badges changed). The evaluator understands only the operators
these conditions use (`==`, `!=`, `&&`, `||`, parentheses) and the six contexts they read; any
other context or operator is an error here, so a new condition cannot be skipped by accident.
Why: The step bodies are tested elsewhere (the ci_tools badge writers and the failure
classifier), but nothing checked the guards: dropping the `pull_request` exclusion, the
`has_payload` half of the sticky-issue step, the `build_ran` half of the close step, or either
half of the two badge conditions left the whole suite green. Each of those is a silent wrong
state -- a fork's pull request build filing issues, an issue opened for a failure with no
payload to describe, a sticky issue closed by a run that never built, a badge left stale
because the step that writes it never ran.
Goal: Make a loosened or tightened guard fail here, on the pull request that changes it.

This reads the workflow's own text. Copying the conditions into the test would assert that the
copy is right.

PyYAML is guarded for the reason tests/test_workflow_boot_test.py gives.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only where PyYAML is absent
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "akmods-failure-triage.yml"

# The contexts these conditions read, by the name a scenario sets them with. A missing step
# output is the empty string at runtime, which is the default here too.
CONTEXTS = {
    "github.event.workflow_run.event": "event",
    "github.event.workflow_run.head_repository.full_name": "head_repo",
    "github.repository": "repository",
    "github.event.workflow_run.conclusion": "conclusion",
    "steps.download.outputs.has_payload": "has_payload",
    "steps.download.outputs.build_ran": "build_ran",
    "steps.badge.outputs.updated": "badge_updated",
    "steps.last_good_build.outputs.updated": "last_good_build_updated",
}

TOKEN = re.compile(r"\s*(?:(\()|(\))|(&&)|(\|\|)|(==|!=)|'([^']*)'|([A-Za-z_][A-Za-z0-9_.]*))")


def evaluate(expression: str, **scenario: str) -> bool:
    """Evaluate a GitHub Actions `if:` built from ==, !=, &&, || and parentheses."""
    tokens: list[tuple[str, str]] = []
    pos = 0
    text = expression.strip()
    while pos < len(text):
        match = TOKEN.match(text, pos)
        if not match or match.end() == pos:
            raise ValueError(f"unsupported syntax at {text[pos:]!r} in {expression!r}")
        pos = match.end()
        lparen, rparen, and_, or_, cmp, literal, name = match.groups()
        if lparen or rparen or and_ or or_ or cmp:
            tokens.append(("op", match.group().strip()))
        elif literal is not None:
            tokens.append(("value", literal))
        elif name in ("true", "false"):
            tokens.append(("value", name))
        else:
            if name not in CONTEXTS:
                raise KeyError(f"{name} is not a context this test models; add it to CONTEXTS")
            tokens.append(("value", scenario.get(CONTEXTS[name], "")))

    def parse_or(i: int) -> tuple[object, int]:
        left, i = parse_and(i)
        while i < len(tokens) and tokens[i] == ("op", "||"):
            right, i = parse_and(i + 1)
            left = left if _truthy(left) else right
        return left, i

    def parse_and(i: int) -> tuple[object, int]:
        left, i = parse_cmp(i)
        while i < len(tokens) and tokens[i] == ("op", "&&"):
            right, i = parse_cmp(i + 1)
            left = right if _truthy(left) else left
        return left, i

    def parse_cmp(i: int) -> tuple[object, int]:
        left, i = parse_atom(i)
        while i < len(tokens) and tokens[i] in (("op", "=="), ("op", "!=")):
            op = tokens[i][1]
            right, i = parse_atom(i + 1)
            left = (left == right) if op == "==" else (left != right)
        return left, i

    def parse_atom(i: int) -> tuple[object, int]:
        kind, value = tokens[i]
        if (kind, value) == ("op", "("):
            inner, i = parse_or(i + 1)
            if tokens[i] != ("op", ")"):
                raise ValueError(f"unbalanced parentheses in {expression!r}")
            return inner, i + 1
        if kind != "value":
            raise ValueError(f"unexpected {value!r} in {expression!r}")
        return value, i + 1

    result, end = parse_or(0)
    if end != len(tokens):
        raise ValueError(f"trailing tokens in {expression!r}")
    return _truthy(result)


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return value not in ("", "false", "0")


class EvaluatorTests(unittest.TestCase):
    """The evaluator itself, so a guard test cannot pass because the evaluator is wrong."""

    def test_comparison_and_logic(self) -> None:
        self.assertTrue(
            evaluate("steps.download.outputs.has_payload == 'true'", has_payload="true")
        )
        self.assertFalse(evaluate("steps.download.outputs.has_payload == 'true'"))
        self.assertTrue(evaluate("github.event.workflow_run.event != 'pull_request'", event="push"))
        expr = (
            "steps.download.outputs.has_payload == 'true' || "
            "(github.event.workflow_run.conclusion == 'success' && "
            "steps.download.outputs.build_ran == 'true')"
        )
        self.assertTrue(evaluate(expr, has_payload="true"))
        self.assertTrue(evaluate(expr, conclusion="success", build_ran="true"))
        self.assertFalse(evaluate(expr, conclusion="success", build_ran="false"))

    def test_an_unmodelled_context_is_an_error(self) -> None:
        with self.assertRaises(KeyError):
            evaluate("github.event_name == 'schedule'")


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class TriageGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        self.job = workflow["jobs"]["triage"]
        self.steps = {step["name"]: step for step in self.job["steps"] if "name" in step}

    def condition(self, step_name: str) -> str:
        self.assertIn(step_name, self.steps, f"no step named {step_name!r}; was it renamed?")
        condition = self.steps[step_name].get("if")
        self.assertIsNotNone(condition, f"{step_name!r} lost its if: guard")
        return condition

    def check(self, condition: str, cases: list[tuple[dict[str, str], bool]]) -> None:
        for scenario, expected in cases:
            with self.subTest(scenario=scenario):
                self.assertEqual(evaluate(condition, **scenario), expected)

    def test_the_job_never_runs_for_a_pull_request_build(self) -> None:
        # A pull request's build is someone else's in-progress work, possibly from a fork; it
        # must not file sticky issues or move the status badges.
        self.check(
            self.job["if"],
            [
                ({"event": "pull_request"}, False),
                ({"event": "schedule"}, True),
                ({"event": "push"}, True),
                ({"event": "workflow_dispatch"}, True),
            ],
        )

    def test_the_job_never_runs_for_a_forks_build(self) -> None:
        # A fork's pull request can start a same-named run through the review events, whose
        # event is not `pull_request`; the repository check is what skips it.
        repo = "Danathar/zfs-aurora-complex"
        self.check(
            self.job["if"],
            [
                ({"event": "pull_request_review", "head_repo": "fork/x", "repository": repo}, False),
                ({"event": "pull_request_review_comment", "head_repo": "fork/x", "repository": repo}, False),
                ({"event": "pull_request_review_comment", "head_repo": repo, "repository": repo}, True),
                ({"event": "push", "head_repo": repo, "repository": repo}, True),
            ],
        )

    def test_the_payload_is_unzipped_only_when_one_was_downloaded(self) -> None:
        # Without the guard, `unzip` fails on a missing archive and the job goes red on every
        # run that had nothing to triage.
        self.check(
            self.condition("Unzip payload"),
            [
                ({"has_payload": "true"}, True),
                ({"has_payload": "false"}, False),
                ({}, False),
            ],
        )

    def test_a_sticky_issue_is_opened_only_for_a_failed_run_with_a_payload(self) -> None:
        # The step reads artifacts/akmods-failure.json; a failure in any other step has none.
        self.check(
            self.condition("Open or update sticky issue on failed run"),
            [
                ({"conclusion": "failure", "has_payload": "true"}, True),
                ({"conclusion": "failure", "has_payload": "false"}, False),
                ({"conclusion": "cancelled", "has_payload": "true"}, False),
                ({"conclusion": "success", "has_payload": "true"}, False),
            ],
        )

    def test_sticky_issues_close_only_after_a_green_run_that_really_built(self) -> None:
        self.check(
            self.condition("Close stale sticky issues on successful run"),
            [
                ({"conclusion": "success", "build_ran": "true"}, True),
                ({"conclusion": "success", "build_ran": "false"}, False),
                ({"conclusion": "failure", "build_ran": "true"}, False),
            ],
        )

    def test_every_guarded_step_has_a_test_here(self) -> None:
        # A step added with an `if:` and no case in this file would be exactly the gap this
        # file was written to close, so the list of guarded steps is pinned.
        guarded = {name for name, step in self.steps.items() if "if" in step}
        self.assertEqual(
            guarded,
            {
                "Unzip payload",
                "Open or update sticky issue on failed run",
                "Close stale sticky issues on successful run",
                "Build OpenZFS/kernel badge payload",
                "Publish badges to status branch",
            },
            "a step gained or lost an if: guard; add or remove its test above",
        )
        unnamed = [step for step in self.job["steps"] if "if" in step and "name" not in step]
        self.assertEqual(unnamed, [], "a guarded step has no name, so no test can find it")

    def test_the_openzfs_badge_is_rebuilt_for_a_payload_or_a_real_green_build(self) -> None:
        # The workflow comment: a gate-skipped green run must not overwrite a red badge, and a
        # failure that left a payload must update it.
        self.check(
            self.condition("Build OpenZFS/kernel badge payload"),
            [
                ({"conclusion": "failure", "has_payload": "true", "build_ran": "true"}, True),
                ({"conclusion": "success", "has_payload": "false", "build_ran": "true"}, True),
                ({"conclusion": "success", "has_payload": "false", "build_ran": "false"}, False),
                ({"conclusion": "failure", "has_payload": "false", "build_ran": "true"}, False),
                ({"conclusion": "cancelled", "has_payload": "false", "build_ran": "true"}, False),
            ],
        )

    def test_the_last_good_build_badge_is_rebuilt_on_every_run(self) -> None:
        # The workflow comment: the day-count keeps advancing even on a run that changes nothing
        # else, so this step carries no guard.
        self.assertNotIn("if", self.steps["Build last-good-build badge payload"])

    def test_badges_are_published_when_either_one_changed(self) -> None:
        self.check(
            self.condition("Publish badges to status branch"),
            [
                ({"badge_updated": "true", "last_good_build_updated": "true"}, True),
                ({"badge_updated": "true"}, True),
                ({"last_good_build_updated": "true"}, True),
                ({"badge_updated": "false", "last_good_build_updated": "false"}, False),
                ({}, False),
            ],
        )


if __name__ == "__main__":
    unittest.main()
