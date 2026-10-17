"""
Script: tests/test_quality_doc.py
What: Joins docs/quality.md to the README badges, workflows, jobs, guard messages and coverage manifest it describes.
Doing: Checks that the five status badges the page tabulates are the five in README.md, that the concurrency block and `paths-ignore` list it quotes are build.yml's, that every workflow, job and step it names exists, that each gate's trigger matches the workflow's `on:`, that the refusal messages this repository raises appear in the code, and that the `unmeasured` entries it counts are the manifest's.
Why: The page is what a reader opens to decide whether a red badge or a green run matters. It names jobs and messages a reader will search for in a log; a renamed job or reworded refusal makes the page point at nothing, and a count of manifest entries goes stale when a file is added.
Goal: Make a renamed job, a reworded refusal, a changed trigger, a badge added or removed, or an `unmeasured` entry added without the page, fail here.

`Cannot build against kernel version` is OpenZFS's own configure message, printed during the akmods build; it is not raised by this repository, so it is not joined to any file here.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import ClassVar

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "quality.md"
README = REPO_ROOT / "README.md"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
THRESHOLDS = REPO_ROOT / ".coverage-thresholds.json"


def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def flat(text: str) -> str:
    return " ".join(text.split())


def workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def section(heading: str) -> str:
    match = re.search(rf"(?ms)^#+ {re.escape(heading)}\n(.*?)(?=^#{{1,3}} |\Z)", doc_text())
    if match is None:
        raise AssertionError(f"docs/quality.md has no '{heading}' heading")
    return match.group(1)


def table_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if line.startswith("|") and not re.match(r"^\|[\s|-]+\|$", line):
            rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows[1:]


class BadgeTests(unittest.TestCase):
    def test_the_table_lists_the_five_status_badges_the_readme_carries(self) -> None:
        in_readme = re.findall(r"!\[(build|tests|nightly compliance|last good build|OpenZFS/kernel status)\]", README.read_text("utf-8"))
        in_page = [re.sub(r"[*]", "", row[0]) for row in table_rows(section("The badges"))]
        self.assertEqual(sorted(in_page), sorted(in_readme))
        self.assertEqual(len(in_page), 5)

    def test_the_build_badge_is_for_build_yml_on_main(self) -> None:
        self.assertIn("actions/workflows/build.yml/badge.svg?branch=main", README.read_text("utf-8"))

    def test_the_tests_and_nightly_compliance_badges_are_for_their_workflows_on_main(self) -> None:
        readme = README.read_text("utf-8")
        for workflow in ("test.yml", "nightly-compliance.yml"):
            self.assertIn(f"actions/workflows/{workflow}/badge.svg?branch=main", readme)

    def test_the_two_status_badges_read_the_status_branch_payloads_the_workflow_writes(self) -> None:
        readme = README.read_text("utf-8")
        triage = workflow("akmods-failure-triage.yml")
        for payload in ("last-good-build-badge.json", "akmods-badge.json"):
            with self.subTest(payload=payload):
                self.assertIn(payload, readme)
                self.assertIn(payload, triage)

    def test_the_concurrency_block_it_quotes_is_the_one_in_build_yml(self) -> None:
        quoted = re.search(r"```yaml\nconcurrency:\n(.*?)```", doc_text(), re.DOTALL).group(1)
        for line in quoted.strip().splitlines():
            with self.subTest(line=line.strip()):
                self.assertIn(line, workflow("build.yml"))

    def test_the_paths_ignore_list_it_quotes_is_build_ymls(self) -> None:
        text = flat(doc_text())
        quoted = re.search(r"`paths-ignore` \((.*?)\) means", text).group(1)
        entries = re.findall(r"`([^`]+)`", quoted)
        block = workflow("build.yml").split("paths-ignore:", 1)[1].split("\n\n", 1)[0]
        actual = [line.strip()[2:].strip("\"'") for line in block.splitlines() if line.strip().startswith("- ")]
        self.assertEqual(entries, actual)

    def test_the_badge_writer_refuses_a_skipped_run_in_code_not_only_in_the_workflow(self) -> None:
        source = (REPO_ROOT / "ci_tools" / "write_akmods_badge.py").read_text(encoding="utf-8")
        self.assertIn("build_ran", source)
        self.assertIn("`build_ran` check", doc_text())


class GateTests(unittest.TestCase):
    def rows(self) -> list[list[str]]:
        return table_rows(section("The gates").split("> **A red")[0])

    def test_every_workflow_a_gate_row_names_exists(self) -> None:
        named = set(re.findall(r"`([\w-]+\.yml)`", doc_text()))
        self.assertGreaterEqual(len(named), 4)
        self.assertEqual(sorted(n for n in named if not (WORKFLOWS / n).is_file()), [])

    def test_every_job_the_gates_name_is_a_job_name_in_the_workflow_beside_it(self) -> None:
        pairs = {
            "Python Unit Tests": "test.yml",
            "Build PR Image (No Push)": "build-pr.yml",
            "Evaluate Stable Signal Gate": "build.yml",
        }
        for job, file in pairs.items():
            with self.subTest(job=job):
                self.assertIn(f"`{job}`", doc_text())
                self.assertRegex(workflow(file), rf"(?m)^    name: {re.escape(job)}$")

    def test_the_gate_commands_it_names_are_registered_in_the_cli(self) -> None:
        from ci_tools.cli import command_map

        for command in ("check-akmods-cache", "promote-stable"):
            with self.subTest(command=command):
                self.assertIn(f"`{command}`", doc_text())
                self.assertIn(command, command_map())

    def test_the_signing_gate_is_the_isolated_script_run_by_the_two_signing_jobs(self) -> None:
        self.assertIn("`ci_tools/sign_image.py` under `python3 -I`", doc_text())
        self.assertTrue((REPO_ROOT / "ci_tools" / "sign_image.py").is_file())
        text = workflow("build.yml")
        self.assertEqual(2, text.count("python3 -I ci_tools/sign_image.py"))
        for job in ("sign-akmods-cache", "sign-candidate-image"):
            with self.subTest(job=job):
                self.assertIn(f"`{job}`", doc_text())
                body = text.split(f"\n  {job}:\n", 1)[1]
                body = re.split(r"\n  [a-z][\w-]*:\n", body, maxsplit=1)[0]
                self.assertIn("environment: production-signing", body)
                self.assertIn("python3 -I ci_tools/sign_image.py", body)

    def test_the_strict_mode_variable_is_the_one_the_check_reads(self) -> None:
        self.assertIn("(`REQUIRE_MATCH`)", doc_text())
        self.assertIn('optional_env("REQUIRE_MATCH")', (REPO_ROOT / "ci_tools" / "check_akmods_cache.py").read_text("utf-8"))
        # The variable is set by the composite action build.yml's akmods job calls.
        action = REPO_ROOT / ".github" / "actions" / "prepare-main-akmods" / "action.yml"
        self.assertIn('REQUIRE_MATCH: "true"', action.read_text("utf-8"))
        self.assertIn("./.github/actions/prepare-main-akmods", workflow("build.yml"))

    def test_bootc_container_lint_is_in_the_containerfile(self) -> None:
        self.assertIn("bootc container lint", (REPO_ROOT / "Containerfile").read_text("utf-8"))

    def test_the_nightly_job_runs_at_0500_utc_with_read_only_contents_and_the_documented_verify_flag(self) -> None:
        nightly = workflow("nightly-compliance.yml")
        self.assertIn("05:00 UTC", flat(section("The gates")))
        self.assertIn('cron: "00 05 * * *"', nightly)
        self.assertIn("runs an hour before `build.yml`'s 06:00", flat(doc_text()))
        self.assertIn('cron: "00 06 * * *"', workflow("build.yml"))
        self.assertRegex(nightly, r"(?m)^permissions:\n  contents: read\n\n")
        self.assertIn("--new-bundle-format=false", nightly)
        self.assertIn("--new-bundle-format=false", (REPO_ROOT / "docs" / "install-and-verify.md").read_text("utf-8"))

    def test_the_nightly_job_reruns_the_suite_and_the_coverage_gate(self) -> None:
        nightly = workflow("nightly-compliance.yml")
        self.assertIn("pytest", nightly)
        self.assertIn("check_coverage.py", nightly)

    def test_the_third_party_bootstrap_step_exists(self) -> None:
        self.assertIn("`Install skopeo and cosign`", doc_text())
        for name in ("build.yml", "build-pr.yml", "nightly-compliance.yml"):
            with self.subTest(workflow=name):
                self.assertIn("- name: Install skopeo and cosign", workflow(name))

    def test_the_unmeasured_entries_are_counted_as_the_manifest_has_them(self) -> None:
        unmeasured = json.loads(THRESHOLDS.read_text("utf-8"))["unmeasured"]
        self.assertEqual(len(unmeasured), 3)
        for path in unmeasured:
            with self.subTest(path=path):
                self.assertIn(path.rsplit("/", 1)[-1], doc_text())
        self.assertIn("two of the three entries", flat(doc_text()))


class RefusalTests(unittest.TestCase):
    ROWS: ClassVar[dict[str, str]] = {
        "SIGNING_SECRET is not configured. Refusing to publish an unsigned production image": ".github/workflows/build.yml",
        "does not provide a kmod-zfs for primary kernel": "ci_tools/check_akmods_cache.py",
        "Promoted digest mismatch": "ci_tools/promote_stable.py",
        "Missing required verification key file": "ci_tools/promote_stable.py",
    }

    def test_every_refusal_this_repository_raises_is_in_the_table_and_in_the_code(self) -> None:
        table = flat(section("The checks that actually protect a booted machine"))
        for message, path in self.ROWS.items():
            with self.subTest(message=message):
                self.assertIn(message, table)
                # A message split across adjacent string literals is one message at run time.
                source = re.sub(r'"\s*\n\s*f?"', "", (REPO_ROOT / path).read_text(encoding="utf-8"))
                self.assertIn(message, flat(source))

    def test_the_table_has_the_rows_this_test_accounts_for(self) -> None:
        rows = table_rows(section("The checks that actually protect a booted machine"))
        # Four raised here plus OpenZFS's own configure message.
        self.assertEqual(len(rows), len(self.ROWS) + 1)

    def test_the_missing_key_message_is_raised_by_both_signing_and_promotion(self) -> None:
        for path in ("ci_tools/sign_image.py", "ci_tools/promote_stable.py"):
            with self.subTest(path=path):
                self.assertIn("Missing required verification key file", (REPO_ROOT / path).read_text("utf-8"))

    def test_an_unsigned_production_image_is_refused_before_the_promotion_job(self) -> None:
        text = workflow("build.yml")
        self.assertLess(text.index("SIGNING_SECRET is not configured"), text.index("\n  promote-stable:\n"))


class RedBuildTableTests(unittest.TestCase):
    def rows(self) -> dict[str, str]:
        text = section("Reading a red build").split("**Then check", 1)[0]
        return {row[0].strip("`"): flat(" ".join(row)) for row in table_rows(text)}

    def test_the_table_covers_the_build_and_test_workflows(self) -> None:
        self.assertEqual(sorted(self.rows()), ["build-pr.yml", "build.yml", "test.yml"])

    def test_each_row_trigger_matches_the_workflow(self) -> None:
        self.assertRegex(workflow("test.yml"), r"(?ms)^on:\n  pull_request:\n  push:\n    branches:\n      - main")
        self.assertRegex(workflow("build-pr.yml"), r"(?m)^  pull_request:")
        build_on = workflow("build.yml").split("jobs:", 1)[0]
        self.assertIn("schedule:", build_on)
        self.assertRegex(build_on, r"(?ms)  push:\n    branches:\n      - main")

    def test_pull_request_validation_stops_before_any_push_or_signing(self) -> None:
        text = workflow("build-pr.yml")
        self.assertNotIn("sign_image.py", text)
        self.assertNotIn("promote-stable", text)
        self.assertNotRegex(text, r"(?m)^\s+packages: write")

    def test_the_akmods_step_the_page_names_is_the_one_whose_last_line_misleads(self) -> None:
        self.assertIn("RUN --mount=type=cache,dst=/var/cache/dnf", doc_text())

    def test_the_prompt_it_links_exists(self) -> None:
        self.assertTrue((REPO_ROOT / ".github" / "prompts" / "diagnose-build-failure.prompt.md").is_file())


if __name__ == "__main__":
    unittest.main()
