"""
Script: ci_tools/common.py
What: Shared helper functions used by all `ci_tools` modules.
Doing: Wraps env reads, command execution, image inspect/copy calls, parsing, and output writes.
Why: Avoids duplicated helper code.
Goal: Keep behavior consistent across all helper modules.
"""

from __future__ import annotations

from functools import lru_cache
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Mapping, Sequence


class CiToolError(RuntimeError):
    """Raised when a workflow helper script hits a known error condition."""


FEDORA_FROM_KERNEL_RE = re.compile(r".*fc([0-9]+).*")
NATURAL_SORT_SPLIT_RE = re.compile(r"([0-9]+)")
REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_DEFAULTS_FILE = REPO_ROOT / "ci" / "defaults.json"


def require_env(name: str) -> str:
    """Return a required environment variable or raise a clear error."""
    value = os.environ.get(name)
    if value is None or value == "":
        raise CiToolError(f"Missing required environment variable: {name}")
    return value


def optional_env(name: str, default: str = "") -> str:
    """Return an environment variable with a fallback default."""
    return os.environ.get(name, default)


@lru_cache(maxsize=1)
def load_repo_defaults() -> dict[str, str]:
    """
    Load checked-in repository defaults from `ci/defaults.json`.

    Keeping these defaults in version control makes workflow input changes
    reviewable. The workflows still pass explicit overrides when needed, but the
    default values themselves live in one file instead of being copied across
    multiple workflow files.
    """
    if not REPO_DEFAULTS_FILE.exists():
        raise CiToolError(f"Missing repository defaults file: {REPO_DEFAULTS_FILE}")

    with REPO_DEFAULTS_FILE.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    defaults: dict[str, str] = {}
    for key, value in data.items():
        defaults[str(key)] = str(value)
    return defaults


def require_env_or_default(name: str) -> str:
    """
    Return an environment variable, falling back to checked-in repo defaults.

    This keeps the Python helpers honest even if the workflow files become
    thinner over time. A command still stops with an error when the value is
    missing from both env and `ci/defaults.json`.
    """
    value = os.environ.get(name)
    if value is not None and value != "":
        return value

    default_value = load_repo_defaults().get(name, "")
    if default_value:
        return default_value

    raise CiToolError(
        f"Missing required environment variable: {name} "
        f"(and no fallback exists in {REPO_DEFAULTS_FILE})"
    )


