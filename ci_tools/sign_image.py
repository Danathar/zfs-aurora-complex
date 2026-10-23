"""
Script: ci_tools/sign_image.py
What: Signs and verifies one published image digest in GitHub Container Registry (GHCR).
Doing: Signs the digest with cosign, then verifies it against the committed public key.
Why: This is the only code that runs while the production signing key is in the
environment. It imports nothing but the standard library, so build.yml runs it as
`python3 -I ci_tools/sign_image.py`: `-I` keeps the working directory, PYTHONPATH and
user site-packages off `sys.path`, so no other file in this repository runs in the
process that holds the key. Keep it that way; tests/test_workflow_build_container.py
fails if it imports anything outside the standard library.
Goal: One small, code-owned signing step.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Exactly what a caller may hand over. Anything else is refused before cosign
# runs, so a malformed job output cannot become a signature on something else.
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
IMAGE_REPO_RE = re.compile(r"ghcr\.io/[a-z0-9._-]+/[a-z0-9._-]+")

# `cosign sign` and `cosign verify`: signature payload upload or verification.
# The same ceiling ci_tools/common.py's COSIGN_TIMEOUT sets, kept here because
# this file imports nothing from the repository. Without it a stalled TLS
# handshake holds the job, and the key, open until the job's own timeout.
COSIGN_TIMEOUT = 300.0


class SigningError(Exception):
    """A refusal or a failed command; `main` prints it and exits 1."""


def run_command(
    args: Sequence[str],
    *,
    capture_output: bool = True,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> str:
    """Run a command and return stdout, raising a readable error on failure or timeout."""

    command_env = None
    if env is not None:
        command_env = dict(os.environ)
        command_env.update(env)
    try:
        result = subprocess.run(
            list(args),
            check=True,
            text=True,
            capture_output=capture_output,
            env=command_env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise SigningError(f"Command timed out after {timeout}s: {' '.join(args)}") from exc
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or "").strip() or (exc.stdout or "").strip() or str(exc)
        # The key travels in the environment, never in argv, so argv is safe to print.
        raise SigningError(f"Command failed: {' '.join(args)}\n{details}") from exc
    if not capture_output:
        return ""
    return (result.stdout or "").strip()


def sign_published_image(
    *,
    image_repo: str,
    image_digest: str,
    cosign_private_key: str,
    command_runner: Callable[..., str] = run_command,
) -> str:
    """
    Sign and verify one published image digest.

    The caller passes the digest it pushed, never a tag. A tag can move between
    the push and this call, for example when another run republishes the shared
    akmods cache tag, and signing a re-resolved tag would sign the other run's
    image and leave this run's digest unsigned forever.
    """

    if not cosign_private_key:
        raise SigningError("SIGNING_SECRET is empty; cannot sign published image.")
    if not IMAGE_REPO_RE.fullmatch(image_repo):
        raise SigningError(
            f"Refusing to sign: {image_repo!r} is not a lowercase ghcr.io/<owner>/<name> repository."
        )
    if not DIGEST_RE.fullmatch(image_digest):
        raise SigningError(f"Refusing to sign: {image_digest!r} is not a sha256 digest.")

    verification_key = os.environ.get("COSIGN_PUBLIC_KEY_PATH", "").strip() or str(REPO_ROOT / "cosign.pub")
    if not os.path.exists(verification_key):
        raise SigningError(f"Missing required verification key file: {verification_key}")

    digest_ref = f"{image_repo}@{image_digest}"

    command_runner(
        [
            "cosign",
            "sign",
            "--yes",
            "--new-bundle-format=false",
            "--use-signing-config=false",
            "--registry-referrers-mode=legacy",
            "--key",
            "env://COSIGN_PRIVATE_KEY",
            digest_ref,
        ],
        capture_output=False,
        env={
            "COSIGN_PASSWORD": os.environ.get("COSIGN_PASSWORD", ""),
            "COSIGN_PRIVATE_KEY": cosign_private_key,
        },
        timeout=COSIGN_TIMEOUT,
    )
    command_runner(
        [
            "cosign",
            "verify",
            "--new-bundle-format=false",
            "--key",
            verification_key,
            digest_ref,
        ],
        timeout=COSIGN_TIMEOUT,
    )

    print(f"Signed published image digest: {digest_ref}")
    return digest_ref


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value.strip():
        raise SigningError(f"Missing required environment variable: {name}")
    return value


def main() -> None:
    try:
        sign_published_image(
            image_repo=_required("IMAGE_REPO").strip(),
            image_digest=_required("IMAGE_DIGEST").strip(),
            cosign_private_key=_required("COSIGN_PRIVATE_KEY"),
        )
    except SigningError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
