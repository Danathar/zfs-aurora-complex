"""
Script: tests/test_sign_image.py
What: Tests for published-image signing.
Doing: Checks the refusals, the exact cosign command sequence, the environment
contract `main` reads, and that the script runs on its own under `python3 -I`.
Why: build.yml's signing jobs run this file as `python3 -I ci_tools/sign_image.py`
with the production key in the environment. It must refuse bad input before cosign
runs, and it must work with nothing but the standard library on `sys.path`.
Goal: Keep digest signing explicit, isolated, and safe to change.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from ci_tools import common, sign_image
from ci_tools.sign_image import (
    COSIGN_TIMEOUT,
    SigningError,
    main,
    run_command,
    sign_published_image,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "ci_tools" / "sign_image.py"
REPO = "ghcr.io/danathar/zfs-aurora-complex"
DIGEST = "sha256:" + "a" * 64


def _exploding_runner(*_args, **_kwargs) -> str:
    raise AssertionError("cosign must not run")


class RefusalTests(unittest.TestCase):
    """Every refusal happens before cosign is called."""

    def _refused(self, **overrides) -> str:
        arguments = {"image_repo": REPO, "image_digest": DIGEST, "cosign_private_key": "key"}
        arguments.update(overrides)
        with self.assertRaises(SigningError) as raised:
            sign_published_image(command_runner=_exploding_runner, **arguments)
        return str(raised.exception)

    def test_an_empty_key_is_refused(self) -> None:
        self.assertEqual(
            self._refused(cosign_private_key=""),
            "SIGNING_SECRET is empty; cannot sign published image.",
        )

    def test_a_repository_that_is_not_a_lowercase_ghcr_path_is_refused(self) -> None:
        for repo in (
            "ghcr.io/Danathar/zfs-aurora-complex",
            "quay.io/danathar/zfs-aurora-complex",
            "ghcr.io/danathar/zfs-aurora-complex:latest",
            "ghcr.io/danathar",
            "ghcr.io/danathar/a/b",
            "",
        ):
            with self.subTest(repo=repo):
                self.assertIn("is not a lowercase ghcr.io/<owner>/<name> repository", self._refused(image_repo=repo))

    def test_anything_but_a_full_sha256_digest_is_refused(self) -> None:
        for digest in ("latest", "sha256:abc", "sha256:" + "A" * 64, "sha512:" + "a" * 64, DIGEST + "\n", ""):
            with self.subTest(digest=digest):
                self.assertIn("is not a sha256 digest", self._refused(image_digest=digest))

    def test_a_missing_verification_key_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "absent.pub")
            with unittest.mock.patch.dict(os.environ, {"COSIGN_PUBLIC_KEY_PATH": missing}):
                self.assertEqual(self._refused(), f"Missing required verification key file: {missing}")


class SigningCommandTests(unittest.TestCase):
    def _sign(self, env: dict[str, str] | None = None) -> tuple[str, list]:
        calls: list = []

        def recorder(args, **kwargs) -> str:
            calls.append((list(args), kwargs))
            return ""

        with unittest.mock.patch.dict(os.environ, env or {}, clear=False):
            ref = sign_published_image(
                image_repo=REPO, image_digest=DIGEST, cosign_private_key="secret", command_runner=recorder
            )
        return ref, calls

    def test_signs_then_verifies_the_digest_with_the_repository_key(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"COSIGN_PUBLIC_KEY_PATH": ""}):
            ref, calls = self._sign()
        self.assertEqual(ref, f"{REPO}@{DIGEST}")
        self.assertEqual(len(calls), 2)
        sign_args, sign_kwargs = calls[0]
        self.assertEqual(
            sign_args,
            [
                "cosign",
                "sign",
                "--yes",
                "--new-bundle-format=false",
                "--use-signing-config=false",
                "--registry-referrers-mode=legacy",
                "--key",
                "env://COSIGN_PRIVATE_KEY",
                f"{REPO}@{DIGEST}",
            ],
        )
        # The key reaches cosign through the environment, never argv.
        self.assertEqual(sign_kwargs["env"]["COSIGN_PRIVATE_KEY"], "secret")
        self.assertNotIn("secret", " ".join(sign_args))
        self.assertEqual(
            calls[1][0],
            ["cosign", "verify", "--new-bundle-format=false", "--key", str(REPO_ROOT / "cosign.pub"), f"{REPO}@{DIGEST}"],
        )

    def test_cosign_password_comes_from_the_environment(self) -> None:
        _ref, calls = self._sign({"COSIGN_PASSWORD": "pw"})
        self.assertEqual(calls[0][1]["env"]["COSIGN_PASSWORD"], "pw")

    def test_the_default_key_path_does_not_depend_on_the_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.dict(os.environ, {"COSIGN_PUBLIC_KEY_PATH": ""}):
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                _ref, calls = self._sign()
            finally:
                os.chdir(previous)
        self.assertIn(str(REPO_ROOT / "cosign.pub"), calls[1][0])

    def test_cosign_sign_and_verify_pass_cosign_timeout(self) -> None:
        # Without a ceiling a stalled TLS handshake holds the job, and the key, open
        # until the job's own timeout.
        _ref, calls = self._sign()
        self.assertEqual([kwargs.get("timeout") for _args, kwargs in calls], [COSIGN_TIMEOUT, COSIGN_TIMEOUT])

    def test_the_timeout_is_the_one_the_rest_of_ci_tools_uses(self) -> None:
        # sign_image.py keeps its own copy because it may import nothing from the repository.
        self.assertEqual(COSIGN_TIMEOUT, common.COSIGN_TIMEOUT)

    def test_a_command_that_outlives_its_timeout_raises(self) -> None:
        with self.assertRaises(SigningError) as raised:
            run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2)
        self.assertIn("timed out after 0.2s", str(raised.exception))

    def test_cosign_sign_prints_to_the_job_log_and_verify_is_captured(self) -> None:
        _ref, calls = self._sign()
        self.assertIs(calls[0][1]["capture_output"], False)
        self.assertNotIn("capture_output", calls[1][1])


class RunCommandTests(unittest.TestCase):
    """The real subprocess wrapper: `cosign sign` only gets the key through `env`."""

    def test_a_failed_command_raises_with_its_output(self) -> None:
        with self.assertRaises(SigningError) as raised:
            run_command([sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"])
        self.assertIn("boom", str(raised.exception))

    def test_stderr_is_reported_before_stdout_and_stdout_when_stderr_is_empty(self) -> None:
        both = "import sys; sys.stdout.write('to-stdout'); sys.stderr.write('to-stderr'); sys.exit(3)"
        with self.assertRaises(SigningError) as raised:
            run_command([sys.executable, "-c", both])
        self.assertTrue(str(raised.exception).endswith("\nto-stderr"), str(raised.exception))

        only_stdout = "import sys; sys.stdout.write('to-stdout'); sys.exit(3)"
        with self.assertRaises(SigningError) as raised:
            run_command([sys.executable, "-c", only_stdout])
        self.assertTrue(str(raised.exception).endswith("\nto-stdout"), str(raised.exception))

    def test_env_is_added_to_the_inherited_environment(self) -> None:
        # cosign needs PATH, HOME and the registry login from the job's own
        # environment as well as the key, so `env` extends os.environ, and wins
        # where both name the same variable.
        script = "import os; print(os.environ['ZAC_INHERITED'], os.environ['ZAC_ADDED'], os.environ['ZAC_BOTH'])"
        with unittest.mock.patch.dict(os.environ, {"ZAC_INHERITED": "job", "ZAC_BOTH": "job"}):
            output = run_command([sys.executable, "-c", script], env={"ZAC_ADDED": "key", "ZAC_BOTH": "key"})
        self.assertEqual(output, "job key key")

    def test_without_env_the_child_sees_only_the_inherited_environment(self) -> None:
        script = "import os; print(os.environ.get('ZAC_INHERITED'), os.environ.get('COSIGN_PRIVATE_KEY'))"
        with unittest.mock.patch.dict(os.environ, {"ZAC_INHERITED": "job"}):
            os.environ.pop("COSIGN_PRIVATE_KEY", None)
            output = run_command([sys.executable, "-c", script])
        self.assertEqual(output, "job None")


class MainTests(unittest.TestCase):
    def _main(self, env: dict[str, str]) -> unittest.mock.MagicMock:
        with (
            unittest.mock.patch.dict(os.environ, env, clear=True),
            unittest.mock.patch.object(sign_image, "sign_published_image") as signer,
        ):
            main()
        return signer

    def test_each_variable_reaches_the_parameter_it_names(self) -> None:
        signer = self._main({"IMAGE_REPO": f" {REPO} ", "IMAGE_DIGEST": f"{DIGEST}\n", "COSIGN_PRIVATE_KEY": "key\n"})
        signer.assert_called_once_with(image_repo=REPO, image_digest=DIGEST, cosign_private_key="key\n")

    def test_a_missing_variable_exits_1_before_anything_is_signed(self) -> None:
        for missing in ("IMAGE_REPO", "IMAGE_DIGEST", "COSIGN_PRIVATE_KEY"):
            env = {"IMAGE_REPO": REPO, "IMAGE_DIGEST": DIGEST, "COSIGN_PRIVATE_KEY": "key"}
            env[missing] = " "
            with (
                self.subTest(missing=missing),
                unittest.mock.patch.dict(os.environ, env, clear=True),
                unittest.mock.patch.object(sign_image, "sign_published_image") as signer,
                self.assertRaises(SystemExit) as raised,
            ):
                main()
            self.assertEqual(raised.exception.code, 1)
            signer.assert_not_called()


class IsolationTests(unittest.TestCase):
    """build.yml runs this file as `python3 -I ci_tools/sign_image.py`."""

    def test_it_imports_only_the_standard_library(self) -> None:
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, "a relative import would load repository code")
                imported.add((node.module or "").split(".")[0])
        self.assertTrue(imported, "the import scan found nothing")
        self.assertEqual(sorted(imported - set(sys.stdlib_module_names)), [])

    def test_it_runs_isolated_from_another_directory_and_refuses_bad_input(self) -> None:
        env = {"PATH": os.environ.get("PATH", ""), "IMAGE_REPO": REPO, "IMAGE_DIGEST": "latest", "COSIGN_PRIVATE_KEY": "k"}
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, "-I", str(SCRIPT)],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("is not a sha256 digest", result.stderr)


if __name__ == "__main__":
    unittest.main()
