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

    print(f"Using pinned akmods ref: {resolved_ref}")


if __name__ == "__main__":
    main()
