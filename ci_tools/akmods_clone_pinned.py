"""
Script: ci_tools/akmods_clone_pinned.py
What: Clones the exact akmods commit configured by the workflow into `/tmp/akmods`.
Doing: Recreates the directory, fetches one commit, checks detached HEAD, and verifies the SHA.
Why: Ensures we build from a known source version.
Goal: Prepare clean akmods source for later configure/build steps.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ci_tools.common import CiToolError, require_env, run_cmd


AKMODS_WORKTREE = Path("/tmp/akmods")
JUSTFILE = AKMODS_WORKTREE / "Justfile"
UPSTREAM_AKMODS_NAME_LINE = (
    "akmods_name := 'akmods' + if akmods_target != 'common' { '-' +akmods_target } else { '' }"
)
PATCHED_AKMODS_NAME_LINE = (
    "akmods_name := env('AKMODS_IMAGE_NAME', "
    "shell('yq \".images.$1[\\\"$2\\\"].$3.name\" images.yaml', version, kernel_flavor, akmods_target))"
)


def patch_publish_name_resolution() -> None:
    """
    Patch the cloned upstream Justfile so publish names come from images.yaml.

    Why this repository needs the patch:
    1. upstream derives the pushed image name from `AKMODS_TARGET`, which turns
       `zfs` into a hardcoded `akmods-zfs` publish path
    2. this repository intentionally publishes to
       `zfs-kinoite-containerfile-akmods` instead
    3. the workflow already updates `images.yaml` with that repo-specific name,
       so teaching the Justfile to read `.name` keeps one source of truth
    """
    if not JUSTFILE.exists():
        raise CiToolError(f"Expected upstream Justfile at {JUSTFILE}")

    content = JUSTFILE.read_text(encoding="utf-8")
    if PATCHED_AKMODS_NAME_LINE in content:
        print("Akmods Justfile already patched for repo-specific publish names.")
        return
    if UPSTREAM_AKMODS_NAME_LINE not in content:
        raise CiToolError(
            "Upstream akmods Justfile no longer matches the expected publish-name "
            "line. Revisit ci_tools/akmods_clone_pinned.py before continuing."
        )

    JUSTFILE.write_text(
        content.replace(UPSTREAM_AKMODS_NAME_LINE, PATCHED_AKMODS_NAME_LINE),
        encoding="utf-8",
    )
    print("Patched upstream Justfile to honor images.yaml publish names.")


def main() -> None:
    # Workflow inputs that define exactly which akmods source to use.
    upstream_repo = require_env("AKMODS_UPSTREAM_REPO")
    upstream_ref = require_env("AKMODS_UPSTREAM_REF")

    # Start from a clean checkout each run so there is no leftover state.
    shutil.rmtree(AKMODS_WORKTREE, ignore_errors=True)
    AKMODS_WORKTREE.mkdir(parents=True, exist_ok=True)

    # Create a minimal local repository at /tmp/akmods.
    # We intentionally fetch only one commit so this stays fast and deterministic.
    run_cmd(["git", "init", "."], cwd=str(AKMODS_WORKTREE))
    run_cmd(["git", "remote", "add", "origin", upstream_repo], cwd=str(AKMODS_WORKTREE))
    run_cmd(["git", "fetch", "--depth", "1", "origin", upstream_ref], cwd=str(AKMODS_WORKTREE))

    # Detached checkout keeps this worktree pinned to one exact commit (not a branch tip).
    run_cmd(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=str(AKMODS_WORKTREE))

    # Defense-in-depth: fail if Git resolved to anything other than the expected SHA.
    resolved_ref = run_cmd(["git", "rev-parse", "HEAD"], cwd=str(AKMODS_WORKTREE)).strip()
    if resolved_ref != upstream_ref:
        raise CiToolError(f"Pinned ref mismatch: expected {upstream_ref}, got {resolved_ref}")

    patch_publish_name_resolution()
    print(f"Using pinned akmods ref: {resolved_ref}")


if __name__ == "__main__":
    main()
