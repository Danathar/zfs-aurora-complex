"""
Script: tests/test_workflow_boot_test.py
What: Tests for .github/workflows/boot-test.yml: when it runs, which image it hands
    ci_tools/boot_test.py, what it uploads afterwards, and the host steps before the boot.
Doing: Parses the workflow with PyYAML, evaluates the job's `if:` expression against hand-built
    `github` contexts, joins the step's env and command to ci/defaults.json, build.yml's name and
    the CLI command map, and runs boot_test.main() with the registry, pull and VM replaced to see
    which files it writes against the upload step's paths.
Why: tests/test_boot_test.py covers the Python. The workflow around it had nothing on a pull
    request, and most ways of breaking it keep the run green. The upload step uses
    `if-no-files-found: ignore`, so a path that drifts from what boot_test.py writes uploads
    nothing and still passes. A dropped clause in the job's `if:` boots on every branch's build,
    or on a fork's; a dropped `workflow_run` name or an image default pointing elsewhere boots the
    wrong thing or never runs, and none of that turns a run red.
Goal: Make a change that stops the boot test from booting this repository's published image
    after a main build, or from keeping what it saw, fail here on the pull request.

PyYAML is not a pytest dependency; test.yml installs it by name. The import is guarded so the suite
still runs under `python3 -m unittest discover -s tests` with nothing installed, matching the other
workflow tests.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from ci_tools import boot_test
from ci_tools.boot_test import GUEST_CHECKS, MARKER
from ci_tools.cli import command_map
from ci_tools.common import CiToolError

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only where PyYAML is absent
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "boot-test.yml"
BUILD_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "build.yml"
DEFAULTS_PATH = REPO_ROOT / "ci" / "defaults.json"

BOOT_STEP = "Boot the image and check ZFS"
UPLOAD_STEP = "Upload logs"
HOST_STEPS = [
    "Prepare the host the way bcvk's CI does",
    "Give the runner user KVM",
    "Install the VM stack",
    "Put container storage on the larger disk",
]


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # PyYAML reads the bare key `on` as the boolean True.
    return workflow.get("on", workflow.get(True))


def _job() -> dict:
    jobs = _workflow()["jobs"]
    if list(jobs) != ["boot"]:
        raise AssertionError(
            f"boot-test.yml has jobs {list(jobs)}, not just `boot`; update this test"
        )
    return jobs["boot"]


def _step(name: str) -> dict:
    """The step named `name`. Raises when it is missing, so a rename fails rather than tests nothing."""

    found = [step for step in _job()["steps"] if step.get("name") == name]
    if len(found) != 1:
        raise AssertionError(
            f"{len(found)} steps named {name!r} in {WORKFLOW_PATH.name}; "
            "update this test if the step was renamed or removed."
        )
    return found[0]


_TOKEN = re.compile(
    r"\s*(?:(?P<op>==|!=|&&|\|\||\(|\))|'(?P<str>[^']*)'|(?P<ident>[A-Za-z_][\w.]*))"
)


def evaluate_if(expression: str, context: dict[str, str]) -> bool:
    """
    Evaluate the subset of GitHub's expression syntax the job's `if:` uses.

    `||` binds looser than `&&`, `==`/`!=` compare strings, and an identifier is looked up in
    `context` by its dotted name. An identifier the context does not carry raises, so a new
    clause shows up here as a failure rather than being read as an empty string.
    """

    tokens: list[tuple[str, str]] = []
    position = 0
    expression = expression.strip()
    while position < len(expression):
        match = _TOKEN.match(expression, position)
        if not match:
            raise AssertionError(f"cannot read the `if:` expression at {expression[position:]!r}")
        kind = match.lastgroup
        tokens.append((kind, match.group(kind)))
        position = match.end()
    tokens.append(("end", ""))
    index = 0

    def take(expected: str | None = None) -> tuple[str, str]:
        nonlocal index
        token = tokens[index]
        if expected is not None and token[1] != expected:
            raise AssertionError(
                f"expected {expected!r} in the `if:` expression, found {token[1]!r}"
            )
        index += 1
        return token

    def value() -> object:
        kind, text = take()
        if text == "(":
            result = either()
            take(")")
            return result
        if kind == "str":
            return text
        if kind == "ident":
            if text not in context:
                raise AssertionError(
                    f"the `if:` expression reads {text}, which this test does not supply"
                )
            return context[text]
        raise AssertionError(f"unexpected {text!r} in the `if:` expression")

    def comparison() -> object:
        left = value()
        if tokens[index][1] in ("==", "!="):
            operator = take()[1]
            right = value()
            return (left == right) if operator == "==" else (left != right)
        return left

    def both() -> object:
        result = comparison()
        while tokens[index][1] == "&&":
            take()
            right = comparison()
            result = result and right
        return result

    def either() -> object:
        result = both()
        while tokens[index][1] == "||":
            take()
            right = both()
            result = result or right
        return result

    result = either()
    take("")
    return bool(result)


def passing_session() -> str:
    lines = [f"{MARKER}STATE running"]
    lines += [f"{MARKER}CHECK {name} pass detail" for name, _ in GUEST_CHECKS]
    lines.append(f"{MARKER}DONE")
    return "\n".join(lines) + "\n"


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class TriggerTests(unittest.TestCase):
    """What starts the job: build.yml finishing, or a person."""

    def test_it_runs_when_the_main_build_workflow_completes(self) -> None:
        build_name = yaml.safe_load(BUILD_WORKFLOW_PATH.read_text(encoding="utf-8"))["name"]
        workflow_run = _triggers(_workflow())["workflow_run"]
        self.assertEqual(
            workflow_run["workflows"],
            [build_name],
            "workflow_run names a workflow by its `name:`. A name build.yml does not carry matches "
            "nothing, and the boot test silently stops running",
        )
        self.assertEqual(workflow_run["types"], ["completed"])

    def test_a_person_can_boot_any_image_or_latest_by_default(self) -> None:
        image = _triggers(_workflow())["workflow_dispatch"]["inputs"]["image"]
        self.assertFalse(image["required"])
        self.assertEqual(image["default"], "")

    def test_a_second_boot_waits_instead_of_cancelling_the_first(self) -> None:
        # A cancelled boot leaves no verdict on the image it was booting.
        concurrency = _workflow()["concurrency"]
        self.assertEqual(concurrency["group"], "${{ github.workflow }}")
        self.assertIs(concurrency["cancel-in-progress"], False)


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class JobConditionTests(unittest.TestCase):
    """The job's `if:`, evaluated: only builds of this repository's main, and not cancelled ones."""

    MAIN_BUILD: ClassVar[dict[str, str]] = {
        "github.event_name": "workflow_run",
        "github.event.repository.default_branch": "main",
        "github.repository": "Danathar/zfs-aurora-complex",
        "github.event.workflow_run.head_branch": "main",
        "github.event.workflow_run.head_repository.full_name": "Danathar/zfs-aurora-complex",
        "github.event.workflow_run.conclusion": "success",
    }

    def runs(self, **changes: str) -> bool:
        context = dict(self.MAIN_BUILD)
        context.update(
            {f"github.{key.replace('__', '.')}": value for key, value in changes.items()}
        )
        return evaluate_if(_job()["if"], context)

    def test_a_successful_main_build_is_booted(self) -> None:
        self.assertTrue(self.runs())

    def test_a_failed_main_build_still_boots_latest(self) -> None:
        # A build that failed before promotion left `:latest` where it was; that image is
        # still what a user pulls, so it is still booted.
        self.assertTrue(self.runs(event__workflow_run__conclusion="failure"))

    def test_a_cancelled_build_is_not_booted(self) -> None:
        self.assertFalse(self.runs(event__workflow_run__conclusion="cancelled"))

    def test_a_build_of_another_branch_is_not_booted(self) -> None:
        self.assertFalse(self.runs(event__workflow_run__head_branch="feature"))

    def test_a_fork_branch_named_main_is_not_booted(self) -> None:
        self.assertFalse(
            self.runs(event__workflow_run__head_repository__full_name="someone/zfs-aurora-complex")
        )

    def test_a_manual_run_boots_whatever_the_context_says_about_workflow_run(self) -> None:
        # workflow_run fields are absent on a dispatch; GitHub reads them as empty.
        self.assertTrue(
            self.runs(
                event_name="workflow_dispatch",
                event__workflow_run__head_branch="",
                event__workflow_run__head_repository__full_name="",
                event__workflow_run__conclusion="",
            )
        )

    def test_the_evaluator_reads_or_looser_than_and(self) -> None:
        self.assertTrue(evaluate_if("'a' == 'a' || 'a' == 'b' && 'a' == 'b'", {}))
        self.assertFalse(evaluate_if("('a' == 'a' || 'a' == 'b') && 'a' == 'b'", {}))
        with self.assertRaises(AssertionError):
            evaluate_if("github.unknown == 'x'", {})


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class BootStepTests(unittest.TestCase):
    """The step that runs boot_test.py: which command, which image, and where it writes."""

    def setUp(self) -> None:
        self.step = _step(BOOT_STEP)

    def test_the_step_runs_the_boot_test_command_and_nothing_else(self) -> None:
        self.assertEqual(self.step["run"].strip(), "python3 -m ci_tools.cli boot-test")
        self.assertIs(command_map()["boot-test"], boot_test.main)

    def test_the_default_image_is_the_one_this_repository_publishes(self) -> None:
        image_name = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))["IMAGE_NAME"]
        self.assertEqual(
            self.step["env"]["BOOT_TEST_IMAGE"],
            f"${{{{ inputs.image || 'ghcr.io/danathar/{image_name}:latest' }}}}",
            "the boot after a build must boot the `:latest` that build promotes, and a manual run "
            "must still be able to name another image",
        )

    def test_the_work_directory_is_the_one_the_upload_step_reads(self) -> None:
        self.assertEqual(self.step["env"]["BOOT_TEST_WORK"], "${{ env.WORK }}")
        self.assertIn("WORK", _job()["env"])


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class UploadStepTests(unittest.TestCase):
    """The upload step's paths, joined to the files boot_test.main() actually writes."""

    def setUp(self) -> None:
        self.step = _step(UPLOAD_STEP)
        self.paths = [
            line.strip() for line in self.step["with"]["path"].splitlines() if line.strip()
        ]

    def test_logs_are_uploaded_when_the_boot_failed(self) -> None:
        # A failed boot is the run whose journal and console someone needs.
        self.assertEqual(self.step["if"], "always()")

    def written_files(self, work: Path) -> set[str]:
        """Run main() with a first boot that never reached the checks, then a passing one."""

        sessions = iter(["Timeout waiting for readiness\n", passing_session()])

        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess:
            stdout = next(sessions) if command[0] == "bcvk" else ""
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        env = {
            "BOOT_TEST_IMAGE": "ghcr.io/danathar/zfs-aurora-complex:latest",
            "BOOT_TEST_WORK": str(work),
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env))
            os.environ.pop("GITHUB_STEP_SUMMARY", None)
            stack.enter_context(
                patch.object(boot_test, "skopeo_inspect_digest", return_value="sha256:" + "c" * 64)
            )
            stack.enter_context(patch.object(boot_test, "run_cmd", return_value=""))
            stack.enter_context(patch.object(boot_test.subprocess, "run", side_effect=fake_run))
            stack.enter_context(contextlib.redirect_stdout(open(os.devnull, "w")))  # noqa: SIM115
            with contextlib.suppress(CiToolError):
                boot_test.main()
        return {path.relative_to(work).as_posix() for path in work.rglob("*") if path.is_file()}

    def uploaded(self, written: set[str], pattern: str) -> set[str]:
        prefix = "${{ env.WORK }}/"
        self.assertTrue(pattern.startswith(prefix), f"{pattern} is not under the work directory")
        relative = pattern[len(prefix) :]
        if relative.endswith("/"):
            directory = re.compile(re.escape(relative[:-1]).replace(r"\*", "[^/]*") + "/")
            return {path for path in written if directory.match(path)}
        return {path for path in written if path == relative}

    def test_every_upload_path_matches_something_main_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            written = self.written_files(Path(tmp))
        for pattern in self.paths:
            self.assertTrue(
                self.uploaded(written, pattern),
                f"{pattern} matches none of {sorted(written)}; with `if-no-files-found: ignore` the "
                "run stays green and the artifact silently loses it",
            )
        self.assertEqual(self.step["with"]["if-no-files-found"], "ignore")

    def test_both_boots_logs_and_the_verdict_are_uploaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            written = self.written_files(Path(tmp))
        uploaded = set().union(*(self.uploaded(written, pattern) for pattern in self.paths))
        self.assertTrue(
            {"result.json", "checks/checks.sh", "boot-1/bcvk.log", "boot-2/bcvk.log"} <= uploaded
        )
        # The injected units are constants in boot_test.py, so they are the one thing left out.
        self.assertTrue(
            all(path.startswith("units/") for path in written - uploaded), written - uploaded
        )


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class HostStepTests(unittest.TestCase):
    """The steps that give the runner a VM stack, in order, always run, and pinned."""

    def test_the_host_is_prepared_before_the_boot_and_no_step_is_skippable(self) -> None:
        names = [step.get("name") for step in _job()["steps"]]
        self.assertEqual(names[1:], [*HOST_STEPS, BOOT_STEP, UPLOAD_STEP])
        for name in [*HOST_STEPS, BOOT_STEP]:
            self.assertNotIn("if", _step(name), f"{name!r} must run on every boot")

    def test_the_checkout_keeps_no_credentials(self) -> None:
        checkout = _job()["steps"][0]
        self.assertTrue(checkout["uses"].startswith("actions/checkout@"))
        self.assertIs(checkout["with"]["persist-credentials"], False)

    def test_bcvks_ci_setup_runs_at_the_pinned_commit(self) -> None:
        step = _step(HOST_STEPS[0])
        self.assertRegex(step["env"]["BOOTC_ACTIONS_SHA"], r"^[0-9a-f]{40}$")
        body = step["run"]
        checkout = body.index('checkout -q "${BOOTC_ACTIONS_SHA}"')
        self.assertLess(checkout, body.index("install-deps.sh"))
        self.assertLess(checkout, body.index("workarounds.sh"))

    def test_the_bcvk_download_is_checked_against_a_digest_before_it_is_unpacked(self) -> None:
        body = _step(HOST_STEPS[2])["run"]
        check = re.search(
            r'echo "[0-9a-f]{64}  \$\{RUNNER_TEMP\}/bcvk\.tar\.gz" \| sha256sum -c -$',
            body,
            re.MULTILINE,
        )
        self.assertIsNotNone(check, "the bcvk tarball is no longer checked against a sha256 digest")
        self.assertLess(check.start(), body.index("tar -xzf"))


if __name__ == "__main__":
    unittest.main()
