"""
Script: ci_tools/common.py
What: Shared helper functions used by all `ci_tools` modules.
Doing: Wraps env reads, command execution, image inspect/copy calls, parsing, and output writes.
Why: Avoids duplicated helper code.
Goal: Keep behavior consistent across all helper modules.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tarfile
from pathlib import Path
from typing import Mapping, Sequence


class CiToolError(RuntimeError):
    """Raised when a workflow helper script hits a known error condition."""


FEDORA_FROM_KERNEL_RE = re.compile(r".*fc([0-9]+).*")
NATURAL_SORT_SPLIT_RE = re.compile(r"([0-9]+)")


def require_env(name: str) -> str:
    """Return a required environment variable or raise a clear error."""
    value = os.environ.get(name)
    if value is None or value == "":
        raise CiToolError(f"Missing required environment variable: {name}")
    return value


def optional_env(name: str, default: str = "") -> str:
    """Return an environment variable with a fallback default."""
    return os.environ.get(name, default)


def kernel_releases_from_env(
    *,
    kernel_releases_var: str = "KERNEL_RELEASES",
    kernel_release_var: str = "KERNEL_RELEASE",
) -> list[str]:
    """
    Return kernel releases from workflow env, preferring the plural form.

    `KERNEL_RELEASES` is a whitespace-separated list used when one base image
    carries more than one kernel under `/lib/modules`.
    `KERNEL_RELEASE` remains supported as the single-kernel fallback.
    """
    releases = [value for value in optional_env(kernel_releases_var).split() if value]
    if releases:
        return releases

    release = optional_env(kernel_release_var).strip()
    return [release] if release else []


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


def unpack_layer_tarballs(layer_files: list[Path], destination: Path) -> None:
    """
    Extract image layer tar files into one filesystem tree.

    We pass `filter="data"` so extraction is safer:
    - blocks absolute paths and parent-directory escapes
    - blocks unsafe link targets
    This is a "fail closed" safety check for untrusted tar metadata.
    """
    for layer_file in layer_files:
        with tarfile.open(layer_file, "r") as tar:
            tar.extractall(destination, filter="data")


def load_layer_files_from_oci_layout(image_dir: Path) -> list[Path]:
    """
    Return filesystem layer tar paths from one local `skopeo copy ... dir:` tree.

    OCI dir layouts store layer digests in `manifest.json`; the actual filenames
    are the same digest strings without the `sha256:` prefix.
    """
    manifest_path = image_dir / "manifest.json"
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    layer_digests = [
        str(layer.get("digest") or "") for layer in manifest_data.get("layers", []) if layer.get("digest")
    ]
    return [image_dir / digest.replace("sha256:", "") for digest in layer_digests]


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


def print_lines_starting_with(file_path: Path, prefix: str) -> None:
    """Print line-numbered matches, similar to `grep -n '^prefix'`."""
    for index, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.startswith(prefix):
            print(f"{index}:{line}")


def extract_fedora_version(kernel_release: str) -> str:
    """
    Parse Fedora major version (for example `43`) from a kernel release.

    Example kernel release: `6.18.12-200.fc43.x86_64`.
    """
    match = FEDORA_FROM_KERNEL_RE.match(kernel_release)
    if not match:
        raise CiToolError(f"Failed to extract Fedora version from kernel release {kernel_release}")
    return match.group(1)


def replace_line_starting_with(file_path: Path, prefix: str, replacement: str) -> None:
    """Replace exactly one line that starts with `prefix`."""
    lines = file_path.read_text(encoding="utf-8").splitlines()
    changed = False
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = replacement
            changed = True
            break
    if not changed:
        raise CiToolError(f"Could not find line starting with '{prefix}' in {file_path}")
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