def run_cmd(
    args: Sequence[str],
    *,
    capture_output: bool = True,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Run a command and return stdout, raising a readable error on failure."""
    try:
        command_env = None
        if env is not None:
            # Command-specific env overrides let helpers inject secrets or one-off
            # flags without mutating global process env for the rest of the job.
            command_env = dict(os.environ)
            command_env.update(env)
        result = subprocess.run(
            list(args),
            check=True,
            text=True,
            capture_output=capture_output,
            cwd=cwd,
            env=command_env,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = stderr or stdout or str(exc)
        raise CiToolError(f"Command failed: {' '.join(args)}\n{details}") from exc

    if not capture_output:
        return ""
    return result.stdout


def git_ls_remote_resolve(repo_url: str, ref: str) -> str:
    """
    Resolve a git ref (branch, tag, or SHA) on a remote repository to a concrete commit SHA.

    Used by the input resolver to float the akmods tracking ref to a pinned SHA
    before the clone step runs. Keeping the resolution here (not in the clone
    helper) preserves the `rev-parse HEAD` SHA-verification invariant in
    `akmods_clone_pinned`, which assumes its input is already a concrete SHA.
    """
    if not repo_url:
        raise CiToolError("git_ls_remote_resolve requires a non-empty repo_url")
    if not ref:
        raise CiToolError("git_ls_remote_resolve requires a non-empty ref")

    output = run_cmd(["git", "ls-remote", "--exit-code", repo_url, ref])
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        sha, _tab, name = line.partition("\t")
        sha = sha.strip()
        name = name.strip()
        if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
            if name == f"refs/heads/{ref}" or name == f"refs/tags/{ref}" or name == ref or name == "HEAD":
                return sha
    # Fall back to the first line's SHA if the name didn't match a known form.
    first = output.strip().splitlines()[0] if output.strip() else ""
    sha = first.split("\t", 1)[0].strip() if first else ""
    if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
        return sha
    raise CiToolError(f"git ls-remote did not return a resolvable commit SHA for {ref} at {repo_url}")


def run_json_cmd(args: Sequence[str]) -> dict:
    """Run a command that returns JSON and parse it."""
    output = run_cmd(args)
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise CiToolError(f"Expected JSON from command: {' '.join(args)}") from exc


def write_github_outputs(values: Mapping[str, str]) -> None:
    """
    Write step outputs for GitHub Actions.

    GitHub provides a file path in `GITHUB_OUTPUT`; writing `name=value` lines
    there makes that value available to later steps in the same job.
    """
    output_file = require_env("GITHUB_OUTPUT")
    with open(output_file, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def write_github_env(values: Mapping[str, str]) -> None:
    """
    Export environment variables for later GitHub Actions steps.

    GitHub exposes the file path through `GITHUB_ENV`. Writing `NAME=value`
    lines there makes the variable available to subsequent steps in the same
    job.
    """
    env_file = require_env("GITHUB_ENV")
    with open(env_file, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def normalize_owner(owner: str) -> str:
    """
    Normalize a GitHub owner/org for container image paths.

    Here, "normalize" means converting to lowercase.
    Example: `Danathar` becomes `danathar`, so image refs are consistent:
    `ghcr.io/danathar/...`.
    """
    return owner.lower()


def skopeo_inspect_json(image_ref: str, *, creds: str | None = None) -> dict:
    """
    Return JSON metadata for one image reference.

    `skopeo` reads image metadata directly from the registry without pulling and
    running a container image.
    """
    command = ["skopeo", "inspect"]
    if creds:
        command.extend(["--creds", creds])
    command.append(image_ref)
    return run_json_cmd(command)


def skopeo_inspect_digest(image_ref: str, *, creds: str | None = None) -> str:
    """Return the image digest from `skopeo inspect` output."""
    inspect_json = skopeo_inspect_json(image_ref, creds=creds)
    digest = str(inspect_json.get("Digest") or "")
    if not digest:
        raise CiToolError(f"Missing digest in skopeo inspect output for {image_ref}")
    return digest


def skopeo_exists(image_ref: str, *, creds: str | None = None) -> bool:
    """True when the given image tag exists in the registry."""
    command = ["skopeo", "inspect"]
    if creds:
        command.extend(["--creds", creds])
    command.append(image_ref)
    try:
        run_cmd(command)
        return True
    except CiToolError:
        return False


def skopeo_copy(
    source: str,
    destination: str,
    *,
    creds: str | None = None,
    retry_times: int = 3,
) -> None:
    """Copy an image between registry references using skopeo."""
    command = ["skopeo", "copy", "--retry-times", str(retry_times)]
    if creds:
        command.extend(["--src-creds", creds, "--dest-creds", creds])
    command.extend([source, destination])
    run_cmd(command, capture_output=False)


def natural_sort_key(value: str) -> list[int | str]:
    """
    Return a natural-sort key so kernel strings order numerically where needed.

    Example:
    - `6.18.9-200.fc43.x86_64` sorts before `6.18.10-200.fc43.x86_64`
    """
    parts = NATURAL_SORT_SPLIT_RE.split(value)
    return [int(part) if part.isdigit() else part for part in parts]


def sort_kernel_releases(kernel_releases: Sequence[str]) -> list[str]:
    """Return unique kernel release strings in stable natural-sort order."""
    return sorted(dict.fromkeys(kernel_releases), key=natural_sort_key)


def extract_fedora_version(kernel_release: str) -> str:
    """
    Parse Fedora major version (for example `43`) from a kernel release.

    Example kernel release: `6.18.12-200.fc43.x86_64`.
    """
    match = FEDORA_FROM_KERNEL_RE.match(kernel_release)
    if not match:
        raise CiToolError(f"Failed to extract Fedora version from kernel release {kernel_release}")
    return match.group(1)
