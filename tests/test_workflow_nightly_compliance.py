"""
Script: tests/test_workflow_nightly_compliance.py
What: Tests the shell bodies of .github/workflows/nightly-compliance.yml -- the published-image
job's signature check and the suite job's coverage-gate step -- by extracting them from the
workflow and executing them against recording stubs.
Doing: Parses the workflow with PyYAML, pulls out the `run:` body of a named step, and runs it
under the same shell GitHub uses for `shell: bash`, with `skopeo`, `cosign` and `python3`
replaced by scripts on PATH that record their argv instead of reaching a registry.
Why: Every other signature check in this repository runs at publish time against an image the
same run just built. This workflow's two shell bodies run only at 05:00 UTC, with nothing
executing them on a pull request. That left three claims resting on shell alone -- that cosign verifies the digest skopeo just inspected rather than the moving tag,
that the workflow verifies with the same flags docs/install-and-verify.md tells users to pass,
and that a red coverage gate is not swallowed by the `| tee` that copies it into the step
summary.
Goal: Make a nightly job that stops checking what it claims to check fail here, on the pull
request that changes it, rather than at 05:00 UTC as a green run that verified nothing.

The digest invariant is the one worth stating twice. The step inspects `:latest`, records the
digest in the run summary, and then verifies. Verifying `${IMAGE}` instead of the digest would
look identical in every passing run and would differ only when `:latest` moves between the two
commands -- a concurrent production build is enough -- at which point the summary records digest
A while cosign verified digest B, and the job reports that the recorded artifact verified
without ever having checked it. `test_cosign_verifies_the_digest_that_skopeo_returned` is that
race written down: the stub returns a digest the tag does not resolve to, so a step that passed
the tag through fails here.

The coverage-gate body is a pipeline, so its exit status is the status of `tee`, not of
`tests/check_coverage.py`. It survives only because `shell: bash` makes GitHub run it with
`-o pipefail`; the default shell for a `run:` step does not set it. Dropping that one line from
the step would turn the nightly floor gate into a step that always passes, with no other visible
change, so the shell these bodies are run under is read from the workflow rather than assumed.

This runs the workflow's own text. Copying a step into the test would assert that the copy
works; extracting it means a renamed or deleted step fails loudly in `_run_body` rather than
silently testing nothing.

PyYAML is not a pytest dependency; the nightly workflow installs it by name.
The import is guarded so the suite still runs under `python3 -m unittest discover -s tests`
with nothing installed, matching the other workflow tests.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only where PyYAML is absent
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "nightly-compliance.yml"
TEST_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "test.yml"
DEFAULTS_PATH = REPO_ROOT / "ci" / "defaults.json"
INSTALL_DOC_PATH = REPO_ROOT / "docs" / "install-and-verify.md"

VERIFY_STEP = "Verify the published :latest signature"
COVERAGE_STEP = "Check per-file coverage decisions"
NIGHTLY_TEST_STEP = "Run tests"
NIGHTLY_LINT_INSTALL_STEP = "Install test runner and linter"

# What GitHub runs a `shell: bash` step with. The flags are not decoration: `-o pipefail` is the
# only reason the coverage step's exit status is check_coverage.py's rather than tee's, and the
# default shell for a `run:` step does not set it. Running the extracted body under anything
# else would test a shell the workflow never uses.
GITHUB_BASH = ["bash", "--noprofile", "--norc", "-eo", "pipefail"]

# A digest the moving tag deliberately does not resolve to, so a step that verified `${IMAGE}`
# instead of the inspected digest cannot accidentally pass.
STUB_DIGEST = "sha256:" + "ab" * 32


def _step(name: str, workflow_path: Path = WORKFLOW_PATH) -> dict:
    """
    Return the step named `name`, from whichever job declares it.

    Raises rather than returning a default when the step is missing: a step that was renamed or
    removed must fail this file, not quietly leave it asserting nothing. Step names repeat across
    jobs in this repository -- `Install test runner and linter` appears in both this workflow and
    test.yml -- so a name that matches more than once in a single workflow is an error too rather
    than a silent first-match.
    """

    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    found = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if step.get("name") == name
    ]
    if not found:
        raise AssertionError(
            f"no step named {name!r} in {workflow_path.name}; "
            "update this test if the step was renamed or removed."
        )
    if len(found) > 1:
        raise AssertionError(
            f"{len(found)} steps named {name!r} in {workflow_path.name}; "
            "this test can no longer tell which one it is asserting about."
        )
    return found[0]


def _run_body(name: str, workflow_path: Path = WORKFLOW_PATH) -> str:
    """
    Return a step's `run:` body, after checking it is a shell body this test can execute.

    The `shell: bash` assertion is part of the contract, not a precondition of the harness: see
    the module docstring on pipefail. An unexpanded `${{ }}` expression means the body depends on
    something only the Actions runner can supply, and executing it here would be executing text
    the workflow never runs.
    """

    step = _step(name, workflow_path)
    if "run" not in step:
        raise AssertionError(
            f"the {name!r} step no longer has a `run:` body; "
            "update this test if that work legitimately moved."
        )
    if step.get("shell") != "bash":
        raise AssertionError(
            f"the {name!r} step declares shell {step.get('shell')!r}, not 'bash'; "
            "without `shell: bash` GitHub runs it without `-o pipefail`."
        )
    body = step["run"]
    if "${{" in body:
        raise AssertionError(
            f"the {name!r} step's body now interpolates a `${{{{ }}}}` expression, "
            "which this test cannot supply; extend the harness or reduce the step."
        )
    return body


def _write_stub(directory: Path, name: str, script: str) -> None:
    path = directory / name
    path.write_text("#!/usr/bin/env bash\n" + script, encoding="utf-8")
    path.chmod(0o755)


def _recorded(path: Path) -> list[str]:
    """Read one recorded argv back. Absent file means the tool was never called."""

    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


@unittest.skipIf(yaml is None, "PyYAML is not installed")
@unittest.skipIf(shutil.which("bash") is None, "bash is not installed")
class VerifyPublishedSignatureStepTests(unittest.TestCase):
    """Executes the published-image job's one shell body against recording stubs."""

    def setUp(self) -> None:
        self.body = _run_body(VERIFY_STEP)
        self.image = _step(VERIFY_STEP)["env"]["IMAGE"]

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.root = Path(temp_dir.name)

        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

        # The real committed key, under its real name. A step that pointed `--key` at some other
        # file would still be recorded, but the join in
        # `test_cosign_uses_the_committed_key_and_the_documented_bundle_format` would fail.
        shutil.copy(REPO_ROOT / "cosign.pub", self.workspace / "cosign.pub")

        self.skopeo_argv = self.root / "skopeo.argv"
        self.cosign_argv = self.root / "cosign.argv"
        self.summary = self.root / "step-summary.md"
        self.summary.touch()

    def _install_stubs(self, *, skopeo_rc: int = 0, cosign_rc: int = 0) -> None:
        _write_stub(
            self.bin,
            "skopeo",
            f'printf "%s\\n" "$@" > {self.skopeo_argv}\n'
            f'if [ "{skopeo_rc}" -ne 0 ]; then\n'
            '  echo "skopeo: stub failure" >&2\n'
            f"  exit {skopeo_rc}\n"
            "fi\n"
            f'printf "%s\\n" "{STUB_DIGEST}"\n',
        )
        _write_stub(
            self.bin,
            "cosign",
            f'printf "%s\\n" "$@" > {self.cosign_argv}\n'
            f'if [ "{cosign_rc}" -ne 0 ]; then\n'
            '  echo "cosign: stub failure" >&2\n'
            "fi\n"
            f"exit {cosign_rc}\n",
        )

    def _run(self, *, skopeo_rc: int = 0, cosign_rc: int = 0) -> subprocess.CompletedProcess[str]:
        self._install_stubs(skopeo_rc=skopeo_rc, cosign_rc=cosign_rc)
        return subprocess.run(
            [*GITHUB_BASH, "-c", self.body],
            cwd=self.workspace,
            env={
                "PATH": f"{self.bin}:/usr/bin:/bin",
                "HOME": str(self.root),
                "IMAGE": self.image,
                "GITHUB_STEP_SUMMARY": str(self.summary),
            },
            capture_output=True,
            text=True,
            check=False,
        )

    # -- what the step asks the registry -------------------------------------

    def test_skopeo_inspects_the_image_the_step_declares(self) -> None:
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            _recorded(self.skopeo_argv),
            ["inspect", "--format", "{{ .Digest }}", f"docker://{self.image}"],
            "the step no longer asks skopeo for the digest of the image it declares",
        )

    def test_cosign_verifies_the_digest_that_skopeo_returned(self) -> None:
        """The step's own comment names this race; nothing until now failed when it was lost."""

        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)

        argv = _recorded(self.cosign_argv)
        self.assertEqual(argv[0], "verify", f"cosign was not asked to verify: {argv}")

        reference = argv[-1]
        repository = self.image.rsplit(":", 1)[0]
        self.assertEqual(
            reference,
            f"{repository}@{STUB_DIGEST}",
            "cosign must verify the digest skopeo just returned, not the tag: a tag that moves "
            "between the two commands makes the summary record one artifact and cosign check "
            "another",
        )
        self.assertNotIn(
            ":latest", reference, "verifying the moving tag defeats the point of the inspect"
        )

    def test_cosign_uses_the_committed_key_and_the_documented_bundle_format(self) -> None:
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)

        argv = _recorded(self.cosign_argv)
        self.assertIn("--key", argv, f"cosign was not given a key: {argv}")
        self.assertEqual(
            argv[argv.index("--key") + 1],
            "cosign.pub",
            "the check is only meaningful against the key committed to this repository",
        )
        self.assertIn(
            "--new-bundle-format=false",
            argv,
            "this repository signs with legacy cosign attachments; default v3 verification does "
            "not read them, so dropping this flag checks a signature users never present",
        )

    # -- what the step tells the reader --------------------------------------

    def test_summary_records_the_digest_that_was_verified(self) -> None:
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)

        summary = self.summary.read_text(encoding="utf-8")
        self.assertIn(self.image, summary, "the summary does not say which image was checked")
        self.assertIn(
            STUB_DIGEST,
            summary,
            "the summary must record the digest, not just the tag: the tag is the thing that "
            "moves",
        )
        self.assertEqual(
            [line for line in _recorded(self.cosign_argv) if STUB_DIGEST in line and "@" in line],
            [f"{self.image.rsplit(':', 1)[0]}@{STUB_DIGEST}"],
            "the digest in the summary and the digest cosign verified must be the same one",
        )

    def test_no_verification_is_claimed_when_cosign_fails(self) -> None:
        result = self._run(cosign_rc=1)
        self.assertNotEqual(
            result.returncode, 0, "a failed cosign verify must fail the nightly job"
        )
        self.assertNotIn(
            "Signature verifies",
            self.summary.read_text(encoding="utf-8"),
            "the summary claimed the signature verified after cosign rejected it",
        )

    def test_a_failed_inspect_stops_before_cosign(self) -> None:
        result = self._run(skopeo_rc=1)
        self.assertNotEqual(
            result.returncode, 0, "a registry that cannot be inspected must fail the job"
        )
        self.assertEqual(
            _recorded(self.cosign_argv),
            [],
            "cosign ran after the inspect failed, so it was handed an empty digest and would "
            "have verified the bare repository path",
        )


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class VerifiedImageMatchesTheRepositoryTests(unittest.TestCase):
    """The image name in the step is a literal. These join it to the things it must agree with."""

    def setUp(self) -> None:
        self.image = _step(VERIFY_STEP)["env"]["IMAGE"]

    def test_the_image_checked_is_the_image_this_repository_publishes(self) -> None:
        image_name = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))["IMAGE_NAME"]
        self.assertEqual(
            self.image,
            f"ghcr.io/danathar/{image_name}:latest",
            "the nightly check hardcodes an image reference while ci/defaults.json names the "
            "image the build publishes; a rename on either side leaves this job verifying "
            "something else, or nothing, every night",
        )

    def test_the_workflow_verifies_the_command_the_install_doc_gives_users(self) -> None:
        """
        The step's comment says verifying differently from the documented command would test
        something users are not doing. Nothing joined the two, so either could move alone.
        """

        doc = INSTALL_DOC_PATH.read_text(encoding="utf-8")
        match = re.search(r"^cosign verify(?P<args>(?:.|\n)*?)\n```", doc, re.MULTILINE)
        self.assertIsNotNone(
            match, f"no `cosign verify` command in {INSTALL_DOC_PATH.name} to compare against"
        )
        documented = match.group("args").replace("\\\n", " ").split()

        # The step's body with its line continuations collapsed, so a flag split across lines
        # reads the same here as it does on one line.
        body = " ".join(_run_body(VERIFY_STEP).replace("\\\n", " ").split())
        for flag in ("--key cosign.pub", "--new-bundle-format=false"):
            head, _, tail = flag.partition(" ")
            self.assertIn(head, documented, f"{INSTALL_DOC_PATH.name} no longer documents {head}")
            if tail:
                self.assertEqual(
                    documented[documented.index(head) + 1],
                    tail,
                    f"{INSTALL_DOC_PATH.name} documents a different value for {head}",
                )
            self.assertIn(
                flag,
                body,
                f"the nightly check no longer passes {flag}, which users are told to pass",
            )

        self.assertIn(
            self.image,
            documented,
            "the documented verify command names a different image than the one the nightly "
            "check verifies",
        )


