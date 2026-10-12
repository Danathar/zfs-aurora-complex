"""
Script: tests/test_metrics_doc.py
What: Joins docs/metrics.md and the dated readings under docs/metrics/ to the workflows, tool messages, tuning policy and coverage gate their commands read.
Doing: Checks that every `--workflow X.yml` the commands name is a file in .github/workflows/, that the repo-originated messages the failure classifier greps for appear in ci_tools/ or build.yml, that the job-duration loop reads policy keys the tuning file has and a job-name example that is a real `name:`, that the coverage command uses the flags test.yml uses, and that every command in a dated reading names this repository.
Why: The page is commands plus the claim that each reads something real. A renamed workflow turns `gh run list --workflow` into an empty answer that looks like "no failures"; a reworded refusal makes the classifier loop report nothing for the failure it exists to count.
Goal: Make a renamed workflow, a reworded guard message, a renamed tuning key or a diverged coverage command fail here, on the pull request that causes it.

Three of the classifier's alternatives (`unexpected EOF`, `Cannot build against kernel version`, `Argument list too long`) come from tools outside this repository -- OpenZFS's configure, the Linux exec limit and podman -- so nothing here can check them, and this file does not pretend to. Only the messages this repository raises are joined to its code.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "metrics.md"
SNAPSHOTS = REPO_ROOT / "docs" / "metrics"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
TUNING = REPO_ROOT / ".github" / "auto-qa-tuning.json"
THRESHOLDS = REPO_ROOT / ".coverage-thresholds.json"
SLUG = "Danathar/zfs-aurora-complex"

# Messages in the classifier's grep that this repository itself prints, and where.
OWN_MESSAGES = {
    "SIGNING_SECRET is not configured": ".github/workflows/build.yml",
    "does not provide a kmod-zfs": "ci_tools/check_akmods_cache.py",
    "Promoted digest mismatch": "ci_tools/promote_stable.py",
}
# Alternatives that come from other tools. Listed so a new alternative has to be placed
# in one group or the other, rather than slipping past both.
EXTERNAL_MESSAGES = {
    "unexpected EOF",
    "Cannot build against kernel version",
    "Argument list too long",
    'Error: building at STEP "[A-Z]+ [^"]{0,40}',
    "failed to get run log: HTTP 410",
}


def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def classifier_alternatives() -> list[str]:
    match = re.search(r'grep -oE "\((.*?)\)" \\\n', doc_text())
    assert match, "the classifier loop's grep is gone from docs/metrics.md"
    return match.group(1).replace('\\"', '"').split("|")


class WorkflowTargetTests(unittest.TestCase):
    def test_every_workflow_a_command_names_exists(self) -> None:
        texts = [DOC.read_text("utf-8")] + [p.read_text("utf-8") for p in sorted(SNAPSHOTS.glob("*.md"))]
        named = {w for text in texts for w in re.findall(r"--workflow ([\w.-]+\.yml)", text)}
        self.assertGreaterEqual(len(named), 2)
        missing = sorted(w for w in named if not (WORKFLOWS / w).is_file())
        self.assertEqual(missing, [])

    def test_the_hive_class_login_is_the_one_the_audit_uses(self) -> None:
        self.assertIn("app/danathar-atomic-hive", doc_text())
        self.assertIn("app/danathar-atomic-hive", (WORKFLOWS / "agent-audit.yml").read_text("utf-8"))


class FailureClassifierTests(unittest.TestCase):
    def test_every_alternative_is_placed_in_one_group(self) -> None:
        alternatives = set(classifier_alternatives())
        self.assertEqual(alternatives, set(OWN_MESSAGES) | EXTERNAL_MESSAGES)

    def test_every_message_this_repository_raises_is_still_raised(self) -> None:
        for message, path in OWN_MESSAGES.items():
            with self.subTest(message=message):
                self.assertIn(message, (REPO_ROOT / path).read_text(encoding="utf-8"))

    def test_the_scheduled_gate_skip_the_page_calls_green_is_a_real_job(self) -> None:
        self.assertIn("name: Evaluate Stable Signal Gate", (WORKFLOWS / "build.yml").read_text("utf-8"))


class JobDurationTests(unittest.TestCase):
    def test_the_policy_key_the_command_reads_exists(self) -> None:
        keys = re.findall(r"jq -r '\.([\w.]+)' \.github/auto-qa-tuning\.json", doc_text())
        self.assertEqual(keys, ["policy.sample_size"])
        policy = json.loads(TUNING.read_text(encoding="utf-8"))
        self.assertIsInstance(policy["policy"]["sample_size"], int)
        self.assertEqual(policy["policy"]["direction"], "report-only")
        self.assertIn("`sample_size`", doc_text())

    def test_the_command_lists_the_workflows_directory(self) -> None:
        self.assertIn("ls .github/workflows", doc_text())

    def test_the_job_name_example_is_the_workflows_name(self) -> None:
        # "`build-zfs-akmods` prints as `Build Shared ZFS Akmods Cache`"
        self.assertIn("`build-zfs-akmods` prints as `Build Shared ZFS Akmods Cache`", " ".join(doc_text().split()))
        text = (WORKFLOWS / "build.yml").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^  build-zfs-akmods:\n    name: Build Shared ZFS Akmods Cache$")

    def test_the_tuning_file_names_only_real_jobs(self) -> None:
        jobs = json.loads(TUNING.read_text(encoding="utf-8"))["jobs"]
        for entry in jobs:
            with self.subTest(entry=entry):
                text = (WORKFLOWS / entry["workflow"]).read_text(encoding="utf-8")
                self.assertRegex(text, rf"(?m)^  {re.escape(entry['job'])}:$")


class CoverageCommandTests(unittest.TestCase):
    def command(self) -> str:
        match = re.search(r"python3 -m pytest tests/ \\\n(.*?)python3 tests/check_coverage\.py", doc_text(), re.DOTALL)
        self.assertIsNotNone(match, "the coverage command is gone from docs/metrics.md")
        return match.group(1)

    def test_the_page_uses_the_cov_flags_test_yml_uses(self) -> None:
        page = sorted(re.findall(r"--cov(?:=[\w/-]+|-branch|-report=\w+)", self.command()))
        ci = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
        in_ci = set(re.findall(r"--cov(?:=[\w/-]+|-branch|-report=\w+)", ci))
        # CI also prints term-missing; the page keeps only what the gate reads.
        self.assertEqual(set(page) - in_ci, set())
        self.assertIn("--cov-report=json", page)
        self.assertEqual({f for f in page if f.startswith("--cov=")}, {f for f in in_ci if f.startswith("--cov=")})

    def test_the_unmeasured_files_the_page_names_are_unmeasured_keys(self) -> None:
        data = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
        unmeasured = data.get("unmeasured", {})
        for path in ("Containerfile", "build_files/build-image.sh"):
            with self.subTest(path=path):
                self.assertIn(path, unmeasured)
                self.assertIn(path, doc_text())

    def test_the_test_the_page_cites_exists(self) -> None:
        self.assertIn("tests/test_workflow_build_container.py", doc_text())
        self.assertTrue((REPO_ROOT / "tests" / "test_workflow_build_container.py").is_file())


class SnapshotTests(unittest.TestCase):
    def test_there_is_a_reading_and_it_is_named_for_a_day(self) -> None:
        files = sorted(SNAPSHOTS.glob("*.md"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(path=path.name):
                self.assertRegex(path.name, r"^\d{4}-\d{2}-\d{2}\.md$")
                self.assertIn(f"docs/metrics/{path.name}", doc_text())

    def test_every_gh_command_in_a_reading_names_this_repository(self) -> None:
        for path in sorted(SNAPSHOTS.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            commands = re.findall(r"(?ms)^```(?:bash|sh)\n(.*?)```", text)
            self.assertTrue(commands)
            for block in commands:
                for statement in re.split(r"\n(?=\S)", block):
                    if re.match(r"(for |gh )", statement) and "gh " in statement:
                        with self.subTest(path=path.name, statement=statement.splitlines()[0]):
                            self.assertIn(SLUG, statement)


if __name__ == "__main__":
    unittest.main()