@unittest.skipIf(yaml is None, "PyYAML is not installed")
@unittest.skipIf(shutil.which("bash") is None, "bash is not installed")
class CoverageDecisionStepTests(unittest.TestCase):
    """
    The suite job's gate step is a pipeline, and a pipeline reports the exit status of its last
    command. `tee` succeeds whenever it can write the file.
    """

    def setUp(self) -> None:
        self.body = _run_body(COVERAGE_STEP)

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.root = Path(temp_dir.name)

        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.python_argv = self.root / "python3.argv"
        self.summary = self.root / "step-summary.md"
        self.summary.touch()

    def _run(self, *, gate_rc: int) -> subprocess.CompletedProcess[str]:
        # Stands in for the real gate. Nothing here needs a Python interpreter: the step's own
        # behaviour under a red gate is the thing being tested, and check_coverage.py has its own
        # tests in tests/test_check_coverage.py.
        _write_stub(
            self.bin,
            "python3",
            f'printf "%s\\n" "$@" > {self.python_argv}\n'
            'echo "coverage decisions: 1 module below floor"\n'
            f"exit {gate_rc}\n",
        )
        return subprocess.run(
            [*GITHUB_BASH, "-c", self.body],
            cwd=self.root,
            env={
                "PATH": f"{self.bin}:/usr/bin:/bin",
                "HOME": str(self.root),
                "GITHUB_STEP_SUMMARY": str(self.summary),
            },
            capture_output=True,
            text=True,
            check=False,
        )

    def test_the_gate_runs_the_repositorys_coverage_checker(self) -> None:
        result = self._run(gate_rc=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            _recorded(self.python_argv),
            ["tests/check_coverage.py"],
            "the nightly gate no longer runs the checked-in coverage checker",
        )

    def test_the_decisions_reach_the_run_summary(self) -> None:
        result = self._run(gate_rc=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "coverage decisions",
            self.summary.read_text(encoding="utf-8"),
            "the point of the tee is that the decisions are readable without opening the log",
        )

    def test_a_red_gate_is_not_swallowed_by_the_tee(self) -> None:
        result = self._run(gate_rc=1)
        self.assertNotEqual(
            result.returncode,
            0,
            "the pipeline reported success while the coverage gate failed; without `-o pipefail` "
            "-- which only `shell: bash` supplies -- this step can never turn the nightly run red",
        )


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class NightlyMatchesThePullRequestSuiteTests(unittest.TestCase):
    """
    The suite job exists to run test.yml's suite on a clock. Where the two drift, the nightly run
    stops being the thing it claims to be a scheduled copy of, and the drift is invisible: both
    jobs still pass.
    """

    @staticmethod
    def _ruff_pin(body: str) -> str:
        match = re.search(r'"(ruff==[^"]+)"', body)
        if match is None:
            raise AssertionError(f"no pinned ruff in installer step body: {body!r}")
        return match.group(1)

    def test_both_suites_pin_the_same_ruff(self) -> None:
        nightly = self._ruff_pin(_run_body(NIGHTLY_LINT_INSTALL_STEP))
        pull_request = self._ruff_pin(_run_body(NIGHTLY_LINT_INSTALL_STEP, TEST_WORKFLOW_PATH))
        self.assertEqual(
            nightly,
            pull_request,
            "a nightly lint on a different ruff than the pull-request lint reports findings no "
            "contributor can reproduce, or misses the ones they can",
        )

    def test_the_nightly_run_writes_the_json_report_its_gate_reads(self) -> None:
        self.assertIn(
            "--cov-report=json",
            _run_body(NIGHTLY_TEST_STEP),
            "tests/check_coverage.py reads coverage.json; without this the following step fails "
            "on a missing file rather than on a coverage decision",
        )


if __name__ == "__main__":
    unittest.main()
